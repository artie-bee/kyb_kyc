"""
What the applicant would see. Nothing else.

Kept as a plain function rather than mixed into the Streamlit page, so a test
can render it for the sensitive cases and check every word of it. A screen you
cannot test is a screen you are trusting on faith, and this is the one screen
where a leak matters.

The rules it follows:

  - only messages actually sent to the applicant, in the wording they were sent;
  - the case status in plain language, from a fixed phrase book. The internal
    status names leak the mechanism: "analyst_review_required" tells a customer
    their case was flagged, and "enhanced_due_diligence" tells them rather more
    than that;
  - no findings, no holds, no risk band, no internal notes, no reason for a
    delay beyond "we are still working on it".

The customer portal (portal/) and the console's customer view both read from
here, so the two cannot tell an applicant different things.
"""

import html
import re
import sys
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from orchestrator.kb import KnowledgeBase                                  # noqa: E402
from orchestrator.steps.communication import scan                          # noqa: E402
from orchestrator.steps import document_quality                           # noqa: E402
from orchestrator.steps.document_quality import (CLOSED_STATUSES,          # noqa: E402
                                                 OPEN_FOR_UPLOAD, accepted_extensions,
                                                 uploaded_names)
from orchestrator.steps.requirement_pack import (ADDED_RULE_ID,            # noqa: E402
                                                 shown_to_customer)

# Internal status -> what the applicant is told. The wording itself is in
# kb/customer_status_wording.csv, under neutral keys, so the file a customer's
# words come from holds no internal status name at all; only this mapping
# knows both. Deliberately vague where the internal status would give away a
# finding: two review statuses share one key.
STATUS_WORDING_KEY = {
    "submitted": "getting_started",
    "document_quality_review": "checking_documents",
    "resubmission_required": "documents_again",
    "verification_in_progress": "checks_under_way",
    "analyst_review_required": "with_the_team",
    "enhanced_due_diligence": "with_the_team",
    "ready_for_decision": "final_look",
    "approved": "account_open",
    "rejected": "not_able",
    "closed_withdrawn": "closed_no_reply",
}
_WORDING = KnowledgeBase().customer_status_wording
PLAIN_STATUS = {status: _WORDING[key] for status, key in STATUS_WORDING_KEY.items()}
FALLBACK = _WORDING["in_progress"]

# Every review state reads the same, whatever is being reviewed. A customer
# whose case has a sanctions hit and one whose identity answer went missing see
# identical words, so the wording itself can never tell them which it is.
REVIEW_LINE = _WORDING["further_review"]
REVIEW_STATUSES = ("analyst_review_required", "enhanced_due_diligence")
PARTNER_LINE = _WORDING["partner_process"]

STEP_TITLES = ("Application submitted", "Documents reviewed", "Your action",
               "Verification and review", "Outcome")

# What each document is called on the portal. Every document_type the KB can ask
# for must have one; tests/test_portal.py fails on a type without a name, so a
# new KB rule cannot put an internal code in front of a customer.
DOCUMENT_LABELS = {
    "authorised_signatory_list": "List of authorised signatories",
    "bank_statement": "Business bank statement",
    "board_resolution": "Board resolution",
    "business_activity_description": "Description of your business activity",
    "certificate_of_incorporation": "Certificate of incorporation",
    "director_register": "Register of directors",
    "id_document": "Identity document (passport or ID card)",
    "liveness_selfie": "Selfie to confirm your identity",
    "nominee_trust_explanation": "Explanation of nominee or trust arrangements",
    "ownership_chart": "Ownership chart",
    "programme_business_plan": "Programme business plan",
    "proof_of_address": "Proof of address",
    "registry_extract": "Company registration extract",
    "shareholder_register": "Register of shareholders",
    "source_of_funds_declaration": "Source of funds declaration",
    "source_of_wealth_statement": "Source of wealth statement",
    "tax_registration_certificate": "Tax registration certificate",
    "ubo_declaration": "Declaration of beneficial owners",
    "website_or_platform_details": "Website or platform details",
}

# The four statuses a customer ever sees. "Under review" covers anything a
# person is still looking at - a document held at the quality screen for any
# reason, the visual check mock mode cannot run, fields waiting to be typed in -
# and says nothing about which: the faults that send a document to a person
# (suspected alteration, a name that does not match) are exactly the ones a
# customer must not be told about.
ACCEPTED = "Accepted"
UNDER_REVIEW = "Under review"
RESUBMIT = "Resubmission needed"
NOT_UPLOADED = "Not uploaded yet"
CUSTOMER_STATUSES = (ACCEPTED, UNDER_REVIEW, RESUBMIT, NOT_UPLOADED)

