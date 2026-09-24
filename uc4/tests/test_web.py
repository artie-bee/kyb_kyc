"""
The HTML console: every screen renders, and every action goes through the rules.

The server is started on a real socket against a real database, because the
things worth protecting here are the ones a unit test cannot see - that a POST
redirects instead of rendering, that a refusal reaches the screen in the
backend's own words, and that a refresh cannot replay an action.

The database is a copy, rebuilt once for this module, so a test that records a
decision cannot change what another test sees.

Run: python -m pytest tests/test_web.py -q
"""

import re
import shutil
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
from web import render, server                                        # noqa: E402


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """A server on a real port, over its own copy of the demo database."""
    if not data.DB_PATH.exists():
        data.reset_demo()
    copy = tmp_path_factory.mktemp("web") / "onboarding.db"
    shutil.copy(data.DB_PATH, copy)

    server.drop_connection()
    server._conn = data.connect(copy)

    httpd = server.serve(0)                       # 0 = any free port
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()),
        NoRedirect())
    yield f"http://127.0.0.1:{port}", opener

    httpd.shutdown()
    httpd.server_close()
    server.drop_connection()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 303 is the thing under test, so it must not be followed away."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get(site, path):
    base, opener = site
    try:
        with opener.open(base + path) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8")


def post(site, path, **fields):
    """Returns (status, location). A POST must never return a page."""
    base, opener = site
    body = urlencode(fields).encode("utf-8")
    try:
        with opener.open(base + path, data=body) as r:
            return r.status, r.headers.get("Location")
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Location")


def flash(location):
    """The outcome the redirect carries back, unquoted."""
    from urllib.parse import parse_qs, unquote, urlparse
    q = parse_qs(urlparse(location).query)
    if "ok" in q:
        return "ok", unquote(q["ok"][0])
    if "err" in q:
        return "err", unquote(q["err"][0])
    return None, ""


# ---------------------------------------------------------------------------
# Every screen renders
# ---------------------------------------------------------------------------

def test_the_dashboard_lists_every_case(site):
    status, html = get(site, "/")
    assert status == 200
    assert "Operations dashboard" in html
    for case_id in ("WAL-ONB-0001", "WAL-ONB-0008", "WAL-ONB-0014"):
        assert case_id in html
    assert html.count('class="rowlink"') == 14, "one clickable row per case"


@pytest.mark.parametrize("path", ["/", "/reuse", "/case/WAL-ONB-0004",
                                  "/customer/WAL-ONB-0006", "/export/WAL-ONB-0006"])
def test_every_screen_renders(site, path):
    status, html = get(site, path)
    assert status == 200, path
    assert "did not render" not in html, path
    assert html.rstrip().endswith("</html>")


@pytest.mark.parametrize("tab", render.TABS)
def test_every_case_detail_tab_renders(site, tab):
    status, html = get(site, "/case/WAL-ONB-0004?tab=" + tab)
    assert status == 200
    assert "did not render" not in html
    assert 'class="tab tab--on"' in html


def test_an_unknown_case_is_a_404_not_a_crash(site):
    status, html = get(site, "/case/WAL-ONB-9999")
    assert status == 404
    assert "Not found" in html


def test_an_unknown_path_is_a_404(site):
    status, _ = get(site, "/nonsense")
    assert status == 404


def test_the_stylesheet_and_script_are_served(site):
    for path, needle in (("/static/app.css", "--accent"),
                         ("/static/app.js", "rowlink")):
        status, body = get(site, path)
        assert status == 200 and needle in body


def test_static_paths_cannot_escape_the_static_folder(site):
    status, _ = get(site, "/static/../server.py")
    assert status in (403, 404), "path traversal must not serve source"


# ---------------------------------------------------------------------------
# What the screens must and must not say
# ---------------------------------------------------------------------------

def test_the_holds_banner_names_each_hold_separately(site):
    _, html = get(site, "/case/WAL-ONB-0004")
    assert "Open holds" in html
    assert html.count('class="hold"') >= 2, "two holds read as two locks"


def test_the_customer_view_leaks_nothing_on_a_sanctions_case(site):
    _, html = get(site, "/customer/WAL-ONB-0006")
    body = html.split('<main class="main">', 1)[-1].lower()
    for word in ("sanction", "screening", "pep", "adverse", "critical", "risk band"):
        assert word not in body, f"{word!r} reached the customer view"


def test_the_decision_tab_says_what_the_band_withholds(site):
    _, html = get(site, "/case/WAL-ONB-0006?tab=Decision")
    assert "Not offered at band" in html
    assert "approve" in html, "the withheld decision is named, not just hidden"


