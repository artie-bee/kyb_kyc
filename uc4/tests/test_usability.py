"""
Usability, strictly presentational: the customer's next-step card and tips, the
form's simulated company lookup, the console's "why is this case here" panel,
"My queue" and "Next case", the side-by-side review with A / C shortcuts, and
the customer status wording in the KB.

Nothing here may add a status, a rule or a permission: every action shown is an
existing route calling an existing function, allowed by the rules already in
force.

Run: python -m pytest tests/test_usability.py -q
"""

import csv
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
sys.path.insert(0, str(ROOT / "tests"))

from app import data                                                  # noqa: E402
from app.customer_view import (PLAIN_STATUS, STATUS_WORDING_KEY,      # noqa: E402
                               customer_checklist, customer_view, leaks, next_step)
from orchestrator import clock, db, demo_samples                      # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import decision, document_quality             # noqa: E402
from orchestrator.steps.communication import scan                     # noqa: E402
from portal import apply, server as portal_server                     # noqa: E402
from test_demo_case_checks import OTHER_COMPANY                       # noqa: E402
from tools import make_sample_documents as samples                    # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import render as console, server as console_server           # noqa: E402

KB = KnowledgeBase()
ROLES = ("analyst", "compliance")


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    return samples.build_demo_pack(tmp_path_factory.mktemp("pack"),
                                   today=clock.current().today())["folder"]


@pytest.fixture()
def conn(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(demo_samples, "MANIFEST", pack / "manifest.json")
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    c = data.connect(path)
    c.execute("PRAGMA busy_timeout = 5000")
    yield c, path
    c.close()


@pytest.fixture(scope="module")
def finished():
    """Every case run to its scripted end: 9 approved, 11 rejected, 14 closed."""
    c = db.connect(":memory:")
    run_demo(c, verbose=False)
    return c


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Browser:
    def __init__(self, base, cookies=None):
        self.base = base
        jar = CookieJar()
        self.o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                             NoRedirect())
        self.headers = {"Cookie": cookies} if cookies else {}

    def go(self, path, fields=None):
        body = urlencode(fields).encode() if fields is not None else None
        try:
            with self.o.open(urllib.request.Request(self.base + path, body, self.headers)) as r:
                return r.status, r.headers.get("Location"), r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location"), e.read().decode("utf-8")


def _serve(httpd):
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture()
def portal(conn):
    c, path = conn
    httpd = portal_server.serve(0, db_path=path, demo=True,
                                uploads_dir=document_quality.UPLOADS)
    yield c, _serve(httpd)
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def console_site(conn):
    c, path = conn
    console_server.drop_connection()
    console_server._conn = data.connect(path)
    httpd = console_server.serve(0)
    yield c, _serve(httpd)
    httpd.shutdown()
    httpd.server_close()
    console_server.drop_connection()


def label(i):
    return i["document"] + (" - " + i["person"] if i["person"] else "")


def step_for(c, case_id):
    return next_step(customer_checklist(c, case_id), customer_view(c, case_id))


# ---------------------------------------------------------------------------
# 1. The next-step card
# ---------------------------------------------------------------------------

def test_the_next_step_is_right_for_cases_1_2_6_and_14(conn):
    c, _ = conn
    nothing = "Nothing to do right now. We'll message you when there's an update."
    assert step_for(c, "WAL-ONB-0001")["text"] == nothing
    assert step_for(c, "WAL-ONB-0006")["text"] == nothing, "a sanctions case says nothing more"
    two = step_for(c, "WAL-ONB-0002")
    assert two["kind"] == "upload"
    assert two["text"] == ("Upload a clearer copy: Identity document (passport or ID card)"
                           " - Denton Halliwell")
    fourteen = step_for(c, "WAL-ONB-0014")
    assert fourteen["text"] == "Upload a clearer copy: Proof of address - Elise Kaarma"
    for case_id in ("WAL-ONB-0001", "WAL-ONB-0002", "WAL-ONB-0006", "WAL-ONB-0014"):
        assert not scan(step_for(c, case_id)["text"])


def test_the_next_step_follows_an_upload_and_an_analyst_action(conn, pack):
    c, _ = conn
    director = step_for(c, "WAL-ONB-0002")["item"]
    data.upload_document(c, "WAL-ONB-0002", director["checklist_item_id"], "id.jpg",
                         (pack / "08_identity_document_clear.jpg").read_bytes() + b"\n")
    after = step_for(c, "WAL-ONB-0002")
    assert after["text"] == "Upload your declaration of beneficial owners", after
    ubo = after["item"]
    data.upload_document(c, "WAL-ONB-0002", ubo["checklist_item_id"], "ubo.pdf",
                         (pack / "06_beneficial_owners_declaration.pdf").read_bytes() + b"\n")
    assert step_for(c, "WAL-ONB-0002")["kind"] == "nothing"

    # the analyst sends the ID back: the card asks for it again, with the reason
    held = c.execute("SELECT document_id FROM document WHERE case_id = 'WAL-ONB-0002' AND"
                     " document_type = 'id_document' AND quality_status ="
                     " 'manual_review_required'").fetchone()[0]
    data.release_document(c, held, "analyst.test", "request_resubmission", "cropped",
                          reason_code="document_incomplete")
    again = step_for(c, "WAL-ONB-0002")
    assert again["kind"] == "upload" and "Identity document" in again["text"]
    assert again["item"]["reason"]


