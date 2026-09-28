"""
The customer portal: one test per customer journey, plus the scans that keep it
honest.

Served on a real socket against a real database, like tests/test_web.py,
because the things worth protecting are the ones a unit test cannot see: that
an upload is a plain form post that works with no script, that a POST
redirects, that the portal answers none of the console's routes, and that every
page a customer receives - not just the view dict behind it - carries nothing
internal.

Two databases:
  journeys   every case run to its scripted end (tools/run_demo.py), so the
             decisions and messages the journeys look for have happened
  demo       the demo start state (app.data.reset_demo), for the uploads

Run: python -m pytest tests/test_portal.py -q
"""

import re
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode

import pytest

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import (DOCUMENT_LABELS, REVIEW_LINE, leaks,   # noqa: E402
                               visible_text)
from orchestrator import db                                           # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from portal import render, server                                     # noqa: E402
from tools.run_demo import run                                        # noqa: E402

PORTAL = ROOT / "portal"
WRITE_SQL = re.compile(
    r"""["'][^"']*\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE
        |CREATE\s+TABLE|ALTER\s+TABLE|REPLACE\s+INTO)\b""",
    re.IGNORECASE | re.VERBOSE)

# Words that would tell a customer how their case is being assessed. Wider than
# the restricted-wording list: "hold", "analyst" and "compliance" are not
# forbidden in a message, but none of them belongs on a customer screen.
INTERNAL = ("sanction", "screening", "pep", "politically", "adverse", "media", "risk",
            "score", "band", "hold", "finding", "compliance", "analyst", "escalat",
            "due diligence", "watchlist", "insufficient", "registry", "dissolved",
            "resubmission_required", "analyst_review_required", "enhanced_due_diligence",
            "ready_for_decision", "closed_withdrawn")
PAGES = ("/", "/checklist", "/messages")
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4        # a JPEG header, then binary


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _start(db_path, uploads, demo=True):
    httpd = server.serve(0, db_path=db_path, demo=demo, uploads_dir=uploads)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture(scope="module")
