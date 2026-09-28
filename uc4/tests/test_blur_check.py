"""
The deterministic blur check (kb rule QR-13) and the portal's three counts.

A blurred upload is refused at Step 3 by a sharpness heuristic - in mock and
live mode, before any model is called - whether or not the file is a known
demo sample. The threshold must refuse every deliberately blurred sample and
pass every clear one.

Run: python -m pytest tests/test_blur_check.py -q
"""

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app import data                                                  # noqa: E402
from app.customer_view import customer_checklist                      # noqa: E402
from orchestrator import clock, demo_samples, sharpness               # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.quality_checker import QualityChecker               # noqa: E402
from orchestrator.steps import document_quality                       # noqa: E402
from portal import apply                                              # noqa: E402
from test_demo_case_checks import _expected_from_console              # noqa: E402
from tools import make_sample_documents as samples                    # noqa: E402
from tools.dataset_to_applications import DEFAULT_DATASET             # noqa: E402

KB = KnowledgeBase()
RULE = next(r for r in KB.document_quality_rules if r["check_name"] == "sharpness_below_threshold")
THRESHOLD = float(RULE["parameter"])
ID_ITEM = "Identity document (passport or ID card) - Kristiina Vaher"


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """Every sample the project makes: the dataset cases, Scenario 2b, the pack."""
    out = tmp_path_factory.mktemp("samples")
    samples.build(DEFAULT_DATASET, out, samples.DEMO_CASES)
    samples.build_scenario_2b(DEFAULT_DATASET, out)
    pack = samples.build_demo_pack(out, today=clock.current().today())
    return {"out": out, "pack": pack["folder"]}


@pytest.fixture()
def conn(generated, tmp_path, monkeypatch):
    monkeypatch.setattr(demo_samples, "MANIFEST", generated["pack"] / "manifest.json")
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    c = data.connect(path)
    yield c
    c.close()


def lumen(conn):
    answers = {key: value for _, _, key, value in samples.FORM_VALUES}
    return data.submit_application(conn, apply.build_application(answers, "PORTAL-TEST"))


def item(conn, case_id, name=ID_ITEM):
    return next(i for i in customer_checklist(conn, case_id)["items"]
                if i["document"] + (" - " + i["person"] if i["person"] else "") == name)


def blurred_names() -> set[str]:
    return {r["file_name"] for r in csv.DictReader(
        open(DEFAULT_DATASET / "document.csv", encoding="utf-8"))
        if r["quality_flags"] == "blurred_unreadable"}


# ---------------------------------------------------------------------------
# The threshold, measured
# ---------------------------------------------------------------------------

def test_the_rule_is_in_the_kb_as_a_deterministic_heuristic():
    assert RULE["check_type"] == "deterministic"
    assert (RULE["failure_flag"], RULE["outcome"], RULE["resubmission_reason"]) == \
        ("blurred_unreadable", "resubmission_required", "document_unreadable")
    assert "heuristic" in RULE["description"].lower()
    assert KB.version == "kb-2026.09-poc-v10"


def test_every_clear_sample_passes_and_every_blurred_one_fails(generated):
    blurred, clear = [], []
    for p in sorted(generated["out"].rglob("*")):
        if p.suffix.lower() not in (".pdf", ".jpg", ".jpeg", ".png"):
            continue
        value = sharpness.measure(p)
        assert value is not None, f"{p.name} could not be measured"
        (blurred if "BLURRED" in p.name or p.name in blurred_names() else clear).append(
            (value, p.name))
    # the project makes two blurred files (case 2's director scan, the pack's
    # blurred ID) and 73 clear ones across the five cases, Scenario 2b and the pack
    assert len(blurred) == 2 and len(clear) == 73, (len(blurred), len(clear))
    worst_blur, sharpest_blur = max(blurred)
    lowest_clear, name = min(clear)
    assert worst_blur < THRESHOLD, f"{sharpest_blur} measures {worst_blur}, not refused"
    assert lowest_clear >= THRESHOLD, f"{name} measures {lowest_clear}: a false refusal"
    # the margin either side, so a future tweak cannot quietly shave it away
    assert THRESHOLD / worst_blur > 1.5 and lowest_clear / THRESHOLD > 1.5


# ---------------------------------------------------------------------------
# A blurred upload, refused instantly
# ---------------------------------------------------------------------------

class NeverCalled(QualityChecker):
    mode, version = "live", "must-not-be-called"

    def check(self, document):
        raise AssertionError("a blurred file must not reach the checker")


def _blurred_pdf(folder: Path) -> Path:
    img = samples.blur(samples.paper({"document_type": "id_document", "document_id": "X",
                                      "case_id": "X"}, [{"name": "full_name",
                                                         "value": "Kristiina Vaher"}], "Issuer"))
    path = folder / "scan_BLURRED.pdf"
    samples.save_pdf(img, path, "2026-09-28T09:00:00Z")
    return path


