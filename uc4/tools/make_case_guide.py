"""
The per-case guide, generated from the knowledge base and the database.

    python tools/make_case_guide.py
    python tools/make_case_guide.py --no-pdf

Writes docs/CASE_GUIDE.md and docs/CASE_GUIDE.pdf.

**Nothing in the guide is typed by hand.** Every rule id, condition, weight,
confidence, verdict and sentence comes out of kb/*.csv or out of a database
built by running tools/run_demo.py end to end. The prose that does exist is
fixed scaffolding - headings, column names and the explanations of what a term
means - and it never states a figure.

That is the point of generating it: a guide with hand-copied numbers goes stale
the first time a weight changes, and nobody notices. tests/test_case_guide.py
regenerates this file and checks its figures against the database, so a KB edit
that the guide does not reflect fails the suite rather than quietly misleading
a reader.

The database is the END state - every case run all the way through, with the
scripted human decisions replayed - not the demo reset state, which stops each
case at its first human action.
"""

import argparse
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from orchestrator import db, holds                                    # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import risk_assessment                        # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402

DEFAULT_OUT = UC4 / "docs"
CLOSED = ("approved", "rejected", "closed_withdrawn")


# ---------------------------------------------------------------------------
# Turning machine-readable rules into plain words
# ---------------------------------------------------------------------------

def words(value: str) -> str:
    """snake_case -> readable words, for a name shown to a reader."""
    return (value or "").replace("_", " ").strip()


def plain_condition(condition: str) -> str:
    """One applicant-type condition in plain words.

    The KB's condition language is four operators; this says each of them as a
    sentence rather than leaving the reader to parse `gt` and `is_true`.
    """
    parts = []
    for clause in (condition or "").split("&&"):
        bits = clause.strip().split(" ", 2)
        if len(bits) < 2:
            continue
        field, op = bits[0], bits[1]
        value = bits[2] if len(bits) > 2 else ""
        name = words(field)
        if op == "eq":
            parts.append(f"{name} is exactly `{value}`")
        elif op == "in":
            options = "`, `".join(value.split(";"))
            parts.append(f"{name} is one of `{options}`")
        elif op == "gt":
            parts.append(f"{name} is greater than {value}")
        elif op == "is_true":
            parts.append(f"{name} is true")
        else:
            parts.append(f"{name} {op} {value}")
    return " **and** ".join(parts) if parts else "no condition - matches anything"


def clause_result(clause: str, facts: dict) -> tuple[bool, str]:
    """Did this one clause hold, and what was the application's value?"""
    bits = clause.strip().split(" ", 2)
    if len(bits) < 2:
        return True, ""
    field, op = bits[0], bits[1]
    value = bits[2] if len(bits) > 2 else ""
    actual = facts.get(field)
    shown = "not given" if actual is None else f"`{actual}`"
    if op == "eq":
        return str(actual) == value, shown
    if op == "in":
        return str(actual) in value.split(";"), shown
    if op == "gt":
        try:
            return actual is not None and float(actual) > float(value), shown
        except (TypeError, ValueError):
            return False, shown
    if op == "is_true":
        return actual is True or str(actual).lower() == "true", shown
    return False, shown


# ---------------------------------------------------------------------------
# Small markdown helpers
# ---------------------------------------------------------------------------

def table(headers: list, rows: list) -> list:
    """A pipe table, or a plain line when there is nothing to show."""
    if not rows:
        return ["_Nothing to show._", ""]
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        cells = [str("" if c is None else c).replace("|", "\\|").replace("\n", " ")
                 for c in row]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return out


def dash(value, blank="&mdash;"):
    return blank if value in (None, "") else value


def yesno(value) -> str:
    return "yes" if value else "no"


def money_int(value):
    return "&mdash;" if value is None else str(value)


# ---------------------------------------------------------------------------
# Reading the case out of the database
# ---------------------------------------------------------------------------

