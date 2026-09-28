"""
One clock: every stamp and every day count reads the injected clock.

The case guide used to flip between "15 days" and "16 days" depending on the
hour: holds were stamped in UTC from the machine clock while the chase counted
days on a clock started from the LOCAL date. These tests pin the fix.

Run: python -m pytest tests/test_clock.py -q
"""

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import clock, db                                    # noqa: E402
from tools.run_demo import run                                        # noqa: E402


def test_both_clocks_answer_in_utc_and_agree_with_their_own_dates():
    for c in (clock.SystemClock(), clock.FakeClock(clock.DEMO_START)):
        now = c.now()
        assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
        assert c.today() == now.date()


def test_every_timestamp_comes_from_the_clock_in_use():
    fixed = clock.FakeClock(datetime(2031, 5, 6, 7, 8, 9, tzinfo=timezone.utc))
    with clock.use(fixed):
        assert db.now() == "2031-05-06T07:08:09Z"
        fixed.advance(1)
        assert db.now() == "2031-05-07T07:08:09Z"
        assert clock.days_since("2031-05-01T07:08:09Z") == 6


@pytest.mark.parametrize("hour", [0, 3, 9, 18, 19, 20, 23])
def test_case_14_waits_the_same_days_at_any_hour(hour):
    """Every hour of a day, including 18:30-24:00 UTC, when India's date is
    already tomorrow - the window in which the old code said 16 days."""
    start = datetime(2026, 9, 28, hour, 30, tzinfo=timezone.utc)
    conn = db.connect(":memory:")
    run(conn, clock=clock.FakeClock(start), verbose=False)
    waits = [r[0] for r in conn.execute(
        "SELECT payload_summary FROM audit_event WHERE case_id = 'WAL-ONB-0014'"
        " AND action IN ('applicant_chased', 'case_closed_no_response') ORDER BY event_id")]
    assert [re.search(r"(\d+) days", w).group(1) for w in waits] == ["15", "30"], waits


def test_no_code_reads_the_machine_clock_for_a_date_or_an_age():
    """Only orchestrator/clock.py reads the real time. The two tools that print
    "generated at" beside a file may too; that is a label, not an age."""
    allowed = {Path("orchestrator/clock.py"), Path("tools/make_case_guide.py"),
               Path("tools/make_dashboard.py")}
    offenders = []
    for folder in ("orchestrator", "app", "portal", "web", "tools"):
        for p in (ROOT / folder).rglob("*.py"):
            rel = p.relative_to(ROOT)
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"date\.today\(\)|datetime\.(now|utcnow)\(|time\.time\(\)", line):
                    if rel not in allowed or "generated" not in line:
                        if rel != Path("orchestrator/clock.py"):
                            offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)
