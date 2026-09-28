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


# Settings a developer's shell may carry (LLM_PROVIDER=groq, say) that would make
# a test's outcome depend on the machine it runs on. Each test starts without
# them; a test that needs one sets it with monkeypatch.
_SHELL_SETTINGS = ("LLM_PROVIDER", "WALLESTER_UC4_MODEL", "WALLESTER_UC4_TEMPERATURE")


@pytest.fixture(autouse=True)
def no_shell_settings(monkeypatch):
    for name in _SHELL_SETTINGS:
        monkeypatch.delenv(name, raising=False)
