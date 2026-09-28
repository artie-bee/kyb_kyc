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
from orchestrator.steps.document_quality import (CLOSED_STATUSES,          # noqa: E402
                                                 MAX_UPLOAD_BYTES, OPEN_FOR_UPLOAD,
                                                 accepted_extensions,
                                                 document_stage_open)

# Internal status -> what the applicant is told. Deliberately vague where the
# internal status would give away a finding.
PLAIN_STATUS = {
    "submitted": "We have your application and are getting started.",
    "document_quality_review": "We are checking the documents you sent.",
    "resubmission_required": "We need one or more documents again before we can "
                             "carry on. Details are in the message we sent you.",
    "verification_in_progress": "Your application is with us and the checks are under way.",
    "analyst_review_required": "Your application is with our onboarding team. "
                               "There is nothing you need to do at the moment.",
    "enhanced_due_diligence": "Your application is with our onboarding team. "
                              "There is nothing you need to do at the moment.",
    "ready_for_decision": "The checks are complete and your application is with us "
                          "for a final look.",
    "approved": "Your account is open.",
    "rejected": "We are not able to open an account at this time.",
    "closed_withdrawn": "We closed this application because we did not hear back. "
                        "You are welcome to apply again.",
}
FALLBACK = "Your application is with us."

# Every review state reads the same, whatever is being reviewed. A customer
# whose case has a sanctions hit and one whose identity answer went missing see
# identical words, so the wording itself can never tell them which it is.
REVIEW_LINE = ("Further review is needed before we can finish. Your application is with "
               "our onboarding team for an additional review step.")
REVIEW_STATUSES = ("analyst_review_required", "enhanced_due_diligence")
PARTNER_LINE = ("This application uses a separate partner onboarding process. Our "
                "programme delivery team will contact you about the next steps.")

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

# The checklist statuses a customer sees. Under review never says why: the
# faults that send a document to a person rather than back to the customer
# (suspected alteration, a name that does not match) are exactly the ones a
# customer must not be told about.
NOT_UPLOADED = "Not yet uploaded"
RECEIVED = "Received"
UNDER_REVIEW = "Under review"
ACCEPTED = "Accepted"
RESUBMIT = "Resubmission needed"
REPLACED = "Replaced by a newer upload"

# Why a resubmission is needed, keyed by the reason code on the document. Only
# resubmission reasons are here, and only what the customer can act on.
PLAIN_REASON = {
    "document_unreadable": "We could not read this document clearly. Please upload a "
                           "sharp, complete copy of the original.",
    "document_expired": "This document has expired. Please upload one that is still valid.",
    "proof_of_address_too_old": "This document is too old. Please upload one dated within "
                                "the last {days} days.",
}
PLAIN_REASON_FALLBACK = "Please upload a new copy of this document."


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
    """The checklist as the customer sees it: waived items are not asked for."""
    return conn.execute(
        "SELECT i.*, ind.full_name FROM checklist_item i JOIN requirement_pack p"
        " USING (pack_id) LEFT JOIN individual ind"
        " ON ind.individual_id = i.subject_individual_id"
        " WHERE p.case_id = ? AND i.status != 'waived' ORDER BY i.item_id",
        (case_id,)).fetchall()


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
    required_open = [i for i in items if i["level"] == "required" and (
        i["status"] != "accepted"
        or _still_being_read(conn, _current_document(conn, i["item_id"]), kb))]
    owed = [i for i in items if i["level"] == "required" and i["status"] in OPEN_FOR_UPLOAD]
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
    if item["status"] == "accepted":
        return RECEIVED if being_read else ACCEPTED
    if item["status"] == "manual_review":
        return UNDER_REVIEW
    if item["status"] == "resubmission_requested":
        return RESUBMIT
    return RECEIVED if current is not None else NOT_UPLOADED


def _reason(current, rule: dict | None) -> str:
    codes = [c for c in ((current["resubmission_reasons"] if current else "") or "").split("|")
             if c]
    for code in codes:
        if code in PLAIN_REASON:
            return PLAIN_REASON[code].format(days=(rule or {}).get("max_age_days") or "90")
    return PLAIN_REASON_FALLBACK


def customer_checklist(conn, case_id: str, kb: KnowledgeBase | None = None) -> dict:
    """What the customer owes and what they have sent, and nothing else.

    Per item: the document name, the person it is for, one of five statuses, a
    reason only when a resubmission is needed, whether it can be uploaded now,
    and every upload made against it - older ones marked as replaced.
    """
    kb = kb or KnowledgeBase()
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    if case is None:
        raise KeyError(f"no such case {case_id}")
    rules = {r["rule_id"]: r for r in kb.requirement_rules}
    open_case = (case["status"] not in CLOSED_STATUSES
                 and not case["white_label_branch_flag"])
    collecting = open_case and document_stage_open(conn, case_id)
    required, other = [], []
    for item in _items(conn, case_id):
        docs = conn.execute(
            "SELECT d.* FROM checklist_item_document cid JOIN document d USING (document_id)"
            " WHERE cid.item_id = ? ORDER BY d.document_id", (item["item_id"],)).fetchall()
        current = docs[-1] if docs else None
        if current is None and item["level"] != "required" and not collecting:
            continue        # an optional item nobody sent, once no more are being taken
        status = _item_status(item, current,
                              _still_being_read(conn, current["document_id"] if current else None,
                                                kb))
        history = [{"file_name": d["file_name"],
                    "uploaded": (d["upload_time"] or "").replace("T", " ").rstrip("Z")[:16],
                    "label": status if d is current else REPLACED}
                   for d in reversed(docs)]
        entry = {
            "item_id": item["item_id"],
            "document": DOCUMENT_LABELS.get(item["document_type"],
                                            item["document_type"].replace("_", " ").capitalize()),
            "person": item["full_name"] or "",
            "status": status,
            "reason": _reason(current, rules.get(item["rule_id"])) if status == RESUBMIT else "",
            "can_upload": collecting and item["status"] in OPEN_FOR_UPLOAD,
            "history": history,
        }
        (required if item["level"] == "required" else other).append(entry)
    extensions = accepted_extensions(kb)
    return {
        "case_id": case_id,
        "required": required,
        "other": other,
        "accepted_types": [x for x in extensions if x != "jpeg"],
        "accept_attr": ",".join("." + x for x in extensions),
        "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "open": open_case,
        "collecting": collecting,
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