class Case:
    """Everything one case needs, read once."""

    def __init__(self, conn, case_id, kb, application):
        self.conn, self.kb, self.id = conn, kb, case_id
        self.app = application
        q = lambda s, p=(): [dict(r) for r in conn.execute(s, p)]
        self.case = q("SELECT * FROM onboarding_case WHERE case_id=?", (case_id,))[0]
        self.applicant = q("SELECT * FROM applicant WHERE applicant_id=?",
                           (self.case["applicant_id"],))[0]
        self.pack = (q("SELECT * FROM requirement_pack WHERE case_id=?", (case_id,)) or [{}])[0]
        self.items = q("SELECT * FROM checklist_item i JOIN requirement_pack p USING(pack_id)"
                       " WHERE p.case_id=? ORDER BY i.item_id", (case_id,))
        self.documents = q("SELECT * FROM document WHERE case_id=? ORDER BY document_id", (case_id,))
        self.fields = q("SELECT f.*, d.file_name FROM extracted_field f JOIN document d"
                        " USING(document_id) WHERE d.case_id=? ORDER BY f.field_id", (case_id,))
        self.people = q("SELECT * FROM individual WHERE applicant_id=? ORDER BY individual_id",
                        (self.case["applicant_id"],))
        self.ubos = q("SELECT u.*, i.full_name FROM ubo u JOIN individual i USING(individual_id)"
                      " WHERE u.applicant_id=? ORDER BY u.ubo_id", (self.case["applicant_id"],))
        self.registry = q("SELECT * FROM registry_check WHERE case_id=?", (case_id,))
        self.identity = q("SELECT c.*, i.full_name FROM identity_check c JOIN individual i"
                          " USING(individual_id) WHERE c.case_id=? ORDER BY c.check_id", (case_id,))
        self.screening = q("SELECT s.*, i.full_name FROM screening_check s LEFT JOIN individual i"
                           " USING(individual_id) WHERE s.case_id=? ORDER BY s.check_id", (case_id,))
        self.assessment = (q("SELECT * FROM risk_assessment WHERE case_id=?", (case_id,)) or [None])[0]
        self.factors = (q("SELECT * FROM risk_factor WHERE assessment_id=? ORDER BY factor_id",
                          (self.assessment["assessment_id"],)) if self.assessment else [])
        self.decisions = q("SELECT * FROM human_decision WHERE case_id=? ORDER BY decision_id", (case_id,))
        self.comms = q("SELECT * FROM communication WHERE case_id=? ORDER BY communication_id", (case_id,))
        self.events = q("SELECT * FROM audit_event WHERE case_id=? ORDER BY event_id", (case_id,))
        self.tasks = q("SELECT * FROM compliance_task WHERE case_id=?", (case_id,))
        self.holds = holds.open_holds(conn, case_id)
        self.facts, self.evidence = risk_assessment.gather_facts(conn, case_id, kb)

    @property
    def name(self):
        return self.applicant["legal_name"]

    def fields_for(self, document_id):
        return [f for f in self.fields if f["document_id"] == document_id]

    def rule(self, rule_id):
        for r in self.kb.requirement_rules:
            if r["rule_id"] == rule_id:
                return r
        return {}

    def person(self, individual_id):
        for p in self.people:
            if p["individual_id"] == individual_id:
                return p
        return None


# ---------------------------------------------------------------------------
# A. Classification
# ---------------------------------------------------------------------------

def section_classification(c) -> list:
    out = ["### A. Classification", "",
           "Intake reads `kb/applicant_type_rules.csv` in priority order and takes the "
           "**first** rule whose conditions all hold. Later rules are never looked at.", ""]

    facts = {"entity_type": c.applicant["entity_type"],
             "programme_type": c.app.get("programme_type", "direct"),
             **(c.app.get("ownership") or {})}

    out += ["The values this application was judged on:", ""]
    out += table(["Value", "This application"],
                 [[f"`{k}`", dash(v, "not given")] for k, v in facts.items()])

    rows, matched = [], None
    for rule in c.kb.applicant_type_rules:
        clauses = [x for x in rule["conditions"].split("&&") if x.strip()]
        results = [clause_result(x, facts) for x in clauses]
        holds_all = all(ok for ok, _ in results)
        if matched is None and holds_all:
            matched, verdict = rule, "**MATCHED - this rule decided the case**"
        elif matched is not None:
            verdict = "not checked - an earlier rule already matched"
        else:
            failed = [f"{words(x.strip().split(' ')[0])} was {shown}"
                      for x, (ok, shown) in zip(clauses, results) if not ok]
            verdict = "did not match: " + "; ".join(failed)
        rows.append([rule["rule_id"], rule["priority"],
                     plain_condition(rule["conditions"]), rule["applicant_type"], verdict])
    out += table(["Rule", "Order", "Condition in plain words", "Would give", "What happened"], rows)

    if matched:
        out += [f"**Result: `{matched['applicant_type']}`, route `{matched['route']}`, "
                f"by rule {matched['rule_id']}.** {matched['description']}", ""]
    else:
        out += ["**No rule matched**, so the case went to manual review.", ""]

    routing = c.kb.jurisdiction_routing.get(c.applicant["country"], {})
    out += ["**Jurisdiction and entity scope.** `kb/jurisdiction_routing.csv` maps the "
            "applicant's country to a rule set and to the Wallester entity that would "
            "hold the relationship.", ""]
    out += table(["Country", "Rule set", "Entity scope", "Supported", "Rule"],
                 [[c.applicant["country"], dash(c.case["jurisdiction_path"]),
                   dash(c.case["entity_scope"]), dash(routing.get("supported")),
                   dash(routing.get("rule_id"))]])
    if (c.case["entity_scope"] or "") == "undetermined":
        out += ["The scope is `undetermined` on purpose: which Wallester entity contracts "
                "with a white-label partner is settled in the programme phase, not here.", ""]
    return out


# ---------------------------------------------------------------------------
# B. Checklist
# ---------------------------------------------------------------------------

