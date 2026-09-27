"""
Live mode must stay opt-in, and must never turn a failure into a pass.

Nothing here calls the API. The point is the guarantees around the call: that
mock is the default everywhere, that the key is read from the environment, that
prompts are versioned files whose version reaches the audit trail, and that a
call which fails puts the document in front of a person.

Run: python -m pytest tests -q
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import claude_client, db, live_mode                   # noqa: E402
from orchestrator.extractor import ClaudeExtractor, get_extractor       # noqa: E402
from orchestrator.kb import KnowledgeBase                               # noqa: E402
from orchestrator.orchestrator import (EXTRACTOR_MODE,                  # noqa: E402
                                       MEDIA_ASSESSOR_MODE, NARRATOR_MODE,
                                       PROVIDER_MODE, QUALITY_CHECKER_MODE,
                                       process_application)
from orchestrator.quality_checker import (ClaudeVisionQualityChecker,   # noqa: E402
                                          get_checker)
from orchestrator.steps import document_quality, extraction             # noqa: E402
from tools.dataset_to_applications import DEFAULT_OUT                   # noqa: E402


def _application(case_id):
    return json.loads(next(DEFAULT_OUT.glob(f"*{case_id}.json")).read_text(encoding="utf-8"))


def test_live_mode_is_a_placeholder_and_says_so():
    """No API access on this network. Selecting live mode stops with a message
    rather than failing part-way through a case."""
    assert live_mode.LIVE_MODE_READY is False
    assert live_mode.status() == "MOCK"

    with pytest.raises(live_mode.LiveModeNotConfigured) as e:
        get_checker("claude_vision")
    assert "LIVE MODE NOT CONFIGURED" in str(e.value)
    assert "ANTHROPIC_API_KEY" in str(e.value)
    assert "evaluate_live" in str(e.value)

    with pytest.raises(live_mode.LiveModeNotConfigured):
        get_extractor("claude")


def test_every_mode_defaults_to_mock():
    assert (QUALITY_CHECKER_MODE, EXTRACTOR_MODE, PROVIDER_MODE, MEDIA_ASSESSOR_MODE,
            NARRATOR_MODE) == ("mock",) * 5
    assert get_checker().mode == "mock" and get_extractor().mode == "mock"


def test_the_api_key_is_read_from_the_environment_and_not_stored():
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with pytest.raises(claude_client.MissingApiKey):
            claude_client.api_key()
        os.environ["ANTHROPIC_API_KEY"] = "test-value-not-a-real-key"
        assert claude_client.api_key() == "test-value-not-a-real-key"
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved

    # and no key is committed anywhere in the source. The needle is assembled so
    # that this file does not trip over its own text.
    needle = "sk" + "-ant-"
    for path in ROOT.rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert needle not in text, f"{path} looks like it contains an API key"


def test_prompts_are_versioned_files_and_the_version_reaches_the_checkers():
    quality = claude_client.load_prompt("quality_check", "v1")
    extraction = claude_client.load_prompt("extraction", "v1")
    assert quality.stamp == "quality_check_v1" and extraction.stamp == "extraction_v1"
    assert len(quality.text) > 400 and len(extraction.text) > 400

    # allow_unready: these assert how the checkers are built, not that live
    # mode is enabled - it is not, and the next test is what proves it.
    checker = ClaudeVisionQualityChecker(allow_unready=True)
    extractor = ClaudeExtractor(allow_unready=True)
    assert checker.version.endswith("/quality_check_v1")
    assert extractor.version.endswith("/extraction_v1")
    assert claude_client.DEFAULT_MODEL in checker.version

    with pytest.raises(FileNotFoundError):
        claude_client.load_prompt("quality_check", "v99")


def test_the_model_comes_from_a_setting():
    saved = os.environ.pop("WALLESTER_UC4_MODEL", None)
    try:
        assert claude_client.model_name() == "claude-sonnet-5"
        os.environ["WALLESTER_UC4_MODEL"] = "claude-opus-5"
        assert ClaudeExtractor(allow_unready=True).model == "claude-opus-5"
    finally:
        os.environ.pop("WALLESTER_UC4_MODEL", None)
        if saved is not None:
            os.environ["WALLESTER_UC4_MODEL"] = saved


def test_only_strict_json_is_accepted():
    assert claude_client.parse_strict_json('{"flags": []}') == {"flags": []}
    assert claude_client.parse_strict_json('```json\n{"a": 1}\n```') == {"a": 1}
    for bad in ("Here you go: {\"a\": 1}", "[1, 2]", "", "not json at all"):
        with pytest.raises(claude_client.CallFailed):
            claude_client.parse_strict_json(bad)


def test_a_failed_quality_call_holds_the_document_and_is_never_a_pass():
    class Failing(ClaudeVisionQualityChecker):
        mode, version = "claude_vision", "test/quality_check_v1"

        def __init__(self):
            pass

        def check(self, document):
            raise claude_client.CallFailed("the API call failed: Timeout")

    app = _application("WAL-ONB-0009")
    conn = db.connect(":memory:")
    kb = KnowledgeBase()
    trace = process_application(conn, app, kb)
    case_id = trace["intake"]["case_id"]
    # children before parents, or the foreign keys refuse
    for table in ("extracted_field", "checklist_item_document", "case_hold", "document"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("UPDATE checklist_item SET status = 'pending'")

    result = document_quality.run(conn, case_id, app, kb, checker=Failing())
    assert result.accepted == 0, "a checker that failed cannot have accepted anything"
    assert result.manual_review == result.documents_checked
    assert not document_quality.accepted_documents(conn, case_id)
    events = {r["action"] for r in conn.execute(
        "SELECT action FROM audit_event WHERE case_id = ?", (case_id,))}
    assert "quality_check_failed" in events


def test_a_failed_extraction_holds_the_document_rather_than_recording_nothing():
    class Failing:
        mode, version = "claude", "test/extraction_v1"

        def extract(self, document, expected_fields):
            raise claude_client.CallFailed("no usable JSON after 2 attempts")

    app = _application("WAL-ONB-0009")
    conn = db.connect(":memory:")
    kb = KnowledgeBase()
    trace = process_application(conn, app, kb)
    case_id = trace["intake"]["case_id"]
    conn.execute("DELETE FROM extracted_field")
    conn.execute("UPDATE document SET quality_status = 'accepted_for_checks'")

    result = extraction.run(conn, case_id, app, kb, extractor=Failing())
    assert result.fields_extracted == 0
    assert result.status == "analyst_review_required", "a silent extractor is not a clean read"
    held = conn.execute("SELECT COUNT(*) FROM document WHERE case_id = ? AND quality_status = "
                        "'manual_review_required'", (case_id,)).fetchone()[0]
    assert held > 0
    events = {r["action"] for r in conn.execute(
        "SELECT action FROM audit_event WHERE case_id = ?", (case_id,))}
    assert "extraction_failed" in events


def test_a_live_checker_needs_the_actual_file():
    checker = ClaudeVisionQualityChecker(allow_unready=True)
    with pytest.raises(claude_client.CallFailed):
        checker.check({"file_name": "nothing.pdf", "document_type": "registry_extract"})


def test_the_call_record_carries_what_the_audit_needs():
    call = claude_client.Call(data={}, model="claude-sonnet-5",
                              prompt_version="quality_check_v1", latency_ms=812,
                              input_tokens=1200, output_tokens=95, attempts=2)
    note = call.audit_note()
    for piece in ("model=claude-sonnet-5", "prompt=quality_check_v1", "attempts=2",
                  "latency=812ms", "tokens in/out=1200/95"):
        assert piece in note


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
