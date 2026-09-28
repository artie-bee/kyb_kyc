"""
Everything the screens read, and every action they can take.

The golden rule: this app never writes to the database itself. It reads freely,
but every change goes through the orchestrator's own functions, so the holds,
the role checks and the sanctions rules apply exactly as they do in the
pipeline. A screen that wrote its own UPDATE would be a second implementation of
the rules, and the second one is always the one that is wrong.

tests/test_app.py scans this package for SQL writes and fails if it finds any.
"""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from orchestrator import (clock, db, demo_scenarios, holds, live_mode,    # noqa: E402
                          portal_access)
from orchestrator.kb import KnowledgeBase                                  # noqa: E402
from orchestrator.quality_checker import get_upload_checker                # noqa: E402
from orchestrator import settings                                          # noqa: E402
from orchestrator.steps import (analyst_review, communication, decision,   # noqa: E402
                                document_quality, evidence_pack, extraction,
                                requirement_pack, verification)
from orchestrator.orchestrator import (QUALITY_CHECKER_MODE,              # noqa: E402
                                       process_application, resume)
from orchestrator.providers import DEMO_SCENARIOS, SIMULATED               # noqa: E402
from orchestrator.steps.intake import DEMO_ORIGIN                          # noqa: E402
from tools.export_case import export                                       # noqa: E402
from tools.run_demo import run as run_demo                                 # noqa: E402
from tools.dataset_to_applications import DEFAULT_OUT as APPLICATIONS       # noqa: E402

DB_PATH = UC4 / "onboarding.db"
SAMPLE_DOCS = UC4 / "sample_documents"
UPLOADS = document_quality.UPLOADS
# Applications typed into the portal's demo form, one JSON file per WAL-DEMO-
# case - the same shape as the dataset's application files, and like them not a
# table. Git-ignored, and emptied by Reset demo.
PORTAL_APPLICATIONS = UC4 / "portal_applications"


# ---------------------------------------------------------------------------
# Connection and demo reset
# ---------------------------------------------------------------------------

def connect(path: Path = DB_PATH):
    # Streamlit reruns its script in a new thread each time, so the connection
    # has to be usable from more than the one it was opened on.
    return db.connect(path, same_thread_only=False)


def reset_demo(path: Path = DB_PATH) -> None:
    """Rebuild the database to the demo start state.

    Runs every case as far as the pipeline can take it alone and stops, so the
    releases and decisions are left for the presenter to make on screen.
    """
    if path.exists():
        path.unlink()
    # Rebuilding the database drops every WAL-DEMO- case with it. Their saved
    # applications and every file uploaded through the portal go too - but only
    # for the demo's own database, never for a copy a test is resetting.
    if Path(path).resolve() == DB_PATH.resolve():
        _clear(PORTAL_APPLICATIONS, "*.json")
        _clear(UPLOADS, "*/*")
    conn = db.connect(path)
    run_demo(conn, verbose=False, stop_before_human_actions=True)
    conn.commit()
    conn.close()


