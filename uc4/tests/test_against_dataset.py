"""
End-to-end check against the scripted 10-case dataset.

Replaces the old test_checklist_counts, which asserted item counts for the
sample requirement matrix that kb/requirement_rule.csv no longer carries.

Run: python -m pytest tests -q   (or: python tests/test_against_dataset.py)
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import db                                                    # noqa: E402
from orchestrator.kb import KnowledgeBase                                      # noqa: E402
from orchestrator.orchestrator import process_application                      # noqa: E402
from orchestrator.quality_checker import (MockQualityChecker,                  # noqa: E402
                                          QualityVerdict, UnknownQualityFlag)
from orchestrator.steps import document_quality                                # noqa: E402
from tools.compare_to_dataset import (compare, compare_documents,              # noqa: E402
                                      render, run_orchestrator, score)
from tools.dataset_to_applications import DEFAULT_DATASET, DEFAULT_OUT         # noqa: E402

# Cases whose pipeline genuinely ends at Step 3, so the dataset's final status is
# the status Step 3 produces. Every other case carries a status set by Steps 4-6
# (risk, screening, decision), which are not built yet.
TERMINAL_AT_STEP3 = {"WAL-ONB-0002": "resubmission_required"}


def _application(case_id: str) -> dict:
    path = next(DEFAULT_OUT.glob(f"*{case_id}.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def _run_one(application: dict):
    conn = db.connect(":memory:")
    trace = process_application(conn, application, KnowledgeBase())
    return conn, trace


def _rows():
    # Rebuild the applications first so the test never runs against stale JSON.
    subprocess.run([sys.executable, str(ROOT / "tools" / "dataset_to_applications.py")],
                   check=True, capture_output=True)
    return compare(run_orchestrator(DEFAULT_OUT), DEFAULT_DATASET)


def test_dataset_comparison_is_a_full_match():
    rows = _rows()
    ok, total = score(rows)
    assert total == 50, f"expected 50 field checks over 10 cases, got {total}"
    assert ok == total, (
        f"{ok}/{total} field checks match; mismatches:\n"
        + render([r for r in rows if r[4] == "NO"]))


def test_every_dataset_case_produced_a_checklist_except_the_blocked_ones():
    conn = run_orchestrator(DEFAULT_OUT)
    packs = dict(conn.execute(
        "SELECT case_id, COUNT(*) FROM requirement_pack GROUP BY case_id"))
    # All ten dataset cases classify cleanly, including the white-label partner,
    # which gets a KYB intake pack and then stops.
    assert len(packs) == 10, f"expected a requirement pack for all 10 cases, got {len(packs)}"


def test_white_label_case_stops_after_the_requirement_pack():
    conn = run_orchestrator(DEFAULT_OUT)
    row = conn.execute(
        "SELECT case_id FROM onboarding_case WHERE white_label_branch_flag = 1").fetchone()
    assert row, "no white-label case found"
    actions = [r["action"] for r in conn.execute(
        "SELECT action FROM audit_event WHERE case_id = ?", (row["case_id"],))]
    assert "routed_to_white_label_branch" in actions
    assert "white_label_kyb_intake_only" in actions
    assert conn.execute(
        "SELECT entity_scope FROM onboarding_case WHERE case_id = ?",
        (row["case_id"],)).fetchone()["entity_scope"] == "undetermined"


# ---------------------------------------------------------------------------
# Step 3 - document quality
# ---------------------------------------------------------------------------

def test_document_quality_matches_the_dataset_in_mock_mode():
    rows = compare_documents(run_orchestrator(DEFAULT_OUT), DEFAULT_DATASET)
    ok, total = score(rows)
    assert total == 117, f"expected 117 assessed documents, got {total}"
    assert ok == total, (
        f"{ok}/{total} documents match; mismatches:\n"
        + render([r for r in rows if r[4] == "NO"]))


def test_white_label_documents_are_not_assessed():
    # The white-label branch stops after the requirement pack, so its uploads
    # never reach Step 3. Asserted explicitly so the 117 above is not a silent gap.
    conn = run_orchestrator(DEFAULT_OUT)
    wl = conn.execute(
        "SELECT case_id FROM onboarding_case WHERE white_label_branch_flag = 1").fetchone()
    assert conn.execute("SELECT COUNT(*) FROM document WHERE case_id = ?",
                        (wl["case_id"],)).fetchone()[0] == 0


def test_case_status_after_step_3():
    conn = run_orchestrator(DEFAULT_OUT)
    ds = {r["case_id"]: r["status"] for r in
          __import__("csv").DictReader(
              open(DEFAULT_DATASET / "onboarding_case.csv", encoding="utf-8-sig"))}
    for case_id, status in conn.execute(
            "SELECT case_id, status FROM onboarding_case ORDER BY case_id"):
        if case_id in TERMINAL_AT_STEP3:
            assert status == TERMINAL_AT_STEP3[case_id] == ds[case_id], (
                f"{case_id} ends at Step 3 and should match the dataset: "
                f"dataset={ds[case_id]} ours={status}")
        else:
            # Not terminal here: Step 3 must leave it in a state a later step can
            # pick up, never in a final status it has not earned.
            assert status in ("verification_in_progress", "analyst_review_required",
                              "document_quality_review", "submitted"), \
                f"{case_id} left Step 3 in unexpected status {status}"


def test_case_2_never_reaches_extraction_or_a_paid_check():
    conn, trace = _run_one(_application("WAL-ONB-0002"))
    dq = trace["document_quality"]
    assert dq["status"] == "resubmission_required"
    assert dq["next_step"] is None, "a failed document must not open the extraction step"
    assert trace.get("waiting_for") != "extraction"

    case_id = trace["intake"]["case_id"]
    accepted = document_quality.accepted_documents(conn, case_id)
    names = {r["file_name"] for r in accepted}
    assert "director_id_halliwell_scan.jpg" not in names, \
        "the blurred director ID must never be offered to a downstream step"
    assert len(accepted) == 9 and dq["documents_checked"] == 10


def test_tampering_goes_to_manual_review_not_resubmission():
    app = copy.deepcopy(_application("WAL-ONB-0009"))
    app["documents"][0]["scripted_quality_flags"] = ["tampering_indicator"]
    conn, trace = _run_one(app)

    doc = conn.execute("SELECT * FROM document WHERE quality_flags = 'tampering_indicator'"
                       ).fetchone()
    assert doc["quality_status"] == "manual_review_required"
    assert doc["resubmission_required"] == 0, "suspected tampering is never sent back to the customer"
    assert trace["document_quality"]["status"] == "analyst_review_required"
    assert trace["document_quality"]["next_step"] is None

    item = conn.execute("SELECT status FROM checklist_item WHERE item_id = "
                        "(SELECT item_id FROM checklist_item_document WHERE document_id = ?)",
                        (doc["document_id"],)).fetchone()
    assert item["status"] == "manual_review"


def test_three_failed_attempts_stop_asking_the_customer():
    app = copy.deepcopy(_application("WAL-ONB-0009"))
    app["documents"][0]["scripted_quality_flags"] = ["blurred_unreadable"]
    conn, trace = _run_one(app)
    case_id, kb = trace["intake"]["case_id"], KnowledgeBase()

    def item_row():
        return conn.execute(
            "SELECT i.status, i.resubmission_attempts FROM checklist_item i "
            "JOIN requirement_pack p USING (pack_id) WHERE p.case_id = ? AND i.document_type = ?",
            (case_id, app["documents"][0]["document_type"])).fetchone()

    # Attempt 1 happened inside process_application.
    assert item_row()["resubmission_attempts"] == 1
    assert item_row()["status"] == "resubmission_requested"

    r2 = document_quality.run(conn, case_id, app, kb, checker=MockQualityChecker())
    assert item_row()["resubmission_attempts"] == 2
    assert item_row()["status"] == "resubmission_requested"
    assert r2.status == "resubmission_required"

    r3 = document_quality.run(conn, case_id, app, kb, checker=MockQualityChecker())
    assert item_row()["resubmission_attempts"] == 3
    assert item_row()["status"] == "manual_review", \
        "after three failures the item goes to an analyst, not back to the customer"
    assert r3.status == "analyst_review_required" and r3.next_step is None


def test_deterministic_rules_fire_without_the_checker():
    """No dataset document trips these, so they are exercised directly."""
    app = copy.deepcopy(_application("WAL-ONB-0009"))
    docs = {d["document_type"]: d for d in app["documents"]}

    docs["certificate_of_incorporation"]["file_name"] = "cert.docx"          # QR-01
    docs["id_document"]["expiry_date"] = "2020-01-01"                        # QR-02
    docs["proof_of_address"]["document_date"] = "2019-05-05"                 # QR-03
    conn, trace = _run_one(app)

    by_name = {r["file_name"]: r for r in conn.execute("SELECT * FROM document")}
    cert = by_name["cert.docx"]
    assert cert["quality_flags"] == "unsupported_file_type"
    assert cert["resubmission_reasons"] == "document_unreadable"

    expired = [r for r in by_name.values() if "expired" in (r["quality_flags"] or "")]
    assert expired, "an expiry date in the past must fail"
    assert {r["resubmission_reasons"] for r in expired} <= {"document_expired",
                                                            "proof_of_address_too_old"}
    assert all(r["quality_status"] == "resubmission_required" for r in expired)
    assert trace["document_quality"]["status"] == "resubmission_required"


def test_structural_missing_pages_outranks_the_generic_rule():
    """The same flag routes differently by document type: an incomplete ownership
    chart needs an analyst (QR-04), an incomplete anything-else needs a resend (QR-06)."""
    kb = KnowledgeBase()
    chart, _, chart_rules = document_quality._resolve({"missing_pages"}, "ownership_chart", kb)
    other, reasons, other_rules = document_quality._resolve(
        {"missing_pages"}, "certificate_of_incorporation", kb)
    assert chart == "manual_review_required" and chart_rules == ["QR-04"]
    assert other == "resubmission_required" and other_rules == ["QR-06"]
    assert reasons == ["document_unreadable"]


def test_a_deterministic_failure_skips_the_ai_call():
    """A document already known to be unusable must not cost a model call."""
    class ExplodingChecker(MockQualityChecker):
        def check(self, document):
            raise AssertionError(f"checker was called for {document['file_name']}")

    app = copy.deepcopy(_application("WAL-ONB-0009"))
    app["documents"] = [dict(app["documents"][0], file_name="scan.tiff")]
    conn = db.connect(":memory:")
    trace = process_application(conn, app, KnowledgeBase())   # mock: no AI flags scripted
    case_id = trace["intake"]["case_id"]
    result = document_quality.run(conn, case_id, app, KnowledgeBase(),
                                  checker=ExplodingChecker())
    assert result.resubmission == 1


def test_a_checker_may_not_invent_a_flag():
    try:
        QualityVerdict(flags=["definitely_dodgy"]).validate()
        raise AssertionError("an unrecognised flag should be rejected")
    except UnknownQualityFlag:
        pass


def test_every_checked_document_has_an_audit_row():
    conn = run_orchestrator(DEFAULT_OUT)
    docs = conn.execute("SELECT COUNT(*) FROM document").fetchone()[0]
    events = conn.execute(
        "SELECT COUNT(*) FROM audit_event WHERE action = 'document_quality_checked'"
    ).fetchone()[0]
    assert events == docs, f"{docs} documents but {events} audit rows"
    sample = conn.execute("SELECT payload_summary FROM audit_event "
                          "WHERE action = 'document_quality_checked' LIMIT 1").fetchone()
    assert "checker=mock" in sample["payload_summary"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
