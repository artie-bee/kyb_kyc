"""
STEP 7 - Risk assessment  (brief Section 5.8)

The score is arithmetic, done in code from the findings table and the case data.
Every weight comes from kb/risk_scoring_matrix.csv, where every one of them is
marked as a placeholder for Wallester to confirm - the brief does not state them,
and a number nobody has agreed should not look like one that has been.

Hard floors in kb/risk_bands.csv override the score, because some findings are
not a matter of degree: a confirmed sanctions match is critical whatever else the
file looks like, and an unresolved gap in the evidence is not scored at all.

The only thing a model does here is write the explanation on each factor, and it
may cite nothing that is not already a row in the database.

Nothing is approved automatically. A low band recommends approval; a person still
has to make it (brief Section 18).
"""

import re
from dataclasses import dataclass, field

from .. import db, holds
from ..kb import KnowledgeBase
from ..narrator import MockNarrator, Narrator

ACTOR = "step.risk_assessment"
CONFIDENCE_FLOOR = 0.70
SPEND_THRESHOLD = 50000
BAND_ORDER = ("low", "medium", "high", "critical")


@dataclass
class RiskResult:
    case_id: str
    assessment_id: str
    score: int | None
    band: str
    recommended_action: str
    requires_human_signoff: bool
    factors: list[dict] = field(default_factory=list)
    status: str = ""
    next_step: str | None = None
    problems: list[str] = field(default_factory=list)


def gather_facts(conn, case_id: str, kb: KnowledgeBase) -> tuple[dict, dict]:
    """Read the case once. Returns (facts, evidence ids per fact)."""
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    applicant = conn.execute("SELECT * FROM applicant WHERE applicant_id = ?",
                             (case["applicant_id"],)).fetchone()
    findings = conn.execute("SELECT * FROM finding WHERE case_id = ?", (case_id,)).fetchall()
    registry = conn.execute("SELECT * FROM registry_check WHERE case_id = ?",
                            (case_id,)).fetchone()
    identity = conn.execute("SELECT * FROM identity_check WHERE case_id = ?", (case_id,)).fetchall()
    screening = conn.execute("SELECT * FROM screening_check WHERE case_id = ?",
                             (case_id,)).fetchall()
    ubos = conn.execute(
        "SELECT u.*, i.residence_country FROM ubo u JOIN individual i USING (individual_id)"
        " WHERE u.applicant_id = ?", (case["applicant_id"],)).fetchall()
    documents = conn.execute("SELECT * FROM document WHERE case_id = ?", (case_id,)).fetchall()
    fields = conn.execute(
        "SELECT f.*, d.document_id FROM extracted_field f JOIN document d USING (document_id)"
        " WHERE d.case_id = ?", (case_id,)).fetchall()
    open_holds = holds.open_holds(conn, case_id)

    def rule(code):
        return [f for f in findings if f["rule_id"] == code]

    def path_len(u):
        chain = [c for c in (u["ownership_chain_percentages"] or "").split("|") if c]
        return len(chain) or len([p for p in (u["ownership_path"] or "").split(">") if p.strip()])

    def screens(column, *values):
        return [s for s in screening if s[column] in values]

    facts, evidence = {}, {}

    def note(condition, rows, ids):
        facts[condition] = bool(rows)
        evidence[condition] = ids

    note("ubo_resident_outside_applicant_country",
         [u for u in ubos if u["residence_country"] != applicant["country"]],
         [u["ubo_id"] for u in ubos if u["residence_country"] != applicant["country"]])
    note("high_risk_industry",
         [registry] if registry and registry["high_risk_jurisdiction_or_industry"] == "True"
         else [], [registry["check_id"]] if registry else [])
    note("ownership_layers_gt_1", [u for u in ubos if path_len(u) > 1],
         [u["ubo_id"] for u in ubos if path_len(u) > 1])
    note("ubo_not_supported_by_registry", rule("UB-04"), [f["finding_id"] for f in rule("UB-04")])
    note("registry_address_mismatch", rule("RG-07"), [f["finding_id"] for f in rule("RG-07")])
    note("registry_director_mismatch", rule("RG-08"), [f["finding_id"] for f in rule("RG-08")])
    entity = [f for f in findings if f["rule_id"] in ("RG-01", "RG-02", "RG-03", "RG-04")]
    note("entity_not_active", entity, [f["finding_id"] for f in entity])
    failed_id = [r for r in identity if r["result"] in ("fail", "review")]
    note("identity_check_failed", failed_id, [r["check_id"] for r in failed_id])
    dupes = [r for r in identity if r["duplicate_individual_detected"] == "True"]
    note("identity_duplicate", dupes, [r["check_id"] for r in dupes])
    # A value an analyst typed in from the file has no machine confidence to be
    # low; only a machine reading can be "accepted as read".
    accepted = [f for f in fields
                if f["entry_method"] == "extracted"
                and float(f["confidence"]) < CONFIDENCE_FLOOR
                and not f["corrected_by_analyst"] and not f["needs_analyst_correction"]]
    note("low_confidence_accepted_as_read", accepted, [f["field_id"] for f in accepted])
    corrected = [f for f in fields if f["corrected_by_analyst"]]
    note("field_corrected_by_analyst", corrected, [f["field_id"] for f in corrected])
    amounts = _spend(applicant["expected_usage"])
    spend = bool(amounts) and max(amounts) > SPEND_THRESHOLD
    note("expected_monthly_spend_above_50k", [applicant] if spend else [],
         [applicant["applicant_id"]] if spend else [])
    for condition, column, values in (
            ("pep_match", "pep_result", ("pep_match", "close_associate_family")),
            ("adverse_media_moderate", "adverse_media_result", ("moderate",)),
            ("adverse_media_serious", "adverse_media_result", ("serious",)),
            ("sanctions_possible_match", "sanctions_result", ("possible_match",)),
            ("sanctions_clear_match", "sanctions_result", ("clear_match",))):
        hits = screens(column, *values)
        note(condition, hits, [s["check_id"] for s in hits])
    resub = [d for d in documents if d["quality_status_at_screen"] == "resubmission_required"]
    note("document_resubmission_required", resub, [d["document_id"] for d in resub])
    # A document held only because no visual check could run (mock mode, a
    # portal upload) was not found at fault; it is not scored as if it were.
    manual = [d for d in documents if d["quality_status_at_screen"] == "manual_review_required"
              and "visual_check_not_run" not in (d["quality_flags"] or "").split("|")]
    note("document_manual_review", manual, [d["document_id"] for d in manual])

    # Insufficient evidence means "could not be determined": a provider that did
    # not answer, or required evidence still outstanding. A failed identity check
    # is a result, not an absence of one - it scores through RS-08 instead.
    gaps = [h for h in open_holds if h.code in ("insufficient_evidence", "resubmission")]
    facts["insufficient_evidence_hold"] = bool(gaps)
    evidence["insufficient_evidence_hold"] = [h.hold_id for h in gaps]
    return facts, evidence


