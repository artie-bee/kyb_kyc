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

# The quality_flags vocabulary. Anything outside this set is rejected.
ALLOWED_FLAGS = frozenset({
    "blurred_unreadable", "cut_off_pages", "expired", "missing_pages",
    "screenshot_not_original", "name_mismatch", "tampering_indicator",
    "unsupported_file_type",
})


class UnknownQualityFlag(ValueError):
    """A checker returned a flag outside ALLOWED_FLAGS."""


@dataclass
class QualityVerdict:
    flags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    notes: str = ""

    def validate(self) -> "QualityVerdict":
        unknown = [f for f in self.flags if f not in ALLOWED_FLAGS]
        if unknown:
            raise UnknownQualityFlag(
                f"checker returned unrecognised flag(s) {unknown}; "
                f"allowed: {sorted(ALLOWED_FLAGS)}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError(f"confidence {self.confidence} is outside 0-1")
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
        return QualityVerdict(flags=flags, confidence=1.0,
                              notes="scripted verdict; no model was called").validate()


class ClaudeVisionQualityChecker(QualityChecker):
    """Send the file to Claude and parse a strict JSON verdict.

    TODO: not wired up. No API call is made yet - calling check() raises.

    When this is implemented it must:
      - send the file bytes plus a prompt naming the expected document_type
        and the individual it was supplied for;
      - require exactly {"flags": [...], "confidence": 0-1, "notes": "..."}
        with no prose around it, and re-ask once if the reply does not parse;
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