def section_checklist(c) -> list:
    out = ["### B. Checklist", "",
           "Step 2 builds this from `kb/requirement_rule.csv`, taking every rule that "
           "applies to this applicant type, jurisdiction and entity type. A rule marked "
           "*required* must be satisfied before the paid checks in Step 5 will run. "
           "A *conditional* rule applies only when the application answers its condition "
           "yes; answered no, it is **waived**. An *optional* rule never blocks anything.", ""]

    flags = {**(c.app.get("flags") or {}), **(c.app.get("ownership") or {})}
    rows = []
    for item in c.items:
        rule = c.rule(item["rule_id"])
        person = c.person(item["subject_individual_id"])
        who = person["full_name"] if person else "the company"
        condition = rule.get("condition") or ""
        key = rule.get("condition_key") or condition
        if condition:
            if key in flags:
                answer = f"`{flags[key]}`"
            else:
                answer = "not answered"
        else:
            answer = "&mdash;"
        rows.append([item["item_id"], item["rule_id"],
                     words(rule.get("requirement_area", "")),
                     words(item["document_type"]), item["level"], who,
                     dash(condition), answer, item["status"]])
    out += table(["Item", "Rule", "Area", "Document", "Level", "Applies to",
                  "Condition", "Application's answer", "Status"], rows)

    levels = OrderedDict((lvl, [i for i in c.items if i["level"] == lvl])
                         for lvl in ("required", "conditional", "optional"))
    totals = []
    for lvl, group in levels.items():
        accepted = sum(1 for i in group if i["status"] == "accepted")
        waived = sum(1 for i in group if i["status"] == "waived")
        outstanding = [i for i in group if i["status"] not in ("accepted", "waived")]
        totals.append([lvl, len(group), accepted, waived, len(outstanding),
                       ", ".join(sorted({words(i["document_type"]) for i in outstanding})) or "&mdash;"])
    out += ["**Totals**", ""]
    out += table(["Level", "Items", "Accepted", "Waived", "Still outstanding", "Which"], totals)
    return out


# ---------------------------------------------------------------------------
# C. Documents
# ---------------------------------------------------------------------------

def quality_rules_for(kb, doc_type, flags):
    """Which quality rules produced these flags, from the KB rather than guessed."""
    found = []
    for flag in [f for f in (flags or "").split("|") if f]:
        for rule in kb.document_quality_rules:
            if rule["failure_flag"] != flag:
                continue
            if rule["document_type"] in ("*", doc_type):
                found.append(rule)
                break
    return found


def section_documents(c) -> list:
    out = ["### C. Documents", "",
           "Step 3 screens every uploaded file against `kb/document_quality_rules.csv`. "
           "A *deterministic* check is arithmetic on the file - its type, its dates, its "
           "page count. An *ai* check is a judgement about the image. The KB decides what "
           "each failure means: back to the customer, or to an analyst.", ""]

    rows = []
    for d in c.documents:
        fired = quality_rules_for(c.kb, d["document_type"], d["quality_flags"])
        rule_text = ", ".join(f"{r['rule_id']} ({r['check_type']})" for r in fired) or "none fired"
        released = (f"released by {d['released_by']}: {d['release_reason']}"
                    if d["released_by"] else "&mdash;")
        rows.append([d["document_id"], d["file_name"], words(d["document_type"]),
                     d["quality_status_at_screen"], d["quality_status"],
                     dash(d["quality_flags"]), rule_text,
                     dash(d["resubmission_reasons"]), released])
    out += table(["Id", "File", "Type", "Verdict at the screen", "Status now",
                  "Flags", "Rule that fired", "Reason code", "Analyst release"], rows)

    out += ["**Extracted fields.** Step 4 reads only documents that passed the screen. "
            f"The confidence floor is **{extraction_floor():.2f}**: a value below it, or "
            "missing, goes to a person before it can be used in any decision.", ""]
    rows = []
    for d in c.documents:
        for f in c.fields_for(d["document_id"]):
            if f["corrected_by_analyst"]:
                state = "corrected by an analyst"
            elif f["needs_analyst_correction"]:
                state = "**waiting for an analyst**"
            elif float(f["confidence"]) < extraction_floor():
                state = "accepted as read by an analyst"
            else:
                state = "above the floor"
            rows.append([f["field_id"], d["file_name"], f["name"],
                         dash(f["value"], "_(not read)_"), f"{float(f['confidence']):.2f}", state])
    if rows:
        out += table(["Field", "From", "Name", "Value", "Confidence", "How it was handled"], rows)
    else:
        out += ["_No fields were extracted on this case._", ""]
    return out


def extraction_floor():
    from orchestrator.steps.extraction import CONFIDENCE_FLOOR
    return CONFIDENCE_FLOOR


# ---------------------------------------------------------------------------
# D. People
# ---------------------------------------------------------------------------

def section_people(c) -> list:
    out = ["### D. People and ownership", "",
           "A *beneficial owner* is the person who ultimately owns or controls the "
           "applicant. Where the holding runs through another company, the percentage "
           "is multiplied along the chain rather than read off any one document.", ""]
    out += table(["Id", "Name", "Role", "Date of birth", "Nationality", "Resident in"],
                 [[p["individual_id"], p["full_name"], words(p["role"]),
                   dash(p["date_of_birth"]), dash(p["nationality"]),
                   dash(p["residence_country"])] for p in c.people])

    threshold = c.kb.ubo_threshold
    out += [f"The threshold is **{threshold:g}%**: at or above it, an owner must be "
            "verified.", ""]
    rows = []
    for u in c.ubos:
        chain = [float(x) for x in (u["ownership_chain_percentages"] or "").split("|") if x]
        if len(chain) > 1:
            product = 1.0
            for step in chain:
                product *= step / 100.0
            working = " x ".join(f"{s:g}%" for s in chain) + f" = **{product * 100:g}%**"
            effective = product * 100
        else:
            effective = chain[0] if chain else float(u["ownership_percentage"])
            working = f"{effective:g}% held directly"
        flag = ("" if u["verification_status"] == "verified" or effective < threshold
                else " **- at or above the threshold and not verified**")
        rows.append([u["ubo_id"], u["full_name"], words(u["control_type"]),
                     dash(u["ownership_path"]), working,
                     f"{float(u['ownership_percentage']):g}%",
                     u["verification_status"] + flag])
    if rows:
        out += table(["Id", "Owner", "Control", "Path", "The multiplication",
                      "Declared", "Verified?"], rows)
    else:
        out += ["_No beneficial owners are declared on this case._", ""]
    return out


