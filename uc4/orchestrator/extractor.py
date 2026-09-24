"""
OCR and structured extraction  (brief Section 5.4)

Same shape as quality_checker.py: the orchestration layer talks to an interface,
so it does not care whether a model, an OCR engine or a script is behind it.

    MockExtractor    replays the fields scripted on the document (tests, demos)
    ClaudeExtractor  sends the file to Claude  (STUB - see below)

An extractor may only return field names the KB lists for that document type.
An unrecognised name has no `used_by`, so nothing downstream would ever read it
and no one could say where the value came from; it is rejected rather than
stored. Confidence travels with every value, because Step 4's whole job is to
know which values are trustworthy enough to act on.
"""

from dataclasses import dataclass, field


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

    TODO: not wired up. No API call is made yet - calling extract() raises.

    When this is implemented it must:
      - send the file bytes plus the KB's field list for this document_type,
        naming each field and whether it is required;
      - require exactly {"fields": [{"name", "value", "confidence", "source_page"}]}
        with no prose around it, and re-ask once if the reply does not parse;
      - only use names from that field list, and pass the result through
        ExtractionResult.validate(), so an invented field raises rather than
        being written to the case;
      - return a field it could not read as value null with a low confidence,
        never as a plausible-looking guess - Step 4 routes an unreadable value
        to an analyst, which is only safe if the model admits to it;
      - set `version` to the model id plus the prompt version, which
        extraction.py already writes into every audit row;
      - only ever be handed documents from accepted_documents().
    """

    mode = "claude"

    def __init__(self, model: str = "claude-opus-5", prompt_version: str = "ex-v1"):
        self.model = model
        self.prompt_version = prompt_version
        self.version = f"{model}/{prompt_version}"

    def extract(self, document: dict, expected_fields: list[dict]) -> ExtractionResult:
        raise NotImplementedError(
            "ClaudeExtractor is a stub: no API call is wired up yet. "
            "Run with the mock extractor (the default) until it is.")


EXTRACTORS = {"mock": MockExtractor, "claude": ClaudeExtractor}


def get_extractor(mode: str = "mock") -> Extractor:
    """Select the extractor by name. Default is mock; nothing calls an API by accident."""
    if mode not in EXTRACTORS:
        raise ValueError(f"unknown extractor mode '{mode}'; choose from {sorted(EXTRACTORS)}")
    return EXTRACTORS[mode]()
