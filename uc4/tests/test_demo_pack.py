"""
Scenario 8: a brand-new application taken to a decision with real uploads.

The demo upload pack (tools/make_sample_documents.py -> sample_documents/
demo_pack/) holds one file for every checklist item its FORM_VALUES.md facts
produce, plus one blurred ID. In mock mode a pack file is recognised by its
SHA-256 and its verdict and fields are replayed, labelled "mock: recognised
demo sample file"; any other file keeps the visual-check hold. Live mode never
reads the manifest.

Run: python -m pytest tests/test_demo_pack.py -q
"""

import re
import sys
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import customer_checklist                      # noqa: E402
from orchestrator import clock, demo_samples                          # noqa: E402
from orchestrator.quality_checker import QualityChecker, QualityVerdict  # noqa: E402
from orchestrator.steps import document_quality                       # noqa: E402
from portal import apply, server                                      # noqa: E402
from tools.make_sample_documents import build_demo_pack               # noqa: E402
from web import render as console                                     # noqa: E402

LABEL = demo_samples.LABEL


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    out = tmp_path_factory.mktemp("pack")
    return build_demo_pack(out, today=clock.current().today())["folder"]


@pytest.fixture()
def site(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(demo_samples, "MANIFEST", pack / "manifest.json")
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    conn = data.connect(path)
    httpd = server.serve(0, db_path=path, demo=True, uploads_dir=tmp_path / "uploads")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"conn": conn, "pack": pack, "base": f"http://127.0.0.1:{httpd.server_address[1]}"}
    httpd.shutdown()
    httpd.server_close()
    conn.close()


# ---------------------------------------------------------------------------
# Reading FORM_VALUES.md the way a presenter does
# ---------------------------------------------------------------------------

def form_values(pack) -> dict:
    """{form key: value} from the table a presenter types from. A choice shown
    by its on-screen label is mapped back to the value the form sends."""
    shown = {label: value for value, label in apply.ENTITY_TYPES + apply.COUNTRIES}
    shown.update({"Yes": "yes", "No": "no", "Directly, in their own name": "direct"})
    out = {}
    for line in (pack / "FORM_VALUES.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| [^|]+ \| [^|]+ \| (.+?) \| `([a-z0-9_]+)` \|$", line)
        if m:
            typed = "" if m.group(1) == "*(leave blank)*" else m.group(1)
            out[m.group(2)] = shown.get(typed, typed)
    return out


def file_table(pack) -> dict:
    """{file name: checklist item name as the portal shows it}."""
    out = {}
    for line in (pack / "FORM_VALUES.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `([^`]+)` \| (.+?)( \*\(upload first.*\)\*)? \|$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Browser:
    def __init__(self, base):
        self.base = base
        self.o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()),
                                             NoRedirect())

    def go(self, path, body=None, headers=None):
        try:
            with self.o.open(urllib.request.Request(self.base + path, body, headers or {})) as r:
                return r.status, r.headers.get("Location"), r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location"), e.read().decode("utf-8")

    def apply(self, answers):
        """Walk the form step by step with plain posts, as a presenter types it."""
        step = apply.BUSINESS
        while True:
            nav = "submit" if step == apply.REVIEW else "next"
            status, where, page = self.go("/apply", urlencode(
                {**answers, "step": step, "nav": nav}).encode())
            if nav == "submit":
                assert status == 303, page
                return
            assert "Please check these answers" not in page, (step, page)
            step = apply.next_step(step, answers)

    def upload(self, item_id, path):
        b = "----packboundary"
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"item_id\"\r\n\r\n{item_id}"
                f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{path.name}\"\r\n\r\n").encode() + path.read_bytes() \
            + f"\r\n--{b}--\r\n".encode()
        status, where, _ = self.go("/upload", body,
                                   {"Content-Type": f"multipart/form-data; boundary={b}"})
        assert status == 303
        return self.go(where)[2]


def start(site):
    browser = Browser(site["base"])
    browser.apply(form_values(site["pack"]))
    case_id = site["conn"].execute("SELECT MAX(case_id) FROM onboarding_case"
                                   " WHERE case_id LIKE 'WAL-DEMO-%'").fetchone()[0]
    return browser, case_id


def items_by_name(conn, case_id):
    return {i["document"] + (" - " + i["person"] if i["person"] else ""): i
            for i in customer_checklist(conn, case_id)["items"]}


def upload_pack(site, browser, case_id, blurred_first=True):
    files = file_table(site["pack"])
    order = sorted(files, key=lambda f: ("BLURRED" not in f) if blurred_first else 0)
    pages = {}
    for name in order:
        if not blurred_first and "BLURRED" in name:
            continue
        item = items_by_name(site["conn"], case_id)[files[name]]
        pages[name] = browser.upload(item["checklist_item_id"], site["pack"] / name)
    return pages


def status(conn, case_id):
    return conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone()[0]


# ---------------------------------------------------------------------------

def test_the_form_values_produce_a_checklist_the_pack_covers_exactly(site):
    browser, case_id = start(site)
    shown = set(items_by_name(site["conn"], case_id))
    files = file_table(site["pack"])
    clear = {f: item for f, item in files.items() if "BLURRED" not in f}
    assert set(clear.values()) == shown, "no item without a file, no file without an item"
    assert len(clear) == len(shown) == 11, "one clear file per item"
    blurred = [item for f, item in files.items() if "BLURRED" in f]
    assert blurred == ["Identity document (passport or ID card) - " + "Kristiina Vaher"]
    on_disk = {p.name for p in site["pack"].iterdir() if p.suffix in (".pdf", ".jpg")}
    assert on_disk == set(files), "every file in the pack is in the table, and only those"


