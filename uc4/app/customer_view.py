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
"""

import sys
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from orchestrator.steps.communication import scan                          # noqa: E402

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


def customer_view(conn, case_id: str) -> dict:
    """Everything, and only everything, the applicant may be shown."""
    case = conn.execute(
        "SELECT status, created_at FROM onboarding_case WHERE case_id = ?",
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
    return {
        "case_id": case_id,
        "applicant_name": applicant["legal_name"] if applicant else "",
        "status_text": PLAIN_STATUS.get(case["status"], FALLBACK),
        "applied_on": (case["created_at"] or "")[:10],
        "messages": messages,
    }


def leaks(view: dict) -> list[str]:
    """Restricted wording anywhere in what the applicant would be shown.

    Used by the test, and by the page itself: if this ever returns anything the
    page refuses to render rather than showing it.
    """
    found = []
    for text in [view["status_text"], view["applicant_name"]] + [m["text"] for m in view["messages"]]:
        found += scan(text or "")
    return sorted(set(found))
