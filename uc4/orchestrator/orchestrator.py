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

from . import db
from .kb import KnowledgeBase
from .extractor import get_extractor
from .quality_checker import get_checker
from .media_relevance import get_media_assessor
from .providers import get_providers, get_screening_provider
from .steps import (analyst_review, document_quality, extraction, intake, requirement_pack,
                    screening, verification)

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

STEPS = {
    "requirement_pack": requirement_pack.run,
    "document_quality": document_quality.run,
    "extraction": extraction.run,
    "verification": verification.run,
    "screening": screening.run,
    # "risk_assessment": risk_assessment.run,   <- next layer
}


def process_application(conn, application: dict, kb: KnowledgeBase,
                        checker_mode: str = QUALITY_CHECKER_MODE,
                        extractor_mode: str = EXTRACTOR_MODE,
                        provider_mode: str = PROVIDER_MODE,
                        media_mode: str = MEDIA_ASSESSOR_MODE) -> dict:
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
        step_result = STEPS[next_step](conn, result.case_id, application, kb, **kwargs)
        trace[next_step] = step_result.__dict__

        # A case held at Step 3 waits for a named analyst. In mock mode we replay
        # the releases the dataset scripted, so the run continues the way the
        # scripted case did; in live mode the case simply stops here until a real
        # analyst calls release_document().
        if (next_step == "document_quality" and checker_mode == "mock"
                and step_result.next_step is None and application.get("analyst_releases")):
            releases = analyst_review.replay_scripted_releases(
                conn, result.case_id, application["analyst_releases"], kb)
            step_result = document_quality.route_case(conn, result.case_id, kb)
            trace["analyst_review"] = {"releases": [r.__dict__ for r in releases],
                                       "status": step_result.status}
            trace["document_quality"] = step_result.__dict__

        # Same idea after extraction: a field OCR could not read confidently waits
        # for an analyst, and mock mode replays the corrections the dataset scripted.
        if (next_step == "extraction" and extractor_mode == "mock"
                and step_result.next_step is None
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

        next_step = step_result.next_step
        if next_step not in STEPS:
            trace["waiting_for"] = next_step
            break

    conn.commit()
    return trace


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
              f"status={sc.get('status') or ex.get('status') or dq.get('status') or pack.get('status') or i['status']}  "
              f"next={trace.get('waiting_for')}")
        for prob in (i["problems"] + pack.get("problems", []) + dq.get("problems", [])
                     + ex.get("problems", []) + sc.get("problems", [])):
            print(f"{'':12}! {prob}")


if __name__ == "__main__":
    main(sys.argv[1:])
