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
WEB = ROOT / "web"          # the HTML console, held to the same rule
PORTAL = ROOT / "portal"    # the customer portal, and the same rule again
WRITE_SQL = re.compile(
    r"""["'][^"']*\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE
        |CREATE\s+TABLE|ALTER\s+TABLE|REPLACE\s+INTO)\b""",
    re.IGNORECASE | re.VERBOSE)


@pytest.fixture(scope="module")
def demo():
    conn = db.connect(":memory:")
    run(conn, verbose=False)
    return conn


def test_a_human_action_carries_the_case_forward(tmp_path):
    """The demo depends on this. Releasing a document or correcting a field
    clears the hold; the case then has to move on by itself, or the presenter is
    left looking at a case that is unblocked and going nowhere."""
    from app import data

    db_path = tmp_path / "demo.db"
    data.reset_demo(db_path)
    conn = data.connect(db_path)

    def row(case_id):
        return next(r for r in data.dashboard(conn) if r["case_id"] == case_id)

    # Case 3: one field read at 0.58, waiting to be confirmed.
    assert row("WAL-ONB-0003")["risk_band"] == "", "case 3 is held and not yet scored"
    field = next(f for d in data.documents(conn, "WAL-ONB-0003") for f in d["fields"]
                 if f["needs_analyst_correction"])
    assert field["name"] == "registered_address"
    assert float(field["confidence"]) == 0.58
    data.correct_field(conn, field["field_id"], "analyst.test", field["value"], "confirmed")
    after = row("WAL-ONB-0003")
    assert after["open_holds"] == 0
    assert (after["risk_band"], after["risk_score"]) == ("medium", "43")
    assert after["status"] == "ready_for_decision"

    # Case 4: a held document and two faint values behind it.
    assert row("WAL-ONB-0004")["risk_band"] == ""
    held = next(d for d in data.documents(conn, "WAL-ONB-0004")
                if d["quality_status"] == "manual_review_required")
    assert held["file_name"] == "ownership_chart_vestmark.pdf"
    data.release_document(conn, held["document_id"], "analyst.test", "accept", "annex is dormant")
    for _ in range(5):
        low = [f for d in data.documents(conn, "WAL-ONB-0004") for f in d["fields"]
               if f["needs_analyst_correction"] and not f["corrected_by_analyst"]]
        if not low:
            break
        data.accept_field_as_read(conn, low[0]["field_id"], "analyst.test", "corroborated")
    after = row("WAL-ONB-0004")
    assert after["open_holds"] == 0
    assert (after["risk_band"], after["risk_score"]) == ("high", "69")
    assert after["status"] == "enhanced_due_diligence"

    # Case 10 needs nobody: it is scored the moment the demo is reset.
    assert (row("WAL-ONB-0010")["risk_band"], row("WAL-ONB-0010")["risk_score"]) == ("low", "15")
    conn.close()


def test_carrying_on_does_not_duplicate_extracted_values(tmp_path):
    """Resuming re-runs the steps; it must not read a document twice."""
    from app import data

    db_path = tmp_path / "dupes.db"
    data.reset_demo(db_path)
    conn = data.connect(db_path)

    def field_count():
        return conn.execute(
            "SELECT COUNT(*) FROM extracted_field f JOIN document d USING (document_id)"
            " WHERE d.case_id = 'WAL-ONB-0003'").fetchone()[0]

    before = field_count()
    field = next(f for d in data.documents(conn, "WAL-ONB-0003") for f in d["fields"]
                 if f["needs_analyst_correction"])
    data.correct_field(conn, field["field_id"], "analyst.test", field["value"], "confirmed")
    assert field_count() == before, "resuming re-extracted documents it had already read"
    conn.close()


def test_the_reuse_screen_names_a_real_kb_file_for_every_override():
    """A reuse claim that points at a file which does not exist, or quotes a
    version nobody bumped, is worse than no claim."""
    from app import data
    import json

    table = data.reuse_table()
    manifest = json.loads((ROOT / "kb" / "kb_manifest.json").read_text(encoding="utf-8"))
    assert table["kb_version"] == manifest["kb_version"]

    # The brief's own wording, quoted verbatim. A paraphrase here would be this
    # POC quietly restating what the split is.
    assert [(r["component"], r["generic"], r["override"]) for r in table["rows"]] == [
        ("Document quality rules", "Reused",
         "Wallester thresholds and accepted document types"),
        ("OCR extraction", "Reused",
         "Wallester field map and required fields"),
        ("Registry validation", "Reused pattern",
         "Configurable registry providers, not Companies House-only"),
        ("Risk scoring", "Reused pattern",
         "Wallester-specific policy matrix"),
        ("Customer communications", "Partially reused",
         "Wallester-approved templates required"),
        ("Audit summary", "Reused",
         "Wallester case fields and decision taxonomy"),
    ]
    assert data.REUSE_CAPTION == (
        "This POC is a standalone build that represents the generic-agent / "
        "Wallester-variant split. It does not run on an existing agent.")

    versions = {f"kb/{i['file']}": i["version"] for i in manifest["items"].values()}
    for row in table["rows"]:
        assert row["generic"] and row["override"], f"{row['component']} says nothing"
        assert row["files"], f"{row['component']} names no KB file"
        for f in row["files"]:
            assert f["exists"], f"{row['component']} points at missing {f['file']}"
            assert f["version"] == versions[f["file"]], (
                f"{f['file']} shown as {f['version']}, manifest says {versions[f['file']]}")
            assert f["rules"] > 0, f"{f['file']} has no rules in it"


def test_only_the_white_label_case_has_a_future_phase(demo):
    """Case 8 is the white-label partner. No other case has a future phase."""
    from app import data

    white_label = [r["case_id"] for r in demo.execute("SELECT case_id FROM onboarding_case")
                   if data.is_white_label(demo, r["case_id"])]
    assert white_label == ["WAL-ONB-0008"]


def test_the_future_phase_steps_are_the_ones_the_branch_audits(demo):
    """The panel and the routing audit event must name the same programme steps."""
    from app import data

    named = [name for name, _ in data.FUTURE_PHASE_STEPS]
    assert named == ["KYB", "API integration", "Visa co-brand approval",
                     "BIN and 3DS configuration", "Go-live testing"]

    event = demo.execute(
        "SELECT payload_summary FROM audit_event WHERE case_id = 'WAL-ONB-0008'"
        " AND action = 'routed_to_white_label_branch'").fetchone()
    assert event is not None, "the white-label branch must audit its routing"


def test_the_app_contains_no_sql_writes():
    """Reads are fine. A write here would bypass every rule in the pipeline.

    All three front-end packages are scanned: app/, the console and the portal. Two user interfaces over one set of rules is
    fine; a second implementation of the rules inside one of them is not, and a
    screen that writes its own UPDATE is exactly that.
    """
    offenders = []
    for path in (sorted(APP.rglob("*.py")) + sorted(WEB.rglob("*.py"))
                 + sorted(PORTAL.rglob("*.py"))):
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
