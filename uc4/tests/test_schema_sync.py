"""
The schema file must describe the data exactly.

Fails if a table, column or enum value exists in the dataset CSVs or in the
orchestrator database but is missing from wallester_uc4_schema.py. Without this
the schema quietly becomes documentation of a system that no longer exists.

Run: python -m pytest tests -q
"""
import csv
import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import db                                                # noqa: E402
from tools.dataset_to_applications import DEFAULT_DATASET                  # noqa: E402

SCHEMA_PATH = DEFAULT_DATASET.parent / "wallester_uc4_schema.py"


def _schema():
    spec = importlib.util.spec_from_file_location("wallester_uc4_schema", SCHEMA_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_schema_file_exists():
    assert SCHEMA_PATH.exists(), f"no schema file at {SCHEMA_PATH}"


def test_every_dataset_table_and_column_is_in_the_schema():
    schema = _schema()
    missing = []
    for path in sorted(DEFAULT_DATASET.glob("*.csv")):
        table = path.stem
        with open(path, newline="", encoding="utf-8-sig") as f:
            columns = next(csv.reader(f))
        if table not in schema.TABLES:
            missing.append(f"table {table} is not in TABLES")
            continue
        for column in columns:
            if column not in schema.TABLES[table]:
                missing.append(f"{table}.{column} is not in TABLES")
    assert not missing, "schema is behind the dataset:\n  " + "\n  ".join(missing)


def test_every_dataset_enum_value_is_in_the_schema():
    schema = _schema()
    missing = []
    for path in sorted(DEFAULT_DATASET.glob("*.csv")):
        table = path.stem
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                for column, value in row.items():
                    allowed = schema.ENUMS.get((table, column))
                    if allowed is None or not value:
                        continue
                    for part in value.split("|"):      # pipe lists are multi-valued
                        if part not in allowed:
                            missing.append(f"{table}.{column} = {part!r}")
    assert not missing, ("enum values in the data but not in the schema:\n  "
                         + "\n  ".join(sorted(set(missing))))


def test_every_database_table_and_column_is_in_the_schema():
    schema = _schema()
    conn = db.connect(":memory:")
    missing = []
    for (table,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if table not in schema.DB_TABLES:
            missing.append(f"table {table} is not in DB_TABLES")
            continue
        for row in conn.execute(f"PRAGMA table_info({table})"):
            if row[1] not in schema.DB_TABLES[table]:
                missing.append(f"{table}.{row[1]} is not in DB_TABLES")
    assert not missing, "schema is behind the database:\n  " + "\n  ".join(missing)


def test_the_columns_this_project_added_are_all_described():
    """The specific additions made while building Steps 3-6, named explicitly so
    a future edit that drops one is caught here rather than in a demo."""
    schema = _schema()
    for table, column in (
            ("document", "quality_status_at_screen"),
            ("document", "released_by"),
            ("extracted_field", "needs_analyst_correction"),
            ("registry_check", "attempts"),
            ("finding", "rule_id"),
            ("finding", "evidence_refs"),
            ("onboarding_case", "restricted_finding"),
            ("onboarding_case", "requires_human_signoff"),
            ("screening_check", "sanctions_result"),
            ("screening_check", "adverse_media_result"),
            ("screening_check", "attempts")):
        assert column in schema.DB_TABLES[table], f"{table}.{column} missing from DB_TABLES"

    for table, column in (
            ("applicant", "vat_registered"),
            ("requirement_rule", "condition_key"),
            ("ubo", "ownership_chain_percentages"),
            ("registry_check", "registry_legal_name"),
            ("registry_check", "registry_number"),
            ("registry_check", "registry_address"),
            ("registry_check", "registry_directors")):
        assert column in schema.TABLES[table], f"{table}.{column} missing from TABLES"

    flags = schema.ENUMS[("document", "quality_flags")]
    assert "wrong_document_type" in flags and "document_too_old" in flags
    assert set(schema.ENUMS[("ubo", "verification_status")]) >= {"verified", "unverified", "pending"}
    assert set(schema.DB_ENUMS[("document", "quality_status_at_screen")]) == \
        set(schema.ENUMS[("document", "quality_status")])
    # the four cases added for the missing scenarios
    assert {"rejected", "closed_withdrawn"} <= set(schema.ENUMS[("onboarding_case", "status")])
    assert "clear_match" in schema.ENUMS[("screening_check", "sanctions_result")]
    assert "unavailable" in schema.ENUMS[("screening_check", "adverse_media_result")]


def test_the_json_schema_export_is_current():
    schema = _schema()
    exported = SCHEMA_PATH.with_suffix(".json")
    assert exported.exists(), f"no JSON Schema export at {exported}; run {SCHEMA_PATH.name}"
    import json
    on_disk = json.loads(exported.read_text(encoding="utf-8"))
    assert on_disk == schema.json_schema(), \
        f"{exported.name} is stale; regenerate it with: python {SCHEMA_PATH.name}"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