def test_the_white_label_case_shows_its_future_phase_and_scope(site):
    _, html = get(site, "/case/WAL-ONB-0008")
    assert "Future phase" in html
    assert "undetermined" in html
    _, other = get(site, "/case/WAL-ONB-0001")
    assert "Future phase" not in other, "only the white-label case shows it"


def test_the_export_bundle_downloads_as_json(site):
    status, body = get(site, "/export/WAL-ONB-0006.json")
    assert status == 200
    import json
    bundle = json.loads(body)
    assert bundle["case_id"] == "WAL-ONB-0006"
    assert bundle["audit_trail"] and bundle["kb_version"]


# ---------------------------------------------------------------------------
# Actions: through the orchestrator, or not at all
# ---------------------------------------------------------------------------

def test_a_post_redirects_so_a_refresh_cannot_replay_it(site):
    status, location = post(site, "/action/send-message",
                            case_id="WAL-ONB-0002", situation="resubmission",
                            back="/case/WAL-ONB-0002?tab=Communications")
    assert status == 303, "a POST must never render a page"
    assert location and location.startswith("/case/WAL-ONB-0002")
    kind, text = flash(location)
    assert kind == "ok" and "COM-" in text


def test_the_backends_refusal_reaches_the_screen_in_its_own_words(site):
    """Case 6 is critical; escalate is compliance's to take."""
    post(site, "/action/settings", role="analyst", reviewer="analyst.demo", back="/")
    status, location = post(site, "/action/record-decision",
                            case_id="WAL-ONB-0006", choice="escalate",
                            reason_code="demo", rationale="trying it",
                            escalation_target="sanctions.desk",
                            back="/case/WAL-ONB-0006?tab=Decision")
    assert status == 303
    kind, text = flash(location)
    assert kind == "err"
    assert "compliance" in text and "critical" in text, \
        "the refusal is the backend's sentence, not one the server invented"


def test_a_role_the_taxonomy_allows_is_accepted(site):
    post(site, "/action/settings", role="compliance",
         reviewer="compliance.demo", back="/")
    status, location = post(site, "/action/record-decision",
                            case_id="WAL-ONB-0006", choice="escalate",
                            reason_code="demo", rationale="to the sanctions desk",
                            escalation_target="sanctions.desk",
                            back="/case/WAL-ONB-0006?tab=Decision")
    kind, text = flash(location)
    assert kind == "ok" and "escalate" in text
    _, html = get(site, "/case/WAL-ONB-0006?tab=Decision")
    assert "compliance.demo" in html


def test_a_field_correction_carries_the_case_forward(site):
    """Case 3 is held on one low-confidence field; correcting it runs the
    remaining steps, exactly as the pipeline does."""
    _, before = get(site, "/case/WAL-ONB-0003?tab=Risk")
    assert "has not reached Step 7" in before

    field_id = re.search(r'name="field_id" value="(FLD-\d+)"',
                         get(site, "/case/WAL-ONB-0003?tab=Documents")[1]).group(1)
    status, location = post(site, "/action/field", field_id=field_id, how="correct",
                            value="Unit 7 Calderwick Way, Leeds LS12 4QT, United Kingdom",
                            reason="Confirmed against the source document.",
                            back="/case/WAL-ONB-0003?tab=Risk")
    assert flash(location)[0] == "ok"

    _, after = get(site, "/case/WAL-ONB-0003?tab=Risk")
    assert "has not reached Step 7" not in after
    assert "medium" in after and "43" in after


def test_an_action_without_a_reason_is_refused(site):
    field = get(site, "/case/WAL-ONB-0004?tab=Documents")[1]
    match = re.search(r'name="field_id" value="(FLD-\d+)"', field)
    if not match:
        pytest.skip("no low-confidence field on this case")
    _, location = post(site, "/action/field", field_id=match.group(1),
                       how="accept", reason="",
                       back="/case/WAL-ONB-0004?tab=Documents")
    assert flash(location)[0] == "err", "a release without a reason is not a release"


def test_the_role_switch_is_remembered_between_requests(site):
    post(site, "/action/settings", role="compliance", reviewer="ines.b", back="/")
    _, html = get(site, "/case/WAL-ONB-0001?tab=Decision")
    assert "Record as compliance" in html
    assert 'value="ines.b"' in html
    post(site, "/action/settings", role="analyst", reviewer="analyst.demo", back="/")


def test_the_server_module_never_writes_sql():
    """The same rule the Streamlit app is held to; asserted here as well so the
    two front ends cannot be reviewed separately and drift."""
    from tests.test_app import WRITE_SQL
    for path in sorted((ROOT / "web").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not WRITE_SQL.search(line), f"{path.name}:{number}: {line.strip()}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