# ---------------------------------------------------------------------------
# E. Checks
# ---------------------------------------------------------------------------

def section_checks(c) -> list:
    out = ["### E. Checks", "",
           "Step 5 and Step 6 are the paid steps. They run only once every required "
           "checklist item is accepted, which is why a case held at Step 3 or 4 has "
           "nothing here.", "", "**Registry.**", ""]

    if not c.registry:
        out += ["_No registry check was run: the case did not reach the paid step._", ""]
    else:
        for reg in c.registry:
            out += table(["Provider", "Company status", "Result", "Confidence", "Attempts",
                          "Register corroborates the owners?"],
                         [[reg["provider_name"], reg["company_status"], reg["result"],
                           f"{float(reg['confidence']):.2f}", reg["attempts"],
                           reg["ubo_supported_by_registry"]]])
            extracted = {}
            for f in c.fields:
                extracted.setdefault(f["name"], f["value"])
            directors = ", ".join(v for n, v in
                                  [(f["name"], f["value"]) for f in c.fields]
                                  if n in ("director_name", "director_name_2") and v)
            out += ["Each row compares what the register holds with what was read from "
                    "the documents. The result is computed here by comparing the two "
                    "columns, corrections included - it is not taken from the provider.", ""]
            out += table(["Compared", "The register holds", "Extracted from documents", "Result"],
                         [["legal name", dash(reg["registry_legal_name"]),
                           dash(extracted.get("company_name") or extracted.get("legal_name")),
                           reg["name_match"]],
                          ["registration number", dash(reg["registry_number"]),
                           dash(extracted.get("registration_number")), reg["number_match"]],
                          ["registered address", dash(reg["registry_address"]),
                           dash(extracted.get("registered_address")), reg["address_match"]],
                          ["directors", dash((reg["registry_directors"] or "").replace("|", ", ")),
                           dash(directors), reg["director_match"]]])

    out += ["**Identity.**", ""]
    out += table(["Check", "Person", "Document", "Liveness", "Biometric", "Name/DOB",
                  "Expired", "Duplicate", "Result"],
                 [[i["check_id"], i["full_name"], i["document_result"], i["liveness_result"],
                   i["biometric_result"], i["name_dob_match"], i["document_expired"],
                   i["duplicate_individual_detected"], i["result"]] for i in c.identity])

    out += ["**Screening.** Internal only - none of this may be repeated to the applicant.", ""]
    out += table(["Check", "Subject", "Sanctions", "PEP", "Adverse media", "Severity",
                  "Provider references"],
                 [[s["check_id"], s["full_name"] or "the applicant entity",
                   s["sanctions_result"], s["pep_result"], s["adverse_media_result"],
                   s["severity"], dash(s["evidence_refs"])] for s in c.screening])
    return out


# ---------------------------------------------------------------------------
# F. Risk
# ---------------------------------------------------------------------------

def section_risk(c) -> list:
    out = ["### F. Risk", "",
           "Step 7 walks every factor in `kb/risk_scoring_matrix.csv` and adds the "
           "points for the ones whose condition is true on this case. The table below "
           "shows **all** of them, fired or not, so you can see what was considered and "
           "not only what counted.", ""]

    if not c.assessment:
        out += ["_This case never reached Step 7, so it has no score and no band._", ""]
        return out

    fired = {f["factor"]: f for f in c.factors}
    running, rows = 0, []
    for entry in c.kb.risk_factors:
        condition = entry["condition"]
        did_fire = bool(c.facts.get(condition))
        points = int(entry["points"])
        hit = fired.get(entry["factor"])
        # A factor can appear twice in the matrix under different conditions;
        # only the row whose condition fired contributes.
        counted = did_fire and hit is not None
        if counted:
            running += points
        # Two rows can share a factor name (RS-10 and RS-11 are both
        # extraction_confidence), so evidence is shown only for the row that
        # actually counted - otherwise the one that did not fire borrows the
        # other's reference and reads as though it had.
        rows.append([entry["factor_id"], entry["factor"], words(condition),
                     "**fired**" if did_fire else "not fired",
                     f"+{points}" if counted else "0",
                     running if counted else "",
                     dash(hit["evidence_refs"] if counted and hit else "")])
    out += table(["Id", "Factor", "Condition", "On this case", "Points added",
                  "Running total", "Evidence"], rows)

    sums = [f"{f['weight']}" for f in c.factors]
    total = sum(int(f["weight"]) for f in c.factors)
    score = c.assessment["risk_score"]
    out += ["**The arithmetic.**", "",
            "```",
            (" + ".join(sums) + f" = {total}") if sums else "no factors fired = 0",
            "```", ""]
    if score is None:
        out += [f"The factors above come to {total}, but **this case carries no score "
                "at all.** An unresolved gap in the evidence means there is not enough "
                "to score: a number here would read as a risk level when the honest "
                "answer is that nobody knows yet. The band carries that instead.", ""]
    band_from_score = None
    for band, low, high in c.kb.score_bands:
        if score is not None and low <= score <= high:
            band_from_score = f"`{band}` ({low}-{high})"
            break

    floors = [(cond, band) for cond, band in c.kb.hard_floors if c.facts.get(cond)]
    out += table(["What", "Value"],
                 [["Score", money_int(score)],
                  ["Band the score alone would give", dash(band_from_score, "none - not scored")],
                  ["Hard floors that applied",
                   "; ".join(f"`{cond}` forces at least **{band}**" for cond, band in floors)
                   or "none"],
                  ["**Final band**", f"**{c.assessment['risk_band']}**"],
                  ["Recommended action", c.assessment["recommended_action"]],
                  ["Human sign-off required", yesno(c.assessment["requires_human_signoff"])]])

    if floors:
        out += ["A **hard floor** is a rule that overrides the arithmetic. Where a floor "
                "applies, the band is guaranteed whatever the points say - which is why "
                "the score and the band can disagree.", ""]
    return out


