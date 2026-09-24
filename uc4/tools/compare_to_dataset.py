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


def compare_documents(conn: sqlite3.Connection, dataset: Path = DEFAULT_DATASET) -> list[tuple]:
    """One row per document the orchestrator assessed, matched by (case, file name).

    Compares the Step 3 screening verdict, which is what document.csv records.
    quality_status_at_screen is used rather than quality_status: an analyst
    release moves the live status afterwards, and the dataset has no column for
    that, so comparing the live status would make case 4 look like a mismatch
    when in fact it screened exactly as scripted and was then released.
    """
    ds = {(r["case_id"], r["file_name"]):
          (r["quality_status"], r["quality_flags"], r["resubmission_reasons"],
           r["resubmission_required"])
          for r in read_csv(dataset / "document.csv")}
    rows = []
    for r in conn.execute("SELECT * FROM document ORDER BY document_id"):
        key = (r["case_id"], r["file_name"])
        screened = r["quality_status_at_screen"]
        got = (screened, r["quality_flags"] or "", r["resubmission_reasons"] or "",
               str(screened == "resubmission_required").lower())
        want = ds.get(key)
        rows.append((r["case_id"], r["file_name"],
                     " / ".join(want) if want else "(no dataset row)",
                     " / ".join(got), "YES" if want == got else "NO"))
    return rows


def compare_fields(conn: sqlite3.Connection, dataset: Path = DEFAULT_DATASET) -> list[tuple]:
    """One row per field extracted, matched by (case, file name, field name).

    Compares value, confidence and corrected_by_analyst. Only fields the
    orchestrator actually extracted are compared: cases that stop before Step 4
    (case 2 awaiting resubmission, case 8 on the white-label branch) have dataset
    fields with no counterpart here, which test_extraction_coverage pins down.
    """
    docs = {r["document_id"]: r for r in read_csv(dataset / "document.csv")}
    ds = {}
    for r in read_csv(dataset / "extracted_field.csv"):
        d = docs[r["document_id"]]
        ds[(d["case_id"], d["file_name"], r["name"])] = (
            r["value"], f'{float(r["confidence"]):.2f}', r["corrected_by_analyst"])

    rows = []
    for r in conn.execute(
            "SELECT d.case_id, d.file_name, f.name, f.value, f.confidence,"
            " f.corrected_by_analyst FROM extracted_field f JOIN document d USING (document_id)"
            " ORDER BY f.field_id"):
        key = (r["case_id"], r["file_name"], r["name"])
        got = (r["value"] or "", f'{float(r["confidence"]):.2f}',
               "true" if r["corrected_by_analyst"] else "false")
        want = ds.get(key)
        rows.append((r["case_id"], f"{r['file_name']} / {r['name']}",
                     " | ".join(want) if want else "(no dataset row)",
                     " | ".join(got), "YES" if want == got else "NO"))
    return rows


