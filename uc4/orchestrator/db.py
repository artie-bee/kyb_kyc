"""
SQLite store for everything the orchestrator WRITES.
(CSV = read-only inputs such as KB rules and mock provider data.)

Only the tables used by the first two steps are created here; later steps add
their own tables with the same column names as the ER diagram.
"""

import sqlite3
from pathlib import Path

from . import clock

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
    -- set by Step 6 and read by Step 8: any sanctions, PEP or adverse-media
    -- finding forces generic customer wording and blocks automatic approval.
    restricted_finding INTEGER NOT NULL DEFAULT 0,
    requires_human_signoff INTEGER NOT NULL DEFAULT 0,
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
    ownership_percentage REAL NOT NULL,
    -- the per-link percentages, so effective ownership can be multiplied along
    -- the chain rather than taken on trust from the declared total
    ownership_chain_percentages TEXT,
    control_type TEXT, ownership_path TEXT,
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
    needs_analyst_correction INTEGER NOT NULL DEFAULT 0,
    -- how the value got here. 'extracted' is a machine reading (OCR or a live
    -- model). In mock mode nothing reads an uploaded file, so an analyst types
    -- the values from it: 'awaiting_analyst_entry' until they do, then
    -- 'entered_by_analyst'. A keyed value is never recorded as extracted.
    entry_method TEXT NOT NULL DEFAULT 'extracted'
        CHECK (entry_method IN ('extracted', 'awaiting_analyst_entry', 'entered_by_analyst'))
);
CREATE TABLE IF NOT EXISTS registry_check (
    check_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    applicant_id TEXT NOT NULL REFERENCES applicant(applicant_id),
    provider_name TEXT NOT NULL, company_status TEXT NOT NULL,
    -- what the register holds; the match columns are computed from these
    registry_legal_name TEXT, registry_number TEXT, registry_address TEXT,
    registry_directors TEXT,
    name_match TEXT, number_match TEXT, address_match TEXT, director_match TEXT,
    ubo_supported_by_registry TEXT, high_risk_jurisdiction_or_industry TEXT,
    confidence REAL, result TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS identity_check (
    check_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    individual_id TEXT NOT NULL REFERENCES individual(individual_id),
    provider_name TEXT NOT NULL, document_result TEXT, liveness_result TEXT,
    biometric_result TEXT, address_result TEXT, name_dob_match TEXT,
    document_expired TEXT, duplicate_individual_detected TEXT, result TEXT NOT NULL
);
-- Non-blocking findings: the case moves on, but these travel with it to the
-- risk step, each pointing at the evidence it was drawn from.
-- One mechanism for 'this case cannot move yet'. Any step may place a hold;
-- only the step that placed it, or a named human, may release it.
CREATE TABLE IF NOT EXISTS risk_assessment (
    assessment_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE REFERENCES onboarding_case(case_id),
    risk_score INTEGER, risk_band TEXT NOT NULL, recommended_action TEXT NOT NULL,
    confidence REAL, insufficient_evidence_flag INTEGER NOT NULL DEFAULT 0,
    requires_human_signoff INTEGER NOT NULL DEFAULT 0, risk_matrix_version TEXT
);
CREATE TABLE IF NOT EXISTS risk_factor (
    factor_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES risk_assessment(assessment_id),
    factor TEXT NOT NULL, weight INTEGER NOT NULL, explanation TEXT,
    evidence_refs TEXT
);
-- The analyst's pack. draft_compliance_narrative is internal and is never
-- copied into anything the customer is shown.
CREATE TABLE IF NOT EXISTS evidence_pack (
    evidence_pack_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE REFERENCES onboarding_case(case_id),
    assessment_id TEXT REFERENCES risk_assessment(assessment_id),
    applicant_summary TEXT, entity_details TEXT, individuals TEXT, ubos TEXT,
    checklist_completeness TEXT, provider_results TEXT, risk_factors TEXT,
    open_holds TEXT, missing_or_conflicting_evidence TEXT,
    recommended_next_action TEXT, draft_compliance_narrative TEXT,
    evidence_refs TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS communication (
    communication_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    template_id TEXT NOT NULL, audience TEXT NOT NULL, message_type TEXT NOT NULL,
    situation TEXT, approval_status TEXT NOT NULL DEFAULT 'pending_approval',
    approved_by TEXT, sent_status TEXT NOT NULL DEFAULT 'not_sent',
    rendered_text TEXT NOT NULL, created_at TEXT NOT NULL
);
-- Where a "sent" message actually goes. No mail leaves this POC.
CREATE TABLE IF NOT EXISTS outbox (
    outbox_id TEXT PRIMARY KEY,
    communication_id TEXT NOT NULL REFERENCES communication(communication_id),
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    audience TEXT NOT NULL, body TEXT NOT NULL, sent_at TEXT NOT NULL
);
-- Work for a person that the system will not do for itself.
CREATE TABLE IF NOT EXISTS compliance_task (
    task_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    task TEXT NOT NULL, reason TEXT NOT NULL, owner TEXT NOT NULL,
    created_at TEXT NOT NULL, completed_by TEXT, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS human_decision (
    decision_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    reviewer TEXT NOT NULL, reviewer_role TEXT NOT NULL, decision TEXT NOT NULL,
    reason_code TEXT NOT NULL, rationale TEXT NOT NULL, evidence_relied_on TEXT,
    override_flag INTEGER NOT NULL DEFAULT 0, override_reason TEXT,
    -- stricter or more_lenient than the recommendation, from the taxonomy's
    -- severity order. A more lenient override is the one that needs scrutiny.
    override_direction TEXT,
    escalation_target TEXT, customer_template_id TEXT, timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS case_hold (
    hold_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    placed_by_step TEXT NOT NULL, reason TEXT NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('customer', 'analyst', 'compliance')),
    placed_at TEXT NOT NULL,
    released_by TEXT, release_reason TEXT, released_at TEXT
);
CREATE TABLE IF NOT EXISTS screening_check (
    check_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    subject_type TEXT NOT NULL,
    applicant_id TEXT REFERENCES applicant(applicant_id),
    individual_id TEXT REFERENCES individual(individual_id),
    provider_name TEXT NOT NULL,
    sanctions_result TEXT NOT NULL, pep_result TEXT NOT NULL,
    adverse_media_result TEXT NOT NULL, severity TEXT NOT NULL,
    evidence_refs TEXT, attempts INTEGER NOT NULL DEFAULT 1
);
-- A sanctions verdict is never rewritten in place: only a human decision can
-- resolve one, so the row that records it is append-only like the audit trail.
CREATE TRIGGER IF NOT EXISTS screening_sanctions_no_downgrade
BEFORE UPDATE OF sanctions_result ON screening_check
WHEN OLD.sanctions_result IN ('possible_match', 'clear_match')
BEGIN SELECT RAISE(ABORT, 'a sanctions match may only be resolved by a human decision'); END;
CREATE TABLE IF NOT EXISTS finding (
    finding_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    source TEXT NOT NULL, rule_id TEXT NOT NULL, summary TEXT NOT NULL,
    evidence_refs TEXT, blocking INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_event (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    actor_type TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL,
    payload_summary TEXT NOT NULL, model_or_prompt_version TEXT,
    timestamp TEXT NOT NULL
);
-- How an applicant reaches their own case in the customer portal. Only a hash
-- of the token is kept, so a copy of this database does not let anyone sign in
-- as a customer. Nothing else about the portal is stored: what it shows is read
-- from the same tables the console reads, so the two cannot disagree.
CREATE TABLE IF NOT EXISTS portal_token (
    token_hash TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES onboarding_case(case_id),
    issued_by TEXT NOT NULL, issued_at TEXT NOT NULL, revoked_at TEXT,
    -- after this the link stops working; a token with none is treated as expired
    expires_at TEXT
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
    "registry_check": ("check_id", "REG-"),
    "identity_check": ("check_id", "IDC-"),
    "finding": ("finding_id", "FND-"),
    "screening_check": ("check_id", "SCR-"),
    "case_hold": ("hold_id", "HLD-"),
    "risk_assessment": ("assessment_id", "RSK-"),
    "risk_factor": ("factor_id", "RF-"),
    "evidence_pack": ("evidence_pack_id", "EVP-"),
    "communication": ("communication_id", "COM-"),
    "outbox": ("outbox_id", "OUT-"),
    "compliance_task": ("task_id", "TSK-"),
    "human_decision": ("decision_id", "DEC-"),
    "audit_event": ("event_id", "EVT-"),
}


# Cases created through the customer portal's demo application form. They are
# numbered on their own, never mixed into the dataset's WAL-ONB- sequence, and
# every dataset comparison leaves them out.
DEMO_CASE_PREFIX = "WAL-DEMO-"


def is_demo_case(case_id: str | None) -> bool:
    return bool(case_id) and case_id.startswith(DEMO_CASE_PREFIX)


# Columns added after a database may already exist. CREATE TABLE IF NOT EXISTS
# leaves an older table as it was, so each one is added here if it is missing.
_ADDED_COLUMNS = (
    ("portal_token", "expires_at", "TEXT"),
    ("extracted_field", "entry_method",
     "TEXT NOT NULL DEFAULT 'extracted' CHECK (entry_method IN "
     "('extracted', 'awaiting_analyst_entry', 'entered_by_analyst'))"),
)


def connect(path: str | Path = "onboarding.db",
            same_thread_only: bool = True) -> sqlite3.Connection:
    """Open the store.

    same_thread_only=False is for the Streamlit app, which reruns its script in
    a new thread each time and would otherwise be refused the connection. Safe
    there because Streamlit runs one script at a time per session; not something
    to reach for elsewhere.
    """
    conn = sqlite3.connect(path, check_same_thread=same_thread_only)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    for table, column, decl in _ADDED_COLUMNS:
        if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return conn


def now() -> str:
    """The timestamp for a row, from the one injected clock (orchestrator/clock.py)."""
    return clock.stamp()


def next_id(conn: sqlite3.Connection, table: str, prefix: str | None = None) -> str:
    col, default = ID_PREFIX[table]
    prefix = prefix or default
    width = 6 if table in ("audit_event", "checklist_item") else 4
    if table == "onboarding_case":
        # Each case series counts itself, so a demo case never takes a number
        # from the dataset's sequence or the other way round.
        (count,) = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE ?",
                                (prefix + "%",)).fetchone()
    else:
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
