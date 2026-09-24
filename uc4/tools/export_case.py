"""
Audit export  (brief Section 10.8)

One JSON bundle per case: every row that belongs to it, the whole audit trail in
order, the KB versions the decisions were taken under, and every model or prompt
version that touched it. The point is that a reviewer can reconstruct what
happened without access to this system.

    python tools/export_case.py                        # every case
    python tools/export_case.py WAL-ONB-0004           # one case
    python tools/export_case.py --db onboarding.db --out exports/
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from orchestrator.kb import KnowledgeBase        # noqa: E402

# table -> the column that ties a row to a case, or a query when it is indirect
DIRECT = ("onboarding_case", "document", "registry_check",
          "identity_check", "screening_check", "finding", "case_hold", "risk_assessment",
          "evidence_pack", "communication", "outbox", "compliance_task", "human_decision",
          "requirement_pack", "audit_event")
INDIRECT = {
    "applicant": "SELECT a.* FROM applicant a JOIN onboarding_case c USING (applicant_id)"
                 " WHERE c.case_id = ?",
    "individual": "SELECT i.* FROM individual i JOIN onboarding_case c USING (applicant_id)"
                  " WHERE c.case_id = ?",
    "ubo": "SELECT u.* FROM ubo u JOIN onboarding_case c USING (applicant_id)"
           " WHERE c.case_id = ?",
    "checklist_item": "SELECT i.* FROM checklist_item i JOIN requirement_pack p USING (pack_id)"
                      " WHERE p.case_id = ?",
    "checklist_item_document": "SELECT d.* FROM checklist_item_document d"
                               " JOIN checklist_item i USING (item_id)"
                               " JOIN requirement_pack p USING (pack_id) WHERE p.case_id = ?",
    "risk_factor": "SELECT f.* FROM risk_factor f JOIN risk_assessment a USING (assessment_id)"
                   " WHERE a.case_id = ?",
    "extracted_field": "SELECT f.* FROM extracted_field f JOIN document d USING (document_id)"
                       " WHERE d.case_id = ?",
}


def export(conn: sqlite3.Connection, case_id: str, kb: KnowledgeBase) -> dict:
    conn.row_factory = sqlite3.Row
    tables = {name: [dict(r) for r in conn.execute(sql, (case_id,))]
              for name, sql in INDIRECT.items()}
    for name in DIRECT:
        if name == "audit_event":
            continue
        tables[name] = [dict(r) for r in conn.execute(
            f"SELECT * FROM {name} WHERE case_id = ?", (case_id,))]

    audit = [dict(r) for r in conn.execute(
        "SELECT * FROM audit_event WHERE case_id = ? ORDER BY event_id", (case_id,))]
    versions = sorted({r["model_or_prompt_version"] for r in audit
                       if r["model_or_prompt_version"]})

    return {
        "case_id": case_id,
        "exported_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kb_version": kb.version,
        "kb_items": json.loads((UC4 / "kb" / "kb_manifest.json").read_text())["items"],
        "model_and_prompt_versions": versions,
        "tables": tables,
        "audit_trail": audit,
        "row_counts": {name: len(rows) for name, rows in sorted(tables.items())},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Export a case as an audit bundle.")
    ap.add_argument("case_ids", nargs="*", help="cases to export; default is all of them")
    ap.add_argument("--db", type=Path, default=UC4 / "onboarding.db")
    ap.add_argument("--out", type=Path, default=UC4 / "exports")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    kb = KnowledgeBase()
    cases = args.case_ids or [r[0] for r in conn.execute(
        "SELECT case_id FROM onboarding_case ORDER BY case_id")]

    args.out.mkdir(parents=True, exist_ok=True)
    for case_id in cases:
        bundle = export(conn, case_id, kb)
        path = args.out / f"{case_id}.json"
        path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{case_id}  {sum(bundle['row_counts'].values()):>4} rows, "
              f"{len(bundle['audit_trail']):>3} audit events, "
              f"{len(bundle['model_and_prompt_versions'])} version stamp(s) -> {path.name}")
    print(f"\n{len(cases)} bundle(s) in {args.out}")


if __name__ == "__main__":
    main()