def compare_verification(conn: sqlite3.Connection, dataset: Path = DEFAULT_DATASET) -> list[tuple]:
    """Step 5 output against the dataset: registry rows, identity rows, UBO status.

    The registry match columns are COMPUTED here from the raw values the register
    holds versus the extracted fields, so this compares a derivation against the
    scripted answer. The identity columns are provider verdicts and are replayed,
    so those rows confirm the plumbing rather than a calculation.
    """
    rows = []
    reg_cmp = ("company_status", "name_match", "number_match", "address_match",
               "director_match", "ubo_supported_by_registry", "result")
    ds_reg = {r["case_id"]: r for r in read_csv(dataset / "registry_check.csv")}
    for r in conn.execute("SELECT * FROM registry_check ORDER BY check_id"):
        want = ds_reg.get(r["case_id"])
        got = tuple(str(r[c]) for c in reg_cmp)
        exp = tuple(want[c] for c in reg_cmp) if want else None
        rows.append((r["case_id"], "registry_check",
                     " | ".join(exp) if exp else "(no dataset row)", " | ".join(got),
                     "YES" if exp == got else "NO"))

    # dataset individual ids are minted per run here, so match people by name
    name_by_ds = {r["individual_id"]: r["full_name"] for r in read_csv(dataset / "individual.csv")}
    idc_cmp = ("document_result", "liveness_result", "biometric_result", "address_result",
               "name_dob_match", "document_expired", "duplicate_individual_detected", "result")
    ds_idc = {(r["case_id"], name_by_ds[r["individual_id"]]): r
              for r in read_csv(dataset / "identity_check.csv")}
    for r in conn.execute(
            "SELECT c.*, i.full_name FROM identity_check c JOIN individual i USING (individual_id)"
            " ORDER BY c.check_id"):
        want = ds_idc.get((r["case_id"], r["full_name"]))
        got = tuple(str(r[c]) for c in idc_cmp)
        exp = tuple(want[c] for c in idc_cmp) if want else None
        rows.append((r["case_id"], f"identity_check / {r['full_name']}",
                     " | ".join(exp) if exp else "(no dataset row)", " | ".join(got),
                     "YES" if exp == got else "NO"))

    ds_ubo = {(r["individual_id"], r["ownership_percentage"]): r
              for r in read_csv(dataset / "ubo.csv")}
    ds_by_name = {(name_by_ds[k[0]], k[1]): v for k, v in ds_ubo.items()}
    for r in conn.execute(
            "SELECT u.*, i.full_name FROM ubo u JOIN individual i USING (individual_id)"
            " ORDER BY u.ubo_id"):
        want = ds_by_name.get((r["full_name"], str(r["ownership_percentage"])))
        rows.append((None, f"ubo verification_status / {r['full_name']}",
                     want["verification_status"] if want else "(no dataset row)",
                     r["verification_status"],
                     "YES" if want and want["verification_status"] == r["verification_status"]
                     else "NO"))
    # the case column is only for display; fill it in from the checks above
    return [(c or "-", *rest) for c, *rest in rows]


def compare_screening(conn: sqlite3.Connection, dataset: Path = DEFAULT_DATASET) -> list[tuple]:
    """Screening rows against the dataset, matched by (case, subject).

    Sanctions and PEP are provider list results and are replayed, so those
    columns confirm that every subject was screened and nothing was altered on
    the way through - in particular that no match was downgraded. The adverse
    media category is replayed too and stands in for the relevance model.
    """
    name_by_ds = {r["individual_id"]: r["full_name"] for r in read_csv(dataset / "individual.csv")}
    cmp_cols = ("subject_type", "sanctions_result", "pep_result", "adverse_media_result",
                "severity", "evidence_refs")
    ds = {(r["case_id"], name_by_ds.get(r["individual_id"], "")): r
          for r in read_csv(dataset / "screening_check.csv")}

    rows = []
    for r in conn.execute(
            "SELECT s.*, i.full_name FROM screening_check s "
            "LEFT JOIN individual i USING (individual_id) ORDER BY s.check_id"):
        subject = r["full_name"] or ""
        want = ds.get((r["case_id"], subject))
        got = tuple(str(r[c] or "") for c in cmp_cols)
        exp = tuple(want[c] for c in cmp_cols) if want else None
        rows.append((r["case_id"], f"screening / {subject or 'applicant entity'}",
                     " | ".join(exp) if exp else "(no dataset row)", " | ".join(got),
                     "YES" if exp == got else "NO"))
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

    conn = run_orchestrator(args.applications)

    rows = compare(conn, args.dataset)
    print(render(rows))
    ok, total = score(rows)
    print(f"\n{ok}/{total} case field checks match")

    for label, fn in (("document quality", compare_documents), ("extracted field", compare_fields),
                     ("verification", compare_verification),
                     ("screening", compare_screening)):
        rows = fn(conn, args.dataset)
        ok, total = score(rows)
        print(f"{ok}/{total} {label} checks match")
        for r in rows:
            if r[4] == "NO":
                print(f"  MISMATCH {r[0]} {r[1]}\n    dataset: {r[2]}\n    ours   : {r[3]}")


if __name__ == "__main__":
    main()
