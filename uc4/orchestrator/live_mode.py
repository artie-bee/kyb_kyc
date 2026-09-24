"""
Whether live mode is usable yet.

Live mode is opt-in and off by default. Selecting it while LIVE_MODE_READY is
False stops here with a message, rather than letting a demo fail with a
connection error halfway through a case.

Which model answers is a separate question, set by LLM_PROVIDER:

    LLM_PROVIDER=anthropic   Claude, reads PDFs natively      ANTHROPIC_API_KEY
    LLM_PROVIDER=xai         Grok, images only - PDFs are     XAI_API_KEY
                             rasterised to page images first
    LLM_PROVIDER=groq        Groq (a different company from   GROQ_API_KEY
                             xAI), images only, 3 per request

Flip LIVE_MODE_READY once a key and the network access are in place, and run
tools/evaluate_live.py before trusting anything it produces.
"""

import os

LIVE_MODE_READY = False

MESSAGE = "\n".join([
    "LIVE MODE NOT CONFIGURED - pending API access.",
    "",
    "The live integration is written but is not enabled. To turn it on:",
    "  1. choose a provider: set LLM_PROVIDER to 'anthropic', 'xai' or 'groq'",
    "  2. set that provider's key in the environment:",
    "       anthropic -> ANTHROPIC_API_KEY",
    "       xai       -> XAI_API_KEY",
    "       groq      -> GROQ_API_KEY",
    "  3. confirm this network or proxy allows that provider's host",
    "  4. set LIVE_MODE_READY = True in orchestrator/live_mode.py",
    "  5. run tools/evaluate_live.py and read eval_report.md before trusting it",
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


def badge() -> str:
    """The badge with the provider named, for a screen that has room for it."""
    if not LIVE_MODE_READY:
        return "MOCK"
    return f"LIVE ({(os.environ.get('LLM_PROVIDER') or 'anthropic').strip().lower()})"