def test_the_pack_runs_the_case_to_ready_for_decision_without_keying(site):
    browser, case_id = start(site)
    upload_pack(site, browser, case_id, blurred_first=False)
    conn = site["conn"]
    assert status(conn, case_id) == "ready_for_decision"
    assert not data.open_holds(conn, case_id)
    methods = {r[0] for r in conn.execute(
        "SELECT f.entry_method FROM extracted_field f JOIN document d USING (document_id)"
        " WHERE d.case_id = ?", (case_id,))}
    assert methods == {"extracted"}, "nothing was typed in by an analyst"
    band = conn.execute("SELECT risk_band FROM risk_assessment WHERE case_id = ?",
                        (case_id,)).fetchone()[0]
    assert band == "low"
    reg = conn.execute("SELECT name_match, number_match, address_match, director_match"
                       " FROM registry_check WHERE case_id = ?", (case_id,)).fetchone()
    assert tuple(reg) == ("match",) * 4, "the replayed fields agree with the form"


def test_the_blurred_id_is_refused_and_the_clear_one_accepted(site):
    browser, case_id = start(site)
    pages = upload_pack(site, browser, case_id, blurred_first=True)
    first = pages["07_identity_document_BLURRED.jpg"]
    text = " ".join(re.sub(r"<[^>]+>", " ", first).split())
    assert "We could not accept this file. We could not read this document clearly" in text
    conn = site["conn"]
    blurred = conn.execute(
        "SELECT quality_status_at_screen, resubmission_reasons FROM document"
        " WHERE case_id = ? ORDER BY document_id LIMIT 1", (case_id,)).fetchone()
    assert tuple(blurred) == ("resubmission_required", "document_unreadable")
    item = items_by_name(conn, case_id)["Identity document (passport or ID card) - "
                                        "Kristiina Vaher"]
    assert item["status"] == "Accepted" and item["previous_uploads"][-1].endswith("BLURRED.jpg")
    assert status(conn, case_id) == "ready_for_decision", "the clear ID took it the rest of the way"


def test_a_file_not_in_the_manifest_still_gets_the_visual_check_hold(site):
    browser, case_id = start(site)
    item = items_by_name(site["conn"], case_id)["Certificate of incorporation"]
    other = site["pack"].parent / "not_in_the_pack.pdf"
    other.write_bytes(b"%PDF-1.4\n% some other file\n")
    browser.upload(item["checklist_item_id"], other)
    reasons = [h.reason for h in data.open_holds(site["conn"], case_id)]
    assert any("visual check not run in mock mode" in r for r in reasons), reasons
    assert not data.recognised_documents(site["conn"], case_id)


def test_a_renamed_pack_file_is_still_recognised_by_its_contents(site):
    browser, case_id = start(site)
    item = items_by_name(site["conn"], case_id)["Certificate of incorporation"]
    renamed = site["pack"].parent / "scan_0042.pdf"
    renamed.write_bytes((site["pack"] / "01_certificate_of_incorporation.pdf").read_bytes())
    browser.upload(item["checklist_item_id"], renamed)
    assert len(data.recognised_documents(site["conn"], case_id)) == 1


def test_every_replayed_result_is_labelled(site):
    browser, case_id = start(site)
    upload_pack(site, browser, case_id, blurred_first=True)
    conn = site["conn"]
    docs = [r[0] for r in conn.execute("SELECT document_id FROM document WHERE case_id = ?",
                                       (case_id,))]
    assert len(docs) == 12
    # The blurred ID never reaches the replay: the sharpness rule refuses it
    # first (QR-13), so that one verdict is the heuristic's and says so.
    for (payload,) in conn.execute("SELECT payload_summary FROM audit_event WHERE case_id = ?"
                                   " AND action = 'document_quality_checked'", (case_id,)):
        assert LABEL in payload or ("rules=QR-13" in payload and "checker=not called" in payload), \
            payload
    for (payload,) in conn.execute("SELECT payload_summary FROM audit_event WHERE case_id = ?"
                                   " AND action = 'fields_extracted'", (case_id,)):
        assert LABEL in payload, payload
    replayed = data.recognised_documents(conn, case_id)
    assert len(replayed) == 11 and replayed == set(docs) - {docs[0]}

    page = console.case_detail(conn, case_id, tab="Documents")
    replayed_fields = conn.execute(
        "SELECT COUNT(*) FROM extracted_field f JOIN document d USING (document_id)"
        " WHERE d.case_id = ?", (case_id,)).fetchone()[0]
    assert page.count(LABEL) == len(replayed) + replayed_fields, "every replay, doc and field"


def test_live_mode_ignores_the_manifest(site):
    """A pack file judged by a live checker gets the live verdict, not the
    manifest's: here a checker that finds the clear ID is the wrong document,
    where the manifest would have passed it."""
    class LiveChecker(QualityChecker):
        mode, version = "live", "test-live"

        def check(self, document):
            return QualityVerdict(flags=["wrong_document_type"], confidence=0.99)

    browser, case_id = start(site)
    item = items_by_name(site["conn"], case_id)["Identity document (passport or ID card) - "
                                                "Kristiina Vaher"]
    clear = (site["pack"] / "08_identity_document_clear.jpg").read_bytes()
    assert demo_samples.recognise(clear)["quality_flags"] == []
    result = document_quality.receive_upload(site["conn"], case_id, item["checklist_item_id"],
                                             "id.jpg", clear, data.kb(), checker=LiveChecker())
    site["conn"].commit()
    assert result.quality_status == "resubmission_required", "the live verdict, not the replay"
    assert not data.recognised_documents(site["conn"], case_id)
