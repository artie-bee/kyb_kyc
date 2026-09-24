"""
The demo app must not be a second implementation of the rules.

Two things are checked here. First, that the app never writes to the database
itself: every change has to go through the orchestrator, or the holds, the role
checks and the sanctions rules stop applying on the one surface a person
actually uses. Second, that the customer view leaks nothing - rendered for the
two cases where a leak would matter most.

Run: python -m pytest tests -q
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.customer_view import PLAIN_STATUS, customer_view, leaks          # noqa: E402
from orchestrator import db                                               # noqa: E402
from orchestrator.steps.communication import scan                         # noqa: E402
from tools.run_demo import run                                            # noqa: E402

APP = ROOT / "app"
WRITE_SQL = re.compile(
    r"""["'][^"']*\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE
        |CREATE\s+TABLE|ALTER\s+TABLE|REPLACE\s+INTO)\b""",
    re.IGNORECASE | re.VERBOSE)


@pytest.fixture(scope="module")
def demo():
    conn = db.connect(":memory:")
    run(conn, verbose=False)
    return conn


SCREENS = ["Operations dashboard", "Case detail", "Customer view", "Audit export"]
# One clean case, one held at Step 3, one with a sanctions match, one closed.
SMOKE_CASES = ["WAL-ONB-0001", "WAL-ONB-0004", "WAL-ONB-0012", "WAL-ONB-0014"]


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=180)


@pytest.mark.parametrize("screen", SCREENS)
def test_every_screen_renders_without_error(screen):
    """Runs the real app script. Catches the kind of fault that only appears
    when a table is actually built - a column of mixed types, say."""
    at = _app().run()
    assert not at.exception, f"dashboard failed: {at.exception}"
    if screen != "Operations dashboard":
        at.radio(key="screen").set_value(screen).run()
        assert not at.exception, f"{screen} failed: {at.exception}"
    assert at.header, f"{screen} rendered no heading"


@pytest.mark.parametrize("case_id", SMOKE_CASES)
def test_case_detail_renders_for_a_spread_of_cases(case_id):
    at = _app().run()
    at.radio(key="screen").set_value("Case detail").run()
    at.selectbox(key="case").set_value(case_id).run()
    assert not at.exception, f"case detail failed for {case_id}: {at.exception}"
    assert any(case_id in h.value for h in at.header)


@pytest.mark.parametrize("case_id", ["WAL-ONB-0006", "WAL-ONB-0012"])
def test_the_customer_screen_renders_for_the_sensitive_cases(case_id):
    at = _app().run()
    at.radio(key="screen").set_value("Customer view").run()
    at.selectbox(key="case").set_value(case_id).run()
    assert not at.exception, f"customer view failed for {case_id}: {at.exception}"
    # nothing on the rendered page carries restricted wording
    shown = " ".join(
        [e.value for e in at.markdown] + [e.value for e in at.info]
        + [e.value for e in at.caption] + [h.value for h in at.header]
        + [s.value for s in at.subheader])
    assert not scan(shown), f"{case_id} customer screen shows {scan(shown)}"
    assert not at.error, "the customer screen refused to render"


def test_the_live_option_is_shown_but_disabled():
    at = _app().run()
    mode = at.radio(key="mode")
    assert mode.disabled is True
    assert mode.options == ["Mock", "Live - pending API access"]
    assert mode.value == "Mock"


def test_the_app_contains_no_sql_writes():
    """Reads are fine. A write here would bypass every rule in the pipeline."""
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if WRITE_SQL.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, (
        "the app writes to the database directly; every change must go through the "
        "orchestrator:\n  " + "\n  ".join(offenders))


def test_the_app_only_changes_things_through_the_orchestrator():
    """Every action in app/data.py delegates to a step module."""
    source = (APP / "data.py").read_text(encoding="utf-8")
    for call in ("analyst_review.release_document", "extraction.accept_field_as_read",
                 "extraction.correct_field", "decision.record_decision",
                 "communication.send_required_message"):
        assert call in source, f"{call} is not wired up; the app would have to improvise"


@pytest.mark.parametrize("case_id", ["WAL-ONB-0006", "WAL-ONB-0012"])
def test_the_customer_view_leaks_nothing_on_the_sensitive_cases(demo, case_id):
    """Case 6 has a possible sanctions match, case 12 a confirmed one. Neither
    applicant may learn anything about it."""
    view = customer_view(demo, case_id)
    assert view["case_id"] == case_id and view["applicant_name"]
    assert not leaks(view), f"{case_id} customer view carries {leaks(view)}"

    # nothing internal is even present to leak
    blob = " ".join([view["status_text"]] + [m["text"] for m in view["messages"]]).lower()
    for word in ("sanction", "screening", "pep", "adverse", "risk", "hold", "finding",
                 "compliance", "escalat", "analyst"):
        assert word not in blob, f"{case_id} customer view mentions {word!r}"
    assert view["messages"], f"{case_id} should have had at least one message"


def test_every_case_renders_a_clean_customer_view(demo):
    for (case_id,) in demo.execute("SELECT case_id FROM onboarding_case"):
        view = customer_view(demo, case_id)
        assert not leaks(view), f"{case_id}: {leaks(view)}"
        assert view["status_text"], f"{case_id} has no plain-language status"


def test_the_plain_status_phrases_are_themselves_clean():
    """The phrase book is customer-facing text and is checked like any other."""
    for status, phrase in PLAIN_STATUS.items():
        assert not scan(phrase), f"the phrase for {status} carries {scan(phrase)}"
    assert "enhanced_due_diligence" in PLAIN_STATUS
    # the two statuses that would give away a finding say the same bland thing
    assert PLAIN_STATUS["enhanced_due_diligence"] == PLAIN_STATUS["analyst_review_required"]


def test_the_customer_view_shows_only_messages_actually_sent(demo):
    for (case_id,) in demo.execute("SELECT case_id FROM onboarding_case"):
        shown = len(customer_view(demo, case_id)["messages"])
        sent = demo.execute(
            "SELECT COUNT(*) FROM outbox WHERE case_id = ? AND audience = 'applicant'",
            (case_id,)).fetchone()[0]
        assert shown == sent, f"{case_id} shows {shown} messages but sent {sent}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
