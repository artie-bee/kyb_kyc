"""
Virus scanning for files a customer uploads. Same shape as providers.py:
an interface, a mock that is the default, and a live stub.

    MockVirusScanner   reports every file clean - the default, calls nothing
    LiveVirusScanner   STUB, raises; see below

Whatever is behind the interface, a scan that did not answer is not a pass:
receive_upload() refuses the file rather than storing something nobody
checked. The verdict and the scanner's name go into the audit trail.
"""

from dataclasses import dataclass

from . import settings

CLEAN, INFECTED, UNAVAILABLE = "clean", "infected", "unavailable"


@dataclass
class ScanResult:
    verdict: str            # clean | infected | unavailable
    scanner: str
    detail: str = ""


class VirusScanner:
    mode = "base"
    name = "base"

    def scan(self, content: bytes, file_name: str) -> ScanResult:
        raise NotImplementedError


class MockVirusScanner(VirusScanner):
    """Every file is clean. Demo only: nothing here is a real scan."""

    mode = "mock"
    name = "MockScan (demo, no real scan)"

    def scan(self, content: bytes, file_name: str) -> ScanResult:
        return ScanResult(CLEAN, self.name, "mock scanner: no real scan was run")


class LiveVirusScanner(VirusScanner):
    """Send the file to a real malware scanner.

    TODO: not wired up. No call is made yet - calling scan() raises.

    When implemented it must:
      - scan the bytes before they are written anywhere a later step reads;
      - return UNAVAILABLE on a timeout or any non-answer, never CLEAN;
      - name the engine and its signature version in `name` for the audit row.
    """

    mode = "live"
    name = "TODO-virus-scanner"

    def scan(self, content: bytes, file_name: str) -> ScanResult:
        raise NotImplementedError(
            "LiveVirusScanner is a stub: no scanner is wired up yet. "
            "Run with the mock scanner (the default) until it is.")


SCANNERS = {"mock": MockVirusScanner, "live": LiveVirusScanner}


def get_virus_scanner(mode: str | None = None) -> VirusScanner:
    mode = mode or settings.VIRUS_SCANNER_MODE
    if mode not in SCANNERS:
        raise ValueError(f"unknown virus scanner mode '{mode}'; choose from {sorted(SCANNERS)}")
    return SCANNERS[mode]()
