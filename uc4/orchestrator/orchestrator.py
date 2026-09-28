"""
Orchestration layer - controls the order of steps, passes data between them,
and decides where each case goes next. Steps do the work; this file only
coordinates. Later steps (document_quality, extraction, ...) plug in by
adding one entry to STEPS.

Run:  python -m orchestrator.orchestrator sample_applications/*.json
"""

import json
import sys
from pathlib import Path

from . import db, holds, reassessment
from .kb import KnowledgeBase
from .extractor import get_extractor
from .quality_checker import get_checker
from .media_relevance import get_media_assessor
from .narrator import get_narrator
from .providers import get_providers, get_screening_provider
from .steps import (analyst_review, document_quality, evidence_pack, extraction, intake,
                    requirement_pack, risk_assessment, screening, verification)

# Which document-quality checker to use. "mock" replays scripted verdicts;
# "claude_vision" is a stub and raises. Default stays mock so that running the
# orchestrator never calls an API by accident.
QUALITY_CHECKER_MODE = "mock"
# Same for OCR: "mock" replays the scripted fields, "claude" is a stub and raises.
EXTRACTOR_MODE = "mock"
# And for the paid provider calls in Step 5. "live" providers are stubs and raise.
PROVIDER_MODE = "mock"
# The adverse-media relevance call in Step 6. "claude" is a stub and raises.
MEDIA_ASSESSOR_MODE = "mock"
# The written parts of Steps 7 and 8. "claude" is a stub and raises.
NARRATOR_MODE = "mock"

STEPS = {
    "requirement_pack": requirement_pack.run,
    "document_quality": document_quality.run,
    "extraction": extraction.run,
    "verification": verification.run,
    "screening": screening.run,
    "risk_assessment": risk_assessment.run,
    # "decision": decision.run,   <- next layer
}


def process_application(conn, application: dict, kb: KnowledgeBase,
                        checker_mode: str = QUALITY_CHECKER_MODE,
                        extractor_mode: str = EXTRACTOR_MODE,
                        provider_mode: str = PROVIDER_MODE,
                        media_mode: str = MEDIA_ASSESSOR_MODE,
                        narrator_mode: str = NARRATOR_MODE) -> dict:
    trace = {"application_id": application.get("application_id")}

    result = intake.run(conn, application, kb)
    trace["intake"] = result.__dict__
    next_step = result.next_step

    while next_step:
        if next_step not in STEPS:          # step not built yet -> stop cleanly
            trace["waiting_for"] = next_step
            break
        kwargs = {}
        if next_step == "document_quality":
            kwargs = {"checker": get_checker(checker_mode)}
        elif next_step == "extraction":
            kwargs = {"extractor": get_extractor(extractor_mode)}
        elif next_step == "verification":
            reg, ident = get_providers(provider_mode, application)
            kwargs = {"registry_provider": reg, "identity_provider": ident}
        elif next_step == "screening":
            kwargs = {"screening_provider": get_screening_provider(provider_mode, application),
                      "media_assessor": get_media_assessor(media_mode)}
        elif next_step == "risk_assessment":
            kwargs = {"narrator": get_narrator(narrator_mode)}
        step_result = STEPS[next_step](conn, result.case_id, application, kb, **kwargs)
        trace[next_step] = step_result.__dict__

        # A case held at Step 3 waits for a named analyst. In mock mode we replay
        # the releases the dataset scripted, so the run continues the way the
        # scripted case did; in live mode the case simply stops here until a real
        # analyst calls release_document().
        held_docs = conn.execute(
            "SELECT COUNT(*) FROM document WHERE case_id = ? AND quality_status = "
            "'manual_review_required'", (result.case_id,)).fetchone()[0]
        if (next_step == "document_quality" and checker_mode == "mock"
                and held_docs and application.get("analyst_releases")):
            releases = analyst_review.replay_scripted_releases(
                conn, result.case_id, application["analyst_releases"], kb)
            step_result = document_quality.route_case(conn, result.case_id, kb)
            trace["analyst_review"] = {"releases": [r.__dict__ for r in releases],
                                       "status": step_result.status}
            trace["document_quality"] = step_result.__dict__

        # Same idea after extraction: a field OCR could not read confidently waits
        # for an analyst, and mock mode replays the corrections the dataset scripted.
        outstanding = conn.execute(
            "SELECT COUNT(*) FROM extracted_field f JOIN document d USING (document_id) "
            "WHERE d.case_id = ? AND f.needs_analyst_correction = 1", (result.case_id,)
        ).fetchone()[0]
        if (next_step == "extraction" and extractor_mode == "mock" and outstanding
                and (application.get("field_corrections")
                     or application.get("field_acceptances"))):
            applied = extraction.replay_scripted_corrections(
                conn, result.case_id, application.get("field_corrections", []), kb)
            applied += extraction.replay_scripted_acceptances(
                conn, result.case_id, application.get("field_acceptances", []), kb)
            step_result = extraction.route_case(conn, result.case_id, kb,
                                                step_result.documents_read,
                                                step_result.fields_extracted,
                                                step_result.low_confidence,
                                                step_result.missing_required)
            trace["field_corrections"] = {"applied": applied, "status": step_result.status}
            trace["extraction"] = step_result.__dict__

        # The pack is assembled from the assessment, so it follows immediately.
        if next_step == "risk_assessment":
            pack = evidence_pack.run(conn, result.case_id, application, kb,
                                     narrator=get_narrator(narrator_mode))
            trace["evidence_pack"] = pack.__dict__

        next_step = step_result.next_step
        if next_step not in STEPS:
            trace["waiting_for"] = next_step
            break

    conn.commit()
    return trace


