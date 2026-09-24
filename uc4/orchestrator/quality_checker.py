"""
The AI half of the document quality check (brief Section 5.3).

Deterministic checks - file type, expiry, age, page count - run in code. The
judgement calls (is this blurred? is it a screenshot? does the name match?) go
through a QualityChecker, so the orchestration layer never depends on which
model is behind it, or on there being a model at all.

Two implementations:
    MockQualityChecker          replays a scripted verdict (tests, demos)
    ClaudeVisionQualityChecker  sends the file to Claude  (STUB - see below)

Whatever the implementation, it may only return flags from ALLOWED_FLAGS. A
model that invents a flag has its verdict rejected rather than trusted: an
unrecognised flag cannot be mapped to an outcome or a reason code, so acting on
it would put an unauditable decision on the case.
"""

from dataclasses import dataclass, field
from datetime import date

# The quality_flags vocabulary. Anything outside this set is rejected.
ALLOWED_FLAGS = frozenset({
    "blurred_unreadable", "cut_off_pages", "expired", "missing_pages",
    "screenshot_not_original", "name_mismatch", "tampering_indicator",
    "unsupported_file_type", "wrong_document_type", "document_too_old",
})


class UnknownQualityFlag(ValueError):
    """A checker returned a flag outside ALLOWED_FLAGS."""


@dataclass
class QualityVerdict:
    """What a checker saw on the page.

    expiry_date and document_date are read off the image itself, because Step 3
    is the first time anyone looks at the file. The extracted_field table does
    not exist yet at this point - Step 4 fills it - so the date rules QR-02 and
    QR-03 must take their dates from here. Step 4 re-reads both dates properly
    and re-runs the same two rules; a disagreement between the two readings
    sends the document to an analyst rather than silently preferring either.

    Both dates are nullable: plenty of documents carry neither, and a checker
    that cannot find a date must say so rather than invent one.
    """

    flags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    notes: str = ""
    expiry_date: str | None = None
    document_date: str | None = None

    def validate(self) -> "QualityVerdict":
        unknown = [f for f in self.flags if f not in ALLOWED_FLAGS]
        if unknown:
            raise UnknownQualityFlag(
                f"checker returned unrecognised flag(s) {unknown}; "
                f"allowed: {sorted(ALLOWED_FLAGS)}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError(f"confidence {self.confidence} is outside 0-1")
        for name in ("expiry_date", "document_date"):
            value = getattr(self, name)
            if value is not None:
                date.fromisoformat(value)      # raises on anything not ISO yyyy-mm-dd
        return self


class QualityChecker:
    """Interface. mode and version are written into the audit trail."""

    mode = "base"
    version: str | None = None

    def check(self, document: dict) -> QualityVerdict:
        raise NotImplementedError


class MockQualityChecker(QualityChecker):
    """Replays the quality flags scripted on the document itself.

    The document payload carries `scripted_quality_flags`, which the dataset
    loader fills from document.csv. Deterministic rules still run alongside
    this, so a scripted flag and a computed flag for the same document merge
    rather than one overwriting the other.
    """

    mode = "mock"
    version = None

    def check(self, document: dict) -> QualityVerdict:
        flags = [f for f in (document.get("scripted_quality_flags") or []) if f]
        # The scripted dates stand in for what a model would read off the page.
        return QualityVerdict(flags=flags, confidence=1.0,
                              notes="scripted verdict; no model was called",
                              expiry_date=document.get("expiry_date") or None,
                              document_date=document.get("document_date") or None).validate()


class ClaudeVisionQualityChecker(QualityChecker):
    """Send the file to Claude and parse a strict JSON verdict.

    TODO: not wired up. No API call is made yet - calling check() raises.

    When this is implemented it must:
      - send the file bytes plus a prompt naming the expected document_type
        and the individual it was supplied for;
      - require exactly {"flags": [...], "confidence": 0-1, "notes": "...",
        "expiry_date": "yyyy-mm-dd"|null, "document_date": "yyyy-mm-dd"|null}
        with no prose around it, and re-ask once if the reply does not parse;
      - read both dates off the image. Step 3 runs before any extraction, so
        these are the only dates the date rules have; a date the model cannot
        find must come back null, never guessed, and a null simply means the
        rule does not fire;
      - pass the result through QualityVerdict.validate(), so a hallucinated
        flag raises UnknownQualityFlag instead of reaching a case;
      - set `version` to the model id plus the prompt version, which
        document_quality.py already writes into every audit row;
      - never send a file that a deterministic rule has already failed - that
        would pay for a model call on a document we know is unusable.
    """

    mode = "claude_vision"

    def __init__(self, model: str = "claude-opus-5", prompt_version: str = "dq-v1"):
        self.model = model
        self.prompt_version = prompt_version
        self.version = f"{model}/{prompt_version}"

    def check(self, document: dict) -> QualityVerdict:
        raise NotImplementedError(
            "ClaudeVisionQualityChecker is a stub: no API call is wired up yet. "
            "Run with the mock checker (the default) until it is.")


CHECKERS = {"mock": MockQualityChecker, "claude_vision": ClaudeVisionQualityChecker}


def get_checker(mode: str = "mock") -> QualityChecker:
    """Select the checker by name. Default is mock; nothing calls an API by accident."""
    if mode not in CHECKERS:
        raise ValueError(f"unknown checker mode '{mode}'; choose from {sorted(CHECKERS)}")
    return CHECKERS[mode]()
