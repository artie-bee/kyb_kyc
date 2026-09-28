"""
End-to-end check against the scripted 10-case dataset.

Replaces the old test_checklist_counts, which asserted item counts for the
sample requirement matrix that kb/requirement_rule.csv no longer carries.

Run: python -m pytest tests -q   (or: python tests/test_against_dataset.py)
"""
import collections
import copy
import csv
import sqlite3
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
from orchestrator import holds, providers                                     # noqa: E402
from orchestrator.media_relevance import (MediaAssessment,                    # noqa: E402
                                          UnknownMediaCategory)
from orchestrator.narrator import (Narrative,                                  # noqa: E402
                                   UnknownEvidenceReference)
from orchestrator.steps import (analyst_review, document_quality,              # noqa: E402
                                evidence_pack, extraction, risk_assessment,
                                verification)
from tools.compare_to_dataset import (compare, compare_documents,              # noqa: E402
                                      compare_fields, compare_risk,
                                      compare_screening, compare_verification,
                                      render, run_orchestrator, score)
from tools.dataset_to_applications import DEFAULT_DATASET, DEFAULT_OUT         # noqa: E402

# Cases whose pipeline genuinely ends at Step 3, so the dataset's final status is
# the status Step 3 produces. Every other case carries a status set by Steps 4-6
# (risk, screening, decision), which are not built yet.
TERMINAL_AT_STEP3 = {"WAL-ONB-0002": "resubmission_required"}
# Case 14 also ends with the customer, but its dataset status (closed_withdrawn)
# is set when the chase expires at Step 8, which is not built yet.


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


def _scripted_providers(app: dict, case_id: str, registry_edit=None, identity_edit=None):
    """Mock providers keyed to THIS run's case id.

    A single-application run mints WAL-ONB-0001 whatever the dataset called the
    case, so the scripted rows have to be re-keyed or every lookup misses and
    every provider looks unavailable.
    """
    registry = {case_id: dict(r) for r in app["scripted_registry"]}
    identity = {(case_id, r["individual_id"]): dict(r) for r in app["scripted_identity"]}
    if registry_edit:
        registry_edit(registry)
    if identity_edit:
        identity_edit(identity)
    return (providers.MockRegistryProvider(registry),
            providers.MockIdentityProvider(identity))


def _rerun_verification(app: dict, registry_edit=None, identity_edit=None,
                        registry_provider=None):
    """Run a case to completion, then redo Step 5 with edited provider answers."""
    conn = db.connect(":memory:")
    trace = process_application(conn, app, KnowledgeBase())
    case_id = trace["intake"]["case_id"]
    for table in ("registry_check", "identity_check", "finding", "case_hold"):
        conn.execute(f"DELETE FROM {table}")
    reg, ident = _scripted_providers(app, case_id, registry_edit, identity_edit)
    return conn, verification.run(conn, case_id, app, KnowledgeBase(),
                                  registry_provider=registry_provider or reg,
                                  identity_provider=ident)


def test_dataset_comparison_is_a_full_match():
    rows = _rows()
    ok, total = score(rows)
    assert total == 70, f"expected 70 field checks over 14 cases, got {total}"
    assert ok == total, (
        f"{ok}/{total} field checks match; mismatches:\n"
        + render([r for r in rows if r[4] == "NO"]))


def test_every_dataset_case_produced_a_checklist_except_the_blocked_ones():
    conn = run_orchestrator(DEFAULT_OUT)
    packs = dict(conn.execute(
        "SELECT case_id, COUNT(*) FROM requirement_pack GROUP BY case_id"))
    # All ten dataset cases classify cleanly, including the white-label partner,
    # which gets a KYB intake pack and then stops.
    assert len(packs) == 14, f"expected a requirement pack for all 14 cases, got {len(packs)}"


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
    assert total == 168, f"expected 168 assessed documents, got {total}"
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
                              "document_quality_review", "resubmission_required",
                              "ready_for_decision", "enhanced_due_diligence",
                              "submitted"), \
                f"{case_id} left the pipeline in unexpected status {status}"


def test_case_2_never_reaches_a_paid_provider_check():
    conn, trace = _run_one(_application("WAL-ONB-0002"))
    dq = trace["document_quality"]
    assert dq["status"] == "resubmission_required"
    # Extraction is free and runs; verification is the paid boundary and does not.
    assert trace["extraction"]["next_step"] is None
    assert trace.get("waiting_for") != "verification"
    assert "verification" not in trace

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
    held = holds.open_holds(conn, trace["intake"]["case_id"])
    assert all(h.owner == "analyst" for h in held), "the case is held for an analyst"
    assert any(h.placed_by_step == "step.document_quality" and h.code == "manual_review"
               for h in held)

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
    assert r3.status == "analyst_review_required"
    assert [h.code for h in holds.open_holds(conn, case_id)] == ["manual_review"]


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
    app["field_corrections"] = []
    app["field_acceptances"] = []
    return _run_one(app)


