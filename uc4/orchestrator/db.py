"""
SQLite store for everything the orchestrator WRITES.
(CSV = read-only inputs such as KB rules and mock provider data.)

Only the tables used by the first two steps are created here; later steps add
their own tables with the same column names as the ER diagram.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS applicant (
    applicant_id TEXT PRIMARY KEY, legal_name TEXT NOT NULL, trading_name TEXT,
    registration_number TEXT, entity_type TEXT NOT NULL, country TEXT NOT NULL,
    business_activity TEXT, expected_usage TEXT, risk_segment TEXT
);
CREATE TABLE IF NOT EXISTS onboarding_case (
    case_id TEXT PRIMARY KEY,
    applicant_id TEXT NOT NULL REFERENCES applicant(applicant_id),
    applicant_type TEXT, jurisdiction_path TEXT, entity_scope TEXT,
    source_channel TEXT NOT NULL, status TEXT NOT NULL, assigned_owner TEXT,
    next_action_owner TEXT, white_label_branch_flag INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS individual (
    individual_id TEXT PRIMARY KEY,
    applicant_id TEXT NOT NULL REFERENCES applicant(applicant_id),
    role TEXT NOT NULL, full_name TEXT NOT NULL, date_of_birth TEXT,
    nationality TEXT, residence_country TEXT, id_document_id TEXT,
    relationship_to_entity TEXT
);
CREATE TABLE IF NOT EXISTS ubo (
    ubo_id TEXT PRIMARY KEY,
    applicant_id TEXT NOT NULL REFERENCES applicant(applicant_id),
    individual_id TEXT NOT NULL REFERENCES individual(individual_id),
    ownership_percentage REAL NOT NULL, control_type TEXT, ownership_path TEXT,
    verification_status TEXT NOT NULL DEFAULT 'review'
);
CREATE TABLE IF NOT EXISTS requirement_pack (
    pack_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE REFERENCES onboarding_case(case_id),
    applicant_type TEXT NOT NULL, jurisdiction TEXT NOT NULL,
    entity_type TEXT NOT NULL, kb_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checklist_item (
    item_id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL REFERENCES requirement_pack(pack_id),
    rule_id TEXT NOT NULL,
    subject_individual_id TEXT REFERENCES individual(individual_id),
    document_type TEXT NOT NULL, level TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    resubmission_attempts INTEGER NOT NULL DEFAULT 0,
    note TEXT
);
CREATE TABLE IF NOT EXISTS document (
    document_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    subject_individual_id TEXT REFERENCES individual(individual_id),
    document_type TEXT NOT NULL, file_name TEXT NOT NULL, upload_time TEXT,
    -- quality_status is the live status and an analyst release can change it.
    -- quality_status_at_screen is what Step 3 decided and is never rewritten, so
    -- the screening verdict stays auditable after a human overrides it.
    quality_status TEXT NOT NULL DEFAULT 'pending',
    quality_status_at_screen TEXT,
    quality_flags TEXT,
    expiry_date TEXT, document_date TEXT, issue_country TEXT,
    resubmission_required INTEGER NOT NULL DEFAULT 0, resubmission_reasons TEXT,
    released_by TEXT, release_reason TEXT
);
-- Which uploaded file satisfies which checklist item (a rule asking for two
-- director IDs produces two items, each with its own document).
CREATE TABLE IF NOT EXISTS checklist_item_document (
    item_id TEXT NOT NULL REFERENCES checklist_item(item_id),
    document_id TEXT NOT NULL REFERENCES document(document_id),
    PRIMARY KEY (item_id, document_id)
);
CREATE TABLE IF NOT EXISTS extracted_field (
    field_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(document_id),
    name TEXT NOT NULL, value TEXT, confidence REAL NOT NULL, source_page INTEGER,
    corrected_by_analyst INTEGER NOT NULL DEFAULT 0,
    -- set when the value is unusable as read: missing, or below the confidence
    -- floor. An analyst supplies the value; the orchestrator never guesses it.
    needs_analyst_correction INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_event (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    actor_type TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL,
    payload_summary TEXT NOT NULL, model_or_prompt_version TEXT,
    timestamp TEXT NOT NULL
);
-- Section 12: audit history must never be modified or deleted.
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;
"""

ID_PREFIX = {
    "onboarding_case": ("case_id", "WAL-ONB-"),
    "applicant": ("applicant_id", "APP-"),
    "individual": ("individual_id", "IND-"),
    "ubo": ("ubo_id", "UBO-"),
    "requirement_pack": ("pack_id", "PACK-"),
    "checklist_item": ("item_id", "CHK-"),
    "document": ("document_id", "DOC-"),
    "extracted_field": ("field_id", "FLD-"),
    "audit_event": ("event_id", "EVT-"),
}


def connect(path: str | Path = "onboarding.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_id(conn: sqlite3.Connection, table: str) -> str:
    col, prefix = ID_PREFIX[table]
    width = 6 if table in ("audit_event", "checklist_item") else 4
    (count,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return f"{prefix}{count + 1:0{width}d}"


def audit(conn, case_id, actor_type, actor_id, action, payload_summary, version=None):
    conn.execute(
        "INSERT INTO audit_event VALUES (?,?,?,?,?,?,?,?)",
        (next_id(conn, "audit_event"), case_id, actor_type, actor_id, action,
         payload_summary, version, now()),
    )


def update_case(conn, case_id, **fields):
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE onboarding_case SET {cols} WHERE case_id = ?", (*fields.values(), case_id))
