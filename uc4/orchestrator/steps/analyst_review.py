"""
Analyst release of a document held at Step 3  (brief Section 5.3)

Step 3 can park a document at manual_review_required. Nothing automatic clears
that: a named analyst has to look at the file and decide, with a reason. This
module is that decision, and it is the only supported way a held document ever
becomes usable.

Two decisions:
    accept                the document is fine as supplied; it becomes
                          accepted_for_checks and its checklist item is accepted
    request_resubmission  the customer must send a better copy; the case goes
                          back to them

Either way the screening verdict itself is preserved. quality_status is the live
status and moves; quality_status_at_screen keeps what Step 3 decided, so the
record still shows the document was flagged and that a human overrode it, rather
than quietly reading as though it had passed first time.

After the decision the case is routed again through the same logic Step 3 uses,
so releasing the last held document moves the case on by itself.
"""

from dataclasses import dataclass

from .. import db, holds
from ..kb import KnowledgeBase
from . import document_quality

ACTOR_ROLE = "analyst"
DECISIONS = ("accept", "request_resubmission")

# decision -> (document quality_status, checklist item status)
_OUTCOME = {
    "accept": (document_quality.ACCEPTED, "accepted"),
    "request_resubmission": ("resubmission_required", "resubmission_requested"),
}


@dataclass
class ReleaseResult:
    document_id: str
    decision: str
    document_status: str
    case_status: str
    next_step: str | None


def resubmission_reason_codes(kb: KnowledgeBase) -> list[str]:
    """The approved reasons an analyst may give the customer for sending a
    document back: kb/resubmission_reason_text.csv, less the catch-all."""
    return [code for code in kb.resubmission_reason_text if code != "*"]


def release_document(conn, document_id: str, analyst_id: str, decision: str, reason: str,
                     kb: KnowledgeBase | None = None,
                     reason_code: str | None = None) -> ReleaseResult:
    """Record an analyst's decision on a held document and re-route the case.

    A reason is required. An override with no stated reason is not auditable, and
    this is precisely the point where a human is overruling the system. Sending a
    document back also needs `reason_code`, one of the approved customer reasons,
    which is what the customer is told.

    Only this document's own hold is lifted, in the analyst's name; any other
    document stays exactly as it was.
    """
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, not {decision!r}")
    if not (reason or "").strip():
        raise ValueError("a reason is required to release a document")
    if not (analyst_id or "").strip():
        raise ValueError("the releasing analyst must be identified")

    kb = kb or KnowledgeBase()
    if decision == "request_resubmission" and reason_code not in resubmission_reason_codes(kb):
        raise ValueError(
            "choose the reason the customer will be given from the approved list "
            f"{resubmission_reason_codes(kb)}")
    doc = conn.execute("SELECT * FROM document WHERE document_id = ?", (document_id,)).fetchone()
    if doc is None:
        raise KeyError(f"no such document {document_id}")
    if doc["quality_status"] != "manual_review_required":
        raise ValueError(
            f"{document_id} is {doc['quality_status']}, not held for manual review; "
            f"only a held document can be released")

    new_status, item_status = _OUTCOME[decision]
    conn.execute(
        "UPDATE document SET quality_status = ?, resubmission_required = ?, released_by = ?,"
        " release_reason = ?, resubmission_reasons = ? WHERE document_id = ?",
        (new_status, int(decision == "request_resubmission"), analyst_id, reason,
         reason_code if decision == "request_resubmission" else doc["resubmission_reasons"],
         document_id))

    item = conn.execute(
        "SELECT item_id, resubmission_attempts FROM checklist_item WHERE item_id = "
        "(SELECT item_id FROM checklist_item_document WHERE document_id = ?)",
        (document_id,)).fetchone()
    if item is not None:
        conn.execute("UPDATE checklist_item SET status = ? WHERE item_id = ?",
                     (item_status, item["item_id"]))

    db.audit(conn, doc["case_id"], ACTOR_ROLE, analyst_id, "document_released_after_review",
             f"{document_id} ({doc['document_type']}) released by {analyst_id}; "
             f"decision {decision}; screen verdict {doc['quality_status_at_screen']} "
             f"(flags {doc['quality_flags'] or 'none'}) overridden; reason: {reason}",
             kb.version)

    # This document's own visual-check hold, released by the person who looked.
    for h in holds.open_holds(conn, doc["case_id"]):
        if document_quality.visual_hold_document(h.reason) == document_id:
            holds.release(conn, h.hold_id, analyst_id, f"{decision}: {reason}", kb=kb)
    routed = document_quality.route_case(conn, doc["case_id"], kb)
    return ReleaseResult(document_id, decision, new_status, routed.status, routed.next_step)


def replay_scripted_releases(conn, case_id: str, releases: list[dict],
                             kb: KnowledgeBase | None = None) -> list[ReleaseResult]:
    """Mock-mode equivalent of an analyst working through the queue.

    `releases` is what the dataset scripted: one entry per held document, each
    naming the analyst, the decision and the reason. Documents are matched by
    file name because ids are minted per run.
    """
    out = []
    for r in releases:
        doc = conn.execute(
            "SELECT document_id FROM document WHERE case_id = ? AND file_name = ?",
            (case_id, r["file_name"])).fetchone()
        if doc is None:
            raise KeyError(f"scripted release names {r['file_name']}, which is not on {case_id}")
        out.append(release_document(conn, doc["document_id"], r["analyst_id"],
                                    r["decision"], r["reason"], kb))
    return out
