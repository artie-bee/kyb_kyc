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


# ---------------------------------------------------------------------------
# After the pack is built: confirming a condition, adding an item later
# ---------------------------------------------------------------------------

# The phrase the pack writes on a conditional item whose question the form did
# not answer. Such an item is not yet asked of the customer.
AWAITING = "analyst to confirm"
# rule_id on an item an analyst added after the pack was built, e.g. during
# enhanced due diligence. It has no KB rule behind it; the note says who and why.
ADDED_RULE_ID = "ADDED-BY-ANALYST"


def awaiting_confirmation(item) -> bool:
    """A conditional item nobody has yet said applies to this case."""
    return (item["level"] == "conditional" and item["status"] == "pending"
            and AWAITING in (item["note"] or ""))


def shown_to_customer(item) -> bool:
    """Whether the customer is asked for this item at all. Waived items and
    conditions still waiting for an analyst are not."""
    return item["status"] != "waived" and not awaiting_confirmation(item)


def confirm_condition(conn, item_id: str, analyst_id: str, applies: bool, reason: str,
                      kb: KnowledgeBase | None = None) -> dict:
    """An analyst settles a condition the form did not answer. If it applies the
    item is asked of the customer; if not it is waived, and stays on the record."""
    kb = kb or KnowledgeBase()
    if not (analyst_id or "").strip():
        raise ValueError("the confirming analyst must be identified")
    if not (reason or "").strip():
        raise ValueError("a reason is required to settle a condition")
    item = conn.execute(
        "SELECT i.*, p.case_id FROM checklist_item i JOIN requirement_pack p USING (pack_id)"
        " WHERE i.item_id = ?", (item_id,)).fetchone()
    if item is None:
        raise KeyError(f"no such checklist item {item_id}")
    if not awaiting_confirmation(item):
        raise ValueError(f"{item_id} is not a condition waiting to be confirmed")
    if applies:
        status, note = "pending", f"Confirmed as applying by {analyst_id}: {reason}"
    else:
        status, note = "waived", f"Confirmed as not applying by {analyst_id}: {reason}"
    conn.execute("UPDATE checklist_item SET status = ?, note = ? WHERE item_id = ?",
                 (status, note, item_id))
    db.audit(conn, item["case_id"], "analyst", analyst_id, "conditional_item_confirmed",
             f"{item_id} ({item['document_type']}) {'applies' if applies else 'does not apply'}"
             f"; reason: {reason}", kb.version)
    return {"item_id": item_id, "status": status}


def add_item(conn, case_id: str, document_type: str, analyst_id: str, reason: str,
             subject_individual_id: str | None = None,
             kb: KnowledgeBase | None = None) -> str:
    """An analyst asks the customer for one more document after the pack was
    built - enhanced due diligence, say. It is required, and it appears on the
    customer's checklist as soon as it exists."""
    kb = kb or KnowledgeBase()
    if not (analyst_id or "").strip():
        raise ValueError("the analyst adding the item must be identified")
    if not (reason or "").strip():
        raise ValueError("a reason is required to add a checklist item")
    if document_type not in {r["document_type"] for r in kb.requirement_rules}:
        raise ValueError(f"{document_type!r} is not a document type the KB knows")
    pack = conn.execute("SELECT pack_id FROM requirement_pack WHERE case_id = ?",
                        (case_id,)).fetchone()
    if pack is None:
        raise ValueError(f"{case_id} has no requirement pack to add to")
    if subject_individual_id and conn.execute(
            "SELECT 1 FROM individual i JOIN onboarding_case c USING (applicant_id)"
            " WHERE c.case_id = ? AND i.individual_id = ?",
            (case_id, subject_individual_id)).fetchone() is None:
        raise ValueError(f"{subject_individual_id} is not a person on {case_id}")
    item_id = db.next_id(conn, "checklist_item")
    conn.execute(
        "INSERT INTO checklist_item (item_id, pack_id, rule_id, subject_individual_id,"
        " document_type, level, status, note) VALUES (?,?,?,?,?,'required','pending',?)",
        (item_id, pack["pack_id"], ADDED_RULE_ID, subject_individual_id, document_type,
         f"Added by {analyst_id}: {reason}"))
    db.audit(conn, case_id, "analyst", analyst_id, "checklist_item_added",
             f"{item_id} ({document_type}) added after the requirement pack; reason: {reason}",
             kb.version)
    return item_id
