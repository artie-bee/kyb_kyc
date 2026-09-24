"""
STEP 2 - Requirement pack (checklist) generation  (brief Section 5.2)

Input : case_id (created by Step 1) + the application's flags
Output: PackResult - one requirement_pack row and its checklist_item rows

Rules come from kb/requirement_rule.csv, matched on
(applicant_type, jurisdiction, entity_type). Rules with
applies_per_individual_role are expanded once per person with that role,
so a company with two directors gets two director ID items.
"""

from dataclasses import dataclass, field

from .. import db
from ..kb import KnowledgeBase

ACTOR = "step.requirement_pack"


@dataclass
class PackResult:
    case_id: str
    pack_id: str | None
    items_required: int
    items_optional: int
    items_conditional: int
    next_step: str | None
    problems: list[str] = field(default_factory=list)


def run(conn, case_id: str, application: dict, kb: KnowledgeBase) -> PackResult:
    case = conn.execute(
        "SELECT c.*, a.entity_type FROM onboarding_case c "
        "JOIN applicant a USING (applicant_id) WHERE c.case_id = ?", (case_id,)
    ).fetchone()
    key = (case["applicant_type"], case["jurisdiction_path"], case["entity_type"])

    rules = [r for r in kb.requirement_rules
             if (r["applicant_type"], r["jurisdiction"], r["entity_type"]) == key]

    # No rules for this combination -> cannot build a checklist; never guess.
    if not rules:
        msg = f"No requirement rules in KB for {key}"
        db.update_case(conn, case_id, status="analyst_review_required", next_action_owner="analyst")
        db.audit(conn, case_id, "system", ACTOR, "requirement_pack_failed", msg, kb.version)
        return PackResult(case_id, None, 0, 0, 0, None, [msg])

    people = conn.execute(
        "SELECT individual_id, role FROM individual WHERE applicant_id = ?",
        (case["applicant_id"],)
    ).fetchall()
    ubo_ids = {r["individual_id"] for r in conn.execute(
        "SELECT individual_id FROM ubo WHERE applicant_id = ?", (case["applicant_id"],))}
    flags = {**application.get("flags", {}), **application.get("ownership", {})}

    pack_id = db.next_id(conn, "requirement_pack")
    conn.execute("INSERT INTO requirement_pack VALUES (?,?,?,?,?,?)",
                 (pack_id, case_id, *key, kb.version))

    counts = {"required": 0, "optional": 0, "conditional": 0}
    problems = []

    for rule in rules:
        # Conditional rules are matched on condition_key, the machine-readable twin
        # of the human-readable condition sentence. A condition that is answered
        # 'no' still produces a checklist item, marked waived, so the decision stays
        # on the record; an unanswered one is left for the analyst to confirm.
        note, status = None, "pending"
        if rule["level"] == "conditional":
            key = rule["condition_key"] or rule["condition"]
            if key not in flags:
                note = (f"Condition '{rule['condition']}' not answered on application; "
                        f"analyst to confirm")
                problems.append(note)
            elif not flags[key]:
                status = "waived"

        # Who does this rule apply to? The KB may list several roles separated
        # by "|" (e.g. "director|ubo|authorised_signatory"); the rule then applies
        # to anyone holding ANY of them. Someone who holds two of the listed roles
        # still gets one item per rule, not one per role.
        role = rule["applies_per_individual_role"]
        roles = [r for r in role.split("|") if r]
        if not roles:
            subjects = [None]
        else:
            matched = {p["individual_id"] for p in people if p["role"] in roles}
            if "ubo" in roles:
                matched |= ubo_ids          # UBOs come from the UBO table, not the role column
            subjects = sorted(matched)

        if roles and not subjects:
            msg = f"Rule {rule['rule_id']} needs a '{role}' but none was declared"
            problems.append(msg)
            subjects = [None]   # still create the item so the gap is visible
            note = msg

        for subject in subjects:
            conn.execute(
                "INSERT INTO checklist_item (item_id, pack_id, rule_id, subject_individual_id,"
                " document_type, level, status, note) VALUES (?,?,?,?,?,?,?,?)",
                (db.next_id(conn, "checklist_item"), pack_id, rule["rule_id"], subject,
                 rule["document_type"], rule["level"], status, note),
            )
            counts[rule["level"]] += 1

    db.audit(conn, case_id, "system", ACTOR, "requirement_pack_generated",
             f"{pack_id}: {counts['required']} required, {counts['optional']} optional, "
             f"{counts['conditional']} conditional from {len(rules)} rules", kb.version)
    for p in problems:
        db.audit(conn, case_id, "system", ACTOR, "requirement_gap_flagged", p, kb.version)

    # White-label partners get the KYB checklist and their documents are screened,
    # but the case stops after Step 3 - document_quality.route_case() ends it.
    # Customer now owes documents; the next step runs when files arrive.
    db.update_case(conn, case_id, next_action_owner="customer")
    return PackResult(case_id, pack_id, counts["required"], counts["optional"],
                      counts["conditional"], "document_quality", problems)