# What an item an analyst added later is called on a case with a restricted
# finding. Naming the document ("source of wealth statement", after a PEP hit)
# would say why it was asked for; the approved message that goes with the
# request says what to send.
GENERIC_DOCUMENT = "An additional document we have asked for"


def customer_view(conn, case_id: str) -> dict:
    """Everything, and only everything, the applicant may be shown."""
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone()
    if case is None:
        raise KeyError(f"no such case {case_id}")
    applicant = conn.execute(
        "SELECT a.legal_name FROM applicant a JOIN onboarding_case c USING (applicant_id)"
        " WHERE c.case_id = ?", (case_id,)).fetchone()

    messages = [
        {"sent_at": r["sent_at"], "text": r["body"]}
        for r in conn.execute(
            "SELECT o.sent_at, o.body FROM outbox o WHERE o.case_id = ? AND o.audience ="
            " 'applicant' ORDER BY o.outbox_id", (case_id,))
    ]
    partner = bool(case["white_label_branch_flag"])
    applied_on = (case["created_at"] or "")[:10]
    return {
        "case_id": case_id,
        "applicant_name": applicant["legal_name"] if applicant else "",
        "status_text": PARTNER_LINE if partner else PLAIN_STATUS.get(case["status"], FALLBACK),
        "applied_on": applied_on,
        "messages": messages,
        "partner": partner,
        "closed": case["status"] in CLOSED_STATUSES,
        "steps": [] if partner else _steps(conn, case_id, case, applied_on),
    }


def _items(conn, case_id: str) -> list:
    """The checklist as the customer sees it. Waived items, and conditions an
    analyst has not yet confirmed apply, are not asked of the customer at all."""
    rows = conn.execute(
        "SELECT i.*, ind.full_name FROM checklist_item i JOIN requirement_pack p"
        " USING (pack_id) LEFT JOIN individual ind"
        " ON ind.individual_id = i.subject_individual_id"
        " WHERE p.case_id = ? ORDER BY i.item_id", (case_id,)).fetchall()
    return [r for r in rows if shown_to_customer(r)]


def _needed(item) -> bool:
    """Counts towards the three header numbers: everything shown except optional
    items. A conditional item that is shown is one that applies."""
    return item["level"] != "optional"


def _still_being_read(conn, document_id: str | None, kb: KnowledgeBase) -> bool:
    """An accepted file whose fields have not been read yet: waiting for an
    analyst to type them in (mock mode), or not yet reached by extraction at all.
    To the customer it has been received, not accepted - nobody has read it."""
    if document_id is None:
        return False
    rows = conn.execute(
        "SELECT entry_method FROM extracted_field WHERE document_id = ?",
        (document_id,)).fetchall()
    if rows:
        return any(r["entry_method"] == "awaiting_analyst_entry" for r in rows)
    doc_type = conn.execute("SELECT document_type FROM document WHERE document_id = ?",
                            (document_id,)).fetchone()["document_type"]
    return bool(kb.fields_for(doc_type))


def _current_document(conn, item_id: str) -> str | None:
    row = conn.execute("SELECT MAX(document_id) FROM checklist_item_document WHERE item_id = ?",
                       (item_id,)).fetchone()
    return row[0] if row else None