def _clear(folder: Path, pattern: str) -> None:
    if not folder.exists():
        return
    for f in folder.glob(pattern):
        if f.is_file():
            f.unlink()
    for d in sorted((p for p in folder.glob("*") if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()


def mode_badge() -> str:
    return live_mode.status()


def kb() -> KnowledgeBase:
    return KnowledgeBase()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


def ageing_days(created_at: str) -> int:
    return clock.days_since(created_at)


def dashboard(conn) -> list[dict]:
    """Every case, with what an operations team needs to triage it."""
    out = []
    for case in _rows(conn, "SELECT * FROM onboarding_case ORDER BY case_id"):
        risk = conn.execute("SELECT risk_band, risk_score FROM risk_assessment "
                            "WHERE case_id = ?", (case["case_id"],)).fetchone()
        applicant = conn.execute("SELECT legal_name FROM applicant WHERE applicant_id = ?",
                                 (case["applicant_id"],)).fetchone()
        open_now = holds.open_holds(conn, case["case_id"])
        out.append({
            "case_id": case["case_id"],
            "applicant": applicant["legal_name"] if applicant else "",
            "applicant_type": case["applicant_type"] or "",
            "status": case["status"],
            "owner": case["next_action_owner"] or "",
            "risk_band": risk["risk_band"] if risk else "",
            # a string, not a mixed int/blank column: the table renderer types
            # each column and a mix of the two will not convert
            "risk_score": ("" if not risk or risk["risk_score"] is None
                           else str(risk["risk_score"])),
            "open_holds": len(open_now),
            "hold_detail": "; ".join(f"{h.code} ({h.owner})" for h in open_now),
            "restricted": bool(case["restricted_finding"]),
            "ageing_days": ageing_days(case["created_at"]),
        })
    return out


# ---------------------------------------------------------------------------
# Agent reuse (brief Section 10.9)
# ---------------------------------------------------------------------------

# The six components the brief names. For each: what a generic KYC/KYB agent
# already does, what Wallester changes, and the KB file that carries the change.
# Every override lives in a CSV rather than in code, which is the whole claim -
# reuse the agent, configure the policy.
# The six components the brief names in Section 10.9. The "generic" and
# "override" strings are the brief's own wording and are quoted verbatim - this
# screen reports the split, it does not paraphrase it. "notes" is this POC's
# own commentary on how the override is realised, kept separate so the two are
# never confused.
REUSE_CAPTION = ("This POC is a standalone build that represents the generic-agent / "
                 "Wallester-variant split. It does not run on an existing agent.")

REUSE_COMPONENTS = [
    {
        "component": "Document quality rules",
        "generic": "Reused",
        "override": "Wallester thresholds and accepted document types",
        "notes": "Which fault does what: an incomplete ownership chart goes to an "
                 "analyst while an incomplete anything else goes back to the customer, "
                 "suspected tampering is never sent back as a resubmission, and each "
                 "failure carries a Wallester reason code.",
        "kb_files": ["document_quality_rules.csv"],
    },
    {
        "component": "OCR extraction",
        "generic": "Reused",
        "override": "Wallester field map and required fields",
        "notes": "Which fields each document type must yield, which are required, and "
                 "what each is used for downstream, plus the 0.70 confidence floor "
                 "below which a value goes to a person rather than into a decision.",
        "kb_files": ["extraction_fields.csv"],
    },
    {
        "component": "Registry validation",
        "generic": "Reused pattern",
        "override": "Configurable registry providers, not Companies House-only",
        "notes": "The provider is an interface, and the EE and UK registers are "
                 "selected by jurisdiction. What the answers mean is Wallester's: a "
                 "dissolved company blocks, an address mismatch is a finding, and a "
                 "provider that does not answer is retried once and then stops the case.",
        "kb_files": ["registry_rules.csv", "ubo_policy.csv"],
    },
    {
        "component": "Risk scoring",
        "generic": "Reused pattern",
        "override": "Wallester-specific policy matrix",
        "notes": "Every factor, weight and threshold, plus the hard floors that "
                 "override the arithmetic. All weights are POC placeholders for "
                 "Wallester to confirm; the brief does not state them.",
        "kb_files": ["risk_scoring_matrix.csv", "risk_bands.csv"],
    },
    {
        "component": "Customer communications",
        "generic": "Partially reused",
        "override": "Wallester-approved templates required",
        "notes": "Only the approved library may be used. A case with a restricted "
                 "finding gets the generic templates alone, and a confirmed sanctions "
                 "match produces no automatic message at all - a compliance task "
                 "instead.",
        "kb_files": ["message_template.csv", "communication_rules.csv",
                     "communication_schedule.csv"],
    },
    {
        "component": "Audit summary",
        "generic": "Reused",
        "override": "Wallester case fields and decision taxonomy",
        "notes": "Which actions every case must carry for each step it passed through, "
                 "who may take which decision at which band, and the export bundle a "
                 "reviewer receives.",
        "kb_files": ["audit_log_standard.csv", "analyst_decision_taxonomy.csv"],
    },
]


def reuse_table() -> dict:
    """The reuse picture, with a real version against every KB file named.

    Versions come from kb_manifest.json rather than this module, so a KB bump
    shows up here without anyone remembering to edit the screen.
    """
    import json
    manifest = json.loads((UC4 / "kb" / "kb_manifest.json").read_text(encoding="utf-8"))
    by_file = {item["file"]: (name, item["version"])
               for name, item in manifest["items"].items()}

    rows = []
    for entry in REUSE_COMPONENTS:
        files = []
        for file_name in entry["kb_files"]:
            name, version = by_file.get(file_name, (file_name, "?"))
            path = UC4 / "kb" / file_name
            files.append({
                "file": f"kb/{file_name}",
                "version": version,
                "exists": path.exists(),
                "rules": max(0, sum(1 for _ in path.open(encoding="utf-8")) - 1)
                         if path.exists() else 0,
                "path": path,
            })
        rows.append({**entry, "files": files})
    return {"kb_version": manifest["kb_version"], "rows": rows}


# ---------------------------------------------------------------------------
# White-label future phase (brief Section 6.2)
# ---------------------------------------------------------------------------

# Named in the routing audit event the white-label branch writes. Listed here so
# the screen and the audit trail cannot drift apart.
FUTURE_PHASE_STEPS = [
    ("KYB", "Partner company verification, which this POC does run"),
    ("API integration", "Connecting the partner's platform to the card APIs"),
    ("Visa co-brand approval", "Scheme approval for the co-branded programme"),
    ("BIN and 3DS configuration", "Card range and authentication setup"),
    ("Go-live testing", "End-to-end readiness before the first real card"),
]


def is_white_label(conn, case_id: str) -> bool:
    row = conn.execute("SELECT white_label_branch_flag FROM onboarding_case WHERE case_id = ?",
                       (case_id,)).fetchone()
    return bool(row and row["white_label_branch_flag"])


def case(conn, case_id: str) -> dict:
    row = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    applicant = conn.execute("SELECT * FROM applicant WHERE applicant_id = ?",
                             (row["applicant_id"],)).fetchone()
    return {"case": dict(row), "applicant": dict(applicant)}


def audit_trail(conn, case_id: str) -> list[dict]:
    return _rows(conn, "SELECT * FROM audit_event WHERE case_id = ? ORDER BY event_id",
                 (case_id,))


def checklist(conn, case_id: str) -> list[dict]:
    """Every checklist item with its CURRENT document. An item a customer has
    uploaded to more than once has a superseded history; that shows on the
    Documents tab, and the item itself appears here once."""
    rows = _rows(conn,
                 "SELECT i.*, d.file_name FROM checklist_item i"
                 " JOIN requirement_pack p USING (pack_id)"
                 " LEFT JOIN document d ON d.document_id = (SELECT MAX(c.document_id)"
                 "  FROM checklist_item_document c WHERE c.item_id = i.item_id)"
                 " WHERE p.case_id = ? ORDER BY i.item_id", (case_id,))
    for r in rows:
        r["awaiting_confirmation"] = requirement_pack.awaiting_confirmation(r)
    return rows


def documents(conn, case_id: str) -> list[dict]:
    out = _rows(conn, "SELECT * FROM document WHERE case_id = ? ORDER BY document_id", (case_id,))
    for doc in out:
        doc["fields"] = _rows(conn, "SELECT * FROM extracted_field WHERE document_id = ?"
                                    " ORDER BY field_id", (doc["document_id"],))
        doc["sample_path"] = document_file(case_id, doc["file_name"])
    return out


def document_file(case_id: str, file_name: str) -> Path | None:
    """Where a document's file is: a generated sample, or a portal upload."""
    for root in (SAMPLE_DOCS, UPLOADS):
        candidate = (root / case_id / file_name).resolve()
        if str(candidate).startswith(str(root.resolve())) and candidate.exists():
            return candidate
    return None


def people(conn, case_id: str) -> list[dict]:
    applicant_id = conn.execute("SELECT applicant_id FROM onboarding_case WHERE case_id = ?",
                                (case_id,)).fetchone()[0]
    return _rows(conn, "SELECT * FROM individual WHERE applicant_id = ? ORDER BY individual_id",
                 (applicant_id,))


def ubos(conn, case_id: str) -> list[dict]:
    applicant_id = conn.execute("SELECT applicant_id FROM onboarding_case WHERE case_id = ?",
                                (case_id,)).fetchone()[0]
    out = _rows(conn, "SELECT u.*, i.full_name FROM ubo u JOIN individual i USING (individual_id)"
                      " WHERE u.applicant_id = ? ORDER BY u.ubo_id", (applicant_id,))
    for u in out:
        chain = [float(c) for c in (u["ownership_chain_percentages"] or "").split("|") if c]
        u["chain"] = chain
        u["effective"] = (verification.effective_ownership(chain) if chain
                          else float(u["ownership_percentage"]))
        # "70% x 45% = 31.5%" - show the arithmetic, not just the answer
        u["working"] = (" x ".join(f"{c:g}%" for c in chain) + f" = {u['effective']:g}%"
                        if len(chain) > 1 else f"{u['effective']:g}% held directly")
    return out


def checks(conn, case_id: str) -> dict:
    registry = conn.execute("SELECT * FROM registry_check WHERE case_id = ?",
                            (case_id,)).fetchone()
    comparisons = []
    if registry:
        extracted = verification.extracted_values(conn, case_id)

        def first(name):
            values = extracted.get(name) or []
            return values[0] if values else None

        declared_directors = [v for name in ("director_name", "director_name_2")
                              for v in (extracted.get(name) or [])]
        comparisons = [
            ("legal name", registry["registry_legal_name"],
             first("company_name") or first("legal_name"), registry["name_match"]),
            ("registration number", registry["registry_number"],
             first("registration_number"), registry["number_match"]),
            ("registered address", registry["registry_address"],
             first("registered_address"), registry["address_match"]),
            ("directors", (registry["registry_directors"] or "").replace("|", ", "),
             ", ".join(declared_directors), registry["director_match"]),
        ]
    return {
        "registry": dict(registry) if registry else None,
        "comparisons": comparisons,
        "identity": _rows(conn, "SELECT c.*, i.full_name FROM identity_check c"
                                " JOIN individual i USING (individual_id)"
                                " WHERE c.case_id = ? ORDER BY c.check_id", (case_id,)),
        "screening": _rows(conn, "SELECT s.*, i.full_name FROM screening_check s"
                                 " LEFT JOIN individual i USING (individual_id)"
                                 " WHERE s.case_id = ? ORDER BY s.check_id", (case_id,)),
    }


def risk(conn, case_id: str) -> dict:
    assessment = conn.execute("SELECT * FROM risk_assessment WHERE case_id = ?",
                              (case_id,)).fetchone()
    if not assessment:
        return {"assessment": None, "factors": [], "floors": [], "pack": None}

    knowledge = kb()
    facts, _ = __import__("orchestrator.steps.risk_assessment", fromlist=["x"]).gather_facts(
        conn, case_id, knowledge)
    floors = [(condition, band) for condition, band in knowledge.hard_floors
              if facts.get(condition)]
    return {
        "assessment": dict(assessment),
        "factors": _rows(conn, "SELECT * FROM risk_factor WHERE assessment_id = ?"
                               " ORDER BY factor_id", (assessment["assessment_id"],)),
        "floors": floors,
        "pack": (lambda r: dict(r) if r else None)(
            conn.execute("SELECT * FROM evidence_pack WHERE case_id = ?", (case_id,)).fetchone()),
    }


def communications(conn, case_id: str) -> list[dict]:
    return _rows(conn, "SELECT * FROM communication WHERE case_id = ? ORDER BY communication_id",
                 (case_id,))


def compliance_tasks(conn, case_id: str) -> list[dict]:
    return _rows(conn, "SELECT * FROM compliance_task WHERE case_id = ? ORDER BY task_id",
                 (case_id,))


def decisions(conn, case_id: str) -> list[dict]:
    return _rows(conn, "SELECT * FROM human_decision WHERE case_id = ? ORDER BY decision_id",
                 (case_id,))


def open_holds(conn, case_id: str):
    return holds.open_holds(conn, case_id)


def band_for_decisions(conn, case_id: str) -> str:
    """The band the taxonomy is read against. A case with no assessment has not
    been scored, which the taxonomy treats as insufficient evidence."""
    assessment = conn.execute("SELECT risk_band FROM risk_assessment WHERE case_id = ?",
                              (case_id,)).fetchone()
    return assessment["risk_band"] if assessment else "insufficient_evidence"


def available_decisions(conn, case_id: str) -> list[str]:
    """What the taxonomy allows at this case's band."""
    band = band_for_decisions(conn, case_id)
    return [name for name, rule in kb().decision_taxonomy.items()
            if band in rule["allowed_bands"].split("|")]


def withheld_decisions(conn, case_id: str) -> list[dict]:
    """The decisions this band does not allow, and the bands that do.

    The dropdown offers only what the taxonomy permits, which is right but
    silent: a critical case simply has no 'approve' on it and the reason is
    nowhere on the screen. This says so, from the same CSV.
    """
    band = band_for_decisions(conn, case_id)
    return [{"decision": name,
             "allowed_bands": rule["allowed_bands"].replace("|", ", "),
             "required_role": rule["required_role"]}
            for name, rule in kb().decision_taxonomy.items()
            if band not in rule["allowed_bands"].split("|")]


def export_bundle(conn, case_id: str) -> dict:
    return export(conn, case_id, kb())


# ---------------------------------------------------------------------------
# Actions - each one calls the orchestrator, never the database
# ---------------------------------------------------------------------------

def _application(case_id: str, conn=None) -> dict:
    """The application this case was built from, for the steps that need it.

    A demo case's application is the one the portal saved, with the demo
    scenario the analyst chose (read from the audit trail) for the simulated
    providers.
    """
    import json
    if db.is_demo_case(case_id):
        path = PORTAL_APPLICATIONS / f"{case_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"no saved demo application for {case_id}")
        application = json.loads(path.read_text(encoding="utf-8"))
        if conn is not None:
            application["demo_scenario"] = demo_scenarios.current(conn, case_id)
        return application
    matches = sorted(APPLICATIONS.glob(f"*{case_id}.json"))
    if not matches:
        raise FileNotFoundError(
            f"no application file for {case_id}; run tools/dataset_to_applications.py")
    return json.loads(matches[0].read_text(encoding="utf-8"))


def carry_on(conn, case_id: str) -> dict:
    """Move the case forward after a person has unblocked it.

    A release or a correction clears a hold; it does not by itself extract the
    document that was released or re-run the checks that were waiting. This asks
    the orchestrator to carry the case on from wherever it now stands, which is
    what happens in the pipeline and so must happen here too.
    """
    if open_holds(conn, case_id):
        return {"stopped_at": "holds", "reason": "holds are still open"}
    trace = resume(conn, case_id, _application(case_id, conn), kb())
    conn.commit()
    return trace

def release_document(conn, document_id, analyst_id, choice, reason):
    case_id = conn.execute("SELECT case_id FROM document WHERE document_id = ?",
                           (document_id,)).fetchone()[0]
    result = analyst_review.release_document(conn, document_id, analyst_id, choice, reason, kb())
    conn.commit()
    # If extraction has already run and left a hold of its own, its wording was
    # written when the document was still held. Releasing the document does not
    # by itself correct that sentence, and the presenter is looking straight at
    # it. Re-routing extraction recomputes the hold from the facts as they now
    # stand - the same call a field correction already makes. Guarded, because a
    # case that never reached extraction has no extraction hold to recompute.
    if _extraction_has_run(conn, case_id):
        extraction.route_case(conn, case_id, kb())
        conn.commit()
    carry_on(conn, case_id)
    return result


def _extraction_has_run(conn, case_id: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM extracted_field f JOIN document d USING (document_id)"
        " WHERE d.case_id = ? LIMIT 1", (case_id,)).fetchone())


def accept_field_as_read(conn, field_id, analyst_id, reason):
    case_id = conn.execute(
        "SELECT d.case_id FROM extracted_field f JOIN document d USING (document_id)"
        " WHERE f.field_id = ?", (field_id,)).fetchone()[0]
    result = extraction.accept_field_as_read(conn, field_id, analyst_id, reason, kb())
    extraction.route_case(conn, case_id, kb())
    conn.commit()
    carry_on(conn, case_id)
    return result


def correct_field(conn, field_id, analyst_id, value, reason):
    case_id = conn.execute(
        "SELECT d.case_id FROM extracted_field f JOIN document d USING (document_id)"
        " WHERE f.field_id = ?", (field_id,)).fetchone()[0]
    result = extraction.correct_field(conn, field_id, analyst_id, value, reason, kb())
    extraction.route_case(conn, case_id, kb())
    conn.commit()
    carry_on(conn, case_id)
    return result


def record_decision(conn, case_id, reviewer, reviewer_role, choice, reason_code, rationale,
                    override_reason=None, escalation_target=None):
    refs = [r[0] for r in conn.execute(
        "SELECT assessment_id FROM risk_assessment WHERE case_id = ?", (case_id,))]
    refs += [r[0] for r in conn.execute(
        "SELECT evidence_pack_id FROM evidence_pack WHERE case_id = ?", (case_id,))]
    result = decision.record_decision(
        conn, case_id, reviewer, reviewer_role, choice, reason_code, rationale, refs,
        override_reason=override_reason, escalation_target=escalation_target, kb=kb())
    conn.commit()
    return result


def send_message(conn, case_id, situation, approver):
    result = communication.send_required_message(conn, case_id, situation, kb(),
                                                 approver=approver)
    conn.commit()
    return result


def known_ids(conn, case_id: str) -> set:
    return evidence_pack.known_ids(conn, case_id)


# ---------------------------------------------------------------------------
# Customer portal actions - the same rule: call the orchestrator, never SQL
# ---------------------------------------------------------------------------

def upload_document(conn, case_id, item_id, file_name, content, uploads_dir=None):
    """A customer's file, through Step 3, and the case carried on if it can be.

    UploadRefused comes back to the caller with wording meant for the customer.
    """
    try:
        result = document_quality.receive_upload(
            conn, case_id, item_id, file_name, content, kb(),
            checker=get_upload_checker(QUALITY_CHECKER_MODE), uploads_dir=uploads_dir)
    except document_quality.UploadRefused:
        conn.commit()           # the refusal is audited; that record must survive it
        raise
    conn.commit()
    carry_on(conn, case_id)
    return result


def issue_portal_access(conn, case_id, issued_by):
    token = portal_access.issue(conn, case_id, issued_by, kb())
    conn.commit()
    return token


def portal_case(conn, token):
    return portal_access.resolve(conn, token)


def end_portal_session(conn, token):
    ended = portal_access.revoke(conn, token, "customer", kb())
    conn.commit()
    return ended


# ---------------------------------------------------------------------------
# Demo applications from the portal (phase 2) - synthetic data only
# ---------------------------------------------------------------------------

def submit_application(conn, application: dict) -> str:
    """A demo application typed into the portal, through intake like any other.

    The applicant type is never taken from the form: whatever the form sent is
    dropped, and Step 1 classifies from the facts with the AT rules. The saved
    application is what the later steps read, exactly as they read a dataset
    application file.
    """
    import json
    application = {k: v for k, v in application.items()
                   if k not in ("applicant_type", "route", "case_id")}
    application["origin"] = DEMO_ORIGIN
    application["source_channel"] = "portal"
    trace = process_application(conn, application, kb())
    case_id = trace["intake"]["case_id"]
    PORTAL_APPLICATIONS.mkdir(parents=True, exist_ok=True)
    (PORTAL_APPLICATIONS / f"{case_id}.json").write_text(
        json.dumps(application, indent=2, ensure_ascii=False), encoding="utf-8")
    conn.commit()
    return case_id


def awaiting_fields(conn, case_id: str) -> dict:
    """Per document, the fields waiting for an analyst to type them in."""
    out = {}
    for doc in conn.execute("SELECT document_id FROM document WHERE case_id = ?"
                            " ORDER BY document_id", (case_id,)):
        rows = [dict(r) for r in extraction.fields_awaiting_entry(conn, doc["document_id"])]
        if rows:
            required = set(kb().required_fields_for(conn.execute(
                "SELECT document_type FROM document WHERE document_id = ?",
                (doc["document_id"],)).fetchone()[0]))
            for r in rows:
                r["required"] = r["name"] in required
            out[doc["document_id"]] = rows
    return out


def enter_fields(conn, document_id, analyst_id, values):
    case_id = conn.execute("SELECT case_id FROM document WHERE document_id = ?",
                           (document_id,)).fetchone()[0]
    result = extraction.enter_fields(conn, document_id, analyst_id, values, kb())
    conn.commit()
    carry_on(conn, case_id)
    return result


def demo_scenario(conn, case_id: str) -> str:
    return demo_scenarios.current(conn, case_id)


def choose_demo_scenario(conn, case_id, scenario, analyst_id):
    chosen = demo_scenarios.choose(conn, case_id, scenario, analyst_id, kb())
    conn.commit()
    return chosen


def is_demo_case(case_id: str) -> bool:
    return db.is_demo_case(case_id)


def document_stage_open(conn, case_id: str) -> bool:
    return document_quality.document_stage_open(conn, case_id)


# ---------------------------------------------------------------------------
# The upload space: what the console shows and does about it
# ---------------------------------------------------------------------------

def uploaded_names(conn, case_id: str) -> dict:
    """{stored file name: the customer's own file name} for portal uploads."""
    return document_quality.uploaded_names(conn, case_id)


def addable_document_types() -> list[str]:
    """Every document type the KB knows, for the analyst's "request another
    document" form. Read from the KB, so a new type needs no screen change."""
    return sorted({r["document_type"] for r in kb().requirement_rules})


def confirm_condition(conn, item_id, analyst_id, applies, reason):
    result = requirement_pack.confirm_condition(conn, item_id, analyst_id, applies, reason, kb())
    conn.commit()
    return result


def add_checklist_item(conn, case_id, document_type, analyst_id, reason, subject=None):
    item_id = requirement_pack.add_item(conn, case_id, document_type, analyst_id, reason,
                                        subject or None, kb())
    # The case now owes a document again; Step 3 routes it like any other.
    document_quality.route_case(conn, case_id, kb())
    conn.commit()
    return item_id


def customer_link(conn, case_id, issued_by) -> str:
    """A fresh customer link for this case. The token is shown this once and
    only its hash is kept; the case id is not in the link."""
    token = portal_access.issue(conn, case_id, issued_by, kb())
    conn.commit()
    return f"{settings.PORTAL_URL}/access/{token}"


# ---------------------------------------------------------------------------
# New evidence after the assessment
# ---------------------------------------------------------------------------

def reassessment_hold(conn, case_id: str):
    from orchestrator import reassessment
    return reassessment.open_hold(conn, case_id)


def rerun_verification(conn, case_id, analyst_id, reason):
    from orchestrator.orchestrator import rerun_verification as rerun
    return rerun(conn, case_id, _application(case_id, conn), kb(), analyst_id, reason)


def keep_assessment(conn, case_id, analyst_id, reason):
    from orchestrator.orchestrator import keep_assessment as keep
    return keep(conn, case_id, kb(), analyst_id, reason)