def resume(conn, case_id: str, application: dict, kb: KnowledgeBase,
           start_step: str = "extraction",
           checker_mode: str = QUALITY_CHECKER_MODE,
           extractor_mode: str = EXTRACTOR_MODE,
           provider_mode: str = PROVIDER_MODE,
           media_mode: str = MEDIA_ASSESSOR_MODE,
           narrator_mode: str = NARRATOR_MODE) -> dict:
    """Carry an existing case forward from where it stopped.

    Intake and the requirement pack have already run; this picks up at a later
    step, which is what is needed after a person releases a document or corrects
    a field. A step whose gate is still shut raises and the case simply stays
    where it is.
    """
    trace, next_step = {}, start_step
    while next_step:
        if next_step not in STEPS:
            trace["waiting_for"] = next_step
            break
        if next_step == "verification" and not document_quality.document_stage_open(conn, case_id):
            # The paid checks have already answered on this case. A document
            # that arrived afterwards has been screened and read, and waits on
            # the "new evidence after assessment" hold; the paid checks run again
            # only when an analyst chooses rerun_verification(). The status is
            # left to the holds, as always.
            # Re-routing the earlier steps just now set their own "clear"
            # status; on an assessed case that is wrong. The status is derived
            # from the open holds as always, and with none open the case sits
            # where its risk band put it.
            assessment = conn.execute("SELECT risk_band FROM risk_assessment WHERE case_id = ?",
                                      (case_id,)).fetchone()
            if assessment:
                holds.apply_status(conn, case_id, kb,
                                   *risk_assessment._clear_routing(assessment["risk_band"]))
            trace["stopped_at"] = "verification"
            trace["reason"] = ("the paid checks have already run; they re-run only when an "
                               "analyst chooses to")
            break
        kwargs = {}
        if next_step == "document_quality":
            kwargs = {"checker": get_checker(checker_mode)}
        elif next_step == "extraction":
            kwargs = {"extractor": get_extractor(extractor_mode)}
        elif next_step == "verification":
            reg, ident = get_providers(provider_mode, application)
            kwargs = {"registry_provider": reg, "identity_provider": ident}
        elif next_step == "screening":
            kwargs = {"screening_provider": get_screening_provider(provider_mode, application),
                      "media_assessor": get_media_assessor(media_mode)}
        elif next_step == "risk_assessment":
            kwargs = {"narrator": get_narrator(narrator_mode)}

        try:
            step_result = STEPS[next_step](conn, case_id, application, kb, **kwargs)
        except verification.VerificationGateError as e:
            # The checklist is not complete, so the paid step stays shut. That is
            # an answer, not a failure.
            trace["stopped_at"] = next_step
            trace["reason"] = str(e)
            break
        trace[next_step] = step_result.__dict__

        if next_step == "risk_assessment":
            pack = evidence_pack.run(conn, case_id, application, kb,
                                     narrator=get_narrator(narrator_mode))
            trace["evidence_pack"] = pack.__dict__

        next_step = step_result.next_step
        if next_step not in STEPS:
            trace["waiting_for"] = next_step
            break
    conn.commit()
    return trace


# ---------------------------------------------------------------------------
# Re-running the paid checks - an analyst's choice, never automatic
# ---------------------------------------------------------------------------

# What a re-run replaces. The schema holds one assessment per case, so the
# previous verification and risk results are archived verbatim in the
# append-only audit trail and then replaced. Screening is not in this list: a
# sanctions or PEP result is never deleted, and it is not re-run either - new
# evidence about the company does not change who was screened.
_REPLACED_BY_RERUN = (
    ("evidence_pack", "case_id = ?"),
    ("risk_factor", "assessment_id IN (SELECT assessment_id FROM risk_assessment"
                    " WHERE case_id = ?)"),
    ("risk_assessment", "case_id = ?"),
    ("finding", "case_id = ? AND source IN ('registry', 'identity', 'ubo')"),
    ("identity_check", "case_id = ?"),
    ("registry_check", "case_id = ?"),
)


class RerunRefused(ValueError):
    """The paid checks cannot be re-run on this case now."""