def journeys(tmp_path_factory):
    """Every case at its scripted end."""
    folder = tmp_path_factory.mktemp("portal_journeys")
    path = folder / "onboarding.db"
    conn = db.connect(path)
    run(conn, verbose=False)
    conn.commit()
    conn.close()
    httpd, base = _start(path, folder / "uploads")
    yield {"base": base, "db": path, "uploads": folder / "uploads"}
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def demo(tmp_path):
    """The demo start state, fresh for each test that uploads."""
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    httpd, base = _start(path, tmp_path / "uploads")
    yield {"base": base, "db": path, "uploads": tmp_path / "uploads"}
    httpd.shutdown()
    httpd.server_close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Customer:
    """One browser with JavaScript off: plain GETs and plain form posts."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()), NoRedirect())

    def _open(self, req):
        try:
            with self.opener.open(req) as r:
                return r.status, r.headers, r.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read().decode("utf-8")

    def get(self, path):
        status, _, body = self._open(self.base + path)
        return status, body

    def post(self, path, headers=None, **fields):
        req = urllib.request.Request(self.base + path, urlencode(fields).encode(),
                                     headers=headers or {})
        status, head, _ = self._open(req)
        return status, head.get("Location")

    def upload(self, item_id, file_name, content):
        boundary = "----portaltestboundary7MA4YWxk"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"item_id\"\r\n\r\n"
                f"{item_id}\r\n--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"file\"; filename=\"{file_name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + content + \
            f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            self.base + "/upload", body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        status, head, _ = self._open(req)
        return status, head.get("Location")

    def open_as(self, case_id):
        status, where = self.post("/demo/open", case_id=case_id)
        assert (status, where) == (303, "/"), f"could not open the portal as {case_id}"
        return self


def words(page: str) -> str:
    return " ".join(visible_text(page).split())


def internal_words(page: str) -> list[str]:
    """Internal vocabulary in what the portal itself says. File names in the
    upload history are left out: "registry_extract_ardenhall.pdf" is what the
    customer called their own file, not something the portal told them. (They
    still go through leaks() with the rest of the page.)"""
    page = re.sub(r'<span class="hist__file">.*?</span>', " ", page, flags=re.S)
    text = words(page).lower()
    return [w for w in INTERNAL if re.search(rf"(?<![a-z]){re.escape(w)}", text)]


def item_block(page: str, label: str) -> str:
    """The visible text of the checklist card whose document name is `label`."""
    for block in re.findall(r'<li class="item[ "].*?</li>\s*(?=<li class="item[ "]|</ul>)',
                            page, re.S):
        if label in block:
            return words(block)
    raise AssertionError(f"no checklist card for {label!r}")


def all_pages(customer):
    out = {}
    for path in PAGES:
        status, page = customer.get(path)
        assert status == 200, f"{path} returned {status}"
        out[path] = page
    return out


# ---------------------------------------------------------------------------
# The journeys
# ---------------------------------------------------------------------------

def test_case_2_id_needs_resubmission_and_the_ubo_declaration_is_not_uploaded(journeys):
    pages = all_pages(Customer(journeys["base"]).open_as("WAL-ONB-0002"))
    checklist = pages["/checklist"]

    director_id = item_block(checklist, "Denton Halliwell")
    assert "Identity document" in director_id and "Resubmission needed" in director_id
    # the reason, in words the customer can act on - and the upload to act with
    assert "could not read this document clearly" in director_id
    assert "Upload a new copy" in director_id

    ubo = item_block(checklist, "Declaration of beneficial owners")
    assert "Not yet uploaded" in ubo and "Send file" in ubo

    steps = words(pages["/"])
    assert "We need 2 documents from you" in steps
    assert "resubmission_required" not in checklist + pages["/"]


@pytest.mark.parametrize("case_id", ["WAL-ONB-0005", "WAL-ONB-0006", "WAL-ONB-0012"])
def test_the_pep_and_sanctions_cases_see_only_neutral_additional_review_wording(
        journeys, case_id):
    """Case 5 is a PEP match, 6 a possible sanctions match, 12 a confirmed one.
    Each applicant is told there is an additional review step, and nothing more."""
    pages = all_pages(Customer(journeys["base"]).open_as(case_id))
    assert "additional review" in words(pages["/"])
    for path, page in pages.items():
        assert not internal_words(page), f"{case_id} {path} shows {internal_words(page)}"
        assert not leaks(page), f"{case_id} {path} carries {leaks(page)}"
    assert "additional" in words(pages["/messages"]) or "additional" in words(pages["/"])


def test_every_kind_of_review_reads_exactly_the_same(journeys):
    """Wording that differed by reason would itself be the leak."""
    lines = {}
    for case_id in ("WAL-ONB-0003", "WAL-ONB-0005", "WAL-ONB-0006", "WAL-ONB-0012",
                    "WAL-ONB-0013"):
        page = Customer(journeys["base"]).open_as(case_id).get("/")[1]
        step4 = re.search(r"Verification and review.*?<p class=\"step__line\">(.*?)</p>",
                          page, re.S).group(1)
        lines[case_id] = step4
    assert set(lines.values()) == {REVIEW_LINE}, lines


def test_case_8_uses_a_separate_partner_onboarding_process(journeys):
    pages = all_pages(Customer(journeys["base"]).open_as("WAL-ONB-0008"))
    for path in ("/", "/checklist"):
        assert "This application uses a separate partner onboarding process" in \
            words(pages[path]), path
    assert 'action="/upload"' not in pages["/checklist"], "a partner uploads elsewhere"
    assert 'class="steps"' not in pages["/"], "the onboarding steps are not this journey"


def test_case_11_sees_the_decline_and_no_risk_or_registry_detail(journeys):
    pages = all_pages(Customer(journeys["base"]).open_as("WAL-ONB-0011"))
    assert "We are not able to open an account at this time" in words(pages["/"])
    assert "not able to open an account" in words(pages["/messages"])
    for path, page in pages.items():
        assert not internal_words(page), f"case 11 {path} shows {internal_words(page)}"
    assert 'action="/upload"' not in pages["/checklist"]


def test_case_13_is_told_further_review_is_needed_and_shown_no_score(journeys):
    pages = all_pages(Customer(journeys["base"]).open_as("WAL-ONB-0013"))
    assert "Further review is needed" in words(pages["/"])
    for path, page in pages.items():
        assert not internal_words(page), f"case 13 {path} shows {internal_words(page)}"


def test_case_14_sees_the_request_then_the_reminder_then_the_closure(journeys):
    pages = all_pages(Customer(journeys["base"]).open_as("WAL-ONB-0014"))
    sent = re.findall(r'<li class="msg">(.*?)</li>', pages["/messages"], re.S)
    assert len(sent) == 3, f"expected request, reminder, closure; got {len(sent)}"
    request, reminder, closure = (words(m) for m in sent)
    assert "we need the following items" in request
    assert "Please upload them in the onboarding portal" in request
    assert "we have closed your application" not in reminder
    assert "we have closed your application" in closure

    assert "closed" in words(pages["/"]).lower()
    assert 'action="/upload"' not in pages["/checklist"], "a closed case takes no files"


def test_every_page_of_every_case_passes_the_leak_scan(journeys):
    conn = db.connect(journeys["db"])
    cases = [r[0] for r in conn.execute("SELECT case_id FROM onboarding_case ORDER BY 1")]
    conn.close()
    assert len(cases) == 14
    for case_id in cases:
        for path, page in all_pages(Customer(journeys["base"]).open_as(case_id)).items():
            assert not leaks(page), f"{case_id} {path}: {leaks(page)}"
            for status in ("analyst_review_required", "enhanced_due_diligence",
                           "resubmission_required", "ready_for_decision"):
                assert status not in page, f"{case_id} {path} shows the status {status}"


# ---------------------------------------------------------------------------
# Uploads, with JavaScript off
# ---------------------------------------------------------------------------

def _item_id(page, label):
    for block in re.findall(r'<li class="item[ "].*?</li>', page, re.S):
        if label in block:
            return re.search(r'name="item_id" value="([^"]+)"', block).group(1)
    raise AssertionError(f"no upload form for {label!r}")


def test_an_upload_is_a_plain_form_post_and_reaches_the_console(demo):
    """The whole journey with no script: a multipart post, a 303, the new file
    on the checklist, the old one marked replaced - and the file waiting in the
    database for an analyst, where the console reads it."""
    customer = Customer(demo["base"]).open_as("WAL-ONB-0002")
    page = customer.get("/checklist")[1]
    assert 'enctype="multipart/form-data"' in page and 'method="post"' in page

    item = _item_id(page, "Denton Halliwell")
    status, where = customer.upload(item, "new_passport.jpg", JPEG)
    assert status == 303 and where.startswith("/checklist?m=")

    after = customer.get(where)[1]
    assert "We have received your file" in words(after)
    card = item_block(after, "Denton Halliwell")
    assert "Under review" in card, "an unscripted upload in mock mode waits for a person"
    assert "Replaced by a newer upload" in card and "Upload history (2)" in card
    assert "Send file" not in card, "nothing more to upload while it is being looked at"

    conn = data.connect(demo["db"])
    doc = conn.execute("SELECT * FROM document WHERE case_id = 'WAL-ONB-0002'"
                       " ORDER BY document_id DESC LIMIT 1").fetchone()
    assert doc["file_name"].startswith("new_passport__")
    assert doc["quality_status"] == "manual_review_required"
    stored = demo["uploads"] / "WAL-ONB-0002" / doc["file_name"]
    assert stored.read_bytes() == JPEG, "the file must arrive byte for byte"
    actions = [r["action"] for r in conn.execute(
        "SELECT action FROM audit_event WHERE case_id = 'WAL-ONB-0002'")]
    assert "document_uploaded" in actions and "portal_access_issued" in actions
    assert any(h.owner == "analyst" for h in data.open_holds(conn, "WAL-ONB-0002"))
    conn.close()


def test_a_file_that_could_never_pass_is_refused_at_the_door(demo):
    customer = Customer(demo["base"]).open_as("WAL-ONB-0002")
    item = _item_id(customer.get("/checklist")[1], "Declaration of beneficial owners")
    conn = data.connect(demo["db"])
    before = conn.execute("SELECT COUNT(*) FROM document").fetchone()[0]
    conn.close()

    for name, content, says in (
            ("renamed.pdf", JPEG, "does not look like a PDF"),
            ("notes.docx", b"PK\x03\x04", "We accept PDF, JPG, PNG files only"),
            ("empty.pdf", b"", "The file was empty")):
        status, where = customer.upload(item, name, content)
        assert status == 303
        assert says in words(customer.get(where)[1]), name

    conn = data.connect(demo["db"])
    assert conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == before
    refused = conn.execute("SELECT COUNT(*) FROM audit_event WHERE action ="
                           " 'portal_upload_refused'").fetchone()[0]
    assert refused == 3
    conn.close()


def test_a_customer_cannot_upload_to_someone_elses_case_or_after_the_checks(demo):
    other = Customer(demo["base"]).open_as("WAL-ONB-0014")
    foreign = _item_id(other.get("/checklist")[1], "Proof of address")

    customer = Customer(demo["base"]).open_as("WAL-ONB-0002")
    status, where = customer.upload(foreign, "poa.pdf", b"%PDF-1.4\n")
    assert "That item is not on your checklist" in words(customer.get(where)[1])

    # Case 1 has been through the paid checks; its documents are no longer taken.
    first = Customer(demo["base"]).open_as("WAL-ONB-0001")
    page = first.get("/checklist")[1]
    assert 'action="/upload"' not in page
    assert "Your documents are with our onboarding team" in words(page)


def test_the_pages_do_not_need_javascript(journeys):
    customer = Customer(journeys["base"]).open_as("WAL-ONB-0002")
    for path, page in all_pages(customer).items():
        scripts = re.findall(r"<script[^>]*>", page)
        assert scripts == ['<script src="/static/portal.js" defer>'], (path, scripts)
        assert not re.search(r"\son[a-z]+=", page), f"{path} has an inline event handler"
        assert "javascript:" not in page
        for form in re.findall(r"<form[^>]*>", page):
            assert 'method="post"' in form, f"{path}: {form}"
    # the one button that needs the script is hidden until the script shows it
    assert '<button class="btn btn--quiet" type="button" id="theme" hidden>' in page


# ---------------------------------------------------------------------------
# The scans: no SQL writes, no analyst routes
# ---------------------------------------------------------------------------

def test_the_portal_contains_no_sql_writes():
    offenders = [f"{p.relative_to(ROOT)}:{n}: {line.strip()}"
                 for p in sorted(PORTAL.rglob("*.py"))
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                 if WRITE_SQL.search(line)]
    assert not offenders, "the portal writes to the database:\n  " + "\n  ".join(offenders)


def test_the_portal_imports_and_serves_no_analyst_route(journeys):
    for path in sorted(PORTAL.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+web\b", source, re.M), \
            f"{path.name} imports the console"
        for route in ("/action/", "/case/", "/export/", "/reuse", "/customer/", "/doc/"):
            assert route not in source, f"{path.name} mentions the console route {route}"

    customer = Customer(journeys["base"]).open_as("WAL-ONB-0001")
    for path in ("/case/WAL-ONB-0001", "/export/WAL-ONB-0001", "/export/WAL-ONB-0001.json",
                 "/reuse", "/customer/WAL-ONB-0001", "/doc/DOC-0001", "/static/app.css",
                 "/static/app.js", "/static/../../web/static/app.css"):
        assert customer.get(path)[0] == 404, path
    for path in ("/action/record-decision", "/action/reset", "/action/release-document"):
        assert customer.post(path, case_id="WAL-ONB-0001")[0] == 404, path


def test_the_banner_is_on_every_page(journeys):
    customer = Customer(journeys["base"])
    assert render.BANNER == "Demo portal - do not upload real documents."
    assert render.BANNER in customer.get("/demo")[1]
    assert render.BANNER in customer.get("/nowhere")[1]
    assert render.BANNER in customer.get("/checklist")[1], "the signed-out page too"
    customer.open_as("WAL-ONB-0009")
    for path, page in all_pages(customer).items():
        assert render.BANNER in page, path


# ---------------------------------------------------------------------------
# Access, and the page guard
# ---------------------------------------------------------------------------

def test_the_demo_selector_opens_all_14_customers(journeys):
    page = Customer(journeys["base"]).get("/demo")[1]
    assert page.count("Open as this customer") == 14


def test_without_the_demo_build_there_is_no_selector_only_access_links(tmp_path, journeys):
    httpd, base = _start(journeys["db"], tmp_path / "uploads", demo=False)
    try:
        customer = Customer(base)
        assert customer.get("/demo")[0] == 404
        assert customer.post("/demo/open", case_id="WAL-ONB-0001")[0] == 404
        assert customer.get("/")[0] == 401

        conn = data.connect(journeys["db"])
        token = data.issue_portal_access(conn, "WAL-ONB-0009", "test.email_link")
        conn.close()
        assert customer.get("/access/not-a-real-token")[0] == 401
        status, where = customer.post("/signout")
        assert customer.get("/access/" + token)[0] == 303
        status, page = customer.get("/")
        assert status == 200 and "Parnu Kohviubade" in page
        # signing out ends the token for good
        customer.post("/signout")
        assert customer.get("/access/" + token)[0] == 401
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_only_the_hash_of_a_token_is_stored(journeys):
    conn = data.connect(journeys["db"])
    token = data.issue_portal_access(conn, "WAL-ONB-0001", "test")
    stored = [r[0] for r in conn.execute("SELECT token_hash FROM portal_token")]
    conn.close()
    assert token not in stored and len(stored[-1]) == 64


def test_a_link_cannot_put_its_own_words_on_the_page(journeys):
    customer = Customer(journeys["base"]).open_as("WAL-ONB-0001")
    page = customer.get("/checklist?m=nope&err=Your+account+is+blocked&ok=Call+us")[1]
    assert "blocked" not in page and "Call us" not in page


def test_a_post_from_another_site_is_refused(journeys):
    customer = Customer(journeys["base"])
    for origin in ("http://evil.example", "null"):
        status, _ = customer.post("/demo/open", headers={"Origin": origin},
                                  case_id="WAL-ONB-0001")
        assert status == 403, origin


def test_the_portals_own_forms_carry_an_origin_it_accepts(journeys):
    """A browser posts the portal's own origin only if the referrer policy lets
    it. Under no-referrer it sends "Origin: null" instead, and every form on the
    portal was refused as cross-site - which is what a real browser showed."""
    base = journeys["base"]
    with urllib.request.urlopen(base + "/demo") as r:
        policy = r.headers.get("Referrer-Policy")
    assert policy == "same-origin", policy

    host = base.split("//", 1)[1]
    for origin in ("http://" + host, "http://localhost:" + host.rsplit(":", 1)[1]):
        status, where = Customer(base).post(
            "/demo/open", headers={"Origin": origin, "Host": origin.split("//", 1)[1]},
            case_id="WAL-ONB-0001")
        assert (status, where) == (303, "/"), origin


def test_a_page_carrying_restricted_wording_is_refused_not_shown(journeys, monkeypatch):
    monkeypatch.setattr(render, "application",
                        lambda view: "<p>A sanctions screening match was found.</p>")
    status, page = Customer(journeys["base"]).open_as("WAL-ONB-0001").get("/")
    assert status == 500
    assert "sanctions" not in page.lower() and "This page is not available" in page


# ---------------------------------------------------------------------------
# The phrase book and the shared styles
# ---------------------------------------------------------------------------

def test_every_document_the_kb_can_ask_for_has_a_customer_name():
    asked = {r["document_type"] for r in KnowledgeBase().requirement_rules}
    missing = sorted(asked - set(DOCUMENT_LABELS))
    assert not missing, f"no customer-facing name for {missing}"
    for label in DOCUMENT_LABELS.values():
        assert "_" not in label and not internal_words(label), label


def test_the_shared_styles_match_the_console():
    """common.css is a copy, so it must stay a faithful one."""
    def tokens(css):
        return set(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", css))
    console = tokens((ROOT / "web" / "static" / "app.css").read_text(encoding="utf-8"))
    common = tokens((PORTAL / "static" / "common.css").read_text(encoding="utf-8"))
    assert common, "common.css declares no tokens"
    drifted = sorted(common - console)
    assert not drifted, f"common.css tokens no longer match the console: {drifted}"
    names = {n for n, _ in common}
    for needed in ("--fg", "--bg", "--surface", "--accent", "--ok", "--warn", "--sans"):
        assert needed in names


def test_multipart_parsing_is_byte_exact():
    content = bytes(range(256)) * 3 + b"\r\n--notaboundary\r\n\r\n"
    body = (b"--XyZ\r\nContent-Disposition: form-data; name=\"item_id\"\r\n\r\nCHK-1\r\n"
            b"--XyZ\r\nContent-Disposition: form-data; name=\"file\"; "
            b"filename=\"C:\\\\Users\\\\me\\\\scan.png\"\r\n\r\n" + content + b"\r\n--XyZ--\r\n")
    fields, files = server.parse_multipart("multipart/form-data; boundary=XyZ", body)
    assert fields == {"item_id": "CHK-1"}
    assert files["file"] == ("scan.png", content)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
