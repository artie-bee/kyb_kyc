"""
New evidence after the assessment.

Once the paid checks have answered, a document that arrives later - one an
analyst added during enhanced due diligence, say - is still screened and read,
but it does not move the case by itself. It places a hold for an analyst:

    "new evidence after assessment - analyst to review"

and the case status follows from that hold as it would from any other. The
risk band stays exactly as it was until the analyst acts, one of two ways:

  - Re-run verification (orchestrator.rerun_verification): the only way the
    paid checks run again. Reason required, audited;
  - Keep the assessment: release the hold, with a reason, and the band stands.

The hold is placed under its own step name, so neither the quality screen nor
extraction - which lift and re-place their own holds every time they route -
can lift it. Only a named person can.
"""

from . import db, holds

STEP = "step.reassessment"
REASON = "new evidence after assessment - analyst to review"


def open_hold(conn, case_id: str):
    return next((h for h in holds.open_holds(conn, case_id) if h.placed_by_step == STEP), None)


def place(conn, case_id: str, document_id: str, kb=None) -> str:
    """Hold the case for an analyst. One hold covers any number of late files."""
    existing = open_hold(conn, case_id)
    if existing:
        db.audit(conn, case_id, "system", STEP, "new_evidence_after_assessment",
                 f"{document_id} added to the evidence waiting on {existing.hold_id}",
                 getattr(kb, "version", None))
        return existing.hold_id
    hold_id = holds.place(conn, case_id, STEP, "manual_review",
                          f"{REASON} ({document_id})", "analyst", kb)
    db.audit(conn, case_id, "system", STEP, "new_evidence_after_assessment",
             f"{document_id} arrived after the paid checks; {hold_id} placed; the risk band "
             f"stands until an analyst re-runs verification or keeps the assessment",
             getattr(kb, "version", None))
    return hold_id


def release(conn, case_id: str, analyst_id: str, reason: str, kb=None) -> str | None:
    """A named person lifts the hold. Returns its id, or None if none was open."""
    if not (analyst_id or "").strip():
        raise ValueError("the analyst must be identified")
    if not (reason or "").strip():
        raise ValueError("a reason is required")
    hold = open_hold(conn, case_id)
    if hold is None:
        return None
    holds.release(conn, hold.hold_id, analyst_id, reason, kb=kb)
    return hold.hold_id