def _steps(conn, case_id: str, case, applied_on: str) -> list[dict]:
    """The five customer steps, each done / current / todo / skipped, with one
    plain line. Read from the checklist and the case status - never from holds,
    bands or findings, which is why nothing here can carry one."""
    status = case["status"]
    kb = KnowledgeBase()
    items = _items(conn, case_id)
    required_open = [i for i in items if _needed(i) and (
        i["status"] != "accepted"
        or _still_being_read(conn, _current_document(conn, i["item_id"]), kb))]
    owed = [i for i in items if _needed(i) and i["status"] in OPEN_FOR_UPLOAD]
    closed = status in CLOSED_STATUSES

    steps = [("done", f"We received your application on {applied_on}.")]

    if not items:
        steps.append(("todo", "We will list the documents we need from you here."))
    elif not required_open:
        steps.append(("done", "We have checked the documents you sent."))
    elif closed:
        steps.append(("skipped", "We did not receive every document we asked for."))
    else:
        steps.append(("current", "We are checking the documents you sent."))

    if closed:
        steps.append(("skipped", "We did not hear back about the documents we asked for.")
                     if owed else ("done", "Nothing more is needed from you."))
    elif owed:
        n = len(owed)
        plural = "s" if n > 1 else ""
        steps.append(("current", f"We need {n} document{plural} from you. "
                                 "Your checklist shows which."))
    elif case["next_action_owner"] == "customer":
        steps.append(("current", "Please read our latest message and reply to it."))
    else:
        steps.append(("done", "Nothing is needed from you at the moment."))

    if status in ("approved", "rejected"):
        steps.append(("done", "Our checks are complete."))
    elif closed:
        steps.append(("skipped", "This step did not start."))
    elif required_open or not items:
        steps.append(("todo", "This starts once we have every document."))
    elif status in REVIEW_STATUSES:
        steps.append(("current", REVIEW_LINE))
    elif status == "ready_for_decision":
        steps.append(("current", PLAIN_STATUS["ready_for_decision"]))
    else:
        steps.append(("current", PLAIN_STATUS["verification_in_progress"]))

    steps.append(("done", PLAIN_STATUS[status]) if closed else
                 ("todo", "We will tell you the outcome here and in a message."))
    return [{"n": n, "title": title, "state": state, "line": line}
            for n, (title, (state, line)) in enumerate(zip(STEP_TITLES, steps), 1)]


def _item_status(item, current, being_read=False) -> str:
    """One of the four customer statuses, from the checklist item's recorded
    outcome. An accepted document is Accepted the moment it is accepted, by the
    quality screen or by an analyst: reading its fields afterwards is internal
    work, not something the customer is waiting on."""
    if item["status"] == "resubmission_requested":
        return RESUBMIT
    if item["status"] == "accepted":
        return ACCEPTED
    if item["status"] == "manual_review" or current is not None:
        return UNDER_REVIEW
    return NOT_UPLOADED


def _reason(current, rule: dict | None, kb: KnowledgeBase, restricted: bool = False) -> str:
    """The approved wording (kb/resubmission_reason_text.csv) for why this
    document must be sent again - the reason the screen found, or the one the
    analyst chose. Never empty: "*" covers any other reason, and is all a case
    with a restricted finding is ever told."""
    texts = kb.resubmission_reason_text
    if restricted:
        return texts["*"]["customer_text"]
    codes = [c for c in ((current["resubmission_reasons"] if current else "") or "").split("|")
             if c]
    code = next((c for c in codes if c in texts), "*")
    return texts[code]["customer_text"].format(days=(rule or {}).get("max_age_days") or "90")


def _tips(item, rule: dict | None, kb: KnowledgeBase) -> list[str]:
    """kb/document_guidance.csv for this document type. A tip naming an age is
    filled from the requirement rule that asked for this item; if that rule sets
    no age, the tip is left out rather than stating a limit nobody set."""
    out = []
    for tip in kb.document_guidance.get(item["document_type"], []):
        if "{max_age_days}" in tip:
            days = (rule or {}).get("max_age_days")
            if not days:
                continue
            tip = tip.replace("{max_age_days}", days)
        out.append(tip)
    return out


def next_step(cl: dict, view: dict) -> dict:
    """The single most useful thing the customer can do now, from their
    checklist as it stands. kind is "upload" (with the item to upload), or
    "nothing"; text is plain wording only - no holds, findings or statuses."""
    if view.get("partner"):
        return {"kind": "nothing", "text": view["status_text"], "item": None}
    if view.get("closed"):
        return {"kind": "nothing", "text": view["status_text"], "item": None}
    for item in cl["items"]:
        if item["can_upload"] and item["status"] == RESUBMIT and not item["optional"]:
            return {"kind": "upload", "item": item,
                    "text": "Upload a clearer copy: " + _label(item)}
    for item in cl["items"]:
        if item["can_upload"] and item["status"] == NOT_UPLOADED and not item["optional"]:
            more = cl["still_needed"] - 1
            return {"kind": "upload", "item": item,
                    "text": "Upload your " + _label(item)[0].lower() + _label(item)[1:]
                    + (f" ({more} more after this)" if more > 0 else "")}
    return {"kind": "nothing", "item": None,
            "text": "Nothing to do right now. We'll message you when there's an update."}


def _label(item) -> str:
    return item["document"] + (" - " + item["person"] if item["person"] else "")


