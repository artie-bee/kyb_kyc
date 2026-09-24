"""
Adverse-media relevance  (brief Section 5.7)

A media provider returns articles. Two questions have to be answered about them
and neither is arithmetic: is this article actually about *this* person, and how
serious is what it alleges. That is the AI part of screening, so it sits behind
an interface like every other judgement in this pipeline.

    MockMediaRelevance    replays the scripted category (tests, demos)
    ClaudeMediaRelevance  asks Claude  (STUB - see below)

Two things this must never do, whatever is behind it:

  - decide that coverage is irrelevant and drop it silently. An assessment of
    'not relevant' is recorded with its reasoning and its evidence references,
    because the analyst may disagree.
  - touch a sanctions or PEP result. Media relevance has no bearing on either,
    and a model that returned one would be ignored: screening.py reads only the
    category from here.

Allegations are allegations. Nothing in this module treats reported conduct as
established fact, and the category describes the seriousness of what is alleged,
not a conclusion about the person.
"""

from dataclasses import dataclass, field

# Must match the category column of kb/adverse_media_categories.csv.
CATEGORIES = ("none", "low_relevance", "moderate", "serious", "unavailable")


class UnknownMediaCategory(ValueError):
    """An assessor returned a category the KB does not define."""


@dataclass
class MediaAssessment:
    relevant: bool = False
    category: str = "none"
    confidence: float = 1.0
    evidence_refs: list[str] = field(default_factory=list)
    notes: str = ""

    def validate(self) -> "MediaAssessment":
        if self.category not in CATEGORIES:
            raise UnknownMediaCategory(
                f"category {self.category!r} is not one of {list(CATEGORIES)}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError(f"confidence {self.confidence} is outside 0-1")
        return self


class MediaRelevanceAssessor:
    mode = "base"
    version: str | None = None

    def assess(self, subject: dict, response) -> MediaAssessment:
        raise NotImplementedError


class MockMediaRelevance(MediaRelevanceAssessor):
    """Replays the category the provider row already carries."""

    mode = "mock"
    version = None

    def assess(self, subject: dict, response) -> MediaAssessment:
        category = response.adverse_media_result
        return MediaAssessment(
            relevant=category in ("moderate", "serious"),
            category=category,
            confidence=1.0 if category != "unavailable" else 0.0,
            evidence_refs=list(response.evidence_refs),
            notes="scripted assessment; no model was called").validate()


class ClaudeMediaRelevance(MediaRelevanceAssessor):
    """Ask Claude whether the coverage is about this subject, and how serious.

    TODO: not wired up. No API call is made yet - calling assess() raises.

    When implemented it must:
      - send the article text plus the subject's name, date of birth and role,
        and require exactly
        {"relevant": bool, "category": one of CATEGORIES, "confidence": 0-1,
         "evidence_refs": [...]}
        with no prose around it, re-asking once if the reply does not parse;
      - pass the result through MediaAssessment.validate(), so an invented
        category raises rather than reaching a case;
      - return every evidence reference it relied on. A finding an analyst
        cannot trace back to an article is not usable;
      - return category 'unavailable' when it cannot tell, never 'none'. Those
        mean opposite things: one is 'no coverage found', the other is 'I could
        not look', and SC-07 turns the second into insufficient evidence;
      - describe what is ALLEGED and by whom. It must not assert that reported
        conduct occurred, and the prompt must say so;
      - never be asked about sanctions or PEP status; those are provider list
        results and no model opinion enters them.
    """

    mode = "claude"

    def __init__(self, model: str = "claude-opus-5", prompt_version: str = "media-v1"):
        self.model = model
        self.prompt_version = prompt_version
        self.version = f"{model}/{prompt_version}"

    def assess(self, subject: dict, response) -> MediaAssessment:
        raise NotImplementedError(
            "ClaudeMediaRelevance is a stub: no API call is wired up yet. "
            "Run with the mock assessor (the default) until it is.")


ASSESSORS = {"mock": MockMediaRelevance, "claude": ClaudeMediaRelevance}


def get_media_assessor(mode: str = "mock") -> MediaRelevanceAssessor:
    if mode not in ASSESSORS:
        raise ValueError(f"unknown media assessor mode '{mode}'; choose from {sorted(ASSESSORS)}")
    return ASSESSORS[mode]()
