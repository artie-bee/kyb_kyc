"""
OCR and structured extraction  (brief Section 5.4)

Same shape as quality_checker.py: the orchestration layer talks to an interface,
so it does not care whether a model, an OCR engine or a script is behind it.

    MockExtractor    replays the fields scripted on the document (tests, demos)
    ClaudeExtractor  sends the file to Claude  (live; needs ANTHROPIC_API_KEY)

An extractor may only return field names the KB lists for that document type.
An unrecognised name has no `used_by`, so nothing downstream would ever read it
and no one could say where the value came from; it is rejected rather than
stored. Confidence travels with every value, because Step 4's whole job is to
know which values are trustworthy enough to act on.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import claude_client, live_mode


class UnknownExtractedField(ValueError):
    """An extractor returned a field name the KB does not list for this type."""


@dataclass
class ExtractedValue:
    name: str
    value: str | None
    confidence: float
    source_page: int | None = None


@dataclass
class ExtractionResult:
    fields: list[ExtractedValue] = field(default_factory=list)
    notes: str = ""

    def validate(self, allowed: list[str]) -> "ExtractionResult":
        unknown = [f.name for f in self.fields if f.name not in allowed]
        if unknown:
            raise UnknownExtractedField(
                f"extractor returned field(s) {unknown} that the KB does not list; "
                f"allowed: {allowed}")
        for f in self.fields:
            if not 0.0 <= float(f.confidence) <= 1.0:
                raise ValueError(f"confidence {f.confidence} for {f.name} is outside 0-1")
        return self


class Extractor:
    """Interface. mode and version are written into the audit trail."""

    mode = "base"
    version: str | None = None

    def extract(self, document: dict, expected_fields: list[dict]) -> ExtractionResult:
        raise NotImplementedError


class MockExtractor(Extractor):
    """Replays the fields scripted on the document payload.

    `scripted_fields` is filled by the dataset loader from extracted_field.csv,
    carrying each value's confidence and source page so the low-confidence path
    is exercised with the same numbers the dataset scripted.
    """

    mode = "mock"
    version = None

    def extract(self, document: dict, expected_fields: list[dict]) -> ExtractionResult:
        allowed = [f["field_name"] for f in expected_fields]
        values = [ExtractedValue(name=f["name"], value=f["value"],
                                 confidence=float(f["confidence"]),
                                 source_page=int(f["source_page"]) if f.get("source_page") else None)
                  for f in (document.get("scripted_fields") or [])]
        return ExtractionResult(fields=values,
                                notes="scripted extraction; no model was called").validate(allowed)


class ClaudeExtractor(Extractor):
    """Send the file to Claude and parse a strict JSON extraction.

    TODO - LIVE MODE IS A PLACEHOLDER.
    
    This code is written and unit-tested, but it has never been run against the real
    Claude API: there is no API access on this network. Treat it as a first draft to
    be exercised, not as working integration. Nothing calls it unless live mode is
    selected explicitly, and selecting live mode currently stops with a message
    rather than attempting a call.
    
    To enable it later: set ANTHROPIC_API_KEY, confirm the network or proxy allows
    api.anthropic.com, set LIVE_MODE_READY = True in orchestrator/live_mode.py, then
    run tools/evaluate_live.py and read eval_report.md before trusting any of it.

    Live mode. Key from ANTHROPIC_API_KEY, model from a setting, prompt from
    prompts/extraction_v1.txt with its version written into every audit row.

    The KB's field list for the document type is sent with the request AND
    checked against the reply: a name outside it discards the extraction rather
    than storing a value nothing downstream could read. A reply that will not
    parse is retried once and then raises, and the caller holds the case.
    """

    mode = "claude"

    def __init__(self, model: str | None = None, prompt_version: str = "v1",
                 document_root: Path | None = None, allow_unready: bool = False):
        if not allow_unready:
            live_mode.require_ready()
        self.prompt = claude_client.load_prompt("extraction", prompt_version)
        self.model = model or claude_client.model_name()
        self.version = f"{self.model}/{self.prompt.stamp}"
        self.document_root = Path(document_root) if document_root else None
        self.last_call: claude_client.Call | None = None

    def _path(self, document: dict) -> Path:
        path = document.get("file_path")
        if path:
            return Path(path)
        if self.document_root:
            return self.document_root / document["file_name"]
        raise claude_client.CallFailed(
            f"{document['file_name']}: no file to send. Live mode needs the actual "
            f"document, not a row about it")

    def extract(self, document: dict, expected_fields: list[dict]) -> ExtractionResult:
        allowed = [f["field_name"] for f in expected_fields]
        wanted = "\n".join(
            f"  {f['field_name']} ({'required' if f['required'].lower() == 'true' else 'optional'})"
            for f in expected_fields)
        instruction = (
            f"This is a {document['document_type'].replace('_', ' ')}. Read these fields "
            f"and no others:\n{wanted}")
        call = claude_client.ask(self._path(document), self.prompt, instruction,
                                 model=self.model, max_tokens=2000)
        self.last_call = call
        rows = call.data.get("fields")
        if not isinstance(rows, list):
            raise claude_client.CallFailed("'fields' is not a list")

        values = []
        for row in rows:
            if not isinstance(row, dict) or "name" not in row:
                raise claude_client.CallFailed(f"a field entry is malformed: {row!r}")
            page = row.get("source_page")
            values.append(ExtractedValue(
                name=str(row["name"]),
                value=None if row.get("value") is None else str(row["value"]),
                confidence=float(row.get("confidence", 0.0)),
                source_page=int(page) if page not in (None, "") else None))
        return ExtractionResult(fields=values,
                                notes=call.audit_note()).validate(allowed)


EXTRACTORS = {"mock": MockExtractor, "claude": ClaudeExtractor}


def get_extractor(mode: str = "mock") -> Extractor:
    """Select the extractor by name. Default is mock; nothing calls an API by accident."""
    if mode not in EXTRACTORS:
        raise ValueError(f"unknown extractor mode '{mode}'; choose from {sorted(EXTRACTORS)}")
    return EXTRACTORS[mode]()
