"""
The one clock. Every timestamp and every age or day count reads it.

The bug this replaces: holds were stamped from the machine clock in UTC
(db.now), while the demo's chase loop counted days on a fake clock started from
date.today(), which is LOCAL time. For the five and a half hours of each night
when the date in India is a day ahead of UTC, case 14 waited "16 days" instead
of 15, and the case guide flipped with it. Two clocks, disagreeing about what
day it is.

So there is one clock, and it is injected:

    current()        the clock in force: the system clock unless one is in use
    use(clock)       make a clock current for a block (the demo, a test run)
    stamp(when)      the timestamp format every table uses

SystemClock is the real UTC time, for the running console and portal.
FakeClock is a fixed instant a test or the demo moves by hand. The demo is
built on DEMO_START, a fixed reference instant, so everything it produces -
every stamp, every "waited N days", every expiry and age rule - is the same
whenever and wherever it is run.

Both clocks answer in UTC, so a date and a timestamp can never disagree about
what day it is.
"""

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone

# The instant the demo is set at: two days after the last event the dataset
# scripts (26 September 2026), so every scripted document is judged against
# the same date whenever the demo is rebuilt.
DEMO_START = datetime(2026, 9, 28, 9, 0, 0, tzinfo=timezone.utc)


class Clock:
    def now(self) -> datetime:
        raise NotImplementedError

    def today(self) -> date:
        """Today, in UTC - the same day the timestamps say it is."""
        return self.now().date()


class SystemClock(Clock):
    """The real time, in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock(Clock):
    """A fixed instant, moved by hand. Given a date, it stands at 09:00 UTC."""

    def __init__(self, start: date | datetime):
        if isinstance(start, datetime):
            self._now = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        else:
            self._now = datetime.combine(start, time(9, 0), tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, days: float) -> date:
        self._now += timedelta(days=days)
        return self._now.date()


_current: Clock = SystemClock()


def current() -> Clock:
    return _current


@contextmanager
def use(clock: Clock):
    """Make `clock` the one every stamp and age reads, for the length of a block."""
    global _current
    before, _current = _current, clock
    try:
        yield clock
    finally:
        _current = before


def stamp(when: datetime | None = None) -> str:
    return (when or _current.now()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_since(timestamp: str | None) -> int:
    """Whole days from a stored timestamp to now, on the clock in force."""
    try:
        then = datetime.fromisoformat((timestamp or "").replace("Z", "+00:00"))
    except ValueError:
        return 0
    return (_current.now() - then).days
