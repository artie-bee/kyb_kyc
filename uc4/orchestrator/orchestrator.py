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
from .steps import intake, requirement_pack

STEPS = {
    "requirement_pack": requirement_pack.run,
    # "document_quality": document_quality.run,   <- next layer
}


def process_application(conn, application: dict, kb: KnowledgeBase) -> dict:
    trace = {"application_id": application.get("application_id")}

    result = intake.run(conn, application, kb)
    trace["intake"] = result.__dict__
    next_step = result.next_step

    while next_step:
        if next_step not in STEPS:          # step not built yet -> stop cleanly
            trace["waiting_for"] = next_step
            break
        step_result = STEPS[next_step](conn, result.case_id, application, kb)
        trace[next_step] = step_result.__dict__
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
        print(f"{i['case_id']}  {Path(p).name:<32} type={i['applicant_type']!s:<24} "
              f"route={i['route']:<18} scope={i['entity_scope']!s:<17} "
              f"items={pack.get('items_required', '-')}/{pack.get('items_optional', '-')}/"
              f"{pack.get('items_conditional', '-')}  next={trace.get('waiting_for')}")
        for prob in i["problems"] + pack.get("problems", []):
            print(f"{'':12}! {prob}")


if __name__ == "__main__":
    main(sys.argv[1:])
