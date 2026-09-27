"""
STEP 8b - Human decision  (brief Sections 5.10, 10.7, 18)

The decision is a person's. This module records it, and refuses to record one
that the configured taxonomy does not permit:

  - nothing is approved while a hold is open. A hold means something is
    outstanding, and approving over it would be the system deciding the hold did
    not matter;
  - kb/analyst_decision_taxonomy.csv says which role may take which decision at
    which band. A critical case, or any case with a sanctions or serious-media
    finding, needs compliance;
  - override_flag is COMPUTED by comparing the decision with the recommended
    action. It is not passed in, because whether a reviewer overrode the system
    is a fact about the two values, not a claim the reviewer gets to make. An
    override needs a reason;
  - escalate needs somewhere to escalate to;
  - evidence_relied_on must cite ids that exist. A decision resting on a
    reference nobody can follow is not auditable.

Recording a decision sets the final status from the taxonomy and triggers the
matching customer message, subject to the restricted and sanctions rules in
communication.py.
"""

from dataclasses import dataclass, field

from .. import db, holds
from ..kb import KnowledgeBase
from . import communication, evidence_pack

ACTOR = "step.decision"
APPROVING = ("approve", "conditional_approve")
COMPLIANCE_ONLY_BANDS = ("critical",)

# situation used for the customer message that follows each decision
_SITUATION = {
    "approve": "final_outcome_approved",
    "conditional_approve": "final_outcome_approved",
    "reject": "final_outcome_declined",
    "withdrawn": "case_closed",
    "request_more_information": "clarification",
    "insufficient_evidence": "clarification",
    "enhanced_due_diligence": "manual_review_underway",
    "escalate": "manual_review_underway",
}


class DecisionRefused(PermissionError):
    """The taxonomy, or an open hold, does not permit this decision."""


@dataclass
class DecisionResult:
    case_id: str
    decision_id: str
    decision: str
    status: str
    override_flag: bool
    override_direction: str | None = None
    customer_message: communication.CommunicationResult | None = None
    problems: list[str] = field(default_factory=list)


def record_decision(conn, case_id: str, reviewer: str, reviewer_role: str, decision: str,
                    reason_code: str, rationale: str, evidence_relied_on,
                    override_reason: str | None = None, escalation_target: str | None = None,
                    kb: KnowledgeBase | None = None,
                    chooser: communication.TemplateChooser | None = None) -> DecisionResult:
    kb = kb or KnowledgeBase()
    rule = kb.decision_taxonomy.get(decision)
    if rule is None:
        raise DecisionRefused(
            f"'{decision}' is not a decision in the taxonomy; "
            f"choose from {sorted(kb.decision_taxonomy)}")
    if not (rationale or "").strip() and rule["requires_reason"].lower() == "true":
        raise DecisionRefused("a decision needs a rationale")
    if not (reviewer or "").strip():
        raise DecisionRefused("the reviewer must be identified")

    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    assessment = conn.execute("SELECT * FROM risk_assessment WHERE case_id = ?",
                              (case_id,)).fetchone()
    band = assessment["risk_band"] if assessment else "insufficient_evidence"
    recommended = assessment["recommended_action"] if assessment else "insufficient_evidence"

    # 1. nothing is approved over an open hold
    open_now = holds.open_holds(conn, case_id)
    if decision in APPROVING and open_now:
        raise DecisionRefused(
            f"cannot {decision} {case_id}: {len(open_now)} hold(s) are still open "
            f"({'; '.join(h.code for h in open_now)}). Release them first, with a reason")

    # 2. the band must permit the decision
    allowed_bands = [b for b in rule["allowed_bands"].split("|") if b]
    if band not in allowed_bands:
        raise DecisionRefused(
            f"'{decision}' is not available at band {band}; the taxonomy allows "
            f"{allowed_bands}")

    # 3. role. Critical, or any sanctions or serious-media finding, needs compliance.
    sensitive = conn.execute(
        "SELECT COUNT(*) FROM screening_check WHERE case_id = ? AND ("
        " sanctions_result IN ('possible_match', 'clear_match')"
        " OR adverse_media_result = 'serious')", (case_id,)).fetchone()[0]
    needs_compliance = (rule["required_role"] == "compliance"
                        or band in COMPLIANCE_ONLY_BANDS or sensitive)
    if needs_compliance and reviewer_role != "compliance":
        why = ("the band is critical" if band in COMPLIANCE_ONLY_BANDS else
               "there is a sanctions or serious adverse-media finding" if sensitive else
               f"'{decision}' is reserved to compliance")
        raise DecisionRefused(
            f"{reviewer} is {reviewer_role}, but {why}, so this decision is compliance's "
            f"to take")

    # 4. override is computed, never claimed
    override = decision != recommended
    direction = kb.override_direction(decision, recommended)
    if override and not (override_reason or "").strip():
        raise DecisionRefused(
            f"the recommendation was '{recommended}' and the decision is '{decision}'. "
            f"That is an override and needs a reason")
    if not override and (override_reason or "").strip():
        raise DecisionRefused("an override reason was given but the decision agrees with the "
                              "recommendation")

    # 5. escalation needs a target
    if decision == "escalate" and not (escalation_target or "").strip():
        raise DecisionRefused("escalate needs an escalation_target")

    # 6. the evidence has to exist
    refs = ([r for r in evidence_relied_on.split("|") if r]
            if isinstance(evidence_relied_on, str) else list(evidence_relied_on or []))
    known = evidence_pack.known_ids(conn, case_id)
    unknown = [r for r in refs if r not in known]
    if unknown:
        raise DecisionRefused(
            f"the decision cites {unknown}, which are not on {case_id}; a reference nobody "
            f"can follow is not evidence")

    decision_id = db.next_id(conn, "human_decision")
    conn.execute(
        "INSERT INTO human_decision VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (decision_id, case_id, reviewer, reviewer_role, decision, reason_code, rationale,
         "|".join(refs), int(override), override_reason, direction, escalation_target,
         rule["customer_template_id"], db.now()))
    db.audit(conn, case_id, reviewer_role, reviewer, "human_decision_recorded",
             f"{decision_id} -> {decision} (reason {reason_code}); band {band}, recommendation "
             f"{recommended}; override={override}"
             + (f" ({direction})" if direction else "")
             + (f"; escalated to {escalation_target}" if escalation_target else ""),
             kb.version)

    status = rule["resulting_status"]
    db.update_case(conn, case_id, status=status,
                   next_action_owner="compliance" if decision == "escalate" else "system")

    comm = None
    situation = _SITUATION.get(decision)
    if situation:
        comm = communication.send_required_message(
            conn, case_id, situation, kb, chooser, approver=reviewer,
            template_id=rule["customer_template_id"]
            if _allowed(kb, situation, rule["customer_template_id"], case) else None)

    db.audit(conn, case_id, "system", ACTOR, "decision_completed",
             f"{decision_id}: case -> {status}"
             + (f"; customer message {comm.communication_id} ({comm.sent_status})"
                if comm and comm.communication_id else "; no automatic customer message"),
             kb.version)
    return DecisionResult(case_id, decision_id, decision, status, override,
                          direction, comm, [])


def _allowed(kb: KnowledgeBase, situation: str, template_id: str, case) -> bool:
    restricted = bool(case["restricted_finding"])
    return template_id in [r["template_id"] for r in kb.templates_for(situation, restricted)]
