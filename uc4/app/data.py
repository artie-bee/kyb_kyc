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

from orchestrator import db, holds, live_mode                              # noqa: E402
from orchestrator.kb import KnowledgeBase                                  # noqa: E402
from orchestrator.steps import (analyst_review, communication, decision,   # noqa: E402
                                evidence_pack, extraction, verification)
from tools.export_case import export                                       # noqa: E402
from tools.run_demo import run as run_demo                                 # noqa: E402

DB_PATH = UC4 / "onboarding.db"
SAMPLE_DOCS = UC4 / "sample_documents"


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
    conn = db.connect(path)
    run_demo(conn, verbose=False, stop_before_human_actions=True)
    conn.commit()
    conn.close()


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
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0
    return (datetime.now(timezone.utc) - created).days


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


def case(conn, case_id: str) -> dict:
    row = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    applicant = conn.execute("SELECT * FROM applicant WHERE applicant_id = ?",
                             (row["applicant_id"],)).fetchone()
    return {"case": dict(row), "applicant": dict(applicant)}


def audit_trail(conn, case_id: str) -> list[dict]:
    return _rows(conn, "SELECT * FROM audit_event WHERE case_id = ? ORDER BY event_id",
                 (case_id,))


def checklist(conn, case_id: str) -> list[dict]:
    return _rows(conn,
                 "SELECT i.*, d.file_name FROM checklist_item i"
                 " JOIN requirement_pack p USING (pack_id)"
                 " LEFT JOIN checklist_item_document cid USING (item_id)"
                 " LEFT JOIN document d USING (document_id)"
                 " WHERE p.case_id = ? ORDER BY i.item_id", (case_id,))


def documents(conn, case_id: str) -> list[dict]:
    out = _rows(conn, "SELECT * FROM document WHERE case_id = ? ORDER BY document_id", (case_id,))
    for doc in out:
        doc["fields"] = _rows(conn, "SELECT * FROM extracted_field WHERE document_id = ?"
                                    " ORDER BY field_id", (doc["document_id"],))
        candidate = SAMPLE_DOCS / case_id / doc["file_name"]
        doc["sample_path"] = candidate if candidate.exists() else None
    return out


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


def available_decisions(conn, case_id: str) -> list[str]:
    """What the taxonomy allows at this case's band."""
    assessment = conn.execute("SELECT risk_band FROM risk_assessment WHERE case_id = ?",
                              (case_id,)).fetchone()
    band = assessment["risk_band"] if assessment else "insufficient_evidence"
    return [name for name, rule in kb().decision_taxonomy.items()
            if band in rule["allowed_bands"].split("|")]


def export_bundle(conn, case_id: str) -> dict:
    return export(conn, case_id, kb())


# ---------------------------------------------------------------------------
# Actions - each one calls the orchestrator, never the database
# ---------------------------------------------------------------------------

def release_document(conn, document_id, analyst_id, choice, reason):
    result = analyst_review.release_document(conn, document_id, analyst_id, choice, reason, kb())
    conn.commit()
    return result


def accept_field_as_read(conn, field_id, analyst_id, reason):
    result = extraction.accept_field_as_read(conn, field_id, analyst_id, reason, kb())
    extraction.route_case(conn, conn.execute(
        "SELECT d.case_id FROM extracted_field f JOIN document d USING (document_id)"
        " WHERE f.field_id = ?", (field_id,)).fetchone()[0], kb())
    conn.commit()
    return result


def correct_field(conn, field_id, analyst_id, value, reason):
    result = extraction.correct_field(conn, field_id, analyst_id, value, reason, kb())
    extraction.route_case(conn, conn.execute(
        "SELECT d.case_id FROM extracted_field f JOIN document d USING (document_id)"
        " WHERE f.field_id = ?", (field_id,)).fetchone()[0], kb())
    conn.commit()
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
