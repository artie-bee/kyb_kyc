"""
The AI half of the document quality check (brief Section 5.3).

Deterministic checks - file type, expiry, age, page count - run in code. The
judgement calls (is this blurred? is it a screenshot? does the name match?) go
through a QualityChecker, so the orchestration layer never depends on which
model is behind it, or on there being a model at all.

Two implementations:
    MockQualityChecker          replays a scripted verdict (tests, demos)
    ClaudeVisionQualityChecker  sends the file to the model named by LLM_PROVIDER
                                (live; needs that provider's key)

Whatever the implementation, it may only return flags from ALLOWED_FLAGS. A
model that invents a flag has its verdict rejected rather than trusted: an
unrecognised flag cannot be mapped to an outcome or a reason code, so acting on
it would put an unauditable decision on the case.
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import demo_samples, live_mode, llm_client

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
    # Set by a checker that could not do the visual half of the check (mock mode,
    # a real upload). Step 3 then holds the document for a person, for this reason.
    hold_reason: str | None = None
    # Set when the verdict was replayed rather than judged - a recognised demo
    # sample file in mock mode. Step 3 writes it into the audit row.
    source_label: str | None = None

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
    """Send the file to the live model and parse a strict JSON verdict.

    Live mode, and opt-in: selecting it while LIVE_MODE_READY is False stops
    with a message rather than attempting a call.

    Which model answers is set by LLM_PROVIDER (anthropic | xai). The key comes
    from that provider's environment variable, the model from a setting, and the
    prompt from prompts/quality_check_v1.txt - the version of which is written
    into the audit row for every call, alongside the provider that served it.

    This class owns the vocabulary check and nothing else; the client owns the
    transport. That split is why the same guarantees hold for every provider,
    and why a test can exercise them with a fake client and no key.

    A reply that will not parse is retried once and then raises. The caller
    places a manual-review hold on that: a failed call is never a pass, because
    "the model did not answer" and "the document is fine" are opposite things.
    """

    mode = "claude_vision"

    def __init__(self, model: str | None = None, prompt_version: str = "v1",
                 document_root: Path | None = None, allow_unready: bool = False,
                 provider: str | None = None, client: llm_client.LLMClient | None = None):
        if not allow_unready:
            live_mode.require_ready()
        self.prompt = llm_client.load_prompt("quality_check", prompt_version)
        # The client owns the transport; this class owns the vocabulary check.
        # Injecting one is how the tests exercise every guardrail without a key.
        self.client = client or llm_client.get_client(provider, model)
        self.model = self.client.model
        self.provider = self.client.provider
        self.version = f"{self.provider}:{self.model}/{self.prompt.stamp}"
        self.document_root = Path(document_root) if document_root else None
        self.last_call: llm_client.Call | None = None

    def _path(self, document: dict) -> Path:
        path = document.get("file_path")
        if path:
            return Path(path)
        if self.document_root:
            return self.document_root / document["file_name"]
        raise llm_client.CallFailed(
            f"{document['file_name']}: no file to send. Live mode needs the actual "
            f"document, not a row about it")

    def check(self, document: dict) -> QualityVerdict:
        instruction = (
            f"The checklist asked for a {document['document_type'].replace('_', ' ')}"
            + (f" for {document['subject_name']}" if document.get("subject_name") else "")
            + ". Assess the document supplied.")
        call = self.client.ask(self._path(document), self.prompt, instruction)
        self.last_call = call
        data = call.data
        if not isinstance(data.get("flags", []), list):
            raise llm_client.CallFailed("'flags' is not a list")
        verdict = QualityVerdict(
            flags=[str(f) for f in data.get("flags", [])],
            confidence=float(data.get("confidence", 0.0)),
            notes=str(data.get("notes", "")),
            expiry_date=data.get("expiry_date") or None,
            document_date=data.get("document_date") or None)
        # An invented flag, or a date that is not a date, discards the verdict.
        return verdict.validate()


# "live" is the provider-neutral name; the two older names are kept so that
# anything selecting a checker by string carries on working.
LiveVisionQualityChecker = ClaudeVisionQualityChecker

CHECKERS = {"mock": MockQualityChecker,
            "live": ClaudeVisionQualityChecker,
            "claude_vision": ClaudeVisionQualityChecker}


def get_checker(mode: str = "mock") -> QualityChecker:
    """Select the checker by name. Default is mock; nothing calls an API by accident."""
    if mode not in CHECKERS:
        raise ValueError(f"unknown checker mode '{mode}'; choose from {sorted(CHECKERS)}")
    return CHECKERS[mode]()


VISUAL_CHECK_NOT_RUN = "visual check not run in mock mode"


class MockUploadChecker(QualityChecker):
    """The checker for a real upload while the pipeline runs in mock mode.

    The mock checker replays a verdict scripted on the document. A file that
    arrived through the portal has no script, and nothing in mock mode can look
    at it. So this checker makes no judgement: no flags, no dates. Step 3 still
    runs its deterministic rules - file type, page count - for real, and then,
    because the visual half of the check (blur, cropping, tampering, the right
    document) has not been done by anyone, it holds the document for an analyst
    with the reason "visual check not run in mock mode". The analyst releases it
    with the ordinary release_document(). That is not a pass anybody gave until
    a person gives it.
    """

    mode = "mock_upload"
    version = None
    uses_judgement = False

    def check(self, document: dict) -> QualityVerdict:
        # A file from the demo upload pack has a verdict on record: replay it,
        # and say that it was replayed. A pack file sent against the wrong
        # checklist item is the wrong document, whatever its own verdict.
        sample = demo_samples.recognise(path=document.get("file_path"))
        if sample is not None:
            flags = list(sample.get("quality_flags") or [])
            if sample["document_type"] != document.get("document_type"):
                flags.append("wrong_document_type")
            return QualityVerdict(flags=flags, confidence=1.0,
                                  notes=f"{demo_samples.LABEL} ({sample['file']})",
                                  expiry_date=sample.get("expiry_date") or None,
                                  document_date=sample.get("document_date") or None,
                                  source_label=f"{demo_samples.LABEL} ({sample['file']})"
                                  ).validate()
        return QualityVerdict(flags=[], confidence=0.0,
                              notes="mock mode: no automated visual check was run",
                              hold_reason=VISUAL_CHECK_NOT_RUN).validate()


def get_upload_checker(mode: str = "mock") -> QualityChecker:
    """The checker for a file a customer uploaded. Live modes read the file; mock
    mode runs the deterministic rules and holds the file for a person to look at."""
    return MockUploadChecker() if mode == "mock" else get_checker(mode)
