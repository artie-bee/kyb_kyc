"""
Compare orchestrator output against the scripted dataset, case by case.

Checks five fields per case (50 checks over 10 cases):
    applicant_type, jurisdiction_path, entity_scope, white_label_branch_flag,
    and the checklist item set as (rule_id, subject individual).

Run standalone to print the table:
    python tools/compare_to_dataset.py
    python tools/compare_to_dataset.py --dataset <dir> --db onboarding.db
"""

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from orchestrator import db                                    # noqa: E402
from orchestrator.kb import KnowledgeBase                      # noqa: E402
from orchestrator.orchestrator import process_application      # noqa: E402
from tools.dataset_to_applications import DEFAULT_DATASET, DEFAULT_OUT   # noqa: E402

FIELDS = ("applicant_type", "jurisdiction_path", "entity_scope",
          "white_label_branch_flag", "checklist items (rule+subject)")


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                for row in csv.DictReader(f)]


def run_orchestrator(app_dir: Path = DEFAULT_OUT) -> sqlite3.Connection:
    """Run every generated application into a fresh in-memory database."""
    conn, kb = db.connect(":memory:"), KnowledgeBase()
    for p in sorted(app_dir.glob("*.json")):
        process_application(conn, json.loads(p.read_text(encoding="utf-8")), kb)
    return conn


def compare(conn: sqlite3.Connection, dataset: Path = DEFAULT_DATASET) -> list[tuple]:
    """Return one (case, field, dataset value, orchestrator value, match) row per check.

    Rows whose field is indented are detail lines about a checklist difference and
    are not counted in the score.
    """
    cases = read_csv(dataset / "onboarding_case.csv")
    packs = {r["pack_id"]: r["case_id"] for r in read_csv(dataset / "requirement_pack.csv")}
    ds_items = defaultdict(set)
    for r in read_csv(dataset / "checklist_item.csv"):
        ds_items[packs[r["pack_id"]]].add((r["rule_id"], r["subject_individual_id"] or "-"))

    # The orchestrator mints its own individual ids; map them back by name.
    name_to_ds = {r["full_name"]: r["individual_id"] for r in read_csv(dataset / "individual.csv")}
    orc_ind = {r["individual_id"]: name_to_ds.get(r["full_name"], r["individual_id"])
               for r in conn.execute("SELECT individual_id, full_name FROM individual")}
    orc_items = defaultdict(set)
    for r in conn.execute(
            "SELECT p.case_id, i.rule_id, i.subject_individual_id FROM checklist_item i "
            "JOIN requirement_pack p USING (pack_id)"):
        subj = orc_ind.get(r["subject_individual_id"], "-") if r["subject_individual_id"] else "-"
        orc_items[r["case_id"]].add((r["rule_id"], subj))

    # Cases are processed in dataset order, so the nth case matches the nth row.
    orc_cases = list(conn.execute("SELECT * FROM onboarding_case ORDER BY case_id"))
    rows = []
    for ds_case, orc in zip(cases, orc_cases):
        cid = ds_case["case_id"]

        def add(field, dsv, orcv):
            rows.append((cid, field, str(dsv), str(orcv), "YES" if str(dsv) == str(orcv) else "NO"))

        add("applicant_type", ds_case["applicant_type"], orc["applicant_type"])
        add("jurisdiction_path", ds_case["jurisdiction_path"], orc["jurisdiction_path"])
        add("entity_scope", ds_case["entity_scope"], orc["entity_scope"])
        add("white_label_branch_flag",
            ds_case["white_label_branch_flag"].lower() == "true",
            bool(orc["white_label_branch_flag"]))

        d, o = ds_items[cid], orc_items[orc["case_id"]]
        add(FIELDS[4], f"{len(d)} items", f"{len(o)} items" if o else "no pack built")
        for label, diff in (("  missing vs dataset", d - o), ("  extra vs dataset", o - d)):
            if diff:
                shown = sorted(diff)[:4]
                rows.append((cid, label, str(len(diff)),
                             ", ".join(f"{r}/{s}" for r, s in shown)
                             + (" ..." if len(diff) > 4 else ""), "NO"))
    return rows


def score(rows: list[tuple]) -> tuple[int, int]:
    scored = [r for r in rows if not r[1].startswith("  ")]
    return sum(1 for r in scored if r[4] == "YES"), len(scored)


def render(rows: list[tuple]) -> str:
    hdr = ("case", "field", "dataset value", "orchestrator value", "match?")
    w = [max(len(r[i]) for r in [*rows, hdr]) for i in range(5)]
    out = ["| " + " | ".join(h.ljust(w[i]) for i, h in enumerate(hdr)) + " |",
           "|" + "|".join("-" * (w[i] + 2) for i in range(5)) + "|"]
    last = None
    for r in rows:
        if last and r[0] != last:
            out.append("|" + "|".join(" " * (w[i] + 2) for i in range(5)) + "|")
        out.append("| " + " | ".join(r[i].ljust(w[i]) for i in range(5)) + " |")
        last = r[0]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare orchestrator output to the dataset.")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--applications", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    rows = compare(run_orchestrator(args.applications), args.dataset)
    print(render(rows))
    ok, total = score(rows)
    print(f"\n{ok}/{total} field checks match")


if __name__ == "__main__":
    main()
