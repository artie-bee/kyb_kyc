"""
Per-document visual-check holds.

Every portal upload that mock mode cannot judge gets its OWN hold. An analyst's
Accept releases that one hold, in the analyst's name, and makes that item
Accepted at once; Request resubmission affects only that item and needs an
approved reason, which is what the customer is shown. No document is ever held
back by another document's hold.

Run: python -m pytest tests/test_per_document_holds.py -q
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import customer_checklist                      # noqa: E402
from orchestrator import clock, demo_samples                          # noqa: E402
from orchestrator.steps import document_quality, verification         # noqa: E402
from portal import apply                                              # noqa: E402
from tools.make_sample_documents import FORM_VALUES, build_demo_pack  # noqa: E402
from web import render as console                                     # noqa: E402

ID = "Identity document (passport or ID card) - Kristiina Vaher"
SELFIE = "Selfie to confirm your identity - Kristiina Vaher"


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    return build_demo_pack(tmp_path_factory.mktemp("pack"), today=clock.current().today())["folder"]


@pytest.fixture()
def conn(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(demo_samples, "MANIFEST", pack / "manifest.json")
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    c = data.connect(path)
    yield c
    c.close()


def names(c, case_id):
    return {i["document"] + (" - " + i["person"] if i["person"] else ""): i
            for i in customer_checklist(c, case_id)["items"]}


def pack_files(pack):
    out = {}
    for line in (pack / "FORM_VALUES.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `([^`]+)` \| (.+?)( \*\(upload first.*\)\*)? \|$", line)
        if m and "BLURRED" not in m.group(1):
            out[m.group(2)] = pack / m.group(1)
    return out


def ten_unrecognised_uploads(c, pack):
    """The Lumen Harbour application, and ten of its eleven items uploaded as
    files the manifest does not know: all ten wait for a person."""
    answers = {key: value for _, _, key, value in FORM_VALUES}
    case_id = data.submit_application(c, apply.build_application(answers, "PORTAL-TEST"))
    files = pack_files(pack)
    skipped = "Source of funds declaration"
    for name, path in files.items():
        if name != skipped:
            data.upload_document(c, case_id, names(c, case_id)[name]["checklist_item_id"],
                                 path.name, path.read_bytes() + b"\n")
    return case_id


def document_for(c, case_id, name):
    item = names(c, case_id)[name]["checklist_item_id"]
    return c.execute("SELECT MAX(document_id) FROM checklist_item_document WHERE item_id = ?",
                     (item,)).fetchone()[0]


def visual_holds(c, case_id):
    return {document_quality.visual_hold_document(h.reason): h.hold_id
            for h in data.open_holds(c, case_id)
            if document_quality.visual_hold_document(h.reason)}


# ---------------------------------------------------------------------------

def test_every_upload_gets_its_own_hold_and_accepting_one_releases_only_that(conn, pack):
    c = conn
    case_id = ten_unrecognised_uploads(c, pack)
    before = visual_holds(c, case_id)
    assert len(before) == 10, "one hold per document, not one for the case"

    first = document_for(c, case_id, "Certificate of incorporation")
    data.release_document(c, first, "analyst.test", "accept", "legible original")
    after = visual_holds(c, case_id)
    assert first not in after
    assert {d: h for d, h in before.items() if d != first} == after, \
        "every other document keeps its own hold, untouched"
    released = c.execute("SELECT released_by, release_reason FROM case_hold WHERE hold_id = ?",
                         (before[first],)).fetchone()
    assert released["released_by"] == "analyst.test", "released by the person who looked"
    assert names(c, case_id)["Certificate of incorporation"]["status"] == "Accepted"


def test_eight_accepted_and_two_sent_back_read_exactly_so_in_both_front_ends(conn, pack):
    c = conn
    case_id = ten_unrecognised_uploads(c, pack)
    for name in names(c, case_id):
        doc = document_for(c, case_id, name)
        if doc is None:
            continue
        if name in (ID, SELFIE):
            data.release_document(c, doc, "analyst.test", "request_resubmission", "not clear",
                                  reason_code="document_unreadable")
        else:
            data.release_document(c, doc, "analyst.test", "accept", "legible original")

    cl = customer_checklist(c, case_id)
    assert (cl["accepted"], cl["under_review"], cl["still_needed"]) == (8, 0, 3)
    assert not visual_holds(c, case_id)

    page = console.case_detail(c, case_id, tab="Documents")
    for block in re.findall(r"<details.*?</details>", page, re.S):
        if "accepted_for_checks" in block.split("</summary>")[0]:
            assert "visual_check_not_run" not in block.split("</summary>")[0], \
                "an accepted document is not tagged as unchecked"
            assert "released by analyst.test" in block


def test_sending_one_back_never_leaves_an_accepted_one_under_review(conn, pack):
    c = conn
    case_id = ten_unrecognised_uploads(c, pack)
    order = [n for n in names(c, case_id) if document_for(c, case_id, n)]
    for n, name in enumerate(order):
        doc = document_for(c, case_id, name)
        if n % 3 == 0:
            data.release_document(c, doc, "analyst.test", "request_resubmission", "cropped",
                                  reason_code="document_incomplete")
        else:
            data.release_document(c, doc, "analyst.test", "accept", "fine")
        # after every single action: whatever was accepted still reads Accepted
        for done in order[:n + 1]:
            status = names(c, case_id)[done]["status"]
            expected = "Resubmission needed" if order.index(done) % 3 == 0 else "Accepted"
            assert status == expected, (name, done, status)


def test_a_resubmission_needs_an_approved_reason_and_the_customer_sees_it(conn, pack):
    c = conn
    case_id = ten_unrecognised_uploads(c, pack)
    doc = document_for(c, case_id, ID)
    for bad in (None, "", "because", "*"):
        with pytest.raises(ValueError, match="approved list"):
            data.release_document(c, doc, "analyst.test", "request_resubmission", "blurred",
                                  reason_code=bad)
    assert doc in visual_holds(c, case_id), "a refused action changes nothing"

    data.release_document(c, doc, "analyst.test", "request_resubmission", "glare",
                          reason_code="document_unreadable")
    item = names(c, case_id)[ID]
    assert item["status"] == "Resubmission needed"
    assert item["reason"] == data.kb().resubmission_reason_text["document_unreadable"][
        "customer_text"]
    assert "Please upload a new copy of this document." != item["reason"]


def test_a_restricted_case_is_told_only_the_generic_reason(conn, pack):
    c = conn
    item_id = data.add_checklist_item(c, "WAL-ONB-0005", "bank_statement", "compliance.test",
                                      "EDD: recent statement")
    data.upload_document(c, "WAL-ONB-0005", item_id, "statement.pdf",
                         (pack / "01_certificate_of_incorporation.pdf").read_bytes() + b"\n")
    doc = c.execute("SELECT MAX(document_id) FROM checklist_item_document WHERE item_id = ?",
                    (item_id,)).fetchone()[0]
    data.release_document(c, doc, "compliance.test", "request_resubmission", "wrong file",
                          reason_code="wrong_document")
    item = next(i for i in customer_checklist(c, "WAL-ONB-0005")["items"]
                if i["checklist_item_id"] == item_id)
    assert item["reason"] == "Please upload a new copy of this document."


def test_the_step_5_gate_stays_shut_until_the_replacements_are_accepted(conn, pack):
    c = conn
    case_id = ten_unrecognised_uploads(c, pack)
    for name in names(c, case_id):
        doc = document_for(c, case_id, name)
        if doc is None:
            continue
        if name in (ID, SELFIE):
            data.release_document(c, doc, "analyst.test", "request_resubmission", "not clear",
                                  reason_code="document_unreadable")
        else:
            data.release_document(c, doc, "analyst.test", "accept", "legible")
    with pytest.raises(verification.VerificationGateError):
        verification.gate(c, case_id)

    files = pack_files(pack)
    for name in (ID, SELFIE, "Source of funds declaration"):
        path = files[name]
        data.upload_document(c, case_id, names(c, case_id)[name]["checklist_item_id"],
                             path.name, path.read_bytes() + b"\n\n")
        with pytest.raises(verification.VerificationGateError):
            verification.gate(c, case_id)       # held for the visual check: not yet
        data.release_document(c, document_for(c, case_id, name), "analyst.test", "accept",
                              "clear replacement")
    verification.gate(c, case_id)               # every required item accepted: open
    assert customer_checklist(c, case_id)["still_needed"] == 0