def test_the_card_is_on_every_page_with_the_upload_inline(portal):
    c, base = portal
    b = Browser(base)
    assert b.go("/demo/open", {"case_id": "WAL-ONB-0002"})[0] == 303
    for path in ("/", "/checklist", "/messages"):
        page = b.go(path)[2]
        card = re.search(r'<section class="nextstep.*?</section>', page, re.S)
        assert card, path
        assert "Upload a clearer copy" in card.group(0)
        assert 'action="/upload"' in card.group(0) and 'type="file"' in card.group(0)
        assert not leaks(page)
    ids = re.findall(r'id="([^"]+)"', b.go("/checklist")[2])
    assert len(ids) == len(set(ids)), "the card's upload control does not clash with the row's"


# ---------------------------------------------------------------------------
# 2. Guidance
# ---------------------------------------------------------------------------

def test_every_document_type_has_two_or_three_distinct_tips():
    types = {r["document_type"] for r in KB.requirement_rules}
    assert set(KB.document_guidance) == types
    for t, tips in KB.document_guidance.items():
        assert 2 <= len(tips) <= 3, t
        assert len(set(tips)) == len(tips), f"duplicate tip for {t}"
        assert not any(scan(tip) for tip in tips), t


def test_no_tip_types_its_own_age_limit_and_shown_limits_equal_the_rule(finished):
    raw = (ROOT / "kb" / "document_guidance.csv").read_text(encoding="utf-8")
    assert not re.search(r"\d+\s*(day|month|week|year)", raw), \
        "an age limit is read from the requirement rule, never typed into a tip"
    rules = {r["rule_id"]: r for r in KB.requirement_rules}
    for (case_id,) in finished.execute("SELECT case_id FROM onboarding_case").fetchall():
        items = {r["item_id"]: r for r in finished.execute(
            "SELECT i.* FROM checklist_item i JOIN requirement_pack p USING (pack_id)"
            " WHERE p.case_id = ?", (case_id,))}
        for item in customer_checklist(finished, case_id)["items"]:
            rule = rules.get(items[item["checklist_item_id"]]["rule_id"], {})
            for tip in item["tips"]:
                for n in re.findall(r"(\d+) days", tip):
                    assert n == rule.get("max_age_days"), (case_id, tip, rule.get("rule_id"))


# ---------------------------------------------------------------------------
# 3. "Look up my company"
# ---------------------------------------------------------------------------

def test_the_lookup_is_labelled_simulated_and_leaves_the_fields_editable(portal):
    _, base = portal
    b = Browser(base)
    status, _, page = b.go("/apply", {"step": "1", "nav": "lookup",
                                      "registration_number": "EE-16550321"})
    assert status == 200
    assert "Simulated registry lookup" in page
    for name, value in (("legal_name", "Lumen Harbour OU"),
                        ("registered_address", "Narva mnt 7, 10117 Tallinn")):
        tag = re.search(r'<input type="text" name="' + name + r'"[^>]*>', page).group(0)
        assert f'value="{value}"' in tag and "readonly" not in tag and "disabled" not in tag
    missing = b.go("/apply", {"step": "1", "nav": "lookup", "registration_number": "XX-0"})[2]
    assert "Simulated registry lookup" in missing and "Nothing found" in missing