# ---------------------------------------------------------------------------
# G. Decision
# ---------------------------------------------------------------------------

def section_decision(c) -> list:
    band = c.assessment["risk_band"] if c.assessment else "insufficient_evidence"
    out = ["### G. Decision", "",
           "`kb/analyst_decision_taxonomy.csv` says which decisions exist at which band "
           "and who may record them. A decision outside the band is never offered; a "
           "decision the person's role does not allow is refused when they try to record "
           "it.", "",
           f"At band **`{band}`**:", ""]

    rows = []
    for name, rule in c.kb.decision_taxonomy.items():
        allowed = band in rule["allowed_bands"].split("|")
        rows.append([name, rule["severity_rank"],
                     "**offered**" if allowed else "not offered",
                     rule["allowed_bands"].replace("|", ", "),
                     rule["required_role"], rule["resulting_status"]])
    out += table(["Decision", "Severity rank", "At this band", "Allowed at",
                  "Role needed", "Resulting status"], rows)

    if not c.decisions:
        out += ["**No decision has been recorded on this case.**", ""]
        if c.case["status"] == "closed_withdrawn":
            out += ["The case closed because the applicant stopped replying. That is "
                    "something that happened to the case, not a judgement anyone made "
                    "about it, so there is no decision row.", ""]
        return out

    for d in c.decisions:
        out += table(["Field", "Value"],
                     [["Decision", f"**{d['decision']}**"],
                      ["Recorded by", f"{d['reviewer']} ({d['reviewer_role']})"],
                      ["Reason code", d["reason_code"]],
                      ["Rationale", d["rationale"]],
                      ["Evidence relied on", dash((d["evidence_relied_on"] or "").replace("|", ", "))],
                      ["Escalation target", dash(d["escalation_target"])],
                      ["Override?", yesno(d["override_flag"])],
                      ["Override direction", dash(d["override_direction"])],
                      ["Override reason", dash(d["override_reason"])]])
        if d["override_flag"]:
            recommended = c.assessment["recommended_action"] if c.assessment else "&mdash;"
            ranks = c.kb.decision_taxonomy
            out += [f"An **override** means the decision differed from the recommendation. "
                    f"The system recommended `{recommended}` "
                    f"(severity rank {ranks.get(recommended, {}).get('severity_rank', '?')}) "
                    f"and the reviewer chose `{d['decision']}` "
                    f"(rank {ranks.get(d['decision'], {}).get('severity_rank', '?')}), so the "
                    f"direction is **{d['override_direction']}**. The reviewer does not "
                    f"declare this; the system computes it from the severity order.", ""]
    return out


# ---------------------------------------------------------------------------
# H. Communications
# ---------------------------------------------------------------------------

def section_communications(c) -> list:
    out = ["### H. Communications", "",
           "Everything an applicant receives comes from `kb/message_template.csv`. The "
           "wording is the template's; only its declared placeholders are filled, from "
           "facts a customer may be told. Every rendered message is scanned for "
           "restricted wording before it can be sent.", ""]

    if c.case["restricted_finding"]:
        out += ["> This case carries a **restricted finding**, so only the generic "
                "templates - the ones that state no reason - were available to it.", ""]

    if not c.comms:
        out += ["_No message has been sent on this case._", ""]
    for m in c.comms:
        out += table(["Field", "Value"],
                     [["Message", m["communication_id"]],
                      ["Template", m["template_id"]],
                      ["Situation", dash(m["situation"])],
                      ["Audience", m["audience"]],
                      ["Type", dash(m["message_type"])],
                      ["Approval", f"{m['approval_status']}"
                       + (f", by {m['approved_by']}" if m["approved_by"] else "")],
                      ["Sent", m["sent_status"]]])
        out += ["> " + (m["rendered_text"] or "").replace("\n", "\n> "), ""]

    for t in c.tasks:
        out += [f"**Compliance task {t['task_id']}: {t['task']}** - {t['reason']}", ""]
    return out


