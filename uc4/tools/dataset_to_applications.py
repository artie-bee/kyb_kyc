"""
Convert the scripted 10-case dataset into orchestrator application JSON files.

The dataset stores the *outcome* of onboarding (applicant.csv, individual.csv,
ubo.csv, onboarding_case.csv ...). The orchestrator expects the *input*: one
application per case, in the shape of uc4/sample_applications/*.json.

This script rebuilds that input. It deliberately uses only fields a customer
could have typed on the form. It never copies applicant_type, jurisdiction_path,
entity_scope or white_label_branch_flag out of onboarding_case.csv, because
those are exactly what the orchestrator is being tested on - feeding them back
in would make the test pass by construction.

Usage:
    python tools/dataset_to_applications.py
    python tools/dataset_to_applications.py --dataset <dir> --out <dir>
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = UC4.parent.parent / "KYB_KYC" / "wallester_uc4_dataset"
DEFAULT_OUT = UC4 / "sample_applications" / "from_dataset"

# Words that mark an ownership chain as nominee/trust held (A5).
TRUST_WORDS = ("nominee", "trust", "fiduciar")
# Words in business_activity / expected_usage that mark a white-label programme (A2).
WHITE_LABEL_WORDS = ("white-label", "white label")

ASSUMPTIONS = [
    ("A1", "entity_type is copied from applicant.csv verbatim ('private_limited_company'). "
           "It is NOT rewritten to the KB's 'private_limited' - the vocabulary gap is a real "
           "finding and normalising it here would hide it."),
    ("A2", "programme_type is absent from the dataset. Derived as 'white_label' when "
           "business_activity or expected_usage contains 'white-label'/'white label', else "
           "'direct'. Not taken from onboarding_case.applicant_type, which would be circular."),
    ("A3", "ownership_layers is absent. Derived as the number of '>'-separated nodes in the "
           "longest ubo.ownership_path (direct holding = 1, two intermediates = 3, no UBO = 0), "
           "matching the convention in the existing sample application files."),
    ("A4", "has_corporate_shareholder is absent. Derived as true when any ownership_path has "
           "more than one node, i.e. a company sits between the person and the applicant."),
    ("A5", "has_trust_or_nominee is absent. Derived by scanning ownership_path and "
           "relationship_to_entity for 'nominee', 'trust' or 'fiduciar'. False for all 10 cases."),
    ("A6", "individual 'ref' reuses the dataset individual_id (IND-xxxx) so checklist subjects "
           "can be traced back when the results are compared."),
    ("A7", "status, assigned_owner, next_action_owner and the *_check tables are outcomes, not "
           "form input, and are not carried into the application."),
    # --- flags, one line per condition_key in requirement_rule.csv ---------------
    ("F1", "spend_above_50k: parsed from the largest monthly figure in "
           "applicant.expected_usage. GBP and EUR are compared at par, as the rule text is "
           "written in EUR only."),
    ("F2", "nominee_or_trust_in_chain: keyword scan of ubo.ownership_path and "
           "individual.relationship_to_entity (same scan as A5). False for all 10 cases."),
    ("F3", "ubo_indirect_25pct_or_more: true when any ubo row has "
           "control_type = indirect_shareholding and ownership_percentage >= 25."),
    ("F4", "signatory_not_director: true when someone holds role 'authorised_signatory' without "
           "also holding role 'director'. False for all 10 cases - every signatory in the "
           "dataset is a registered director."),
    ("F5", "vat_registered: read straight from applicant.vat_registered, which the applicant "
           "declares on the form. No derivation and no reading of the documents supplied."),
    ("F6", "remote_onboarding: NOT DERIVABLE, left unanswered. Both source channels (portal, "
           "email) are remote, so the column cannot distinguish the one case the dataset "
           "treats as requiring a liveness selfie from the ones it waives."),
    ("F7", "registered_with_trade_register: NOT DERIVABLE, left unanswered. It applies only to "
           "UK sole traders, and the dataset contains no such case."),
    # --- documents, for Step 3 -------------------------------------------------
    ("D1", "documents[] comes from document.csv: type, file name, upload time, expiry date and "
           "issue country are all upload-time facts a portal would capture."),
    ("D2", "document_date is not a column on document.csv. It is taken from the extracted_field "
           "row named 'document_date' where one exists, on the basis that a portal asks for the "
           "date on a proof of address at upload. It feeds the max_age_days rule only, and on "
           "this dataset no document is old enough to fail it."),
    ("D3", "scripted_quality_flags carries document.csv's quality_flags through to the mock "
           "checker. It is the scripted verdict being replayed, not an input a real applicant "
           "would supply, and a live checker ignores it."),
    ("D4", "page_count / expected_page_count are absent from the dataset, so the deterministic "
           "missing-pages rule never fires here; DOC-0038's missing_pages flag arrives as a "
           "scripted verdict instead."),
    ("D5", "analyst_releases[] is parsed from the audit_event rows with action "
           "document_released_after_review. Mock mode replays them so a case held at Step 3 "
           "moves on exactly as the dataset says an analyst moved it."),
    # --- extraction, for Step 4 ------------------------------------------------
    ("E1", "scripted_fields carries extracted_field.csv (name, value, confidence, "
           "source_page) through to the mock extractor, so the low-confidence path runs on "
           "the same numbers the dataset scripted. A live extractor ignores it."),
    ("E2", "extracted_field rows whose corrected_by_analyst is true are replayed as scripted "
           "analyst corrections (field_corrections[]), not as the value OCR read - otherwise "
           "the correction would look like a confident first reading."),
    ("E3", "field_acceptances[] is parsed from the audit rows with action "
           "extracted_field_accepted_as_read: a low-confidence value an analyst read against "
           "the original and kept. It clears the field without changing it."),
    # --- verification, for Step 5 -----------------------------------------------
    ("V1", "scripted_registry[] and scripted_identity[] are registry_check.csv and "
           "identity_check.csv rows, replayed by the mock providers. The registry row now "
           "carries the raw values the register holds, so Step 5 computes the match results "
           "rather than being handed them."),
    ("V2", "ownership_chain_percentages is carried onto each UBO so effective ownership can "
           "be multiplied along the chain and cross-checked against the declared total."),
]

# "DOC-0038 (ownership_chart) released by a.name; decision accept; reason: ..."
# "director_name on file.pdf accepted as read by a.name; reason: ..."
ACCEPT_RE = re.compile(
    r"^(?P<name>\S+) on (?P<file>\S+) accepted as read by (?P<analyst>[^;]+); "
    r"reason: (?P<reason>.+)$", re.S)

RELEASE_RE = re.compile(
    r"^(?P<doc>DOC-\d+).*?released by (?P<analyst>\S+); "
    r"decision (?P<decision>\w+); reason: (?P<reason>.+)$", re.S)

# Rule-of-thumb monthly spend threshold, in the rule's own currency terms (F1).
SPEND_THRESHOLD = 50000


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                for row in csv.DictReader(f)]


# ---------------------------------------------------------------------------
# Derivations - each one is an assumption, listed at the end of the run
# ---------------------------------------------------------------------------

def derive_programme_type(applicant: dict) -> str:
    """A2: the dataset has no programme_type column; infer it from the free text."""
    blob = f"{applicant['business_activity']} {applicant['expected_usage']}".lower()
    return "white_label" if any(w in blob for w in WHITE_LABEL_WORDS) else "direct"


def path_nodes(ownership_path: str) -> list[str]:
    return [p.strip() for p in (ownership_path or "").split(">") if p.strip()]


def derive_ownership(ubos: list[dict], people: list[dict]) -> dict:
    """A3/A4/A5: ownership_layers, has_corporate_shareholder, has_trust_or_nominee."""
    layers = max((len(path_nodes(u["ownership_path"])) for u in ubos), default=0)
    corporate = any(len(path_nodes(u["ownership_path"])) > 1 for u in ubos)
    blob = " ".join([u["ownership_path"] for u in ubos]
                    + [p["relationship_to_entity"] for p in people]).lower()
    return {"ownership_layers": layers,
            "has_corporate_shareholder": corporate,
            "has_trust_or_nominee": any(w in blob for w in TRUST_WORDS)}


def derive_flags(applicant: dict, people: list[dict], ubos: list[dict]) -> dict:
    """F1-F7: answer each condition_key the dataset can actually answer.

    Conditions that nothing in the dataset determines are deliberately left out
    rather than guessed - the orchestrator then flags them for an analyst, which
    is the honest outcome.
    """
    flags = {}

    # F1 - largest monthly figure quoted in the expected usage free text
    amounts = [int(m) for m in re.findall(r"(\d{4,})\s*(?:EUR|GBP)", applicant["expected_usage"])]
    flags["spend_above_50k"] = bool(amounts) and max(amounts) > SPEND_THRESHOLD

    # F2 - same nominee/trust scan as the ownership block
    flags["nominee_or_trust_in_chain"] = derive_ownership(ubos, people)["has_trust_or_nominee"]

    # F3 - an indirect chain carrying 25 percent or more
    flags["ubo_indirect_25pct_or_more"] = any(
        u["control_type"] == "indirect_shareholding" and float(u["ownership_percentage"]) >= 25
        for u in ubos)

    # F4 - a signatory who is not also a director
    roles = {p["individual_id"]: p["role"] for p in people}
    directors = {i for i, r in roles.items() if r == "director"}
    flags["signatory_not_director"] = any(
        r == "authorised_signatory" and i not in directors for i, r in roles.items())

    # F5 - declared by the applicant on the form
    flags["vat_registered"] = applicant["vat_registered"].lower() == "true"

    # F6, F7 are intentionally absent: nothing in the dataset determines them.
    return flags


def build_documents(case_docs: list[dict], doc_dates: dict[str, str],
                    fields_by_doc: dict[str, list[dict]]) -> list[dict]:
    """D1-D4: what the customer uploaded, as Step 3 receives it."""
    return [
        {"document_type": d["document_type"],
         "file_name": d["file_name"],
         "subject_ref": d["subject_individual_id"] or None,
         "upload_time": d["upload_time"] or None,
         "expiry_date": d["expiry_date"] or None,
         "document_date": doc_dates.get(d["document_id"]),          # D2
         "issue_country": d["issue_country"] or None,
         "scripted_quality_flags": [f for f in d["quality_flags"].split("|") if f],   # D3
         "scripted_fields": fields_by_doc.get(d["document_id"], [])}                  # E1
        for d in case_docs
    ]


def build_corrections(case_fields: list[dict], docs: dict[str, str],
                      correction_events: dict[str, dict]) -> list[dict]:
    """E2: the analyst corrections the dataset scripted, matched to their audit row."""
    out = []
    for f in case_fields:
        if f["corrected_by_analyst"].lower() != "true":
            continue
        event = correction_events.get(f["field_id"])
        out.append({"file_name": docs[f["document_id"]], "name": f["name"],
                    "value": f["value"],
                    "analyst_id": event["actor_id"] if event else "analyst.unknown",
                    "reason": event["payload_summary"] if event
                              else "scripted correction with no audit row in the dataset"})
    return out


def build_releases(events: list[dict], file_names: dict[str, str]) -> list[dict]:
    """D5: the analyst releases the dataset scripted for this case."""
    out = []
    for e in events:
        m = RELEASE_RE.match(e["payload_summary"].strip())
        if not m:
            raise ValueError("cannot parse release audit row: " + e["payload_summary"][:80])
        out.append({"file_name": file_names[m["doc"]], "analyst_id": m["analyst"],
                    "decision": m["decision"], "reason": m["reason"].strip()})
    return out


def build_application(case: dict, applicant: dict, people: list[dict], ubos: list[dict],
                      documents: list[dict], releases: list[dict],
                      corrections: list[dict], acceptances: list[dict],
                      registry: list[dict], identity: list[dict]) -> dict:
    return {
        "application_id": f"{case['source_channel'].upper()}-{case['case_id']}",
        "source_channel": case["source_channel"],
        "programme_type": derive_programme_type(applicant),
        "applicant": {
            "legal_name": applicant["legal_name"],
            "trading_name": applicant["trading_name"] or None,
            "registration_number": applicant["registration_number"] or None,
            "entity_type": applicant["entity_type"],          # A1: passed through unchanged
            "country": applicant["country"],
            "business_activity": applicant["business_activity"],
            "expected_usage": applicant["expected_usage"],
            "vat_registered": applicant["vat_registered"].lower() == "true",
        },
        "ownership": derive_ownership(ubos, people),
        "individuals": [
            {"ref": p["individual_id"],                        # A6
             "role": p["role"],
             "full_name": p["full_name"],
             "date_of_birth": p["date_of_birth"] or None,
             "nationality": p["nationality"] or None,
             "residence_country": p["residence_country"] or None,
             "relationship_to_entity": p["relationship_to_entity"] or None}
            for p in people
        ],
        "ubos": [
            {"individual_ref": u["individual_id"],
             "ownership_percentage": float(u["ownership_percentage"]),
             "ownership_chain_percentages": [float(c) for c in
                                        (u.get("ownership_chain_percentages") or "").split("|")
                                        if c],                                          # V2
         "control_type": u["control_type"] or None,
             "ownership_path": u["ownership_path"] or None}
            for u in ubos
        ],
        "flags": derive_flags(applicant, people, ubos),         # F1-F7
        "documents": documents,                                 # D1-D4
        "analyst_releases": releases,                           # D5
        "field_corrections": corrections,                       # E2
        "field_acceptances": acceptances,                       # E3
        "scripted_registry": registry,                          # V1
        "scripted_identity": identity,                          # V1
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build application JSONs from the UC4 dataset.")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    cases = read_csv(args.dataset / "onboarding_case.csv")
    applicants = {r["applicant_id"]: r for r in read_csv(args.dataset / "applicant.csv")}
    people_by_applicant = defaultdict(list)
    for r in read_csv(args.dataset / "individual.csv"):
        people_by_applicant[r["applicant_id"]].append(r)
    ubos_by_applicant = defaultdict(list)
    for r in read_csv(args.dataset / "ubo.csv"):
        ubos_by_applicant[r["applicant_id"]].append(r)
    docs_by_case = defaultdict(list)
    for r in read_csv(args.dataset / "document.csv"):
        docs_by_case[r["case_id"]].append(r)
    all_fields = read_csv(args.dataset / "extracted_field.csv")
    doc_dates = {r["document_id"]: r["value"]
                 for r in all_fields if r["name"] == "document_date"}
    fields_by_doc = defaultdict(list)
    for r in all_fields:
        fields_by_doc[r["document_id"]].append(r)
    file_names = {r["document_id"]: r["file_name"]
                  for case_rows in docs_by_case.values() for r in case_rows}
    releases_by_case = defaultdict(list)
    correction_events = {}
    acceptances_by_case = defaultdict(list)
    registry_by_case = defaultdict(list)
    for r in read_csv(args.dataset / "registry_check.csv"):
        registry_by_case[r["case_id"]].append(r)
    identity_by_case = defaultdict(list)
    for r in read_csv(args.dataset / "identity_check.csv"):
        identity_by_case[r["case_id"]].append(r)
    for r in read_csv(args.dataset / "audit_event.csv"):
        if r["action"] == "document_released_after_review":
            releases_by_case[r["case_id"]].append(r)
        elif r["action"] == "extracted_field_corrected":
            m = re.search(r"(FLD-\d+)", r["payload_summary"])
            if m:
                correction_events[m.group(1)] = r
        elif r["action"] == "extracted_field_accepted_as_read":
            m = ACCEPT_RE.match(r["payload_summary"].strip())
            if not m:
                raise ValueError("cannot parse acceptance row: " + r["payload_summary"][:80])
            acceptances_by_case[r["case_id"]].append(
                {"name": m["name"], "file_name": m["file"], "analyst_id": m["analyst"],
                 "reason": m["reason"].strip()})
    doc_case = {r["document_id"]: r["case_id"]
                for rows in docs_by_case.values() for r in rows}
    fields_by_case = defaultdict(list)
    for r in all_fields:
        fields_by_case[doc_case[r["document_id"]]].append(r)

    args.out.mkdir(parents=True, exist_ok=True)
    for stale in args.out.glob("*.json"):
        stale.unlink()

    for n, case in enumerate(cases, start=1):
        aid = case["applicant_id"]
        app = build_application(
            case, applicants[aid], people_by_applicant[aid], ubos_by_applicant[aid],
            build_documents(docs_by_case[case["case_id"]], doc_dates, fields_by_doc),
            build_releases(releases_by_case[case["case_id"]], file_names),
            build_corrections(fields_by_case[case["case_id"]], file_names,
                              correction_events),
            acceptances_by_case[case["case_id"]],
            registry_by_case[case["case_id"]], identity_by_case[case["case_id"]])
        out = args.out / f"case_{n:02d}_{case['case_id']}.json"
        out.write_text(json.dumps(app, indent=2, ensure_ascii=False), encoding="utf-8")
        own = app["ownership"]
        print(f"{case['case_id']}  {out.name:<30} programme={app['programme_type']:<12}"
              f" entity_type={app['applicant']['entity_type']:<24}"
              f" layers={own['ownership_layers']}"
              f" corp={str(own['has_corporate_shareholder']):<5}"
              f" people={len(app['individuals'])} ubos={len(app['ubos'])}"
              f" docs={len(app['documents'])}"
              f" releases={len(app['analyst_releases'])}")

    print(f"\nWrote {len(cases)} applications to {args.out}")
    print("\nAssumptions made (fields the application format needs but the dataset lacks):")
    for code, text in ASSUMPTIONS:
        print(f"  {code}  {text}")


if __name__ == "__main__":
    main()