def test_documents_for_another_company_still_mismatch_after_a_lookup(portal, pack):
    c, base = portal
    b = Browser(base)
    answers = {key: value for _, _, key, value in samples.FORM_VALUES}
    page = b.go("/apply", {**answers, "legal_name": "", "registered_address": "",
                           "step": "1", "nav": "lookup"})[2]
    found = {n: re.search(r'name="' + n + r'" value="([^"]*)"', page).group(1)
             for n in ("legal_name", "registered_address")}
    data.submit_application(c, apply.build_application({**answers, **found}, "PORTAL-LOOKUP"))
    case_id = c.execute("SELECT MAX(case_id) FROM onboarding_case"
                        " WHERE case_id LIKE 'WAL-DEMO-%'").fetchone()[0]
    for item in customer_checklist(c, case_id)["items"]:
        if item["can_upload"] and not item["optional"]:
            data.upload_document(c, case_id, item["checklist_item_id"], "other.pdf",
                                 (pack / "01_certificate_of_incorporation.pdf").read_bytes()
                                 + b"\n")
    for (doc,) in c.execute("SELECT document_id FROM document WHERE case_id = ? AND"
                            " quality_status = 'manual_review_required'", (case_id,)).fetchall():
        data.release_document(c, doc, "analyst.test", "accept", "legible")
    for doc, fields in data.awaiting_fields(c, case_id).items():
        typed = {f["name"]: OTHER_COMPANY.get(f["name"], "synthetic" if f["required"] else "")
                 for f in fields}
        if "document_date" in typed:
            typed["document_date"] = clock.current().today().isoformat()
        data.enter_fields(c, doc, "analyst.test", typed)
    reg = c.execute("SELECT name_match, number_match, address_match FROM registry_check"
                    " WHERE case_id = ?", (case_id,)).fetchone()
    assert tuple(reg) == ("mismatch", "mismatch", "mismatch")


# ---------------------------------------------------------------------------
# 4-5. "Why is this case here?", My queue, Next case
# ---------------------------------------------------------------------------

def _can_act(c, case_id, role) -> bool:
    """Independently of data.queue(): may this role do anything on this case?"""
    status = c.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                       (case_id,)).fetchone()[0]
    if status in ("approved", "rejected", "closed_withdrawn"):
        return False
    rows = data.why_here(c, case_id, role)
    staff = [r for r in rows if r["owner"] != "customer"]
    if any(r["permitted"] for r in staff):
        return True
    assessed = c.execute("SELECT 1 FROM risk_assessment WHERE case_id = ?",
                         (case_id,)).fetchone()
    return (not staff and bool(assessed)
            and bool(decision.permitted_decisions(c, case_id, role, KB)))


@pytest.mark.parametrize("role", ROLES)
def test_the_queue_shows_only_cases_the_role_can_act_on(conn, finished, role):
    for c in (conn[0], finished):
        queued = [r["case_id"] for r in data.queue(c, role)]
        for (case_id,) in c.execute("SELECT case_id FROM onboarding_case").fetchall():
            assert (case_id in queued) == _can_act(c, case_id, role), (role, case_id)
    closed = [r["case_id"] for r in data.queue(finished, role)]
    for case_id in ("WAL-ONB-0009", "WAL-ONB-0011", "WAL-ONB-0014"):
        assert case_id not in closed, "a closed case needs nobody"
    if role == "analyst":
        assert "WAL-ONB-0006" not in [r["case_id"] for r in data.queue(conn[0], role)], \
            "only compliance may decide a possible sanctions match"


def test_the_queue_puts_critical_and_sanctions_cases_first(conn):
    order = [r["case_id"] for r in data.queue(conn[0], "compliance")]
    assert order[:2] == ["WAL-ONB-0012", "WAL-ONB-0006"]


@pytest.mark.parametrize("role", ROLES)
def test_next_case_only_ever_opens_an_eligible_case(console_site, role):
    c, base = console_site
    b = Browser(base, cookies=f"role={role}")
    eligible = [r["case_id"] for r in data.queue(c, role)]
    seen, current = [], None
    for _ in range(len(eligible) + 1):
        status, where, _ = b.go("/queue/next" + (f"?after={current}" if current else ""))
        assert status == 303 and where.startswith("/case/"), where
        current = where[len("/case/"):]
        assert current in eligible, (role, current)
        seen.append(current)
    assert set(seen) == set(eligible), "Next case walks the whole queue"
    for case_id in ("WAL-ONB-0002", "WAL-ONB-0014"):     # waiting on the customer
        status, where, _ = b.go(f"/queue/next?after={case_id}")
        assert where[len("/case/"):] in eligible


def test_why_here_shows_an_action_only_when_the_role_may_use_it(conn):
    c, _ = conn
    routes = (ROOT / "web" / "server.py").read_text(encoding="utf-8")
    shown = 0
    for (case_id,) in c.execute("SELECT case_id FROM onboarding_case").fetchall():
        for role in ROLES:
            page = console.case_detail(c, case_id, role=role)
            panel = re.search(r'<section class="why".*?</section>', page, re.S)
            rows = data.why_here(c, case_id, role)
            if not rows:
                assert panel is None
                continue
            buttons = re.findall(r'data-route="([^"]+)" data-function="([^"]+)"',
                                 panel.group(0))
            assert len(buttons) == sum(r["permitted"] for r in rows), (case_id, role)
            for route, function in buttons:
                assert f'path == "{route}"' in routes, f"{route} is not an existing route"
                assert callable(getattr(data, function, None)), f"data.{function} missing"
                shown += 1
    assert shown > 0
    # case 6: the analyst sees what needs doing, but no button to do it
    analyst = console.case_detail(c, "WAL-ONB-0006", role="analyst")
    compliance = console.case_detail(c, "WAL-ONB-0006", role="compliance")
    assert 'data-function="record_decision"' not in analyst.split('<section class="why"')[1]
    assert 'data-function="record_decision"' in compliance.split('<section class="why"')[1]