# ---------------------------------------------------------------------------
# I. Timeline
# ---------------------------------------------------------------------------

def section_timeline(c) -> list:
    out = ["### I. Timeline", "",
           f"Every audit event on this case, oldest first - **{len(c.events)} in total**. "
           "The actor says who acted: `system` for the orchestration, `ai_agent` for a "
           "model, `external_provider` for a paid check, and a person's name for a human "
           "decision. The version column is the knowledge base or model that was in force.", ""]
    out += table(["Event", "When", "Actor", "Action", "Summary", "Version"],
                 [[e["event_id"], e["timestamp"], f"{e['actor_type']}: {e['actor_id']}",
                   e["action"], dash(e["payload_summary"]),
                   dash(e["model_or_prompt_version"])] for e in c.events])
    return out


# ---------------------------------------------------------------------------
# J. Dashboard line
# ---------------------------------------------------------------------------

def ageing_days(created_at):
    try:
        created = datetime.fromisoformat((created_at or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0
    return (datetime.now(timezone.utc) - created).days


def dashboard_row(c) -> list:
    score = c.assessment["risk_score"] if c.assessment else None
    return [c.id, c.name, c.case["applicant_type"], c.case["status"],
            dash(c.case["next_action_owner"]),
            dash(c.assessment["risk_band"] if c.assessment else None),
            money_int(score), len(c.holds),
            dash("; ".join(f"{h.code} ({h.owner})" for h in c.holds)),
            ageing_days(c.case["created_at"])]


def section_dashboard(c) -> list:
    out = ["### J. Dashboard line", "",
           "Exactly what the operations dashboard shows for this case. A dash in Band or "
           "Score means the case never reached risk scoring - it is not a score of zero.", ""]
    row = dashboard_row(c)
    out += table(["Case", "Applicant", "Type", "Status", "Owner", "Band", "Score",
                  "Holds", "Hold detail", "Age (days)"], [row])
    if c.holds:
        out += ["The open holds in full:", ""]
        out += table(["Hold", "Code", "Owner", "Placed by", "Why"],
                     [[h.hold_id, h.code, h.owner, h.placed_by_step,
                       h.reason.split(": ", 1)[-1]] for h in c.holds])
        out += ["A **hold** is a lock. Only the step that placed it, or the named person "
                "it belongs to, can release it, and the case moves only when every hold "
                "is clear.", ""]
    if c.case["restricted_finding"]:
        out += ["This case is marked **restricted**: customer messages are limited to "
                "generic templates.", ""]
    return out


# ---------------------------------------------------------------------------
# PART 1 - the knowledge base, as an appendix
# ---------------------------------------------------------------------------

def appendix(kb) -> list:
    out = ["## Appendix - the knowledge base", "",
           "Every rule the pipeline follows lives in a CSV under `kb/`. Changing a rule "
           "is an edit to one of these files and a version bump in `kb/kb_manifest.json` "
           "- no code change and no release. This appendix prints them as they stand "
           f"at **{kb.version}**.", ""]

    out += ["### Applicant-type rules", "",
            "Checked in priority order. The **first** rule whose conditions all hold "
            "decides the applicant type, and the rest are never evaluated - which is why "
            "the order matters as much as the conditions.", ""]
    out += table(["Order", "Rule", "Condition in plain words", "Applicant type", "Route", "Why it exists"],
                 [[r["priority"], r["rule_id"], plain_condition(r["conditions"]),
                   r["applicant_type"], r["route"], r["description"]]
                  for r in kb.applicant_type_rules])
    out += ["> The last rule has no extra condition, so every company reaches it "
            "eventually. It only ever wins because the rules above it were tried first.", ""]

    out += ["### Jurisdiction routing", "",
            "The applicant's country decides which rule set applies and which Wallester "
            "entity would hold the relationship.", ""]
    rows = []
    for country, r in sorted(kb.jurisdiction_routing.items()):
        rows.append([country, r.get("rule_id", ""), r.get("jurisdiction_path", ""),
                     r.get("entity_scope", ""), r.get("supported", ""),
                     r.get("description", "")])
    out += table(["Country", "Rule", "Rule set", "Entity scope", "Supported", "Notes"], rows)

    out += ["### Risk scoring matrix", "",
            "Each factor adds its points when its condition is true on the case. "
            "**Every weight is a POC placeholder** - the brief does not state them, so "
            "they were set so the scripted dataset reproduces, and each carries that "
            "status in the file itself.", ""]
    out += table(["Id", "Factor", "Reads from", "Condition in plain words", "Points",
                  "What it means", "Weight status"],
                 [[r["factor_id"], r["factor"], words(r["source"]), words(r["condition"]),
                   r["points"], r["description"],
                   r.get("weight_status", "")] for r in kb.risk_factors])

    out += ["### Risk bands and hard floors", "",
            "A band is either a score range, or a **hard floor** - a condition that "
            "forces a minimum band whatever the score says. Where a floor applies, the "
            "score explains the case and the floor decides it.", ""]
    scored = [r for r in kb.risk_bands if r["min_score"] != ""]
    out += ["**Bands by score**", ""]
    out += table(["Id", "Band", "From", "To", "Recommended action", "Notes"],
                 [[r["band_id"], r["band"], r["min_score"], r["max_score"],
                   r["recommended_action"], r["description"]] for r in scored])
    floors = [r for r in kb.risk_bands if r["min_score"] == ""]
    out += ["**Hard floors**", ""]
    out += table(["Id", "What triggers it", "Forces at least", "Recommended action", "Why"],
                 [[r["band_id"], words(r["hard_floor_condition"]), r["band"],
                   r["recommended_action"], r["description"]] for r in floors])

    out += ["### Decision taxonomy", "",
            "Which decisions exist, where they are offered, who may record them, and how "
            "strict each one is. The **severity rank** is what lets the system work out "
            "whether a reviewer was stricter or more lenient than the recommendation, "
            "without anyone having to say so.", ""]
    out += table(["Decision", "Severity rank", "Allowed at bands", "Role needed",
                  "Reason required", "Resulting status", "Customer template"],
                 [[name, r["severity_rank"], r["allowed_bands"].replace("|", ", "),
                   r["required_role"], r["requires_reason"], r["resulting_status"],
                   dash(r.get("customer_template_id"))]
                  for name, r in kb.decision_taxonomy.items()])
    return out


# ---------------------------------------------------------------------------
# The whole document
# ---------------------------------------------------------------------------

HOW_TO_READ = """\
## How to read this guide

Every case in the demo set exists to exercise one specific behaviour of the
pipeline. This guide walks each of them from the application arriving to the
decision being recorded.

**Nothing here was typed by hand.** Every figure, rule id, verdict and message
is read out of the knowledge base files or out of the database after a full
run, so the guide cannot drift away from what the system actually does.

Each case has the same ten sub-sections:

| | |
|---|---|
| **A. Classification** | which rule decided what kind of applicant this is, and why the earlier rules did not match |
| **B. Checklist** | every document the case was asked for, and whether it arrived |
| **C. Documents** | the quality verdict on each file, and every value read from it |
| **D. People and ownership** | who is involved, and the ownership arithmetic |
| **E. Checks** | the registry, identity and screening results |
| **F. Risk** | every factor considered, the points, the band, and any floor |
| **G. Decision** | what was offered, what was recorded, and by whom |
| **H. Communications** | every message the applicant received, in full |
| **I. Timeline** | the complete audit trail |
| **J. Dashboard line** | the row an operations team sees |

Some terms appear throughout:

- A **hold** is a lock placed by one step. Only that step, or the named person
  it belongs to, can release it, and the case moves only when every hold is
  clear. Clearing one hold can surface another.
- A **band** is the risk bucket: low, medium, high, critical, or
  *insufficient evidence* when there is not enough to score at all.
- A **hard floor** is a rule that forces a minimum band regardless of the score.
- A **restricted finding** means the case carries something the applicant may
  not be told, so only generic customer wording is available to it.
- The **confidence floor** is the level below which a value read from a
  document goes to a person rather than into a decision.

The eight steps every case passes through, in order: intake, requirement pack,
document quality, extraction, verification, screening, risk assessment,
decision. **Steps 5 and 6 are the paid ones** - a case that stops before them
has cost nothing.

The knowledge base itself is printed in the appendix at the end.
"""


def build(conn, kb, applications, generated_at) -> str:
    cases = []
    for row in conn.execute("SELECT case_id FROM onboarding_case ORDER BY case_id"):
        cid = row["case_id"]
        cases.append(Case(conn, cid, kb, applications.get(cid, {})))

    out = ["# Wallester UC4 - the 14 cases, in full", "",
           f"Generated {generated_at} from `kb/` at **{kb.version}** and from a database "
           f"built by running `tools/run_demo.py` end to end. "
           f"**{len(cases)} cases.**", "",
           "Do not edit this file by hand - it is regenerated by "
           "`python tools/make_case_guide.py`, and `tests/test_case_guide.py` checks "
           "its figures against the database.", "",
           "## All 14 cases at a glance", ""]

    out += table(["Case", "Applicant", "Type", "Status", "Owner", "Band", "Score",
                  "Holds", "Hold detail", "Age (days)"],
                 [dashboard_row(c) for c in cases])

    open_cases = [c for c in cases if c.case["status"] not in CLOSED]
    decided = [c for c in cases if c.decisions]
    overrides = [c for c in cases for d in c.decisions if d["override_flag"]]
    unscored = [c for c in cases if not c.assessment or c.assessment["risk_score"] is None]
    out += table(["Measure", "Count"],
                 [["Cases", len(cases)],
                  ["Still open", len(open_cases)],
                  ["With at least one open hold", sum(1 for c in cases if c.holds)],
                  ["Carrying a restricted finding", sum(1 for c in cases if c.case["restricted_finding"])],
                  ["With a decision recorded", len(decided)],
                  ["Where the decision was an override", len(overrides)],
                  ["Never scored", len(unscored)],
                  ["Messages sent to applicants", sum(len(c.comms) for c in cases)],
                  ["Audit events in total", sum(len(c.events) for c in cases)]])

    out += [HOW_TO_READ, ""]

    for c in cases:
        out += [f"## {c.id} - {c.name}", "",
                f"*{c.applicant['business_activity']}* &middot; "
                f"{c.applicant['entity_type']} in {c.applicant['country']}, "
                f"registered as `{c.applicant['registration_number']}` &middot; "
                f"expected usage: {c.applicant['expected_usage']}", ""]
        out += section_classification(c)
        out += section_checklist(c)
        out += section_documents(c)
        out += section_people(c)
        out += section_checks(c)
        out += section_risk(c)
        out += section_decision(c)
        out += section_communications(c)
        out += section_timeline(c)
        out += section_dashboard(c)

    out += appendix(kb)
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def write_pdf(markdown: str, path: Path) -> None:
    """A readable PDF of the same content.

    Deliberately plain: headings, paragraphs, tables and code blocks, in a
    layout that survives a 10-column table. The markdown is the source of
    truth; this is a copy for people who would rather not read it in a
    repository.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    base = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=18, spaceAfter=10,
                             textColor=colors.HexColor("#232a33")),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=13.5, spaceBefore=14,
                             spaceAfter=6, textColor=colors.HexColor("#2c5fa8")),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=11, spaceBefore=10,
                             spaceAfter=4, textColor=colors.HexColor("#3d4854")),
        "p": ParagraphStyle("p", parent=base["BodyText"], fontSize=8.6, leading=12,
                            alignment=TA_LEFT, spaceAfter=5),
        "quote": ParagraphStyle("quote", parent=base["BodyText"], fontSize=8.4, leading=12,
                                leftIndent=10, textColor=colors.HexColor("#617082"),
                                spaceAfter=5),
        "code": ParagraphStyle("code", parent=base["Code"], fontSize=7.4, leading=9.5,
                               leftIndent=8, spaceAfter=6),
        "cell": ParagraphStyle("cell", parent=base["BodyText"], fontSize=6.6, leading=8.2),
        "head": ParagraphStyle("head", parent=base["BodyText"], fontSize=6.6, leading=8.2,
                               textColor=colors.white),
    }

    def inline(text: str) -> str:
        """Markdown emphasis and code spans -> reportlab's mini-HTML."""
        text = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("&amp;mdash;", "—").replace("&amp;middot;", "·")
                .replace("&amp;amp;", "&amp;"))
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", text)
        text = re.sub(r"`([^`]+?)`", r'<font face="Courier">\1</font>', text)
        text = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1", text)
        text = text.replace("\\|", "|")
        return text

    story, lines, i = [], markdown.splitlines(), 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):                     # code block
            i += 1
            block = []
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i].replace("&", "&amp;").replace("<", "&lt;"))
                i += 1
            i += 1
            story.append(Paragraph("<br/>".join(block) or "&nbsp;", styles["code"]))
            continue

        if line.startswith("|"):                       # table
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                width = max(len(r) for r in rows)
                data = [[Paragraph(inline(c), styles["head"] if n == 0 else styles["cell"])
                         for c in r + [""] * (width - len(r))]
                        for n, r in enumerate(rows)]
                available = landscape(A4)[0] - 24 * mm
                t = Table(data, colWidths=[available / width] * width, repeatRows=1)
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3d4854")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c3ccd6")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                     [colors.white, colors.HexColor("#f4f6f8")]),
                ]))
                story += [t, Spacer(1, 5)]
            continue

        if line.startswith("### "):
            story.append(Paragraph(inline(line[4:]), styles["h3"]))
        elif line.startswith("## "):
            story.append(Paragraph(inline(line[3:]), styles["h2"]))
        elif line.startswith("# "):
            story.append(Paragraph(inline(line[2:]), styles["h1"]))
        elif line.startswith("> "):
            story.append(Paragraph(inline(line[2:]), styles["quote"]))
        elif line.startswith("- "):
            story.append(Paragraph("• " + inline(line[2:]), styles["p"]))
        elif line.strip():
            story.append(Paragraph(inline(line), styles["p"]))
        i += 1

    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm,
                            title="Wallester UC4 - the 14 cases, in full")
    doc.build(story)


# ---------------------------------------------------------------------------

def generate(out_dir: Path = DEFAULT_OUT, pdf: bool = True,
             generated_at: str | None = None) -> Path:
    """Run every case end to end, then write the guide from what that produced."""
    import json
    from tools.dataset_to_applications import DEFAULT_OUT as APPLICATIONS

    conn = db.connect(":memory:")
    run_demo(conn, verbose=False)
    kb = KnowledgeBase()

    applications = {}
    for path in sorted(APPLICATIONS.glob("*.json")):
        app = json.loads(path.read_text(encoding="utf-8"))
        case_id = path.stem.split("_")[-1]
        applications[case_id] = app

    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = build(conn, kb, applications, stamp)

    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "CASE_GUIDE.md"
    md_path.write_text(text, encoding="utf-8")
    if pdf:
        write_pdf(text, out_dir / "CASE_GUIDE.pdf")
    conn.close()
    return md_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    path = generate(args.out, pdf=not args.no_pdf)
    text = path.read_text(encoding="utf-8")
    print(f"{path}  {len(text.splitlines())} lines, {len(text):,} characters")
    if not args.no_pdf:
        pdf = args.out / "CASE_GUIDE.pdf"
        print(f"{pdf}  {pdf.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