def test_case_4_is_held_until_an_analyst_releases_it():
    conn, trace = _held_case()
    assert trace["document_quality"]["status"] == "analyst_review_required"
    assert any(h.placed_by_step == "step.document_quality"
               for h in holds.open_holds(conn, trace["intake"]["case_id"]))
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
    # The quality hold is gone. The case is still with an analyst because
    # extraction independently held it over two low-confidence fields, which is
    # the point of central holds: one step does not speak for another.
    assert not [h for h in holds.open_holds(conn, case_id)
                if h.placed_by_step == "step.document_quality"]
    assert out.case_status == "analyst_review_required"

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
        "the annex is needed in full to confirm the chain", reason_code="document_incomplete")
    assert out.document_status == "resubmission_required"
    quality = [h for h in holds.open_holds(conn, held["case_id"])
               if h.placed_by_step == "step.document_quality"]
    assert [(h.owner, h.code) for h in quality] == [("customer", "resubmission")]
    # The customer owes a document, but an analyst still owns the case overall
    # because extraction is holding it too. Precedence decides, not the last
    # step to speak.
    assert conn.execute("SELECT next_action_owner FROM onboarding_case WHERE case_id = ?",
                        (held["case_id"],)).fetchone()[0] == "analyst"


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
    assert total == 256, f"expected 256 extracted fields, got {total}"
    assert ok == total, (
        f"{ok}/{total} fields match; mismatches:\n"
        + render([r for r in rows if r[4] == "NO"]))


def test_extraction_coverage_accounts_for_every_dataset_field():
    """Every extracted_field row in the dataset has a counterpart here - no gap.

    Case 2 is extracted while it waits for a resubmission (only the paid checks
    wait); case 8 stops before Step 4 and the dataset gives it no fields.
    """
    docs = {r["document_id"]: r for r in _csv("document.csv")}
    ds = collections.Counter(
        docs[r["document_id"]]["case_id"] for r in _csv("extracted_field.csv"))
    assert ds["WAL-ONB-0008"] == 0, "white-label stops before Step 4 and extracts nothing"

    conn = run_orchestrator(DEFAULT_OUT)
    ours = collections.Counter(
        r["case_id"] for r in conn.execute(
            "SELECT d.case_id FROM extracted_field f JOIN document d USING (document_id)"))
    assert ours == ds, f"per-case field counts differ: dataset={dict(ds)} ours={dict(ours)}"

    rows = compare_fields(conn, DEFAULT_DATASET)
    assert score(rows)[1] == sum(ds.values()) == 256


def test_case_2_is_extracted_while_it_waits_for_a_resubmission():
    conn, trace = _run_one(_application("WAL-ONB-0002"))
    case_id = trace["intake"]["case_id"]
    assert trace["extraction"]["documents_read"] == 9, "the nine accepted documents are read"
    assert trace["extraction"]["next_step"] is None, "but verification stays shut"
    case = conn.execute("SELECT status, next_action_owner FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone()
    assert case["status"] == "resubmission_required" and case["next_action_owner"] == "customer",         "extraction must not take the case away from the customer"


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

    docs = {r["document_id"]: r for r in _csv("document.csv")}
    ds_value = next(r["value"] for r in _csv("extracted_field.csv")
                    if docs[r["document_id"]]["file_name"] == "registry_extract_calderwick.pdf"
                    and r["name"] == "registered_address")
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
                        (case_id,)).fetchone()["status"] == "ready_for_decision"
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


# ---------------------------------------------------------------------------
# Step 5 - verification
# ---------------------------------------------------------------------------

def test_verification_matches_the_dataset_in_mock_mode():
    rows = compare_verification(run_orchestrator(DEFAULT_OUT), DEFAULT_DATASET)
    ok, total = score(rows)
    assert total == 50, f"expected 11 registry + 28 identity + 11 UBO checks, got {total}"
    assert ok == total, (
        f"{ok}/{total} verification checks match; mismatches:\n"
        + render([r for r in rows if r[4] == "NO"]))