# ---------------------------------------------------------------------------
# 6. The review area and its shortcuts
# ---------------------------------------------------------------------------

def test_shortcuts_press_the_same_buttons_and_ignore_typing():
    js = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
    block = js[js.index("review shortcuts"):js.index("case picker")]
    assert "INPUT|TEXTAREA|SELECT" in block and "isContentEditable" in block
    assert 'closest("[data-review]")' in block, "only inside a review area"
    assert "btn.click()" in block and "fetch(" not in block, "it presses the existing button"
    assert "ctrlKey" in block and "metaKey" in block


def test_the_review_area_is_side_by_side_with_the_shortcut_buttons_marked(conn):
    c, _ = conn
    page = console.case_detail(c, "WAL-ONB-0004", tab="Documents")
    for block in re.findall(r"<details.*?</details>", page, re.S):
        marked = re.findall(r"<button[^>]*data-shortcut=\"([ac])\"[^>]*>", block)
        if not marked:
            continue
        assert "A: Accept &middot; C: Correct" in block
        assert "data-review" in block.split(">", 1)[0]
        grid = block[block.index('class="grid2"'):]
        assert grid.index('class="filecard"') < grid.index("data-shortcut")
        for form in re.findall(r"<form[^>]*>.*?</form>", block, re.S):
            for tag in re.findall(r"<button[^>]*data-shortcut=\"[ac]\"[^>]*>", form):
                action = re.search(r'action="([^"]+)"', form).group(1)
                assert action in ("/action/release-document", "/action/field")
                if 'data-shortcut="c"' in tag:
                    assert 'value="correct"' in tag
                else:
                    assert 'value="accept"' in tag
    assert "fieldrow--low" in page, "case 4's faint values are highlighted"


def test_a_replacement_shows_the_old_upload_beside_the_new(conn, pack):
    c, _ = conn
    item = step_for(c, "WAL-ONB-0002")["item"]
    data.upload_document(c, "WAL-ONB-0002", item["checklist_item_id"], "id.jpg",
                         (pack / "08_identity_document_clear.jpg").read_bytes() + b"\n")
    page = console.case_detail(c, "WAL-ONB-0002", tab="Documents")
    compare = re.findall(r'<div class="compare">.*?Replaced \((DOC-\d+)\)', page, re.S)
    assert compare, "the replaced director scan is shown beside the new one"


# ---------------------------------------------------------------------------
# 7. The customer wording
# ---------------------------------------------------------------------------

def test_the_wording_file_holds_no_internal_status_name():
    rows = list(csv.DictReader(open(ROOT / "kb" / "customer_status_wording.csv",
                                    encoding="utf-8")))
    raw = (ROOT / "kb" / "customer_status_wording.csv").read_text(encoding="utf-8").lower()
    statuses = set(STATUS_WORDING_KEY) | {"insufficient_evidence", "white_label"}
    for name in statuses:
        assert name not in raw, f"the wording file names the internal status {name!r}"
        assert name.replace("_", " ") not in raw, name
    for r in rows:
        assert not scan(r["customer_wording"]), r
    assert set(STATUS_WORDING_KEY.values()) <= {r["wording_key"] for r in rows}


def test_both_front_ends_read_the_same_wording(conn):
    c, _ = conn
    for (case_id,) in c.execute("SELECT case_id FROM onboarding_case").fetchall():
        status = c.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                           (case_id,)).fetchone()[0]
        view = customer_view(c, case_id)
        if not view["partner"]:
            assert view["status_text"] == KB.customer_status_wording[STATUS_WORDING_KEY[status]]
        assert view["status_text"] in console.customer(c, case_id)
    assert PLAIN_STATUS["enhanced_due_diligence"] == PLAIN_STATUS["analyst_review_required"]


def test_my_queue_can_be_viewed_as_either_role_through_the_existing_settings_route(
        console_site):
    """The queue's "View as" only changes whose queue is shown, through the
    existing /action/settings route; every action still checks the role."""
    c, base = console_site
    b = Browser(base)
    page = b.go("/queue")[2]
    form = re.search(r'<form method="post" action="/action/settings".*?</form>', page, re.S)
    assert form and 'name="role"' in form.group(0)
    status, where, _ = b.go("/action/settings", {"role": "compliance", "back": "/queue"})
    assert (status, where) == (303, "/queue")
    listed = re.findall(r'<td><a href="/case/(WAL-ONB-\d+)">', b.go("/queue")[2])
    assert listed[:2] == ["WAL-ONB-0012", "WAL-ONB-0006"]
