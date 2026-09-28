"""
New evidence after the assessment waits for an analyst.

A document uploaded once the paid checks have answered is checked and read as
usual, places the hold "new evidence after assessment - analyst to review",
runs no paid check and leaves the risk band exactly as it was. The paid checks
run again only when an analyst chooses "Re-run verification" (reason required,
audited); the previous results are archived first, and screening is untouched.

Run: python -m pytest tests/test_reassessment.py -q
"""

import json
import sys
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import customer_checklist                      # noqa: E402
from orchestrator.orchestrator import RerunRefused                    # noqa: E402
from orchestrator.reassessment import REASON                          # noqa: E402
from orchestrator.steps import document_quality                       # noqa: E402
from web import server as console_server                              # noqa: E402

PDF = b"%PDF-1.4\n% synthetic demo file\n"
CASE = "WAL-ONB-0005"            # enhanced due diligence at the demo start
PAID = ("registry_check", "identity_check", "screening_check", "risk_assessment")


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    return path


@pytest.fixture()
def conn(db_path):
    c = data.connect(db_path)
    yield c
    c.close()


def snapshot(conn):
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE case_id = ?", (CASE,))
              .fetchone()[0] for t in PAID}
    risk = conn.execute("SELECT assessment_id, risk_band, risk_score FROM risk_assessment"
                        " WHERE case_id = ?", (CASE,)).fetchone()
    return counts, tuple(risk)


def status(conn):
    return conn.execute("SELECT status, next_action_owner FROM onboarding_case"
                        " WHERE case_id = ?", (CASE,)).fetchone()[:]


def late_upload(conn):
    """During enhanced due diligence an analyst asks for one more document, and
    the customer sends it."""
    item = data.add_checklist_item(conn, CASE, "bank_statement", "compliance.test",
                                   "EDD: recent statement")
    data.upload_document(conn, CASE, item, "statement_aug.pdf", PDF)
    return item


def finish_reading(conn):
    """The analyst releases the visual check and types in the fields."""
    for (doc,) in conn.execute("SELECT document_id FROM document WHERE case_id = ?"
                               " AND quality_status = 'manual_review_required'",
                               (CASE,)).fetchall():
        data.release_document(conn, doc, "analyst.test", "accept", "clear copy")
    for doc, fields in data.awaiting_fields(conn, CASE).items():
        data.enter_fields(conn, doc, "analyst.test", {f["name"]: "synthetic" for f in fields})


def test_an_upload_during_edd_holds_runs_no_paid_checks_and_keeps_the_band(conn):
    assert status(conn) == ("enhanced_due_diligence", "compliance")
    before = snapshot(conn)

    late_upload(conn)
    hold = data.reassessment_hold(conn, CASE)
    assert hold is not None and hold.owner == "analyst" and REASON in hold.reason
    assert snapshot(conn) == before, "no paid check ran and the band did not move"
    assert status(conn) == ("analyst_review_required", "analyst"), "derived from the holds"

    # reading the file to the end changes none of that either
    finish_reading(conn)
    assert data.reassessment_hold(conn, CASE) is not None
    assert snapshot(conn) == before
    assert status(conn) == ("analyst_review_required", "analyst")

    # the customer sees the item under review, and nothing about any hold
    assert all(i["status"] in ("Accepted", "Under review")
               for i in customer_checklist(conn, CASE)["items"] if not i["optional"])


def test_only_the_analysts_re_run_runs_the_paid_checks_again(conn):
    counts, (old_assessment, band, score) = snapshot(conn)
    screening_before = [tuple(r) for r in conn.execute(
        "SELECT * FROM screening_check WHERE case_id = ?", (CASE,))]
    late_upload(conn)

    with pytest.raises(RerunRefused, match="finish reading"):
        data.rerun_verification(conn, CASE, "analyst.test", "new statement")
    finish_reading(conn)
    with pytest.raises(RerunRefused, match="reason"):
        data.rerun_verification(conn, CASE, "analyst.test", "  ")

    trace = data.rerun_verification(conn, CASE, "analyst.test", "statement shows new income")
    assert trace["risk_assessment"]["band"] == band == "high", "same facts, same band"
    after, (new_assessment, _, _) = snapshot(conn)
    assert new_assessment != old_assessment, "a fresh assessment replaced the old one"
    assert after["registry_check"] == counts["registry_check"] == 1
    assert data.reassessment_hold(conn, CASE) is None
    # screening - the PEP match included - is untouched
    assert [tuple(r) for r in conn.execute(
        "SELECT * FROM screening_check WHERE case_id = ?", (CASE,))] == screening_before

    events = {r["action"]: r for r in conn.execute(
        "SELECT * FROM audit_event WHERE case_id = ?", (CASE,))}
    archived = json.loads(events["prior_assessment_archived"]["payload_summary"])
    assert archived["risk_assessment"][0]["assessment_id"] == old_assessment
    assert archived["risk_assessment"][0]["risk_score"] == score
    assert "screening_check" not in archived
    rerun = events["verification_rerun_requested"]
    assert (rerun["actor_type"], rerun["actor_id"]) == ("analyst", "analyst.test")
    assert "statement shows new income" in rerun["payload_summary"]
    assert status(conn) == ("enhanced_due_diligence", "compliance")


def test_keeping_the_assessment_releases_the_hold_and_the_band_stands(conn):
    before = snapshot(conn)
    late_upload(conn)
    finish_reading(conn)
    data.keep_assessment(conn, CASE, "analyst.test", "the statement changes nothing")
    assert data.reassessment_hold(conn, CASE) is None
    assert snapshot(conn) == before
    assert status(conn) == ("enhanced_due_diligence", "compliance")
    with pytest.raises(RerunRefused):
        data.keep_assessment(conn, CASE, "analyst.test", "again")


def test_the_re_run_is_not_offered_before_the_case_was_assessed(conn):
    with pytest.raises(RerunRefused, match="not been verified yet"):
        data.rerun_verification(conn, "WAL-ONB-0002", "analyst.test", "why not")


def test_the_console_offers_the_choice_and_carries_it_out(conn, db_path):
    late_upload(conn)
    finish_reading(conn)
    console_server.drop_connection()
    console_server._conn = data.connect(db_path)
    httpd = console_server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/case/" + CASE) as r:
            page = r.read().decode("utf-8")
        assert "New evidence after assessment" in page and 'value="rerun"' in page

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect())
        body = urlencode({"case_id": CASE, "choice": "rerun", "reason": "checked it",
                          "back": "/case/" + CASE}).encode()
        try:
            opener.open(base + "/action/reassess", body)
        except urllib.error.HTTPError as err:
            assert err.code == 303 and "ok=" in err.headers["Location"]
            assert "risk+band+now+high" in err.headers["Location"].replace("%20", "+")
        fresh = data.connect(db_path)
        assert fresh.execute("SELECT COUNT(*) FROM audit_event WHERE case_id = ?"
                             " AND action = 'verification_rerun_requested'",
                             (CASE,)).fetchone()[0] == 1
        fresh.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        console_server.drop_connection()