def _spend(expected_usage: str | None) -> list[int]:
    """Monthly figures quoted in the expected usage free text."""
    return [int(m) for m in re.findall(r"(\d{4,})\s*(?:EUR|GBP)", expected_usage or "")]


def band_for(score: int, kb: KnowledgeBase) -> str:
    for band, lo, hi in kb.score_bands:
        if lo <= score <= hi:
            return band
    return "critical"


def apply_floors(band: str, facts: dict, kb: KnowledgeBase) -> str:
    """A floor raises the band. It never lowers one."""
    for condition, floor in kb.hard_floors:
        if not facts.get(condition):
            continue
        if floor == "insufficient_evidence":
            return "insufficient_evidence"
        if band == "insufficient_evidence":
            continue
        if BAND_ORDER.index(floor) > BAND_ORDER.index(band):
            band = floor
    return band


def run(conn, case_id: str, application: dict, kb: KnowledgeBase,
        narrator: Narrator | None = None) -> RiskResult:
    narrator = narrator or MockNarrator()
    facts, evidence = gather_facts(conn, case_id, kb)

    fired = [f for f in kb.risk_factors if facts.get(f["condition"])]
    score = sum(int(f["points"]) for f in fired)
    band = apply_floors(band_for(score, kb), facts, kb)
    insufficient = band == "insufficient_evidence"
    action = kb.action_for_band(band)
    for condition, floor_action in kb.action_floors:     # first match wins
        if facts.get(condition):
            action = floor_action
            break

    screening_finding = any(facts[c] for c in
                            ("pep_match", "adverse_media_moderate", "adverse_media_serious",
                             "sanctions_possible_match", "sanctions_clear_match"))
    human = band in ("medium", "high", "critical") or screening_finding or insufficient

    if insufficient and action == "approve":
        raise AssertionError("insufficient evidence can never recommend approval")

    assessment_id = db.next_id(conn, "risk_assessment")
    conn.execute(
        "INSERT INTO risk_assessment VALUES (?,?,?,?,?,?,?,?,?)",
        (assessment_id, case_id, None if insufficient else score, band, action, 0.9,
         int(insufficient), int(human), kb.version))

    factors = []
    for f in fired:
        refs = [r for r in evidence.get(f["condition"], []) if r]
        note = narrator.explain_factor(dict(f), {"evidence_refs": refs})
        factor_id = db.next_id(conn, "risk_factor")
        conn.execute("INSERT INTO risk_factor VALUES (?,?,?,?,?,?)",
                     (factor_id, assessment_id, f["factor"], int(f["points"]),
                      note.text, "|".join(refs)))
        factors.append({"factor_id": factor_id, "factor": f["factor"],
                        "weight": int(f["points"]), "explanation": note.text,
                        "evidence_refs": refs, "condition": f["condition"]})

    db.update_case(conn, case_id, requires_human_signoff=int(human))
    status, owner = holds.apply_status(conn, case_id, kb,
                                       *_clear_routing(band))
    db.audit(conn, case_id, "ai_agent", ACTOR, "risk_assessment_completed",
             f"{assessment_id} -> band {band}"
             + (f", score {score}" if not insufficient else ", not scored")
             + f", recommended action {action}, requires_human_signoff {human}; "
             f"{len(factors)} factor(s) fired; narrator={narrator.mode}; "
             f"matrix weights are POC placeholders", narrator.version or kb.version)

    return RiskResult(case_id, assessment_id, None if insufficient else score, band, action,
                      human, factors, status, "decision", [])


def _clear_routing(band: str) -> tuple[str, str]:
    """Where the case sits when nothing is holding it."""
    if band in ("low", "medium"):
        return "ready_for_decision", "analyst"
    if band == "high":
        return "enhanced_due_diligence", "compliance"
    return "analyst_review_required", "analyst"