@pytest.mark.parametrize("kind", ["recognised", "unrecognised", "pdf"])
def test_a_blurred_upload_is_refused_instantly_with_the_reason(conn, generated, kind, tmp_path):
    case_id = lumen(conn)
    pack_blur = (generated["pack"] / "07_identity_document_BLURRED.jpg").read_bytes()
    content, name = {
        "recognised": (pack_blur, "id.jpg"),
        "unrecognised": (pack_blur + b"\n", "my_scan.jpg"),     # not in the manifest
        "pdf": (_blurred_pdf(tmp_path).read_bytes(), "scan.pdf"),
    }[kind]
    if kind == "recognised":
        assert demo_samples.recognise(content) is not None
    else:
        assert demo_samples.recognise(content) is None

    result = document_quality.receive_upload(conn, case_id, item(conn, case_id)["checklist_item_id"],
                                             name, content, KB, checker=NeverCalled())
    conn.commit()
    assert result.quality_status == "resubmission_required"
    shown = item(conn, case_id)
    assert shown["status"] == "Resubmission needed"
    assert shown["reason"].startswith("We could not read this document clearly")
    assert not [h for h in data.open_holds(conn, case_id)
                if "visual check not run" in h.reason], "no analyst needed to refuse it"
    payload = conn.execute(
        "SELECT payload_summary FROM audit_event WHERE case_id = ? AND action ="
        " 'document_quality_checked' ORDER BY event_id DESC LIMIT 1", (case_id,)).fetchone()[0]
    assert "QR-13" in payload and f"threshold {THRESHOLD:g}" in payload
    assert "sharpness=" in payload and "checker=not called" in payload


def test_a_clear_upload_passes_the_rule_and_its_measurement_is_audited(conn, generated):
    case_id = lumen(conn)
    clear = (generated["pack"] / "08_identity_document_clear.jpg").read_bytes()
    data.upload_document(conn, case_id, item(conn, case_id)["checklist_item_id"], "id.jpg", clear)
    payload = conn.execute(
        "SELECT payload_summary FROM audit_event WHERE case_id = ? AND action ="
        " 'document_quality_checked' ORDER BY event_id DESC LIMIT 1", (case_id,)).fetchone()[0]
    assert "passes" in payload and "sharpness=" in payload
    assert item(conn, case_id)["status"] != "Resubmission needed"


def test_a_stored_upload_screened_before_the_rule_is_corrected_by_a_rescreen(
        conn, generated, monkeypatch):
    """How WAL-DEMO-0002 was put right: the blurred ID had gone to the visual
    hold before the rule existed; Step 3 again refuses it."""
    case_id = lumen(conn)
    blurred = (generated["pack"] / "07_identity_document_BLURRED.jpg").read_bytes() + b"\n"
    with monkeypatch.context() as m:
        m.setattr(sharpness, "measure", lambda path: None)      # as it was before QR-13
        data.upload_document(conn, case_id, item(conn, case_id)["checklist_item_id"],
                             "old.jpg", blurred)
    before = item(conn, case_id)
    old_doc = conn.execute("SELECT MAX(document_id) FROM document WHERE case_id = ?",
                           (case_id,)).fetchone()[0]
    attempts = conn.execute("SELECT resubmission_attempts FROM checklist_item WHERE item_id = ?",
                            (before["checklist_item_id"],)).fetchone()[0]
    assert before["status"] == "Under review"

    result = data.rescreen_document(conn, old_doc, "ops.test", "blur rule added")
    assert result.quality_status == "resubmission_required"
    after = item(conn, case_id)
    assert after["status"] == "Resubmission needed" and after["can_upload"]
    assert conn.execute("SELECT quality_status FROM document WHERE document_id = ?",
                        (old_doc,)).fetchone()[0] == "superseded", "the first verdict is kept"
    assert conn.execute("SELECT resubmission_attempts FROM checklist_item WHERE item_id = ?",
                        (before["checklist_item_id"],)).fetchone()[0] == attempts
    assert conn.execute("SELECT COUNT(*) FROM audit_event WHERE case_id = ?"
                        " AND action = 'document_rescreened'", (case_id,)).fetchone()[0] == 1


# ---------------------------------------------------------------------------
# The three counts
# ---------------------------------------------------------------------------

def test_the_three_counts_add_up_and_match_the_console(conn, generated):
    case_id = lumen(conn)
    files = sorted(p for p in generated["pack"].iterdir() if p.suffix in (".pdf", ".jpg"))
    names = {i["document"] + (" - " + i["person"] if i["person"] else ""): i["checklist_item_id"]
             for i in customer_checklist(conn, case_id)["items"]}
    table = {}
    for line in (generated["pack"] / "FORM_VALUES.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("| `"):
            f, label = line.split("|")[1:3]
            table[f.strip().strip("`")] = label.split(" *(")[0].strip()
    # a mix along the way: some recognised, some not, the blurred ID in between
    for n, f in enumerate(files[:8]):
        content = f.read_bytes() if n % 2 else f.read_bytes() + b"\n"
        try:
            data.upload_document(conn, case_id, names[table[f.name]], f.name, content)
        except document_quality.UploadRefused:
            pass
        for (cid,) in conn.execute("SELECT case_id FROM onboarding_case").fetchall():
            cl = customer_checklist(conn, cid)
            assert cl["accepted"] + cl["under_review"] + cl["still_needed"] == \
                cl["total_needed"], cid
            expected, owed = _expected_from_console(conn, cid)
            needed = [i for i in cl["items"] if not i["optional"]]
            assert cl["accepted"] == sum(expected[i["checklist_item_id"]] == "Accepted"
                                         for i in needed), cid
            assert cl["under_review"] == sum(expected[i["checklist_item_id"]] == "Under review"
                                             for i in needed), cid
            assert cl["still_needed"] == owed, cid
