"""
STEP 1 - Intake, case creation and classification  (brief Sections 5.1, 6.2)

Input : one application (dict) from the portal or email channel
Output: IntakeResult with the new case_id, applicant_type, jurisdiction route
        and the next step to run

This step is deterministic. It uses KB rules only - no LLM - because the
applicant type and jurisdiction come from structured form fields. An AI
classifier would only be needed if applications arrived as free-text email.
"""

from dataclasses import dataclass, field

from .. import db
from ..kb import KnowledgeBase, match_rule

ACTOR = "step.intake"
REQUIRED_FIELDS = ["legal_name", "entity_type", "country"]
# application["origin"] for an application made in the portal's demo form
DEMO_ORIGIN = "portal_demo"


@dataclass
class IntakeResult:
    case_id: str
    applicant_id: str
    applicant_type: str | None
    jurisdiction_path: str | None
    entity_scope: str | None
    route: str                     # primary | white_label_branch | manual_review | incomplete
    status: str
    next_step: str | None          # name of the next orchestrator step, or None to stop
    problems: list[str] = field(default_factory=list)


def classify(application: dict, kb: KnowledgeBase) -> tuple[str | None, str, str | None]:
    """Return (applicant_type, route, rule_id) from the first matching KB rule."""
    facts = {
        "entity_type": application["applicant"].get("entity_type"),
        "programme_type": application.get("programme_type", "direct"),
        **application.get("ownership", {}),
    }
    for rule in kb.applicant_type_rules:
        if match_rule(rule["conditions"], facts):
            return rule["applicant_type"], rule["route"], rule["rule_id"]
    return None, "manual_review", None


def route_jurisdiction(country: str, kb: KnowledgeBase) -> tuple[str | None, str]:
    row = kb.jurisdiction_routing.get(country)
    if not row or row["supported"].lower() != "true":
        return None, "undetermined"
    return row["jurisdiction_path"], row["entity_scope"]


def run(conn, application: dict, kb: KnowledgeBase) -> IntakeResult:
    a = application["applicant"]
    ts = db.now()

    # 1. Create applicant + case first, so even a broken application is tracked.
    applicant_id = db.next_id(conn, "applicant")
    conn.execute(
        "INSERT INTO applicant VALUES (?,?,?,?,?,?,?,?,?)",
        (applicant_id, a.get("legal_name") or "(missing)", a.get("trading_name"),
         a.get("registration_number"), a.get("entity_type") or "(missing)",
         a.get("country") or "(missing)", a.get("business_activity"),
         a.get("expected_usage"), None),
    )
    # An application typed into the portal's demo form is synthetic and has no
    # dataset row behind it, so it gets its own case series (WAL-DEMO-).
    demo = application.get("origin") == DEMO_ORIGIN
    if demo and application["source_channel"] != "portal":
        raise ValueError("a demo application can only arrive through the portal")
    case_id = db.next_id(conn, "onboarding_case",
                         prefix=db.DEMO_CASE_PREFIX if demo else None)
    conn.execute(
        "INSERT INTO onboarding_case (case_id, applicant_id, source_channel, status,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (case_id, applicant_id, application["source_channel"], "submitted", ts, ts),
    )
    db.audit(conn, case_id, "system", ACTOR, "case_created",
             f"Application {application.get('application_id')} via {application['source_channel']}"
             + ("; demo application typed into the customer portal (synthetic data)"
                if demo else ""))

    # 2. Completeness check on the form itself.
    missing = [f"applicant.{f}" for f in REQUIRED_FIELDS if not a.get(f)]
    is_white_label = application.get("programme_type") == "white_label"
    if not application.get("individuals") and not is_white_label:
        missing.append("individuals (at least one director, UBO or sole trader)")
    if missing:
        db.update_case(conn, case_id, next_action_owner="customer")
        db.audit(conn, case_id, "system", ACTOR, "intake_incomplete", "Missing: " + ", ".join(missing))
        return IntakeResult(case_id, applicant_id, None, None, None, "incomplete",
                            "submitted", None, missing)

    # 3. Store individuals and UBOs.
    ref_to_id = {}
    for person in application.get("individuals", []):
        ind_id = db.next_id(conn, "individual")
        ref_to_id[person["ref"]] = ind_id
        conn.execute(
            "INSERT INTO individual VALUES (?,?,?,?,?,?,?,?,?)",
            (ind_id, applicant_id, person["role"], person["full_name"],
             person.get("date_of_birth"), person.get("nationality"),
             person.get("residence_country"), None, person.get("relationship_to_entity")),
        )
    for u in application.get("ubos", []):
        conn.execute(
            "INSERT INTO ubo (ubo_id, applicant_id, individual_id, ownership_percentage,"
            " ownership_chain_percentages, control_type, ownership_path)"
            " VALUES (?,?,?,?,?,?,?)",
            (db.next_id(conn, "ubo"), applicant_id, ref_to_id[u["individual_ref"]],
             u["ownership_percentage"],
             "|".join(str(c) for c in u.get("ownership_chain_percentages") or []),
             u.get("control_type"), u.get("ownership_path")),
        )

    # 4. Classify applicant type and jurisdiction from the KB.
    applicant_type, route, rule_id = classify(application, kb)
    jurisdiction_path, entity_scope = route_jurisdiction(a["country"], kb)
    db.audit(conn, case_id, "system", ACTOR, "applicant_classified",
             f"type={applicant_type} via {rule_id}; country={a['country']} -> "
             f"jurisdiction={jurisdiction_path}, entity_scope={entity_scope}", kb.version)

    problems = []
    if applicant_type is None:
        problems.append(f"No applicant-type rule matched entity_type '{a['entity_type']}'")
    if jurisdiction_path is None:
        problems.append(f"Country '{a['country']}' has no supported jurisdiction route")

    # 5. Decide where the case goes next.
    if problems:
        route, status, next_owner, next_step = "manual_review", "analyst_review_required", "analyst", None
        db.audit(conn, case_id, "system", ACTOR, "routed_to_manual_review", "; ".join(problems))
    elif route == "white_label_branch":
        # KYB intake only: the requirement pack is still built so the partner knows
        # what to supply, but the case stops there. The legal entity is chosen in the
        # later programme phase, so the scope stays undetermined.
        entity_scope = "undetermined"
        status, next_owner, next_step = "submitted", "system", "requirement_pack"
        db.audit(conn, case_id, "system", ACTOR, "routed_to_white_label_branch",
                 "Out of primary POC scope; KYB intake only, future-phase steps shown separately")
    else:
        status, next_owner, next_step = "submitted", "system", "requirement_pack"

    db.update_case(conn, case_id, applicant_type=applicant_type,
                   jurisdiction_path=jurisdiction_path, entity_scope=entity_scope,
                   status=status, next_action_owner=next_owner,
                   white_label_branch_flag=int(route == "white_label_branch"))
    return IntakeResult(case_id, applicant_id, applicant_type, jurisdiction_path,
                        entity_scope, route, status, next_step, problems)
