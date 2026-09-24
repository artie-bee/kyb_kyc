"""
Whether live mode is usable yet.

It is not. The Claude integration is written and unit-tested but has never met
the real API, because this network has no access to it. Rather than let a demo
fail with a connection error halfway through a case, selecting live mode stops
here with a message saying so.

Flip LIVE_MODE_READY once the key and the network access are in place, and run
tools/evaluate_live.py before trusting anything it produces.
"""

LIVE_MODE_READY = False

MESSAGE = "\n".join([
    "LIVE MODE NOT CONFIGURED - pending API access.",
    "",
    "The Claude integration is written but has never been run against the real API.",
    "To enable it:",
    "  1. set ANTHROPIC_API_KEY in the environment",
    "  2. confirm this network or proxy allows api.anthropic.com",
    "  3. set LIVE_MODE_READY = True in orchestrator/live_mode.py",
    "  4. run tools/evaluate_live.py and read eval_report.md before trusting it",
    "",
    "Mock mode is the default and needs none of this.",
])


class LiveModeNotConfigured(RuntimeError):
    """Live mode was selected before it was ready to be used."""


def require_ready() -> None:
    """Called wherever live mode would begin.

    Stops before anything is attempted, rather than part-way through a case with
    a connection error, which is the difference between a clear message and a
    confusing one during a demo.
    """
    if not LIVE_MODE_READY:
        raise LiveModeNotConfigured(MESSAGE)


def status() -> str:
    """One word for a badge: what mode the system is actually in."""
    return "LIVE" if LIVE_MODE_READY else "MOCK"
