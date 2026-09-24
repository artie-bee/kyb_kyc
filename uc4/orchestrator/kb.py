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

    @property
    def ubo_threshold(self) -> float:
        """The percentage at or above which an owner must be verified (UB-01)."""
        return float(self.ubo_policy["ubo_threshold_percent"]["parameter"])

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
