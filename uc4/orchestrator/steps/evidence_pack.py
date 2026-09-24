"""
STEP 7b - Evidence pack  (brief Section 5.11)

What an analyst opens. Everything in it is read out of the database, because a
pack that restates what a model believes is worth nothing to the person who has
to sign the decision. The only written part is the compliance narrative, and it
may cite nothing that is not already a row here.

The pack is internal. draft_compliance_narrative names findings that must never
reach the customer, so it is stored in the pack and nowhere else; Step 8 uses the
case's restricted_finding flag to pick generic wording instead.
"""

import json
from dataclasses import dataclass, field

from .. import db, holds
from ..kb import KnowledgeBase
from ..narrator import MockNarrator, Narrator

ACTOR = "step.evidence_pack"


@dataclass
class PackResult:
    case_id: str
    evidence_pack_id: str
    recommended_next_action: str
    evidence_refs: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def known_ids(conn, case_id: str) -> set[str]:
    """Every id on this case that a narrative is allowed to cite."""
    found = set()
    for sql, params in (
            ("SELECT document_id FROM document WHERE case_id = ?", (case_id,)),
            ("SELECT f.field_id FROM extracted_field f JOIN document d USING (document_id)"
             " WHERE d.case_id = ?", (case_id,)),
            ("SELECT check_id FROM registry_check WHERE case_id = ?", (case_id,)),
            ("SELECT check_id FROM identity_check WHERE case_id = ?", (case_id,)),
            ("SELECT check_id FROM screening_check WHERE case_id = ?", (case_id,)),
            ("SELECT finding_id FROM finding WHERE case_id = ?", (case_id,)),
            ("SELECT hold_id FROM case_hold WHERE case_id = ?", (case_id,)),
            ("SELECT assessment_id FROM risk_assessment WHERE case_id = ?", (case_id,)),
            ("SELECT f.factor_id FROM risk_factor f JOIN risk_assessment a USING (assessment_id)"
             " WHERE a.case_id = ?", (case_id,)),
            ("SELECT u.ubo_id FROM ubo u JOIN onboarding_case c USING (applicant_id)"
             " WHERE c.case_id = ?", (case_id,)),
            ("SELECT i.item_id FROM checklist_item i JOIN requirement_pack p USING (pack_id)"
             " WHERE p.case_id = ?", (case_id,)),
            # the applicant record itself: a factor about expected usage cites it
            ("SELECT applicant_id FROM onboarding_case WHERE case_id = ?", (case_id,)),
            ("SELECT case_id FROM onboarding_case WHERE case_id = ?", (case_id,))):
        found.update(r[0] for r in conn.execute(sql, params))
    return found