def test_step_5_refuses_to_run_while_a_required_item_is_not_accepted():
    """The paid-check boundary. Case 2 owes a document, so nothing external runs."""
    conn, trace = _run_one(_application("WAL-ONB-0002"))
    case_id = trace["intake"]["case_id"]
    assert "verification" not in trace

    try:
        verification.run(conn, case_id, _application("WAL-ONB-0002"), KnowledgeBase())
        raise AssertionError("verification should refuse to run on an incomplete checklist")
    except verification.VerificationGateError as e:
        assert "id_document" in str(e)

    for table in ("registry_check", "identity_check"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, \
            f"{table} must be empty: the gate refused before any provider was called"


def test_case_3_address_mismatch_is_found_using_the_corrected_address():
    conn = run_orchestrator(DEFAULT_OUT)
    case_id = conn.execute(
        "SELECT case_id FROM document WHERE file_name = 'registry_extract_calderwick.pdf'"
    ).fetchone()["case_id"]

    corrected = conn.execute(
        "SELECT f.value FROM extracted_field f JOIN document d USING (document_id) "
        "WHERE d.case_id = ? AND f.name = 'registered_address' AND f.corrected_by_analyst = 1",
        (case_id,)).fetchone()["value"]
    reg = conn.execute("SELECT * FROM registry_check WHERE case_id = ?", (case_id,)).fetchone()

    assert reg["address_match"] == "mismatch"
    assert reg["registry_address"] != corrected, "the mismatch is between these two values"
    # and it is the corrected value, not the 0.58 reading, that was compared
    assert corrected == "Unit 7 Calderwick Way, Leeds LS12 4QT, United Kingdom"
    assert verification._match(reg["registry_address"], corrected) == "mismatch"

    finding = conn.execute(
        "SELECT * FROM finding WHERE case_id = ? AND rule_id = 'RG-07'", (case_id,)).fetchone()
    assert finding is not None and finding["blocking"] == 0, "an address mismatch is not blocking"
    assert reg["check_id"] in finding["evidence_refs"]
    # non-blocking, so the case ran through to a decision-ready state
    assert not holds.open_holds(conn, case_id)
    assert conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone()["status"] == "ready_for_decision"


def test_case_4_effective_ubo_ownership_is_31_5_percent():
    assert verification.effective_ownership([70, 45]) == 31.5
    assert verification.effective_ownership([100, 70]) == 70.0
    assert verification.effective_ownership([60]) == 60.0

    conn = run_orchestrator(DEFAULT_OUT)
    row = conn.execute(
        "SELECT u.*, i.full_name FROM ubo u JOIN individual i USING (individual_id) "
        "WHERE i.full_name = 'Ruben Halvorsen'").fetchone()
    assert float(row["ownership_percentage"]) == 31.5
    assert row["verification_status"] == "unverified", \
        "the register does not support this indirect chain"

    event = conn.execute(
        "SELECT payload_summary FROM audit_event WHERE action = 'ubo_verified' "
        "AND payload_summary LIKE '%Ruben Halvorsen%'").fetchone()
    assert "[70.0, 45.0] -> 31.5% effective" in event["payload_summary"]

    # the direct 24.5% holder on the same case is unaffected by the chain finding
    other = conn.execute(
        "SELECT u.verification_status FROM ubo u JOIN individual i USING (individual_id) "
        "WHERE i.full_name = 'Anneli Sormus'").fetchone()
    assert other["verification_status"] == "verified"


def test_an_unavailable_provider_is_never_a_pass():
    class SilentRegistry(providers.MockRegistryProvider):
        mode, name = "mock", "MockRegistryHub"
        calls = 0

        def lookup(self, applicant, case):
            SilentRegistry.calls += 1
            return providers.RegistryResponse(available=False, provider_name=self.name)

    app = _application("WAL-ONB-0009")
    conn, result = _rerun_verification(app, registry_provider=SilentRegistry())

    assert result.registry_result == "unavailable" != "pass"
    assert result.status == "analyst_review_required"
    assert result.next_step == "screening", "a blocked case is still screened"
    assert any("RG-09" in b for b in result.blocking)
    assert SilentRegistry.calls == verification.MAX_REGISTRY_ATTEMPTS,         "RG-09 retries once, then stops rather than hammering the provider"

    row = conn.execute("SELECT * FROM registry_check").fetchone()
    assert row["result"] == "unavailable" and row["attempts"] == 2


def test_blocking_registry_outcomes_stop_the_case():
    """No dataset case is dissolved or name-mismatched, so these are driven directly."""
    app = _application("WAL-ONB-0009")

    def dissolve(registry):
        for row in registry.values():
            row["company_status"] = "dissolved"

    _, result = _rerun_verification(app, registry_edit=dissolve)
    assert result.status == "analyst_review_required"
    assert result.registry_result == "fail", "a dissolved company fails, it does not merely review"
    assert any("RG-01" in b for b in result.blocking)

    def rename(registry):
        for row in registry.values():
            row["registry_legal_name"] = "Some Other Company OU"

    _, mismatched = _rerun_verification(app, registry_edit=rename)
    assert any("RG-05" in b for b in mismatched.blocking), "a name mismatch is blocking"


def test_identity_failures_route_correctly():
    """Every dataset identity row is a clean pass, so each branch is driven here."""
    app = _application("WAL-ONB-0009")

    def first(key, value):
        def edit(identity):
            identity[sorted(identity)[0]][key] = value
        return edit

    _, failed = _rerun_verification(app, identity_edit=first("result", "fail"))
    assert failed.status == "analyst_review_required"

    _, review = _rerun_verification(app, identity_edit=first("result", "review"))
    assert review.status == "analyst_review_required", "'review' is an analyst question too"

    _, dup = _rerun_verification(
        app, identity_edit=first("duplicate_individual_detected", "true"))
    assert dup.status == "analyst_review_required"
    assert any("ID-DUPLICATE" in b for b in dup.blocking)

    conn, expired = _rerun_verification(app, identity_edit=first("document_expired", "true"))
    assert expired.status == "verification_in_progress",         "an expired ID is the customer's to replace, not an analyst's to judge"
    assert not expired.blocking
    assert conn.execute(
        "SELECT COUNT(*) FROM checklist_item WHERE document_type = 'id_document' "
        "AND status = 'resubmission_requested'").fetchone()[0] == 1


def test_non_blocking_findings_still_let_screening_run():
    conn = run_orchestrator(DEFAULT_OUT)
    for case_id in ("WAL-ONB-0003", "WAL-ONB-0004"):
        found = conn.execute("SELECT COUNT(*) FROM finding WHERE case_id = ? AND blocking = 0",
                             (case_id,)).fetchone()[0]
        assert found, f"{case_id} should carry non-blocking findings forward"
        assert not holds.open_holds(conn, case_id), "a finding is not a hold"
        # every subject was still checked, findings or not
        subjects = conn.execute(
            "SELECT COUNT(*) FROM individual WHERE applicant_id = "
            "(SELECT applicant_id FROM onboarding_case WHERE case_id = ?)", (case_id,)).fetchone()[0]
        checks = conn.execute("SELECT COUNT(*) FROM identity_check WHERE case_id = ?",
                              (case_id,)).fetchone()[0]
        assert checks == subjects


def test_every_provider_call_is_audited_with_name_and_mode():
    conn = run_orchestrator(DEFAULT_OUT)
    reg = conn.execute("SELECT COUNT(*) FROM registry_check").fetchone()[0]
    idc = conn.execute("SELECT COUNT(*) FROM identity_check").fetchone()[0]
    events = conn.execute(
        "SELECT payload_summary FROM audit_event WHERE action IN "
        "('registry_check_completed', 'identity_check_completed')").fetchall()
    assert len(events) == reg + idc
    assert all("mode=mock" in e["payload_summary"] for e in events)
    assert all("rules=" in e["payload_summary"] for e in events)


# ---------------------------------------------------------------------------
# Step 6 - screening
# ---------------------------------------------------------------------------

def test_screening_matches_the_dataset_in_mock_mode():
    rows = compare_screening(run_orchestrator(DEFAULT_OUT), DEFAULT_DATASET)
    ok, total = score(rows)
    assert total == 39, f"expected 39 screening rows, got {total}"
    assert ok == total, (
        f"{ok}/{total} screening checks match; mismatches:\n"
        + render([r for r in rows if r[4] == "NO"]))


def _case(dataset_case_id):
    """Cases run in dataset order, so the nth minted id matches the nth case."""
    return dataset_case_id


def test_screening_runs_on_every_director_ubo_and_signatory():
    """Missing a subject is the failure that matters here, so count them all."""
    conn = run_orchestrator(DEFAULT_OUT)
    screened = {r["case_id"] for r in conn.execute(
        "SELECT DISTINCT case_id FROM screening_check")}
    assert screened, "no case was screened at all"
    for case_id in sorted(screened):
        people = conn.execute(
            "SELECT COUNT(*) FROM individual WHERE applicant_id = "
            "(SELECT applicant_id FROM onboarding_case WHERE case_id = ?) "
            "AND role IN ('director','ubo','authorised_signatory','sole_trader')",
            (case_id,)).fetchone()[0]
        rows = conn.execute("SELECT subject_type, individual_id FROM screening_check "
                            "WHERE case_id = ?", (case_id,)).fetchall()
        entity = [r for r in rows if r["subject_type"] == "applicant"]
        individuals = [r for r in rows if r["subject_type"] == "individual"]
        assert len(entity) == 1, f"{case_id} must screen the entity exactly once"
        assert len(individuals) == people, (
            f"{case_id} has {people} screenable people but {len(individuals)} were screened")
        assert len({r["individual_id"] for r in individuals}) == people, \
            f"{case_id} screened a subject twice"


def test_case_5_pep_on_ubo_goes_to_edd_and_needs_a_human():
    conn = run_orchestrator(DEFAULT_OUT)
    case_id = _case("WAL-ONB-0005")
    row = conn.execute(
        "SELECT s.* FROM screening_check s JOIN individual i USING (individual_id) "
        "WHERE s.case_id = ? AND i.full_name = 'Aurelio Vantano'", (case_id,)).fetchone()
    assert row["pep_result"] == "pep_match"

    finding = conn.execute(
        "SELECT * FROM finding WHERE case_id = ? AND rule_id = 'SC-03'", (case_id,)).fetchone()
    assert finding is not None, "a PEP match must be recorded as a finding"
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    assert case["requires_human_signoff"] == 1
    assert case["restricted_finding"] == 1
    # PEP alone does not hold the case: it continues with the finding attached
    # and lands on the enhanced due diligence path at Step 7.
    assert not holds.open_holds(conn, case_id)
    assert case["status"] == "enhanced_due_diligence"


def test_case_6_possible_match_holds_the_case_and_is_never_downgraded():
    conn = run_orchestrator(DEFAULT_OUT)
    case_id = _case("WAL-ONB-0006")
    row = conn.execute("SELECT * FROM screening_check WHERE case_id = ? "
                       "AND sanctions_result = 'possible_match'", (case_id,)).fetchone()
    assert row is not None

    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    assert case["status"] == "analyst_review_required"
    assert case["next_action_owner"] == "analyst"
    assert case["restricted_finding"] == 1
    assert case["requires_human_signoff"] == 1

    # No code path clears it, and the database refuses to let one try.
    assert conn.execute(
        "SELECT COUNT(*) FROM screening_check WHERE case_id = ? AND sanctions_result = 'no_match'",
        (case_id,)).fetchone()[0] == 3, "only the three clean subjects are no_match"
    try:
        conn.execute("UPDATE screening_check SET sanctions_result = 'no_match' "
                     "WHERE check_id = ?", (row["check_id"],))
        raise AssertionError("a sanctions match must not be updatable to no_match")
    except sqlite3.IntegrityError:
        pass


def test_case_7_serious_adverse_media_escalates_to_compliance():
    conn = run_orchestrator(DEFAULT_OUT)
    case_id = _case("WAL-ONB-0007")
    row = conn.execute("SELECT * FROM screening_check WHERE case_id = ? "
                       "AND adverse_media_result = 'serious'", (case_id,)).fetchone()
    assert row is not None
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    assert case["status"] == "analyst_review_required"
    assert case["next_action_owner"] == "compliance"
    assert case["restricted_finding"] == 1 and case["requires_human_signoff"] == 1
    finding = conn.execute("SELECT * FROM finding WHERE case_id = ? AND rule_id = 'AM-serious'",
                           (case_id,)).fetchone()
    assert finding["blocking"] == 1
    assert row["evidence_refs"], "a media finding must carry its provider references"


def test_case_12_clear_sanctions_match_goes_to_compliance():
    conn = run_orchestrator(DEFAULT_OUT)
    case_id = _case("WAL-ONB-0012")
    row = conn.execute("SELECT * FROM screening_check WHERE case_id = ? "
                       "AND sanctions_result = 'clear_match'", (case_id,)).fetchone()
    assert row is not None and row["severity"] == "critical"
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    assert case["status"] == "analyst_review_required"
    assert case["next_action_owner"] == "compliance"
    assert case["requires_human_signoff"] == 1 and case["restricted_finding"] == 1
    finding = conn.execute("SELECT * FROM finding WHERE case_id = ? AND rule_id = 'SC-02'",
                           (case_id,)).fetchone()
    assert finding is not None and finding["blocking"] == 1


def test_case_13_unavailable_media_is_never_a_pass():
    conn = run_orchestrator(DEFAULT_OUT)
    case_id = _case("WAL-ONB-0013")
    row = conn.execute("SELECT * FROM screening_check WHERE case_id = ? "
                       "AND adverse_media_result = 'unavailable'", (case_id,)).fetchone()
    assert row is not None and row["attempts"] >= 1
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    assert case["status"] == "analyst_review_required", "silence must not clear the case"
    finding = conn.execute("SELECT * FROM finding WHERE case_id = ? AND rule_id = 'AM-unavailable'",
                           (case_id,)).fetchone()
    assert finding is not None
    # An unanswered check is a gap in the evidence, not a finding about a person.
    assert case["restricted_finding"] == 0


def test_screening_does_not_release_a_case_verification_held():
    """Case 11 is dissolved on the register and screens clean; it stays held."""
    conn = run_orchestrator(DEFAULT_OUT)
    case_id = _case("WAL-ONB-0011")
    assert conn.execute("SELECT COUNT(*) FROM screening_check WHERE case_id = ?",
                        (case_id,)).fetchone()[0] == 4
    assert not conn.execute("SELECT 1 FROM finding WHERE case_id = ? AND source = 'screening'",
                            (case_id,)).fetchone()
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    assert case["status"] == "analyst_review_required"
    assert case["restricted_finding"] == 0, "nothing adverse was found about anyone here"


def test_screening_findings_stay_internal():
    """Nothing a customer could be shown carries a screening term."""
    conn = run_orchestrator(DEFAULT_OUT)
    restricted = ("sanction", "pep ", "politically exposed", "adverse media", "watchlist",
                  "screening")
    for table, column in (("checklist_item", "note"), ("document", "resubmission_reasons"),
                          ("document", "release_reason")):
        for row in conn.execute(f"SELECT {column} AS v FROM {table} WHERE {column} IS NOT NULL"):
            text = (row["v"] or "").lower()
            assert not any(word in text for word in restricted), \
                f"{table}.{column} carries screening wording: {row['v']!r}"


def test_a_media_assessor_may_not_invent_a_category():
    try:
        MediaAssessment(category="extremely_bad").validate()
        raise AssertionError("an unknown category should be rejected")
    except UnknownMediaCategory:
        pass


# ---------------------------------------------------------------------------
# Central holds
# ---------------------------------------------------------------------------

def test_a_step_cannot_release_another_steps_hold():
    """The rule the whole mechanism exists for."""
    conn = db.connect(":memory:")
    trace = process_application(conn, _application("WAL-ONB-0011"), KnowledgeBase())
    case_id = trace["intake"]["case_id"]

    hold = next(h for h in holds.open_holds(conn, case_id)
                if h.placed_by_step == "step.verification")
    for impostor in ("step.screening", "step.document_quality", "step.extraction"):
        try:
            holds.release(conn, hold.hold_id, impostor, "looks fine to me", by_step=impostor)
            raise AssertionError(f"{impostor} must not be able to release a verification hold")
        except holds.HoldReleaseError:
            pass
    # nor by pretending to be a person
    try:
        holds.release(conn, hold.hold_id, "step.screening", "looks fine to me")
        raise AssertionError("a step must not release a hold as if it were a human")
    except holds.HoldReleaseError:
        pass

    assert holds.open_holds(conn, case_id), "the hold is still open"
    # the step that placed it may lift it, and so may a named human
    holds.release(conn, hold.hold_id, "step.verification", "register re-checked",
                  by_step="step.verification")
    assert not [h for h in holds.open_holds(conn, case_id) if h.hold_id == hold.hold_id]


def test_a_human_may_release_any_hold_with_a_reason():
    conn = db.connect(":memory:")
    trace = process_application(conn, _application("WAL-ONB-0011"), KnowledgeBase())
    case_id = trace["intake"]["case_id"]
    hold = holds.open_holds(conn, case_id)[0]

    try:
        holds.release(conn, hold.hold_id, "analyst.j.okoro", "   ")
        raise AssertionError("a release needs a reason")
    except ValueError:
        pass

    holds.release(conn, hold.hold_id, "analyst.j.okoro", "company restored to the register")
    event = conn.execute("SELECT * FROM audit_event WHERE action = 'case_hold_released'").fetchone()
    assert event["actor_type"] == "analyst" and "analyst.j.okoro" in event["payload_summary"]


def test_case_status_comes_from_the_worst_open_hold():
    conn = db.connect(":memory:")
    trace = process_application(conn, _application("WAL-ONB-0009"), KnowledgeBase())
    case_id = trace["intake"]["case_id"]
    for h in holds.open_holds(conn, case_id):
        holds.release(conn, h.hold_id, "analyst.test", "clearing for the test")

    holds.place(conn, case_id, "step.document_quality", "resubmission", "a document", "customer")
    assert holds.apply_status(conn, case_id) == ("resubmission_required", "customer")
    holds.place(conn, case_id, "step.screening", "sanctions_escalation", "a match", "compliance")
    assert holds.apply_status(conn, case_id) == ("analyst_review_required", "compliance"),         "compliance outranks the customer hold"


def test_every_hold_is_audited():
    conn = run_orchestrator(DEFAULT_OUT)
    placed = conn.execute("SELECT COUNT(*) FROM case_hold").fetchone()[0]
    released = conn.execute(
        "SELECT COUNT(*) FROM case_hold WHERE released_at IS NOT NULL").fetchone()[0]
    events = {a: n for a, n in conn.execute(
        "SELECT action, COUNT(*) FROM audit_event WHERE action LIKE 'case_hold_%' "
        "GROUP BY action")}
    assert events.get("case_hold_placed") == placed
    assert events.get("case_hold_released", 0) == released


# ---------------------------------------------------------------------------
# Step 7 - risk assessment and evidence pack
# ---------------------------------------------------------------------------

def test_risk_and_evidence_pack_match_the_dataset():
    rows = compare_risk(run_orchestrator(DEFAULT_OUT), DEFAULT_DATASET)
    ok, total = score(rows)
    assert total == 33, f"expected 33 risk checks, got {total}"
    assert ok == total, (
        f"{ok}/{total} risk checks match; mismatches:\n"
        + render([r for r in rows if r[4] == "NO"]))


def test_risk_coverage_accounts_for_every_dataset_assessment():
    """Cases 2 and 14 never reach Step 7: the paid gate is shut, so there is
    nothing to assess. Pinned down so the gap cannot widen unnoticed."""
    ds = {r["case_id"] for r in _csv("risk_assessment.csv")}
    conn = run_orchestrator(DEFAULT_OUT)
    ours = {r["case_id"] for r in conn.execute("SELECT case_id FROM risk_assessment")}
    assert ds - ours == {"WAL-ONB-0002", "WAL-ONB-0014"}
    assert not ours - ds, "assessed a case the dataset does not have"
    for case_id in ("WAL-ONB-0002", "WAL-ONB-0014"):
        assert holds.open_holds(conn, case_id), f"{case_id} must still be held"


def test_every_weight_is_marked_as_a_placeholder():
    """The brief does not state weights. None of these may look agreed."""
    kb = KnowledgeBase()
    assert kb.risk_factors, "no risk factors loaded"
    for row in kb.risk_factors:
        assert row["weight_status"] == "poc_placeholder - Wallester to confirm", \
            f"{row['factor_id']} does not say its weight is a placeholder"


def test_hard_floors_override_the_score():
    conn = run_orchestrator(DEFAULT_OUT)

    def band(case_id):
        return conn.execute("SELECT * FROM risk_assessment WHERE case_id = ?",
                            (case_id,)).fetchone()

    # case 5: PEP -> at least high
    five = band("WAL-ONB-0005")
    assert five["risk_band"] == "high" and five["requires_human_signoff"] == 1

    # case 6: possible sanctions match -> critical, whatever the score
    six = band("WAL-ONB-0006")
    assert six["risk_band"] == "critical"
    assert six["risk_score"] < 80, "the floor is what makes this critical, not the score"
    assert six["recommended_action"] == "escalate"

    # case 7: serious adverse media -> at least high
    seven = band("WAL-ONB-0007")
    assert seven["risk_band"] == "high" and seven["risk_score"] < 80

    # case 12: confirmed sanctions match -> critical
    twelve = band("WAL-ONB-0012")
    assert twelve["risk_band"] == "critical" and twelve["risk_score"] < 80
    assert twelve["requires_human_signoff"] == 1

    # and a floor never lowers a band
    kb = KnowledgeBase()
    assert risk_assessment.apply_floors("critical", {"pep_match": True}, kb) == "critical"


def test_case_13_is_insufficient_evidence_and_never_recommends_approve():
    conn = run_orchestrator(DEFAULT_OUT)
    row = conn.execute("SELECT * FROM risk_assessment WHERE case_id = 'WAL-ONB-0013'").fetchone()
    assert row["risk_band"] == "insufficient_evidence"
    assert row["risk_score"] is None, "a case with gaps is not scored"
    assert row["insufficient_evidence_flag"] == 1
    assert row["recommended_action"] != "approve"
    assert row["recommended_action"] == "insufficient_evidence"
    assert row["requires_human_signoff"] == 1

    # no assessment anywhere recommends approval on insufficient evidence
    for r in conn.execute("SELECT * FROM risk_assessment "
                          "WHERE risk_band = 'insufficient_evidence'"):
        assert r["recommended_action"] != "approve"


def test_case_10_scores_lower_than_case_4():
    """Same applicant type, cleaner evidence. If the control does not actually
    score lower, it is not a control."""
    conn = run_orchestrator(DEFAULT_OUT)
    four = conn.execute("SELECT * FROM risk_assessment WHERE case_id = 'WAL-ONB-0004'").fetchone()
    ten = conn.execute("SELECT * FROM risk_assessment WHERE case_id = 'WAL-ONB-0010'").fetchone()
    assert conn.execute("SELECT applicant_type FROM onboarding_case WHERE case_id = 'WAL-ONB-0004'"
                        ).fetchone()[0] == conn.execute(
        "SELECT applicant_type FROM onboarding_case WHERE case_id = 'WAL-ONB-0010'").fetchone()[0]
    assert ten["risk_score"] < four["risk_score"], (
        f"control scored {ten['risk_score']}, case 4 scored {four['risk_score']}")
    assert ten["risk_band"] == "low" and four["risk_band"] == "high"


def test_every_risk_factor_cites_an_existing_id():
    conn = run_orchestrator(DEFAULT_OUT)
    checked = 0
    for r in conn.execute(
            "SELECT a.case_id, f.factor_id, f.factor, f.evidence_refs FROM risk_factor f "
            "JOIN risk_assessment a USING (assessment_id)"):
        ids = evidence_pack.known_ids(conn, r["case_id"])
        refs = [x for x in (r["evidence_refs"] or "").split("|") if x]
        assert refs, f"{r['factor_id']} ({r['factor']}) cites nothing"
        for ref in refs:
            assert ref in ids, f"{r['factor_id']} cites {ref}, which is not on {r['case_id']}"
            checked += 1
    assert checked > 20, "too few references checked to mean anything"


def test_a_narrative_citing_an_unknown_id_is_rejected():
    conn = run_orchestrator(DEFAULT_OUT)
    ids = evidence_pack.known_ids(conn, "WAL-ONB-0009")
    try:
        Narrative(text="all clear", evidence_refs=["DOC-9999"]).validate(ids)
        raise AssertionError("an invented reference should be rejected")
    except UnknownEvidenceReference:
        pass
    # and a real one passes
    Narrative(text="all clear", evidence_refs=[sorted(ids)[0]]).validate(ids)


def test_the_narrative_never_reaches_a_customer_field():
    conn = run_orchestrator(DEFAULT_OUT)
    narratives = [r["draft_compliance_narrative"] for r in
                  conn.execute("SELECT draft_compliance_narrative FROM evidence_pack")]
    assert narratives and any(n for n in narratives)
    for table, column in (("checklist_item", "note"), ("document", "resubmission_reasons"),
                          ("document", "release_reason"), ("case_hold", "reason")):
        for row in conn.execute(f"SELECT {column} AS v FROM {table} WHERE {column} IS NOT NULL"):
            for narrative in narratives:
                if narrative:
                    assert narrative not in (row["v"] or ""), \
                        f"narrative text leaked into {table}.{column}"


def test_the_evidence_pack_is_assembled_from_the_database():
    conn = run_orchestrator(DEFAULT_OUT)
    pack = evidence_pack.build(conn, "WAL-ONB-0004", KnowledgeBase())
    assert pack["entity_details"]["legal_name"] == "Vestmark Nordic OU"
    assert pack["checklist_completeness"]["required"] > 0
    assert pack["provider_results"]["registry"]["result"] == "review"
    assert len(pack["provider_results"]["screening"]) == 4
    # effective ownership is carried, not just the declared figure
    indirect = [u for u in pack["ubos"] if u["control_type"] == "indirect_shareholding"]
    assert indirect and indirect[0]["effective_ownership"] == 31.5
    assert pack["recommended_next_action"] == "enhanced_due_diligence"
    assert pack["risk_band"] == "high"


def test_low_band_still_needs_a_human_decision():
    """Section 18: nothing is approved automatically."""
    conn = run_orchestrator(DEFAULT_OUT)
    low = conn.execute("SELECT * FROM risk_assessment WHERE risk_band = 'low'").fetchall()
    assert low
    for r in low:
        assert r["recommended_action"] == "approve"
        case = conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                            (r["case_id"],)).fetchone()
        assert case["status"] != "approved", "the system must not approve a case by itself"
        assert case["status"] == "ready_for_decision"


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