def customer_checklist(conn, case_id: str, kb: KnowledgeBase | None = None) -> dict:
    """The customer's checklist: what the backend says this case needs, read
    fresh from the checklist_item rows every time. The portal only displays it.

    Included: required items; optional items, labelled and not counted; items
    an analyst added later, as soon as they exist (by a generic name when the
    case has a restricted finding). Left out: waived items, and conditions an
    analyst has not yet confirmed.

    Each item carries only what a customer may see - its checklist item id, a
    plain document name, the person it is for, one of four statuses, the
    approved reason when a resubmission is needed, whether it can be uploaded
    now, and the names of the files uploaded for it before. No rule ids,
    conditions, notes, holds, flags, internal statuses, bands, scores or findings.
    """
    kb = kb or KnowledgeBase()
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    if case is None:
        raise KeyError(f"no such case {case_id}")
    applicant = conn.execute("SELECT legal_name FROM applicant WHERE applicant_id = ?",
                             (case["applicant_id"],)).fetchone()
    rules = {r["rule_id"]: r for r in kb.requirement_rules}
    names = uploaded_names(conn, case_id)
    open_case = case["status"] not in CLOSED_STATUSES
    restricted = bool(case["restricted_finding"])

    items, needed, still_needed = [], 0, 0
    counts = {ACCEPTED: 0, UNDER_REVIEW: 0}
    for item in _items(conn, case_id):
        docs = conn.execute(
            "SELECT d.* FROM checklist_item_document cid JOIN document d USING (document_id)"
            " WHERE cid.item_id = ? ORDER BY d.document_id", (item["item_id"],)).fetchall()
        current = docs[-1] if docs else None
        # A partner case stops after the quality screen by design, so nothing is
        # waiting to read its accepted files: accepted there means accepted.
        being_read = (not case["white_label_branch_flag"] and _still_being_read(
            conn, current["document_id"] if current else None, kb))
        status = _item_status(item, current, being_read)
        generic = item["rule_id"] == ADDED_RULE_ID and restricted
        if _needed(item):
            needed += 1
            # Still needed means the customer still owes it: nothing uploaded
            # yet, or an upload we had to send back. A file that is in and
            # being looked at is not owed.
            still_needed += status in (NOT_UPLOADED, RESUBMIT)
            if status in counts:
                counts[status] += 1
        items.append({
            "checklist_item_id": item["item_id"],
            "document": GENERIC_DOCUMENT if generic else DOCUMENT_LABELS.get(
                item["document_type"], item["document_type"].replace("_", " ").capitalize()),
            "person": "" if generic else (item["full_name"] or ""),
            "optional": item["level"] == "optional",
            "status": status,
            "reason": _reason(current, rules.get(item["rule_id"]), kb, restricted)
            if status == RESUBMIT
            else "",
            "can_upload": open_case and item["status"] in OPEN_FOR_UPLOAD,
            # guidance only, never a requirement; ages come from the item's rule
            "tips": [] if generic else _tips(item, rules.get(item["rule_id"]), kb),
            # newest first; a portal upload by the customer's own file name
            "previous_uploads": [names.get(d["file_name"], d["file_name"])
                                 for d in reversed(docs)],
        })
    extensions = accepted_extensions(kb)
    return {
        "applicant_name": applicant["legal_name"] if applicant else "",
        "items": items,
        # The three header numbers, over the items the customer must supply:
        # they always add up to total_needed. Optional items are counted apart.
        "accepted": counts[ACCEPTED],
        "under_review": counts[UNDER_REVIEW],
        "still_needed": still_needed,
        "total_needed": needed,
        "optional": sum(1 for i in items if i["optional"]),
        "accepted_types": [x for x in extensions if x != "jpeg"],
        "accept_attr": ",".join("." + x for x in extensions),
        "max_mb": document_quality.MAX_UPLOAD_BYTES // (1024 * 1024),
        "open": open_case,
        "partner": bool(case["white_label_branch_flag"]),
    }


def visible_text(page: str) -> str:
    """The words a person would read on a rendered page: no tags, no scripts,
    no styles, entities decoded."""
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", page))


def leaks(view) -> list[str]:
    """Restricted wording anywhere in what the applicant would be shown.

    Takes the view dict, or a whole rendered page as a string - the portal
    passes every page it serves through here. Used by the tests, and by the
    pages themselves: if this ever returns anything the page refuses to render
    rather than showing it.
    """
    if isinstance(view, str):
        return scan(visible_text(view))
    found = []
    texts = ([view["status_text"], view["applicant_name"]]
             + [m["text"] for m in view["messages"]]
             + [s["line"] for s in view.get("steps", [])])
    for text in texts:
        found += scan(text or "")
    return sorted(set(found))
