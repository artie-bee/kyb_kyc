"""
Every test runs on the demo's fixed clock.

The pipeline reads one injected clock (orchestrator/clock.py) for every
timestamp, expiry, age and "waited N days". Here it is fixed at DEMO_START for
the whole session, so no test depends on the hour it happens to run - which is
exactly what the case guide's "15 days" / "16 days" flip was. A test that needs
time to pass moves a FakeClock of its own.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import clock                                        # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def fixed_clock():
    with clock.use(clock.FakeClock(clock.DEMO_START)) as fixed:
        yield fixed
