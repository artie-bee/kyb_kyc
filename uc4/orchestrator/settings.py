"""
Settings that an operator may reasonably change without a code release.

Read from the environment, with the demo's defaults. Rules about documents,
risk and communications live in kb/; these are operating limits, not rules.

    WALLESTER_UC4_MAX_UPLOAD_MB      largest file the portal accepts      10
    WALLESTER_UC4_PORTAL_LINK_DAYS   how long a customer link works       14
    WALLESTER_UC4_PORTAL_URL         where the customer portal is served  http://127.0.0.1:8701
    WALLESTER_UC4_VIRUS_SCANNER      mock | live                          mock
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
