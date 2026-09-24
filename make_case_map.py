# -*- coding: utf-8 -*-
"""Builds case_map.csv: the demo answer key linking each case to its scenario."""
import csv, os
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wallester_uc4_dataset")
def load(n):
    f = open(os.path.join(OUT, n + ".csv"), encoding="utf-8"); r = list(csv.DictReader(f)); f.close(); return r
cases = load("onboarding_case"); apps = {a["applicant_id"]: a for a in load("applicant")}
docs = load("document"); regs = load("registry_check"); scrs = load("screening_check")
risks = {r["case_id"]: r for r in load("risk_assessment")}
decs = load("human_decision"); packs = load("evidence_pack")

META = {
 "WAL-ONB-0001": ("Case 1 - low-risk freelancer, complete documents", "8.1 Case 1 / 14.1",
   "Happy path: every requirement-pack item accepted at first attempt, clean provider results, low risk, no human escalation."),
 "WAL-ONB-0002": ("Case 2 - SME with unreadable director ID", "8.1 Case 2 / 14.2",
   "Bad documents stopped before paid checks: unreadable director ID plus a missing UBO declaration, resubmission request issued, no provider calls commissioned."),
 "WAL-ONB-0003": ("Case 3 - corporate with registry address mismatch", "8.1 Case 3 / 14.3",
   "Evidence-grounded exception: registration number matches, registered address does not; low-confidence OCR field corrected by an analyst before the comparison is relied on."),
 "WAL-ONB-0004": ("Case 4 - complex ownership, unclear UBO", "8.1 Case 4 / 14.4",
   "Structural opacity rather than an adverse finding: two-layer indirect ownership chain not supported by the registry, incomplete ownership chart, enhanced due diligence."),
 "WAL-ONB-0005": ("Case 5 - PEP match on UBO with moderate adverse media", "8.1 Case 5 / 14.5",
   "Human-gated compliance acceleration: PEP match plus moderate adverse media on the majority owner, internal narrative prepared, generic customer message only."),
 "WAL-ONB-0006": ("Case 6 - possible sanctions match on a director", "8.1 Case 6 / 14.6",
   "Critical stop: possible sanctions match on a director, no automated clearance, escalation to the MLRO queue, no customer disclosure of the reason."),
 "WAL-ONB-0007": ("Case 7 - serious adverse media on a director", "8.1 Case 7",
   "Analyst escalation with an adverse-media summary, and the one recorded override: the model recommended enhanced due diligence, the reviewer escalated."),
 "WAL-ONB-0008": ("Case 8 - white-label partner branch", "8.1 Case 8 / 14.7",
   "KYB intake only, routed to the future-phase branch: no risk assessment, screening, registry, identity, decision or evidence-pack rows."),
 "WAL-ONB-0009": ("Control A - clean SME", "control",
   "Baseline for the problem cases: complete SME submission, clean results throughout, analyst approval recorded."),
 "WAL-ONB-0010": ("Control B - clean corporate on the complex-ownership path", "control",
   "Negative control for Case 4: same complex-corporate requirement pack, but a single-layer registry-supported 70 percent owner."),
}
rows = []
for c in cases:
    cid = c["case_id"]; a = apps[c["applicant_id"]]
    ev = []
    ev += [d["document_id"] for d in docs if d["case_id"] == cid and d["quality_status"] != "accepted_for_checks"]
    ev += [r["check_id"] for r in regs if r["case_id"] == cid and r["result"] != "pass"]
    ev += [s["check_id"] for s in scrs if s["case_id"] == cid and
           (s["sanctions_result"] != "no_match" or s["pep_result"] != "no_match" or s["adverse_media_result"] != "none")]
    if cid in risks: ev.append(risks[cid]["assessment_id"])
    ev += [d["decision_id"] for d in decs if d["case_id"] == cid]
    ev += [p["evidence_pack_id"] for p in packs if p["case_id"] == cid]
    label, section, demo = META[cid]
    rows.append({"case_id": cid, "case_label": label, "brief_section": section,
                 "applicant_id": a["applicant_id"], "applicant_name": a["legal_name"],
                 "applicant_type": c["applicant_type"], "jurisdiction_path": c["jurisdiction_path"],
                 "entity_scope": c["entity_scope"], "source_channel": c["source_channel"],
                 "expected_status": c["status"],
                 "expected_risk_band": risks[cid]["risk_band"] if cid in risks else "",
                 "next_action_owner": c["next_action_owner"], "what_it_demonstrates": demo,
                 "key_evidence_ids": "|".join(ev)})
cols = ["case_id", "case_label", "brief_section", "applicant_id", "applicant_name", "applicant_type",
        "jurisdiction_path", "entity_scope", "source_channel", "expected_status",
        "expected_risk_band", "next_action_owner", "what_it_demonstrates", "key_evidence_ids"]
f = open(os.path.join(OUT, "case_map.csv"), "w", newline="", encoding="utf-8")
w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n"); w.writeheader()
for r in rows: w.writerow(r)
f.close()
print("case_map.csv: %d rows" % len(rows))
for r in rows: print("  %s  %-22s %-24s %s" % (r["case_id"], r["expected_status"], r["expected_risk_band"] or "-", r["key_evidence_ids"]))
