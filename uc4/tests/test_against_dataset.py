"""
End-to-end check against the scripted 10-case dataset.

Replaces the old test_checklist_counts, which asserted item counts for the
sample requirement matrix that kb/requirement_rule.csv no longer carries.

Run: python -m pytest tests -q   (or: python tests/test_against_dataset.py)
"""
import collections
import copy
import csv
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
from orchestrator.extractor import (ExtractedValue, ExtractionResult,        # noqa: E402
                                    UnknownExtractedField)
from orchestrator.steps import analyst_review, document_quality, extraction  # noqa: E402
from tools.compare_to_dataset import (compare, compare_documents,              # noqa: E402
                                      compare_fields, render, run_orchestrator, score)
from tools.dataset_to_applications import DEFAULT_DATASET, DEFAULT_OUT         # noqa: E402

# Cases whose pipeline genuinely ends at Step 3, so the dataset's final status is
# the status Step 3 produces. Every other case carries a status set by Steps 4-6
# (risk, screening, decision), which are not built yet.
TERMINAL_AT_STEP3 = {"WAL-ONB-0002": "resubmission_required"}


def _csv(name: str) -> list[dict]:
    with open(DEFAULT_DATASET / name, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


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


def test_white_label_case_is_branched_and_scoped_undetermined():
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
    assert total == 123, f"expected 123 assessed documents, got {total}"
    assert ok == total, (
        f"{ok}/{total} documents match; mismatches:\n"
        + render([r for r in rows if r[4] == "NO"]))


def test_white_label_documents_are_assessed_then_the_case_stops():
    """KYB intake only: the uploads are screened, nothing after Step 3 runs."""
    conn = run_orchestrator(DEFAULT_OUT)
    wl = conn.execute(
        "SELECT case_id, status FROM onboarding_case WHERE white_label_branch_flag = 1").fetchone()
    docs = conn.execute("SELECT quality_status FROM document WHERE case_id = ?",
                        (wl["case_id"],)).fetchall()
    assert len(docs) == 6 and all(d["quality_status"] == "accepted_for_checks" for d in docs)
    assert wl["status"] == "submitted"
    actions = [r["action"] for r in conn.execute(
        "SELECT action FROM audit_event WHERE case_id = ?", (wl["case_id"],))]
    assert "white_label_kyb_intake_only" in actions


def test_case_status_after_step_3():
    conn = run_orchestrator(DEFAULT_OUT)
    ds = {r["case_id"]: r["status"] for r in _csv("onboarding_case.csv")}
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


# ---------------------------------------------------------------------------
# Analyst release of a held document
# ---------------------------------------------------------------------------

def _held_case():
    """Case 4 without its scripted release, so the document is still held."""
    app = copy.deepcopy(_application("WAL-ONB-0004"))
    app["analyst_releases"] = []
    return _run_one(app)


def test_case_4_is_held_until_an_analyst_releases_it():
    conn, trace = _held_case()
    assert trace["document_quality"]["status"] == "analyst_review_required"
    assert trace["document_quality"]["next_step"] is None
    held = conn.execute("SELECT * FROM document WHERE quality_status = 'manual_review_required'"
                        ).fetchone()
    assert held["file_name"] == "ownership_chart_vestmark.pdf"
    assert held["document_id"] not in {
        r["document_id"] for r in document_quality.accepted_documents(conn, held["case_id"])}


def test_release_accept_moves_the_case_on_and_keeps_the_screen_verdict():
    conn, trace = _held_case()
    case_id = trace["intake"]["case_id"]
    held = conn.execute("SELECT * FROM document WHERE quality_status = 'manual_review_required'"
                        ).fetchone()

    out = analyst_review.release_document(
        conn, held["document_id"], "analyst.m.sild", "accept",
        "missing annex lists dormant subsidiaries only; the chain is legible")

    assert out.document_status == "accepted_for_checks"
    assert out.case_status == "verification_in_progress"
    assert out.next_step == "extraction", "releasing the last held document should unblock the case"

    row = conn.execute("SELECT * FROM document WHERE document_id = ?",
                       (held["document_id"],)).fetchone()
    assert row["quality_status"] == "accepted_for_checks"
    assert row["quality_status_at_screen"] == "manual_review_required", \
        "the screening verdict must survive the override"
    assert row["quality_flags"] == "missing_pages" and row["released_by"] == "analyst.m.sild"

    item = conn.execute("SELECT status FROM checklist_item WHERE item_id = "
                        "(SELECT item_id FROM checklist_item_document WHERE document_id = ?)",
                        (held["document_id"],)).fetchone()
    assert item["status"] == "accepted"

    event = conn.execute("SELECT * FROM audit_event WHERE action = 'document_released_after_review'"
                         ).fetchone()
    assert event["actor_type"] == "analyst" and event["actor_id"] == "analyst.m.sild"
    assert "dormant subsidiaries" in event["payload_summary"]


def test_release_request_resubmission_sends_the_case_back_to_the_customer():
    conn, trace = _held_case()
    held = conn.execute("SELECT * FROM document WHERE quality_status = 'manual_review_required'"
                        ).fetchone()
    out = analyst_review.release_document(
        conn, held["document_id"], "analyst.m.sild", "request_resubmission",
        "the annex is needed in full to confirm the chain")
    assert out.document_status == "resubmission_required"
    assert out.case_status == "resubmission_required" and out.next_step is None
    assert conn.execute("SELECT next_action_owner FROM onboarding_case WHERE case_id = ?",
                        (held["case_id"],)).fetchone()[0] == "customer"


def test_a_release_needs_a_reason_a_decision_and_a_held_document():
    conn, trace = _held_case()
    held = conn.execute("SELECT document_id FROM document "
                        "WHERE quality_status = 'manual_review_required'").fetchone()
    accepted = conn.execute("SELECT document_id FROM document "
                            "WHERE quality_status = 'accepted_for_checks'").fetchone()

    for kwargs, why in (
            (dict(decision="accept", reason="  "), "blank reason"),
            (dict(decision="accept", reason=""), "no reason"),
            (dict(decision="looks_fine", reason="ok"), "unknown decision")):
        try:
            analyst_review.release_document(conn, held["document_id"], "analyst.x", **kwargs)
            raise AssertionError(f"{why} should be rejected")
        except ValueError:
            pass

    try:
        analyst_review.release_document(conn, accepted["document_id"], "analyst.x",
                                        "accept", "already fine")
        raise AssertionError("releasing a document that was never held should be rejected")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Step 4 - OCR and extraction
# ---------------------------------------------------------------------------

def test_extracted_fields_match_the_dataset_in_mock_mode():
    rows = compare_fields(run_orchestrator(DEFAULT_OUT), DEFAULT_DATASET)
    ok, total = score(rows)
    assert total == 161, f"expected 161 extracted fields, got {total}"
    assert ok == total, (
        f"{ok}/{total} fields match; mismatches:\n"
        + render([r for r in rows if r[4] == "NO"]))


def test_extraction_coverage_accounts_for_every_dataset_field():
    """161 of the dataset's 189 fields. The other 28 belong to the two cases that
    stop before Step 4 - so the gap is the routing, not a silent failure."""
    docs = {r["document_id"]: r for r in _csv("document.csv")}
    by_case = collections.Counter(
        docs[r["document_id"]]["case_id"] for r in _csv("extracted_field.csv"))
    assert sum(by_case.values()) == 189
    # case 2 waits on a resubmission, case 8 is white-label KYB intake only
    assert by_case["WAL-ONB-0002"] + by_case["WAL-ONB-0008"] == 28

    conn = run_orchestrator(DEFAULT_OUT)
    for case_id in ("WAL-ONB-0002", "WAL-ONB-0008"):
        n = conn.execute(
            "SELECT COUNT(*) FROM extracted_field f JOIN document d USING (document_id) "
            "WHERE d.case_id = ?", (case_id,)).fetchone()[0]
        assert n == 0, f"{case_id} stops before Step 4 and should have extracted nothing"


def test_extraction_never_reads_a_document_that_is_not_accepted():
    conn, trace = _run_one(_application("WAL-ONB-0002"))
    case_id = trace["intake"]["case_id"]
    blurred = conn.execute(
        "SELECT document_id FROM document WHERE file_name = 'director_id_halliwell_scan.jpg'"
    ).fetchone()

    # Force Step 4 to run even though the case routing stopped the case.
    extraction.run(conn, case_id, _application("WAL-ONB-0002"), KnowledgeBase())
    read = conn.execute(
        "SELECT COUNT(*) FROM extracted_field WHERE document_id = ?",
        (blurred["document_id"],)).fetchone()[0]
    assert read == 0, "a document that failed the quality screen must never be extracted"
    # the nine accepted ones were read
    assert conn.execute("SELECT COUNT(*) FROM extracted_field").fetchone()[0] > 0


def test_low_confidence_goes_to_analyst_correction_not_onward():
    app = copy.deepcopy(_application("WAL-ONB-0009"))
    app["field_corrections"] = []
    for d in app["documents"]:
        if d["document_type"] == "certificate_of_incorporation":
            d["scripted_fields"][0]["confidence"] = "0.42"
    conn, trace = _run_one(app)

    ex = trace["extraction"]
    assert ex["low_confidence"] == 1
    assert ex["status"] == "analyst_review_required" and ex["next_step"] is None, \
        "a value read at 0.42 must not be acted on without a human"

    row = conn.execute("SELECT * FROM extracted_field WHERE confidence = 0.42").fetchone()
    assert row["needs_analyst_correction"] == 1 and row["corrected_by_analyst"] == 0
    assert row["value"] is not None, "the low-confidence reading is kept, not blanked or guessed"


def test_a_missing_required_field_goes_to_an_analyst_and_is_not_guessed():
    app = copy.deepcopy(_application("WAL-ONB-0009"))
    app["field_corrections"] = []
    for d in app["documents"]:
        if d["document_type"] == "authorised_signatory_list":
            d["scripted_fields"] = []          # KB requires signatory_name
    conn, trace = _run_one(app)

    assert trace["extraction"]["missing_required"] == 1
    assert trace["extraction"]["status"] == "analyst_review_required"
    row = conn.execute("SELECT * FROM extracted_field WHERE name = 'signatory_name'").fetchone()
    assert row["value"] is None, "a field that could not be read must stay empty, never invented"
    assert row["needs_analyst_correction"] == 1


def test_a_date_read_differently_by_ocr_sends_the_document_to_manual_review():
    app = copy.deepcopy(_application("WAL-ONB-0009"))
    app["field_corrections"] = []
    for d in app["documents"]:
        if d["document_type"] == "id_document":
            # screen read a valid expiry; OCR reads one that has long passed
            d["scripted_fields"] = [{"name": "full_name", "value": "Katrin Ilves",
                                     "confidence": "0.95", "source_page": "1"},
                                    {"name": "expiry_date", "value": "2019-01-01",
                                     "confidence": "0.95", "source_page": "1"}]
    conn, trace = _run_one(app)

    assert trace["extraction"]["date_conflicts"] >= 1
    assert trace["extraction"]["status"] == "analyst_review_required"
    held = conn.execute("SELECT * FROM document WHERE document_type = 'id_document' "
                        "AND quality_status = 'manual_review_required'").fetchone()
    assert held is not None, "the two readings disagree, so neither is trusted automatically"


def test_case_3_corrected_address_survives_for_the_registry_check():
    """The analyst's value, not the 0.58 OCR reading, is what Step 5 will compare."""
    conn = run_orchestrator(DEFAULT_OUT)
    row = conn.execute(
        "SELECT f.* FROM extracted_field f JOIN document d USING (document_id) "
        "WHERE d.file_name = 'registry_extract_calderwick.pdf' AND f.name = 'registered_address'"
    ).fetchone()

    ds_value = next(r["value"] for r in _csv("extracted_field.csv")
                    if r["field_id"] == "FLD-0040")
    assert row["value"] == ds_value == "Unit 7 Calderwick Way, Leeds LS12 4QT, United Kingdom"
    assert row["corrected_by_analyst"] == 1
    assert row["needs_analyst_correction"] == 0, "a corrected field no longer blocks the case"
    assert float(row["confidence"]) == 0.58, \
        "the original low confidence is kept on the record; the value is what changed"

    # The case moved on, and this is the address the registry mismatch is found on.
    case_id = conn.execute(
        "SELECT case_id FROM document WHERE file_name = 'registry_extract_calderwick.pdf'"
    ).fetchone()["case_id"]
    assert conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone()["status"] == "verification_in_progress"
    reg = next(r for r in _csv("registry_check.csv") if r["case_id"] == "WAL-ONB-0003")
    assert reg["address_match"] == "mismatch"

    event = conn.execute("SELECT * FROM audit_event WHERE action = 'extracted_field_corrected'"
                         ).fetchone()
    assert event["actor_type"] == "analyst" and event["actor_id"] == "analyst.r.toome"


def test_an_extractor_may_not_invent_a_field():
    kb = KnowledgeBase()
    allowed = [f["field_name"] for f in kb.fields_for("registry_extract")]
    try:
        ExtractionResult(fields=[ExtractedValue("favourite_colour", "blue", 0.99)]).validate(allowed)
        raise AssertionError("an unlisted field name should be rejected")
    except UnknownExtractedField:
        pass


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
