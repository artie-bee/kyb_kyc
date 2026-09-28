"""
The customer portal's upload space and dynamic checklist. Demo only.

The portal and the console are both served on real sockets over one copy of
the demo database, so a file the customer sends is the file the analyst
releases, and a status the customer sees is read from the same rows.

Run: python -m pytest tests/test_portal_uploads.py -q
"""

import re
import sys
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import (CUSTOMER_STATUSES, DOCUMENT_LABELS,    # noqa: E402
                               GENERIC_DOCUMENT, customer_checklist, leaks, visible_text)
from orchestrator import db                                           # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import communication, document_quality       # noqa: E402
from orchestrator.steps.requirement_pack import awaiting_confirmation  # noqa: E402
from orchestrator.virus_scanner import ScanResult, VirusScanner      # noqa: E402
from portal import server as portal_server                            # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import server as console_server                              # noqa: E402

PORTAL = ROOT / "portal"
PDF = b"%PDF-1.4\n% synthetic demo file\n"
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 120          # a Windows executable
KB = KnowledgeBase()


# ---------------------------------------------------------------------------
# Fixtures: a demo database, the portal and the console over it
# ---------------------------------------------------------------------------

def _serve(httpd):
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture()
def demo(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    conn = data.connect(path)

    portal = portal_server.serve(0, db_path=path, demo=True, uploads_dir=tmp_path / "uploads")
    console_server.drop_connection()
    console_server._conn = data.connect(path)
    console = console_server.serve(0)
    yield {"conn": conn, "db": path, "tmp": tmp_path,
           "portal": _serve(portal), "console": _serve(console)}
    for httpd in (portal, console):
        httpd.shutdown()
        httpd.server_close()
    console_server.drop_connection()
    conn.close()


@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    """Every case run to its scripted end: case 14 is closed, 9 approved."""
    path = tmp_path_factory.mktemp("uploads_finished") / "onboarding.db"
    conn = db.connect(path)
    run_demo(conn, verbose=False)
    conn.commit()
    yield conn
    conn.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Browser:
    """JavaScript off: plain GETs, plain form posts, plain multipart uploads."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()), NoRedirect())
        self.locations = []

    def _open(self, req):
        try:
            with self.opener.open(req) as r:
                loc = r.headers.get("Location")
                self.locations.append(loc)
                return r.status, loc, r.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            loc = err.headers.get("Location")
            self.locations.append(loc)
            return err.code, loc, err.read().decode("utf-8")

    def get(self, path):
        status, _, page = self._open(self.base + path)
        return status, page

    def post(self, path, **fields):
        return self._open(urllib.request.Request(self.base + path, urlencode(fields).encode()))

    def upload(self, item_id, file_name, content):
        b = "----uploadspaceboundary"
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"item_id\"\r\n\r\n{item_id}"
                f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{file_name}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
                ).encode() + content + f"\r\n--{b}--\r\n".encode()
        status, where, _ = self._open(urllib.request.Request(
            self.base + "/upload", body,
            headers={"Content-Type": f"multipart/form-data; boundary={b}"}))
        assert status == 303, status
        return self.get(where)[1]

    def open_as(self, case_id):
        status, where, _ = self.post("/demo/open", case_id=case_id)
        assert (status, where) == (303, "/")
        return self


def words(page: str) -> str:
    return " ".join(visible_text(page).split())


def row(page: str, item_id: str) -> str:
    m = re.search(r'<li class="item[^"]*" id="item-' + re.escape(item_id) + r'">(.*?)</li>',
                  page, re.S)
    assert m, f"no row for {item_id}"
    return m.group(1)


def item_like(cl, prefix, person=None):
    return next(i for i in cl["items"] if i["document"].startswith(prefix)
                and (person is None or i["person"] == person))


def refusal(conn, case_id, item_id, name, content, **kw) -> str:
    try:
        with pytest.raises(document_quality.UploadRefused) as err:
            document_quality.receive_upload(conn, case_id, item_id, name, content, KB, **kw)
    finally:
        # the refusal is audited; commit it, or this connection holds the write
        # lock and the servers sharing the file cannot write
        conn.commit()
    return str(err.value)


# ---------------------------------------------------------------------------
# One quality-check path
# ---------------------------------------------------------------------------

def test_there_is_exactly_one_quality_check_path(demo, monkeypatch):
    """A portal upload goes through the same Step 3 run() as a scripted
    document - and nothing else in the code base creates a document row."""
    calls = []
    real = document_quality.run

    def spy(conn, case_id, application, kb, **kw):
        calls.append([d["file_name"] for d in application["documents"]])
        return real(conn, case_id, application, kb, **kw)

    monkeypatch.setattr(document_quality, "run", spy)
    cl = customer_checklist(demo["conn"], "WAL-ONB-0002")
    item = item_like(cl, "Declaration of beneficial owners")
    data.upload_document(demo["conn"], "WAL-ONB-0002", item["checklist_item_id"], "ubo.pdf", PDF)
    assert len(calls) == 1 and len(calls[0]) == 1, "Step 3 ran once, for that item only"

    writers = [f"{p.relative_to(ROOT)}"
               for folder in ("orchestrator", "app", "portal", "web", "tools")
               for p in (ROOT / folder).rglob("*.py")
               if re.search(r"INSERT INTO document\s*\(", p.read_text(encoding="utf-8"))]
    assert writers == [str(Path("orchestrator/steps/document_quality.py"))], writers


def test_portal_code_names_no_document_type():
    """The backend says what a case needs; the portal only lays it out."""
    types = {r["document_type"] for r in KB.requirement_rules}
    source = "\n".join(p.read_text(encoding="utf-8") for p in PORTAL.rglob("*")
                       if p.is_file() and p.suffix in (".py", ".js", ".css")).lower()
    for t in sorted(types):
        assert t not in source, f"portal code names the document type {t!r}"
        assert t.replace("_", " ") not in source, f"portal code names {t!r}"
    for label in DOCUMENT_LABELS.values():
        assert label.lower() not in source, f"portal code carries the label {label!r}"


# ---------------------------------------------------------------------------
# The dynamic checklist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_id, length", [("WAL-ONB-0001", 7), ("WAL-ONB-0004", 17),
                                             ("WAL-ONB-0008", 6), ("WAL-ONB-0010", 16)])
def test_each_case_shows_exactly_its_own_checklist(demo, case_id, length):
    conn = demo["conn"]
    rows = conn.execute("SELECT i.* FROM checklist_item i JOIN requirement_pack p"
                        " USING (pack_id) WHERE p.case_id = ?", (case_id,)).fetchall()
    expected = [r["item_id"] for r in rows
                if r["status"] != "waived" and not awaiting_confirmation(r)]
    cl = customer_checklist(conn, case_id)
    assert [i["checklist_item_id"] for i in cl["items"]] == expected
    assert len(cl["items"]) == length

    page = Browser(demo["portal"]).open_as(case_id).get("/checklist")[1]
    assert page.count('<li class="item') == length


def test_each_item_carries_only_what_a_customer_may_see(demo):
    allowed = {"checklist_item_id", "document", "person", "optional", "status", "reason",
               "can_upload", "previous_uploads"}
    for (case_id,) in demo["conn"].execute("SELECT case_id FROM onboarding_case"):
        for item in customer_checklist(demo["conn"], case_id)["items"]:
            assert set(item) == allowed, set(item) ^ allowed
            assert item["status"] in CUSTOMER_STATUSES


def test_waived_and_unconfirmed_items_never_appear_and_cannot_be_uploaded(demo):
    conn = demo["conn"]
    rows = conn.execute("SELECT i.* FROM checklist_item i JOIN requirement_pack p"
                        " USING (pack_id) WHERE p.case_id = 'WAL-ONB-0004'").fetchall()
    waived = [r["item_id"] for r in rows if r["status"] == "waived"]
    unconfirmed = [r["item_id"] for r in rows if awaiting_confirmation(r)]
    assert waived and unconfirmed
    shown = {i["checklist_item_id"] for i in customer_checklist(conn, "WAL-ONB-0004")["items"]}
    browser = Browser(demo["portal"]).open_as("WAL-ONB-0004")
    page = browser.get("/checklist")[1]
    for item_id in waived + unconfirmed:
        assert item_id not in shown and item_id not in page
        assert "We have not asked you for this document" in refusal(
            conn, "WAL-ONB-0004", item_id, "x.pdf", PDF)
        assert "We have not asked you for this document" in words(
            browser.upload(item_id, "x.pdf", PDF))

    # once an analyst confirms the condition applies, the customer is asked for it
    data.confirm_condition(conn, unconfirmed[0], "analyst.test", True, "remote onboarding")
    item = next(i for i in customer_checklist(conn, "WAL-ONB-0004")["items"]
                if i["checklist_item_id"] == unconfirmed[0])
    assert item["status"] == "Not uploaded yet" and item["can_upload"]


def test_optional_items_are_labelled_and_not_counted(demo):
    cl = customer_checklist(demo["conn"], "WAL-ONB-0001")
    optional = [i for i in cl["items"] if i["optional"]]
    assert len(optional) == 1
    assert (cl["still_needed"], cl["total_needed"]) == (0, 6)
    page = Browser(demo["portal"]).open_as("WAL-ONB-0001").get("/checklist")[1]
    assert "0 of 6 still needed" in words(page)
    assert '<span class="item__optional">optional</span>' in row(
        page, optional[0]["checklist_item_id"])


def test_an_item_added_after_a_restricted_finding_shows_only_generic_wording(demo):
    conn = demo["conn"]
    owner = conn.execute(
        "SELECT i.individual_id, i.full_name FROM ubo u JOIN individual i USING (individual_id)"
        " JOIN onboarding_case c ON c.applicant_id = u.applicant_id"
        " WHERE c.case_id = 'WAL-ONB-0005'").fetchone()
    item_id = data.add_checklist_item(conn, "WAL-ONB-0005", "source_of_wealth_statement",
                                      "compliance.test", "EDD after the PEP match",
                                      owner["individual_id"])
    item = next(i for i in customer_checklist(conn, "WAL-ONB-0005")["items"]
                if i["checklist_item_id"] == item_id)
    assert item["document"] == GENERIC_DOCUMENT and item["person"] == ""
    assert item["status"] == "Not uploaded yet" and item["can_upload"]

    page = Browser(demo["portal"]).open_as("WAL-ONB-0005").get("/checklist")[1]
    card = row(page, item_id)
    assert "wealth" not in card.lower() and owner["full_name"] not in card
    assert not leaks(page)

    # the same request on a case with no restricted finding names the document
    other = data.add_checklist_item(conn, "WAL-ONB-0010", "source_of_wealth_statement",
                                    "analyst.test", "periodic refresh")
    named = next(i for i in customer_checklist(conn, "WAL-ONB-0010")["items"]
                 if i["checklist_item_id"] == other)
    assert named["document"] == DOCUMENT_LABELS["source_of_wealth_statement"]


def test_a_late_document_is_read_but_the_paid_checks_do_not_run_again(demo):
    conn = demo["conn"]
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE case_id = 'WAL-ONB-0010'")
              .fetchone()[0] for t in ("registry_check", "screening_check", "risk_assessment")}
    item_id = data.add_checklist_item(conn, "WAL-ONB-0010", "bank_statement",
                                      "analyst.test", "a recent statement")
    data.upload_document(conn, "WAL-ONB-0010", item_id, "statement.pdf", PDF)
    doc = conn.execute("SELECT MAX(document_id) FROM checklist_item_document WHERE item_id = ?",
                       (item_id,)).fetchone()[0]
    data.release_document(conn, doc, "analyst.test", "accept", "clear")
    for document_id, fields in data.awaiting_fields(conn, "WAL-ONB-0010").items():
        data.enter_fields(conn, document_id, "analyst.test",
                          {f["name"]: "synthetic" for f in fields})
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE case_id = 'WAL-ONB-0010'")
             .fetchone()[0] for t in before}
    assert after == before, "the providers' answers stand; they are not asked again"
    status = conn.execute("SELECT status FROM onboarding_case WHERE case_id = 'WAL-ONB-0010'"
                          ).fetchone()[0]
    assert status == "ready_for_decision", "back where its band put it"


def test_accepted_items_show_no_upload_and_resubmissions_always_a_reason(demo, finished):
    for conn in (demo["conn"], finished):
        for (case_id,) in conn.execute("SELECT case_id FROM onboarding_case"):
            for item in customer_checklist(conn, case_id)["items"]:
                if item["status"] == "Accepted":
                    assert not item["can_upload"], (case_id, item)
                if item["status"] == "Resubmission needed":
                    assert item["reason"], (case_id, item)
    page = Browser(demo["portal"]).open_as("WAL-ONB-0002").get("/checklist")[1]
    for item in customer_checklist(demo["conn"], "WAL-ONB-0002")["items"]:
        card = row(page, item["checklist_item_id"])
        if item["status"] == "Accepted":
            assert 'type="file"' not in card
        if item["status"] == "Resubmission needed":
            assert item["reason"] in words(card) and 'type="file"' in card


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

def test_case_ids_never_appear_in_portal_urls(demo):
    cases = demo["conn"].execute("SELECT case_id FROM onboarding_case").fetchall()
    for (case_id,) in cases:
        browser = Browser(demo["portal"]).open_as(case_id)
        for path in ("/", "/checklist", "/messages"):
            page = browser.get(path)[1]
            for url in re.findall(r'(?:href|action|src)="([^"]*)"', page):
                assert "WAL-" not in url, (case_id, path, url)
        assert not any("WAL-" in (loc or "") for loc in browser.locations)


def test_expired_wrong_and_other_case_tokens_are_refused(demo):
    conn = demo["conn"]
    expired = data.portal_access.issue(conn, "WAL-ONB-0002", "test", valid_days=0)
    conn.commit()
    browser = Browser(demo["portal"])
    assert browser.get("/access/" + expired)[0] == 401
    assert browser.get("/access/not-a-token-at-all")[0] == 401

    a = data.issue_portal_access(conn, "WAL-ONB-0002", "test")
    assert browser.get("/access/" + a)[0] == 303
    page = browser.get("/checklist")[1]
    assert "Northbridge Craft Supplies Ltd" in page and "Calderwick" not in page
    # case A's session cannot reach an item on case B
    b_item = customer_checklist(conn, "WAL-ONB-0014")["items"][0]["checklist_item_id"]
    assert "We have not asked you for this document" in words(
        browser.upload(b_item, "poa.pdf", PDF))


def test_copy_customer_link_in_the_console_opens_only_that_case(demo):
    console = Browser(demo["console"])
    status, where, _ = console.post("/action/customer-link", case_id="WAL-ONB-0002",
                                    back="/case/WAL-ONB-0002")
    assert status == 303 and "link=" in where
    page = console.get(where[len(demo["console"]):] if where.startswith("http") else where)[1]
    assert "Copy customer link" in page
    link = re.search(r'id="customer-link" readonly value="([^"]+)"', page).group(1)
    assert "/access/" in link and "WAL-" not in link
    assert "WAL-" not in where.split("link=")[1], "the console's own address carries no token"

    portal = Browser(demo["portal"])
    assert portal.get(urlparse(link).path)[0] == 303
    assert "Northbridge Craft Supplies Ltd" in portal.get("/")[1]
    # shown once: reloading the console page does not show it again
    assert 'id="customer-link"' not in console.get(where)[1]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_a_renamed_exe_and_a_file_over_the_limit_are_refused_in_plain_words(demo):
    conn = demo["conn"]
    item = item_like(customer_checklist(conn, "WAL-ONB-0002"),
                     "Declaration of beneficial owners")["checklist_item_id"]
    browser = Browser(demo["portal"]).open_as("WAL-ONB-0002")
    page = browser.upload(item, "invoice.pdf", EXE)
    assert "This file is not really a PDF" in words(page)

    big = b"%PDF-1.4\n" + b"0" * (document_quality.MAX_UPLOAD_BYTES + 1)
    assert "larger than 10 MB" in refusal(conn, "WAL-ONB-0002", item, "big.pdf", big)
    page = browser.upload(item, "big.pdf", big)
    assert "larger than 10 MB" in words(page)
    assert conn.execute("SELECT COUNT(*) FROM document WHERE case_id = 'WAL-ONB-0002'"
                        ).fetchone()[0] == 10, "nothing refused was stored"


def test_a_file_the_scanner_does_not_clear_is_refused(demo):
    class Scanner(VirusScanner):
        mode, name = "test", "test-scanner"

        def __init__(self, verdict):
            self.verdict = verdict

        def scan(self, content, file_name):
            return ScanResult(self.verdict, self.name)

    item = item_like(customer_checklist(demo["conn"], "WAL-ONB-0002"),
                     "Declaration of beneficial owners")["checklist_item_id"]
    assert "cannot accept this file" in refusal(demo["conn"], "WAL-ONB-0002", item, "u.pdf",
                                                PDF, scanner=Scanner("infected"))
    assert "could not check this file" in refusal(demo["conn"], "WAL-ONB-0002", item, "u.pdf",
                                                  PDF, scanner=Scanner("unavailable"))
    assert not list((demo["tmp"] / "uploads").rglob("*.pdf"))


def test_uploads_to_an_accepted_item_an_unrequested_item_or_a_closed_case_are_refused(
        demo, finished):
    conn = demo["conn"]
    accepted = next(i for i in customer_checklist(conn, "WAL-ONB-0002")["items"]
                    if i["status"] == "Accepted")
    assert "We already have this document" in refusal(
        conn, "WAL-ONB-0002", accepted["checklist_item_id"], "a.pdf", PDF)
    assert "We have not asked you for this document" in refusal(
        conn, "WAL-ONB-0002", "CHK-999999", "a.pdf", PDF)

    closed = customer_checklist(finished, "WAL-ONB-0014")
    assert not closed["open"]
    owed = next(i for i in closed["items"] if i["status"] == "Resubmission needed")
    assert not owed["can_upload"]
    assert "This application is closed" in refusal(
        finished, "WAL-ONB-0014", owed["checklist_item_id"], "poa.pdf", PDF)


# ---------------------------------------------------------------------------
# The journeys
# ---------------------------------------------------------------------------

def test_case_2_end_to_end_portal_upload_console_release_through_step_5(demo):
    """The customer sends a clear director ID and the ownership declaration; the
    analyst releases the visual-check hold in the console, types in the fields
    mock mode cannot read, and the case runs on through Step 5."""
    conn = demo["conn"]
    portal = Browser(demo["portal"]).open_as("WAL-ONB-0002")
    cl = customer_checklist(conn, "WAL-ONB-0002")
    director_id = item_like(cl, "Identity document", "Denton Halliwell")
    ubo = item_like(cl, "Declaration of beneficial owners")
    assert director_id["status"] == "Resubmission needed"
    assert director_id["previous_uploads"] == ["director_id_halliwell_scan.jpg"]
    page = portal.get("/checklist")[1]
    assert ("Previous upload: director_id_halliwell_scan.jpg (replaced when you upload a "
            "new one)") in words(row(page, director_id["checklist_item_id"]))

    for item, name, content in ((director_id, "halliwell_passport_clear.jpg", JPEG),
                                (ubo, "ownership_declaration.pdf", PDF)):
        after = portal.upload(item["checklist_item_id"], name, content)
        assert "now under review" in words(after)
        assert "Under review" in words(row(after, item["checklist_item_id"]))
    assert conn.execute("SELECT COUNT(*) FROM registry_check WHERE case_id = 'WAL-ONB-0002'"
                        ).fetchone()[0] == 0, "no paid check before a person has looked"

    console = Browser(demo["console"])
    docs = console.get("/case/WAL-ONB-0002?tab=Documents")[1]
    assert docs.count("uploaded by applicant") >= 2
    assert "superseded" in docs, "the replaced director ID stays in the history"
    held = conn.execute("SELECT document_id FROM document WHERE case_id = 'WAL-ONB-0002'"
                        " AND quality_status = 'manual_review_required'").fetchall()
    assert len(held) == 2
    for (document_id,) in held:     # the existing release, through the console
        status, _, _ = console.post("/action/release-document", document_id=document_id,
                                    choice="accept", reason="looked at it: clear",
                                    back="/case/WAL-ONB-0002?tab=Documents")
        assert status == 303
    names = conn.execute("SELECT full_name FROM individual i JOIN onboarding_case c"
                         " USING (applicant_id) WHERE c.case_id = 'WAL-ONB-0002'").fetchall()
    for document_id, fields in data.awaiting_fields(conn, "WAL-ONB-0002").items():
        values = {f["name"]: ("2031-01-01" if f["name"] == "expiry_date" else
                              "1980-01-01" if f["name"] == "date_of_birth" else
                              names[0][0] if "name" in f["name"] else "synthetic")
                  for f in fields}
        status, _, _ = console.post("/action/enter-fields", document_id=document_id,
                                    back="/case/WAL-ONB-0002?tab=Documents",
                                    **{"f_" + k: v for k, v in values.items()})
        assert status == 303

    assert conn.execute("SELECT COUNT(*) FROM registry_check WHERE case_id = 'WAL-ONB-0002'"
                        ).fetchone()[0] == 1, "Step 5 ran"
    assert conn.execute("SELECT COUNT(*) FROM screening_check WHERE case_id = 'WAL-ONB-0002'"
                        ).fetchone()[0] > 0, "and on through screening"
    final = customer_checklist(conn, "WAL-ONB-0002")
    assert final["still_needed"] == 0


def test_case_14_an_upload_stops_the_reminders(demo, tmp_path):
    conn = demo["conn"]
    later = communication.FakeClock(date.today() + timedelta(days=40))

    # Without an upload, forty silent days close the case (a separate copy).
    control_path = tmp_path / "control.db"
    data.reset_demo(control_path)
    control = data.connect(control_path)
    assert communication.chase(control, "WAL-ONB-0014", KB, later) is not None
    control.close()

    poa = item_like(customer_checklist(conn, "WAL-ONB-0014"), "Proof of address")
    page = Browser(demo["portal"]).open_as("WAL-ONB-0014").upload(
        poa["checklist_item_id"], "utility_bill_new.pdf", PDF)
    assert "now under review" in words(page)
    assert communication.chase(conn, "WAL-ONB-0014", KB, later) is None
    stopped = conn.execute("SELECT payload_summary FROM audit_event WHERE case_id ="
                           " 'WAL-ONB-0014' AND action = 'reminders_stopped'").fetchone()
    assert stopped and poa["checklist_item_id"] in stopped[0]
    assert conn.execute("SELECT status FROM onboarding_case WHERE case_id = 'WAL-ONB-0014'"
                        ).fetchone()[0] != "closed_withdrawn"


@pytest.mark.parametrize("case_id", ["WAL-ONB-0005", "WAL-ONB-0006", "WAL-ONB-0012"])
def test_the_documents_page_for_the_sensitive_cases_carries_no_restricted_wording(demo, case_id):
    page = Browser(demo["portal"]).open_as(case_id).get("/checklist")[1]
    assert not leaks(page), leaks(page)


def test_the_upload_page_works_with_javascript_disabled(demo):
    page = Browser(demo["portal"]).open_as("WAL-ONB-0002").get("/checklist")[1]
    forms = re.findall(r"<form[^>]*>", page)
    uploads = [f for f in forms if 'action="/upload"' in f]
    assert uploads and all('method="post"' in f and 'enctype="multipart/form-data"' in f
                           for f in uploads)
    assert page.count('type="file"') == len(uploads)
    assert not re.search(r"\son[a-z]+=", page), "no inline script"
    # the drag-and-drop hint promises nothing until the script shows it
    assert '<span class="dropzone__hint" hidden>' in page
    assert "Accepted files: PDF, JPG, PNG, up to 10 MB." in words(page)
    assert "Demo portal - do not upload real documents." in page


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
