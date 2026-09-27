"""
The written parts of Steps 7 and 8  (brief Sections 5.8 and 5.11)

Two pieces of prose: the explanation on each risk factor, and the compliance
narrative in the evidence pack. Both are the only places in this pipeline where
a model writes something a person will read and act on, so both are fenced the
same way:

  - every evidence reference cited must already exist in the database. An id
    the model invented is rejected, not stored, because an analyst who cannot
    follow a reference back to a row has no way to check the claim;
  - nothing here decides anything. The band, the score and the recommended
    action are computed in code before a word is written. The narrative
    describes that decision; it does not make it;
  - the narrative is internal. evidence_pack.py stores it in the pack and
    nowhere a customer could be shown.

    MockNarrator    replays the scripted text (tests, demos)
    ClaudeNarrator  asks Claude  (STUB - see below)
"""

from dataclasses import dataclass, field


class UnknownEvidenceReference(ValueError):
    """Narrative text cited an id that is not in the database."""


@dataclass
class Narrative:
    text: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 1.0

    def validate(self, known_ids: set[str]) -> "Narrative":
        unknown = [r for r in self.evidence_refs if r not in known_ids]
        if unknown:
            raise UnknownEvidenceReference(
                f"narrative cites {unknown}, which do not exist on this case; "
                f"a reference an analyst cannot follow is worse than none")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError(f"confidence {self.confidence} is outside 0-1")
        return self


class Narrator:
    mode = "base"
    version: str | None = None

    def explain_factor(self, factor: dict, context: dict) -> Narrative:
        raise NotImplementedError

    def compliance_narrative(self, pack: dict, known_ids: set[str]) -> Narrative:
        raise NotImplementedError


class MockNarrator(Narrator):
    """Uses the KB's own description for each factor and assembles the narrative
    from the pack. Deterministic, and it cites only ids the pack already holds."""

    mode = "mock"
    version = None

    def explain_factor(self, factor: dict, context: dict) -> Narrative:
        return Narrative(text=factor["description"],
                         evidence_refs=list(context.get("evidence_refs", [])),
                         confidence=1.0)

    def compliance_narrative(self, pack: dict, known_ids: set[str]) -> Narrative:
        lines = [pack["applicant_summary"]]
        if pack["open_holds"]:
            lines.append("Open holds: " + "; ".join(h["reason"] for h in pack["open_holds"]) + ".")
        if pack["risk_factors"]:
            lines.append("Risk factors: "
                         + "; ".join(f"{f['factor']} ({f['weight']})" for f in pack["risk_factors"])
                         + ".")
        else:
            lines.append("No risk factors fired.")
        if pack["missing_or_conflicting_evidence"]:
            lines.append(pack["missing_or_conflicting_evidence"])
        lines.append(f"Band {pack['risk_band']}"
                     + (f" at score {pack['risk_score']}" if pack["risk_score"] != "" else "")
                     + f"; recommended next action {pack['recommended_next_action']}.")
        if pack["requires_human_signoff"]:
            lines.append("Human sign-off is required; the system cannot decide this case.")
        refs = sorted({r for f in pack["risk_factors"] for r in f["evidence_refs"]} & known_ids)
        return Narrative(text=" ".join(lines), evidence_refs=refs, confidence=1.0)


class ClaudeNarrator(Narrator):
    """Ask Claude to write the explanation and the compliance narrative.

    TODO: not wired up. No API call is made yet - either method raises.

    When implemented it must:
      - be given the computed band, score and factors and told to DESCRIBE them.
        It must not be asked what the risk is; that is decided before it is
        called and a model that disagreed would be ignored;
      - require exactly {"text": "...", "evidence_refs": [...], "confidence": 0-1}
        with no prose around it, re-asking once if the reply does not parse;
      - pass the result through Narrative.validate() against the ids actually on
        the case, so an invented reference raises rather than reaching an
        analyst who would reasonably assume it was real;
      - write allegations as allegations. Adverse media is what was reported,
        not what happened, and the prompt must say so;
      - never name a sanctions or PEP finding in anything customer-facing. The
        narrative is internal and evidence_pack.py keeps it that way;
      - set `version` to the model id plus the prompt version for the audit row.
    """

    mode = "claude"

    def __init__(self, model: str = "claude-opus-5", prompt_version: str = "narr-v1"):
        self.model = model
        self.prompt_version = prompt_version
        self.version = f"{model}/{prompt_version}"

    def _stub(self):
        raise NotImplementedError(
            "ClaudeNarrator is a stub: no API call is wired up yet. "
            "Run with the mock narrator (the default) until it is.")

    def explain_factor(self, factor: dict, context: dict) -> Narrative:
        self._stub()

    def compliance_narrative(self, pack: dict, known_ids: set[str]) -> Narrative:
        self._stub()


NARRATORS = {"mock": MockNarrator, "claude": ClaudeNarrator}


def get_narrator(mode: str = "mock") -> Narrator:
    if mode not in NARRATORS:
        raise ValueError(f"unknown narrator mode '{mode}'; choose from {sorted(NARRATORS)}")
    return NARRATORS[mode]()
