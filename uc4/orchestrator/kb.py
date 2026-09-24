"""
Knowledge base loader.

All decisions in the intake and requirement-pack steps come from these files,
never from code or model knowledge (brief Section 7). To change a rule, edit
the CSV and bump the version in kb_manifest.json - no code change needed.
"""

import csv
import json
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent / "kb"


def _read_csv(name: str) -> list[dict]:
    with open(KB_DIR / name, newline="", encoding="utf-8") as f:
        return [{k: (v.strip() if v else "") for k, v in row.items()} for row in csv.DictReader(f)]


class KnowledgeBase:
    def __init__(self, kb_dir: Path = KB_DIR):
        manifest = json.loads((kb_dir / "kb_manifest.json").read_text())
        self.version = manifest["kb_version"]
        self.applicant_type_rules = sorted(
            _read_csv("applicant_type_rules.csv"), key=lambda r: int(r["priority"])
        )
        self.jurisdiction_routing = {r["country_code"]: r for r in _read_csv("jurisdiction_routing.csv")}
        self.requirement_rules = _read_csv("requirement_rule.csv")
        self.document_quality_rules = _read_csv("document_quality_rules.csv")
        self.extraction_fields = _read_csv("extraction_fields.csv")
        self.registry_rules = _read_csv("registry_rules.csv")
        self.ubo_policy = {r["rule"]: r for r in _read_csv("ubo_policy.csv")}
        self.screening_rules = _read_csv("screening_rules.csv")
        self.risk_factors = _read_csv("risk_scoring_matrix.csv")
        self.risk_bands = _read_csv("risk_bands.csv")
        self.message_templates = {r["template_id"]: r for r in _read_csv("message_template.csv")}
        self.communication_rules = _read_csv("communication_rules.csv")
        self.decision_taxonomy = {r["decision"]: r for r in
                                  _read_csv("analyst_decision_taxonomy.csv")}
        self.audit_log_standard = {r["step"]: [a for a in r["required_actions"].split("|") if a]
                                   for r in _read_csv("audit_log_standard.csv")}
        self.communication_schedule = {r["setting"]: int(r["days"])
                                       for r in _read_csv("communication_schedule.csv")}
        self.adverse_media_categories = {r["category"]: r
                                         for r in _read_csv("adverse_media_categories.csv")}

    @property
    def ubo_threshold(self) -> float:
        """The percentage at or above which an owner must be verified (UB-01)."""
        return float(self.ubo_policy["ubo_threshold_percent"]["parameter"])

    @property
    def score_bands(self) -> list[tuple]:
        """(band, min, max) for the bands that are scored, worst last."""
        return [(r["band"], int(r["min_score"]), int(r["max_score"]))
                for r in self.risk_bands if r["min_score"] != ""]

    @property
    def hard_floors(self) -> list[tuple]:
        """(condition, band) floors that override the score outright."""
        return [(r["hard_floor_condition"], r["band"])
                for r in self.risk_bands if r["hard_floor_condition"]]

    @property
    def action_floors(self) -> list[tuple]:
        """(condition, recommended_action) in file order; the first match wins,
        so a sanctions escalation outranks an eligibility rejection."""
        return [(r["hard_floor_condition"], r["recommended_action"])
                for r in self.risk_bands if r["hard_floor_condition"]]

    def action_for_band(self, band: str) -> str:
        return next(r["recommended_action"] for r in self.risk_bands if r["band"] == band)

    def templates_for(self, situation: str, restricted: bool = False) -> list[dict]:
        """Templates allowed for a situation. A restricted case gets only the
        generic ones - those that state no reason and reveal no finding."""
        return [r for r in self.communication_rules
                if r["situation"] == situation
                and (not restricted or r["allowed_when_restricted"].lower() == "true")]

    def communication_rule(self, template_id: str) -> dict | None:
        return next((r for r in self.communication_rules if r["template_id"] == template_id), None)

    def screening_rule(self, check: str, result: str) -> dict | None:
        return next((r for r in self.screening_rules
                     if r["check"] == check and r["result"] == result), None)

    def media_category(self, category: str) -> dict | None:
        return self.adverse_media_categories.get(category)

    def registry_rule(self, check: str, condition: str) -> dict | None:
        return next((r for r in self.registry_rules
                     if r["check"] == check and r["condition"] == condition), None)

    def fields_for(self, document_type: str) -> list[dict]:
        """Every field this document type should yield, required ones included."""
        return [r for r in self.extraction_fields if r["document_type"] == document_type]

    def required_fields_for(self, document_type: str) -> list[str]:
        return [r["field_name"] for r in self.fields_for(document_type)
                if r["required"].lower() == "true"]

    def quality_rules_for(self, document_type: str) -> list[dict]:
        """Rules that apply to a document type, most specific first.

        A rule naming the document type wins over the catch-all '*', so
        missing_pages on an ownership chart routes to manual review while the
        same flag on any other document asks for a resubmission.
        """
        return sorted((r for r in self.document_quality_rules
                       if r["document_type"] in (document_type, "*")),
                      key=lambda r: r["document_type"] == "*")


# ---------------------------------------------------------------------------
# Tiny rule engine for applicant_type_rules.csv
# Condition format:  "<field> <op> <value>"  joined with " && "
# Operators: eq, in (values separated by ;), gt, is_true
# ---------------------------------------------------------------------------

def _check(condition: str, facts: dict) -> bool:
    field, op, *rest = condition.strip().split(" ", 2)
    value = rest[0] if rest else None
    actual = facts.get(field)
    if op == "eq":
        return str(actual) == value
    if op == "in":
        return str(actual) in value.split(";")
    if op == "gt":
        return actual is not None and float(actual) > float(value)
    if op == "is_true":
        return actual is True or str(actual).lower() == "true"
    raise ValueError(f"Unknown operator '{op}' in KB condition: {condition}")


def match_rule(conditions: str, facts: dict) -> bool:
    return all(_check(c, facts) for c in conditions.split("&&"))