def build(conn, case_id: str, kb: KnowledgeBase) -> dict:
    """Assemble the pack from the database. No opinions, only rows."""
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    applicant = conn.execute("SELECT * FROM applicant WHERE applicant_id = ?",
                             (case["applicant_id"],)).fetchone()
    assessment = conn.execute("SELECT * FROM risk_assessment WHERE case_id = ?",
                              (case_id,)).fetchone()
    factors = conn.execute(
        "SELECT * FROM risk_factor WHERE assessment_id = ? ORDER BY factor_id",
        (assessment["assessment_id"],)).fetchall() if assessment else []
    people = conn.execute("SELECT * FROM individual WHERE applicant_id = ? ORDER BY individual_id",
                          (case["applicant_id"],)).fetchall()
    ubos = conn.execute(
        "SELECT u.*, i.full_name FROM ubo u JOIN individual i USING (individual_id)"
        " WHERE u.applicant_id = ? ORDER BY u.ubo_id", (case["applicant_id"],)).fetchall()
    items = conn.execute(
        "SELECT i.* FROM checklist_item i JOIN requirement_pack p USING (pack_id)"
        " WHERE p.case_id = ?", (case_id,)).fetchall()
    registry = conn.execute("SELECT * FROM registry_check WHERE case_id = ?", (case_id,)).fetchone()
    identity = conn.execute("SELECT c.*, i.full_name FROM identity_check c "
                            "JOIN individual i USING (individual_id) WHERE c.case_id = ?",
                            (case_id,)).fetchall()
    screening = conn.execute(
        "SELECT s.*, i.full_name FROM screening_check s LEFT JOIN individual i"
        " USING (individual_id) WHERE s.case_id = ?", (case_id,)).fetchall()
    open_holds = holds.open_holds(conn, case_id)

    def effective(u):
        chain = [float(c) for c in (u["ownership_chain_percentages"] or "").split("|") if c]
        from .verification import effective_ownership
        return effective_ownership(chain) if chain else float(u["ownership_percentage"])

    required = [i for i in items if i["level"] == "required"]
    accepted = [i for i in required if i["status"] == "accepted"]
    missing = []
    if len(accepted) != len(required):
        missing.append(f"{len(required) - len(accepted)} of {len(required)} required checklist "
                       f"item(s) are not accepted")
    for hold in open_holds:
        missing.append(hold.reason)
    if registry and registry["result"] != "pass":
        missing.append(f"registry check {registry['check_id']} returned {registry['result']}")

    return {
        "case_id": case_id,
        "applicant_summary": (
            f"{applicant['legal_name']}, {applicant['entity_type']} registered in "
            f"{applicant['country']} as {applicant['registration_number']}, "
            f"{applicant['business_activity']}. {len(people)} individual(s), {len(ubos)} "
            f"declared beneficial owner(s). Expected usage: {applicant['expected_usage']}."),
        "entity_details": {
            "applicant_id": applicant["applicant_id"], "legal_name": applicant["legal_name"],
            "entity_type": applicant["entity_type"], "country": applicant["country"],
            "registration_number": applicant["registration_number"],
            "applicant_type": case["applicant_type"], "entity_scope": case["entity_scope"]},
        "individuals": [{"individual_id": p["individual_id"], "full_name": p["full_name"],
                         "role": p["role"], "residence_country": p["residence_country"]}
                        for p in people],
        "ubos": [{"ubo_id": u["ubo_id"], "full_name": u["full_name"],
                  "declared_percentage": u["ownership_percentage"],
                  "effective_ownership": effective(u),
                  "control_type": u["control_type"],
                  "verification_status": u["verification_status"]} for u in ubos],
        "checklist_completeness": {
            "required": len(required), "accepted": len(accepted),
            "outstanding": [i["document_type"] for i in required if i["status"] != "accepted"]},
        "provider_results": {
            "registry": ({"check_id": registry["check_id"], "result": registry["result"],
                          "company_status": registry["company_status"]} if registry else None),
            "identity": [{"check_id": c["check_id"], "subject": c["full_name"],
                          "result": c["result"]} for c in identity],
            "screening": [{"check_id": s["check_id"],
                           "subject": s["full_name"] or "applicant entity",
                           "sanctions": s["sanctions_result"], "pep": s["pep_result"],
                           "adverse_media": s["adverse_media_result"]} for s in screening]},
        "risk_factors": [{"factor_id": f["factor_id"], "factor": f["factor"],
                          "weight": f["weight"], "explanation": f["explanation"],
                          "evidence_refs": [r for r in (f["evidence_refs"] or "").split("|") if r]}
                         for f in factors],
        "risk_band": assessment["risk_band"] if assessment else "",
        "risk_score": assessment["risk_score"] if assessment and assessment["risk_score"] is not None else "",
        "requires_human_signoff": bool(assessment["requires_human_signoff"]) if assessment else False,
        "open_holds": [{"hold_id": h.hold_id, "owner": h.owner, "code": h.code,
                        "reason": h.reason, "placed_by_step": h.placed_by_step}
                       for h in open_holds],
        "missing_or_conflicting_evidence": " ".join(missing),
        "recommended_next_action": assessment["recommended_action"] if assessment else "",
    }


def run(conn, case_id: str, application: dict, kb: KnowledgeBase,
        narrator: Narrator | None = None) -> PackResult:
    narrator = narrator or MockNarrator()
    pack = build(conn, case_id, kb)
    ids = known_ids(conn, case_id)

    # Rejects any reference the narrator invented.
    narrative = narrator.compliance_narrative(pack, ids).validate(ids)

    refs = sorted({r for f in pack["risk_factors"] for r in f["evidence_refs"]}
                  | {h["hold_id"] for h in pack["open_holds"]})
    pack_id = db.next_id(conn, "evidence_pack")
    assessment = conn.execute("SELECT assessment_id FROM risk_assessment WHERE case_id = ?",
                              (case_id,)).fetchone()
    conn.execute(
        "INSERT INTO evidence_pack VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pack_id, case_id, assessment["assessment_id"] if assessment else None,
         pack["applicant_summary"], json.dumps(pack["entity_details"]),
         json.dumps(pack["individuals"]), json.dumps(pack["ubos"]),
         json.dumps(pack["checklist_completeness"]), json.dumps(pack["provider_results"]),
         json.dumps(pack["risk_factors"]), json.dumps(pack["open_holds"]),
         pack["missing_or_conflicting_evidence"], pack["recommended_next_action"],
         narrative.text, "|".join(refs), db.now()))

    db.audit(conn, case_id, "ai_agent", ACTOR, "evidence_pack_generated",
             f"{pack_id} assembled for human review; {len(pack['risk_factors'])} risk factor(s), "
             f"{len(pack['open_holds'])} open hold(s); recommended next action "
             f"{pack['recommended_next_action']}; narrator={narrator.mode}; internal only",
             narrator.version or kb.version)
    return PackResult(case_id, pack_id, pack["recommended_next_action"], refs, [])