def rerun_verification(conn, case_id: str, application: dict, kb: KnowledgeBase,
                       analyst_id: str, reason: str,
                       provider_mode: str = PROVIDER_MODE,
                       narrator_mode: str = NARRATOR_MODE) -> dict:
    """Re-run verification and the risk assessment, because an analyst chose to.

    The only path by which the paid checks run a second time. It needs a named
    analyst and a reason, a complete checklist, and nothing else outstanding
    (a file still being looked at or typed in must be finished first). The
    previous results are archived in the audit trail before they are replaced,
    the "new evidence" hold is released in the analyst's name, and then Steps 5
    and 7 run as they did the first time. Screening results stand untouched.
    """
    import json
    if not (analyst_id or "").strip():
        raise RerunRefused("the analyst re-running verification must be identified")
    if not (reason or "").strip():
        raise RerunRefused("a reason is required to re-run verification")
    if document_quality.document_stage_open(conn, case_id):
        raise RerunRefused(f"{case_id} has not been verified yet; verification runs by itself "
                           f"once the checklist is complete")
    pending = [h for h in holds.open_holds(conn, case_id)
               if h.placed_by_step in ("step.document_quality", "step.extraction")]
    if pending:
        raise RerunRefused(
            f"finish reading the new evidence first: {'; '.join(h.reason for h in pending)}")
    try:
        verification.gate(conn, case_id)
    except verification.VerificationGateError as e:
        raise RerunRefused(str(e)) from None

    archive = {}
    for table, where in _REPLACED_BY_RERUN:
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {where}", (case_id,))]
        archive[table] = rows
    db.audit(conn, case_id, "analyst", analyst_id, "prior_assessment_archived",
             json.dumps(archive, ensure_ascii=False, default=str), kb.version)
    for table, where in _REPLACED_BY_RERUN:
        conn.execute(f"DELETE FROM {table} WHERE {where}", (case_id,))

    released = reassessment.release(conn, case_id, analyst_id,
                                    f"re-running verification: {reason}", kb)
    db.audit(conn, case_id, "analyst", analyst_id, "verification_rerun_requested",
             f"re-running verification and the risk assessment; reason: {reason}"
             + (f"; {released} released" if released else "")
             + "; screening results kept as they were", kb.version)

    reg, ident = get_providers(provider_mode, application)
    trace = {"verification": verification.run(conn, case_id, application, kb,
                                              registry_provider=reg,
                                              identity_provider=ident).__dict__}
    trace["risk_assessment"] = risk_assessment.run(
        conn, case_id, application, kb, narrator=get_narrator(narrator_mode)).__dict__
    trace["evidence_pack"] = evidence_pack.run(conn, case_id, application, kb,
                                               narrator=get_narrator(narrator_mode)).__dict__
    conn.commit()
    return trace


def keep_assessment(conn, case_id: str, kb: KnowledgeBase, analyst_id: str,
                    reason: str) -> str | None:
    """The analyst has read the new evidence and the assessment stands."""
    released = reassessment.release(conn, case_id, analyst_id,
                                    f"assessment kept after new evidence: {reason}", kb)
    if released is None:
        raise RerunRefused(f"{case_id} has no new evidence waiting to be reviewed")
    band = conn.execute("SELECT risk_band FROM risk_assessment WHERE case_id = ?",
                        (case_id,)).fetchone()
    if band:
        holds.apply_status(conn, case_id, kb, *risk_assessment._clear_routing(band[0]))
    db.audit(conn, case_id, "analyst", analyst_id, "assessment_kept",
             f"{released} released; the risk assessment stands; reason: {reason}", kb.version)
    conn.commit()
    return released


def main(paths: list[str], db_path: str = "onboarding.db") -> None:
    kb = KnowledgeBase()
    conn = db.connect(db_path)
    for p in paths:
        application = json.loads(Path(p).read_text())
        trace = process_application(conn, application, kb)
        i = trace["intake"]
        pack = trace.get("requirement_pack", {})
        dq = trace.get("document_quality", {})
        ex = trace.get("extraction", {})
        sc = trace.get("screening", {})
        rk = trace.get("risk_assessment", {})
        print(f"{i['case_id']}  {Path(p).name:<32} type={i['applicant_type']!s:<24} "
              f"route={i['route']:<18} scope={i['entity_scope']!s:<17} "
              f"items={pack.get('items_required', '-')}/{pack.get('items_optional', '-')}/"
              f"{pack.get('items_conditional', '-')}  "
              f"docs={dq.get('accepted', '-')}/{dq.get('resubmission', '-')}/"
              f"{dq.get('manual_review', '-')}  "
              f"fields={ex.get('fields_extracted', '-')}"
              f"/{ex.get('low_confidence', '-')}  "
              f"scr={sc.get('subjects_screened', '-')}"
              f"/{len(sc.get('findings', [])) if sc else '-'}  "
              f"risk={rk.get('band', '-')}/{rk.get('score', '-')}  "
              f"status={rk.get('status') or sc.get('status') or ex.get('status') or dq.get('status') or pack.get('status') or i['status']}  "
              f"next={trace.get('waiting_for')}")
        for prob in (i["problems"] + pack.get("problems", []) + dq.get("problems", [])
                     + ex.get("problems", []) + sc.get("problems", [])):
            print(f"{'':12}! {prob}")


if __name__ == "__main__":
    main(sys.argv[1:])
