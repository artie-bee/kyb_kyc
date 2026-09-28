"""
Settings that an operator may reasonably change without a code release.

Read from the environment, with the demo's defaults. Rules about documents,
risk and communications live in kb/; these are operating limits, not rules.

    WALLESTER_UC4_MAX_UPLOAD_MB      largest file the portal accepts      10
    WALLESTER_UC4_PORTAL_LINK_DAYS   how long a customer link works       14
    WALLESTER_UC4_PORTAL_URL         where the customer portal is served  http://127.0.0.1:8701
    WALLESTER_UC4_VIRUS_SCANNER      mock | live                          mock
    WALLESTER_UC4_EXTRACTION_FOR_NEW_UPLOADS
                                     mock_only | live_if_available        mock_only
    WALLESTER_UC4_LIVE_READ_INTERVAL seconds between live-model calls     3

EXTRACTION_FOR_NEW_UPLOADS decides only what happens to a file uploaded to a
WAL-DEMO- case through the portal that is NOT a recognised demo sample. With
live_if_available it is queued for the live model (orchestrator/live_reading.py);
anything else - every scripted case, every recognised sample - stays mock.
"""

import os


def _int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        raise ValueError(f"{name} must be a whole number") from None
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


MAX_UPLOAD_MB = _int("WALLESTER_UC4_MAX_UPLOAD_MB", 10)
PORTAL_LINK_DAYS = _int("WALLESTER_UC4_PORTAL_LINK_DAYS", 14)
PORTAL_URL = (os.environ.get("WALLESTER_UC4_PORTAL_URL") or "http://127.0.0.1:8701").rstrip("/")
VIRUS_SCANNER_MODE = (os.environ.get("WALLESTER_UC4_VIRUS_SCANNER") or "mock").strip().lower()

EXTRACTION_MODES = ("mock_only", "live_if_available")
EXTRACTION_FOR_NEW_UPLOADS = (os.environ.get("WALLESTER_UC4_EXTRACTION_FOR_NEW_UPLOADS")
                              or "mock_only").strip().lower()
if EXTRACTION_FOR_NEW_UPLOADS not in EXTRACTION_MODES:
    raise ValueError(f"WALLESTER_UC4_EXTRACTION_FOR_NEW_UPLOADS must be one of "
                     f"{EXTRACTION_MODES}, not {EXTRACTION_FOR_NEW_UPLOADS!r}")
LIVE_READ_INTERVAL = _int("WALLESTER_UC4_LIVE_READ_INTERVAL", 3)
