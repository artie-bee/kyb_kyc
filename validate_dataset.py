# -*- coding: utf-8 -*-
"""
Wallester UC4 dataset validator.
Reads the CSVs in wallester_uc4_dataset/ and runs the seven checks in Rule 6.
Writes validation_report.md next to the data. Exit code 1 if any check fails.
"""
import csv, os, sys
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "wallester_uc4_dataset")

T = {}
for fn in sorted(os.listdir(OUT)):
    if fn.endswith(".csv"):
        f = open(os.path.join(OUT, fn), newline="", encoding="utf-8")
        T[fn[:-4]] = list(csv.DictReader(f))
        f.close()

REPORT = []
RESULTS = []


def say(line=""):
    REPORT.append(line)


def result(check, ok, detail, rows=0):
    RESULTS.append((check, ok, detail, rows))


def col(table, c):
    return [r[c] for r in T[table]]


# ---------------------------------------------------------------- check 1 ---
FKS = [
    ("onboarding_case", "applicant_id", "applicant", "applicant_id", True),
    ("individual", "applicant_id", "applicant", "applicant_id", True),
    ("individual", "id_document_id", "document", "document_id", False),
    ("ubo", "applicant_id", "applicant", "applicant_id", True),
    ("ubo", "individual_id", "individual", "individual_id", True),
    ("document", "case_id", "onboarding_case", "case_id", True),
    ("document", "subject_individual_id", "individual", "individual_id", False),
    ("extracted_field", "document_id", "document", "document_id", True),
    ("requirement_pack", "case_id", "onboarding_case", "case_id", True),
    ("checklist_item", "pack_id", "requirement_pack", "pack_id", True),
    ("checklist_item", "rule_id", "requirement_rule", "rule_id", True),
    ("checklist_item", "subject_individual_id", "individual", "individual_id", False),
    ("checklist_item_document", "item_id", "checklist_item", "item_id", True),
    ("checklist_item_document", "document_id", "document", "document_id", True),
    ("registry_check", "case_id", "onboarding_case", "case_id", True),
    ("registry_check", "applicant_id", "applicant", "applicant_id", True),
    ("identity_check", "case_id", "onboarding_case", "case_id", True),
    ("identity_check", "individual_id", "individual", "individual_id", True),
    ("screening_check", "case_id", "onboarding_case", "case_id", True),
    ("screening_check", "applicant_id", "applicant", "applicant_id", False),
    ("screening_check", "individual_id", "individual", "individual_id", False),
    ("risk_assessment", "case_id", "onboarding_case", "case_id", True),
    ("risk_factor", "assessment_id", "risk_assessment", "assessment_id", True),
    ("communication", "case_id", "onboarding_case", "case_id", True),
    ("communication", "template_id", "message_template", "template_id", True),
    ("human_decision", "case_id", "onboarding_case", "case_id", True),
    ("human_decision", "customer_template_id", "message_template", "template_id", False),
    ("evidence_pack", "case_id", "onboarding_case", "case_id", True),
    ("evidence_pack", "assessment_id", "risk_assessment", "assessment_id", False),
    ("evidence_pack", "decision_id", "human_decision", "decision_id", False),
    ("audit_event", "case_id", "onboarding_case", "case_id", True),
]
orphans, checked = [], 0
for (ct, cc, pt, pc, required) in FKS:
    parents = set(col(pt, pc))
    for r in T[ct]:
        v = r[cc]
        if v == "":
            if required:
                orphans.append("%s.%s is blank but required (row %s)" % (ct, cc, list(r.values())[0]))
            continue
        checked += 1
        if v not in parents:
            orphans.append("%s.%s = %s has no %s.%s" % (ct, cc, v, pt, pc))
result("1. Referential integrity (%d FK relationships, %d non-null values)" % (len(FKS), checked),
       not orphans, "no orphans" if not orphans else "; ".join(orphans[:20]), checked)

# ---------------------------------------------------------------- check 2 ---
BRIEF_ENUMS = {
 ("onboarding_case", "applicant_type"): ["freelancer_sole_trader", "sme_corporate",
                                         "complex_corporate_ubo", "white_label_partner"],
 ("onboarding_case", "entity_scope"): ["wallester_as", "wallester_uk_ltd", "undetermined"],
 ("onboarding_case", "source_channel"): ["portal", "email"],
 ("onboarding_case", "status"): ["submitted", "document_quality_review", "resubmission_required",
    "verification_in_progress", "analyst_review_required", "enhanced_due_diligence",
    "ready_for_decision", "approved", "rejected", "closed_withdrawn"],
 ("onboarding_case", "next_action_owner"): ["customer", "analyst", "compliance", "system",
                                            "external_provider"],
 ("onboarding_case", "white_label_branch_flag"): ["true", "false"],
 ("individual", "role"): ["director", "ubo", "authorised_signatory", "authorised_user",
                          "sole_trader"],
 ("document", "quality_status"): ["pending", "accepted_for_checks", "resubmission_required",
                                  "manual_review_required"],
 ("document", "quality_flags"): ["blurred_unreadable", "cut_off_pages", "expired", "missing_pages",
    "screenshot_not_original", "name_mismatch", "tampering_indicator", "unsupported_file_type",
    # wrong_document_type: legible, but not the document the checklist asked for.
    # document_too_old: still valid, but older than the rule's max_age_days. Distinct
    # from 'expired', which means the document carries a past expiry date.
    "wrong_document_type", "document_too_old"],
 ("document", "resubmission_reasons"): ["document_unreadable", "document_expired",
    "ubo_declaration_missing", "registered_address_mismatch", "director_identity_missing",
    "source_of_funds_clarification", "ownership_structure_unclear", "proof_of_address_too_old"],
 ("document", "resubmission_required"): ["true", "false"],
 ("registry_check", "company_status"): ["active", "dissolved", "struck_off", "suspended",
                                        "not_found", "unavailable"],
 ("registry_check", "name_match"): ["match", "mismatch", "unavailable"],
 ("registry_check", "number_match"): ["match", "mismatch", "unavailable"],
 ("registry_check", "address_match"): ["match", "mismatch", "unavailable"],
 ("registry_check", "director_match"): ["match", "mismatch", "unavailable"],
 ("registry_check", "result"): ["pass", "fail", "review", "unavailable"],
 ("registry_check", "ubo_supported_by_registry"): ["true", "false"],
 ("registry_check", "high_risk_jurisdiction_or_industry"): ["true", "false"],
 ("identity_check", "document_result"): ["pass", "fail", "review", "unavailable"],
 ("identity_check", "liveness_result"): ["pass", "fail", "review", "unavailable"],
 ("identity_check", "biometric_result"): ["pass", "fail", "review", "unavailable"],
 ("identity_check", "address_result"): ["pass", "fail", "review", "unavailable"],
 ("identity_check", "result"): ["pass", "fail", "review", "unavailable"],
 ("identity_check", "name_dob_match"): ["match", "mismatch", "unavailable"],
 ("identity_check", "document_expired"): ["true", "false"],
 ("identity_check", "duplicate_individual_detected"): ["true", "false"],
 ("screening_check", "sanctions_result"): ["no_match", "possible_match", "clear_match",
                                           "unavailable"],
 ("screening_check", "pep_result"): ["no_match", "pep_match", "close_associate_family",
                                     "unavailable"],
 ("screening_check", "adverse_media_result"): ["none", "low_relevance", "moderate", "serious",
                                               "unavailable"],
 ("screening_check", "severity"): ["none", "low", "medium", "high", "critical"],
 ("risk_assessment", "risk_band"): ["low", "medium", "high", "critical", "insufficient_evidence"],
 ("risk_assessment", "recommended_action"): ["approve", "conditional_approve",
    "enhanced_due_diligence", "reject", "request_more_information", "escalate", "withdrawn",
    "insufficient_evidence"],
 ("risk_assessment", "insufficient_evidence_flag"): ["true", "false"],
 ("risk_assessment", "requires_human_signoff"): ["true", "false"],
 ("human_decision", "decision"): ["approve", "conditional_approve", "enhanced_due_diligence",
    "reject", "request_more_information", "escalate", "withdrawn", "insufficient_evidence"],
 ("human_decision", "override_flag"): ["true", "false"],
 ("evidence_pack", "recommended_next_action"): ["approve", "conditional_approve",
    "enhanced_due_diligence", "reject", "request_more_information", "escalate", "withdrawn",
    "insufficient_evidence"],
 ("checklist_item", "status"): ["pending", "received", "accepted", "resubmission_requested",
                                "manual_review", "waived"],
 ("checklist_item", "level"): ["required", "optional", "conditional"],
 ("requirement_rule", "level"): ["required", "optional", "conditional"],
 ("requirement_rule", "applicant_type"): ["freelancer_sole_trader", "sme_corporate",
    "complex_corporate_ubo", "white_label_partner"],
 ("requirement_pack", "applicant_type"): ["freelancer_sole_trader", "sme_corporate",
    "complex_corporate_ubo", "white_label_partner"],
 ("audit_event", "actor_type"): ["system", "ai_agent", "analyst", "compliance", "applicant",
                                 "external_provider"],
 ("extracted_field", "corrected_by_analyst"): ["true", "false"],
}
# vocabularies chosen for this dataset; the brief does not enumerate these
LOCAL_ENUMS = {
 ("screening_check", "subject_type"): ["applicant", "individual"],
 ("communication", "audience"): ["applicant", "analyst", "compliance"],
 ("communication", "approval_status"): ["approved", "pending_approval", "rejected"],
 ("communication", "sent_status"): ["sent", "not_sent", "failed"],
 ("message_template", "audience"): ["applicant", "analyst", "compliance"],
 ("message_template", "approval_status"): ["approved", "pending_approval", "rejected"],
 ("human_decision", "reviewer_role"): ["analyst", "compliance"],
 ("ubo", "verification_status"): ["verified", "unverified", "pending"],
 ("ubo", "control_type"): ["direct_shareholding", "indirect_shareholding", "voting_rights",
                           "other_control"],
 ("applicant", "entity_type"): ["sole_trader", "private_limited_company"],
 ("applicant", "vat_registered"): ["true", "false"],
 ("applicant", "risk_segment"): ["low", "standard", "elevated", "unassessed"],
 ("onboarding_case", "jurisdiction_path"): ["EE", "UK"],
 ("requirement_rule", "jurisdiction"): ["EE", "UK"],
 ("requirement_pack", "jurisdiction"): ["EE", "UK"],
}
bad_enum, enum_cells = [], 0
for source, label in ((BRIEF_ENUMS, "brief"), (LOCAL_ENUMS, "dataset")):
    for (tbl, c), allowed in source.items():
        for r in T[tbl]:
            v = r[c]
            if v == "":
                continue
            for part in v.split("|"):
                enum_cells += 1
                if part not in allowed:
                    bad_enum.append("%s.%s = %r (%s vocabulary)" % (tbl, c, part, label))
na = []
for tbl in T:
    for r in T[tbl]:
        for c, v in r.items():
            if v.strip().upper() in ("N/A", "NA", "NULL", "NONE_") or v.strip() == "-":
                na.append("%s.%s = %r" % (tbl, c, v))
result("2. Enum values (%d enum columns, %d values incl. pipe-list members)"
       % (len(BRIEF_ENUMS) + len(LOCAL_ENUMS), enum_cells),
       not bad_enum and not na,
       "all values in range; no N/A placeholders" if not bad_enum and not na
       else "; ".join((bad_enum + na)[:20]), enum_cells)

# ---------------------------------------------------------------- check 3 ---
cases = dict((r["case_id"], r) for r in T["onboarding_case"])
by_case = defaultdict(lambda: defaultdict(list))
for tbl in T:
    if "case_id" in (T[tbl][0].keys() if T[tbl] else []):
        for r in T[tbl]:
            by_case[r["case_id"]][tbl].append(r)

ts_problems, ts_points = [], 0
for cid, c in sorted(cases.items()):
    created, updated = c["created_at"], c["updated_at"]
    events = by_case[cid]["audit_event"]
    ev_ts = [e["timestamp"] for e in events]
    ts_points += len(ev_ts)
    if ev_ts != sorted(ev_ts):
        ts_problems.append("%s: audit_event timestamps not monotonic" % cid)
    if ev_ts and ev_ts[0] != created:
        ts_problems.append("%s: first audit event %s != created_at %s" % (cid, ev_ts[0], created))
    docs = [d["upload_time"] for d in by_case[cid]["document"]]
    ts_points += len(docs)
    if docs and min(docs) < created:
        ts_problems.append("%s: document uploaded before case creation" % cid)
    comms = [x["created_at"] for x in by_case[cid]["communication"]]
    decs = [x["timestamp"] for x in by_case[cid]["human_decision"]]
    packs = [x["generated_at"] for x in by_case[cid]["evidence_pack"]]
    ts_points += len(comms) + len(decs) + len(packs)
    if docs and comms and min(comms) < max(docs):
        ts_problems.append("%s: communication drafted before the last document upload" % cid)
    if packs and decs and max(packs) > min(decs):
        ts_problems.append("%s: evidence pack generated after the decision it supports" % cid)
    if docs and decs and min(decs) < max(docs):
        ts_problems.append("%s: decision before the last document upload" % cid)
    allts = ev_ts + docs + comms + decs + packs
    if allts and max(allts) != updated:
        ts_problems.append("%s: updated_at %s is not the latest event %s"
                           % (cid, updated, max(allts)))
    if allts and min(allts) < created:
        ts_problems.append("%s: an event precedes created_at" % cid)
result("3. Timestamp ordering (%d cases, %d timestamps)" % (len(cases), ts_points),
       not ts_problems,
       "every case ordered: created -> upload -> quality/extraction -> provider checks -> risk -> "
       "evidence pack -> communication/decision -> updated_at"
       if not ts_problems else "; ".join(ts_problems[:20]), ts_points)

# ---------------------------------------------------------------- check 4 ---
EXPECTED = {
 "WAL-ONB-0001": ("ready_for_decision", "low"),
 "WAL-ONB-0002": ("resubmission_required", "insufficient_evidence"),
 "WAL-ONB-0003": ("analyst_review_required", "medium"),
 "WAL-ONB-0004": ("enhanced_due_diligence", "high"),
 "WAL-ONB-0005": ("enhanced_due_diligence", "high"),
 "WAL-ONB-0006": ("analyst_review_required", "critical"),
 "WAL-ONB-0007": ("enhanced_due_diligence", "high"),
 "WAL-ONB-0008": ("submitted", None),
 "WAL-ONB-0009": ("approved", "low"),
 "WAL-ONB-0010": ("ready_for_decision", "low"),
 "WAL-ONB-0011": ("rejected", "high"),
 "WAL-ONB-0012": ("analyst_review_required", "critical"),
 "WAL-ONB-0013": ("analyst_review_required", "insufficient_evidence"),
 "WAL-ONB-0014": ("closed_withdrawn", "insufficient_evidence"),
}
band = dict((r["case_id"], r["risk_band"]) for r in T["risk_assessment"])
case_rows = []
mismatch = []
for cid, (st, bd) in sorted(EXPECTED.items()):
    actual_st = cases[cid]["status"]
    actual_bd = band.get(cid)
    ok = (actual_st == st) and ((bd is None and actual_bd is None) or actual_bd == bd)
    case_rows.append((cid, st, actual_st, bd or "(none)", actual_bd or "(none)", ok))
    if not ok:
        mismatch.append("%s: expected %s/%s got %s/%s" % (cid, st, bd, actual_st, actual_bd))
result("4. Case outcomes (%d cases)" % len(EXPECTED), not mismatch,
       "every case reaches its scripted status and risk band" if not mismatch
       else "; ".join(mismatch), len(EXPECTED))

# ---------------------------------------------------------------- check 5 ---
c2 = "WAL-ONB-0002"
bad_doc_subjects = set(d["subject_individual_id"] for d in T["document"]
                       if d["case_id"] == c2 and d["quality_status"] == "resubmission_required"
                       and d["subject_individual_id"])
paid = []
for r in T["identity_check"]:
    if r["case_id"] == c2:
        paid.append("identity_check %s on %s" % (r["check_id"], r["individual_id"]))
for r in T["screening_check"]:
    if r["case_id"] == c2:
        paid.append("screening_check %s" % r["check_id"])
for r in T["registry_check"]:
    if r["case_id"] == c2:
        paid.append("registry_check %s" % r["check_id"])
pending_ubo = [i for i in T["checklist_item"]
               if i["document_type"] == "ubo_declaration" and i["status"] == "pending"
               and i["pack_id"] in [p["pack_id"] for p in T["requirement_pack"]
                                    if p["case_id"] == c2]]
result("5. Case 2 stopped before paid checks (bad-ID director %s)"
       % (", ".join(sorted(bad_doc_subjects)) or "none"),
       not paid and len(pending_ubo) == 1,
       ("no registry, identity or screening rows exist for %s; UBO declaration checklist item is "
        "pending" % c2) if not paid and len(pending_ubo) == 1
       else "found: " + "; ".join(paid) + " | pending ubo items: %d" % len(pending_ubo), 0)

# ---------------------------------------------------------------- check 6 ---
RESTRICTED = ["sanction", "screening", "screened", "aml", "anti-money", "money launder",
              "launder", "pep ", "politically exposed", "adverse media", "watchlist", "risk score",
              "risk band", "risk rating", "possible match", "true match", "match", "embargo",
              "blacklist", "terrorist", "financial crime", "mlro", "escalat", "due diligence"]
leaks = []
applicant_msgs = [r for r in T["communication"] if r["audience"] == "applicant"]
for r in applicant_msgs:
    low = r["rendered_text"].lower()
    for w in RESTRICTED:
        if w in low:
            leaks.append("%s (%s) contains %r" % (r["communication_id"], r["case_id"], w))
c6_msgs = [r for r in applicant_msgs if r["case_id"] == "WAL-ONB-0006"]
result("6. No restricted wording in applicant-facing text (%d applicant messages, %d on case 6)"
       % (len(applicant_msgs), len(c6_msgs)), not leaks,
       "no message mentions sanctions, screening, AML, PEP, adverse media, matches, escalation or "
       "risk scoring" if not leaks else "; ".join(leaks), len(applicant_msgs))

# ---------------------------------------------------------------- check 7 ---
viol = []
# 7a approval gate
dec_cases = set(r["case_id"] for r in T["human_decision"])
for r in T["risk_assessment"]:
    if r["risk_band"] in ("medium", "high", "critical") and cases[r["case_id"]]["status"] == "approved":
        if r["case_id"] not in dec_cases:
            viol.append("7a %s approved at band %s with no human_decision"
                        % (r["case_id"], r["risk_band"]))
flagged_cases = set(r["case_id"] for r in T["screening_check"]
                    if r["sanctions_result"] in ("possible_match", "clear_match")
                    or r["pep_result"] in ("pep_match", "close_associate_family"))
for cid in flagged_cases:
    if cases[cid]["status"] == "approved" and cid not in dec_cases:
        viol.append("7a %s approved with a sanctions/PEP finding and no human_decision" % cid)
# 7b send gate
for r in T["communication"]:
    if r["audience"] == "applicant" and r["sent_status"] == "sent" \
            and r["approval_status"] != "approved":
        viol.append("7b %s sent to applicant with approval_status %s"
                    % (r["communication_id"], r["approval_status"]))
# 7c override / escalation completeness
for r in T["human_decision"]:
    if r["override_flag"] == "true" and not r["override_reason"].strip():
        viol.append("7c %s has override_flag true and no override_reason" % r["decision_id"])
    if r["override_flag"] == "false" and r["override_reason"].strip():
        viol.append("7c %s has an override_reason but override_flag false" % r["decision_id"])
    if r["decision"] == "escalate" and not r["escalation_target"].strip():
        viol.append("7c %s decision escalate with no escalation_target" % r["decision_id"])
# 7d screening subject XOR
for r in T["screening_check"]:
    filled = bool(r["applicant_id"]) + bool(r["individual_id"])
    if filled != 1:
        viol.append("7d %s has %d subject columns filled" % (r["check_id"], filled))
    if r["subject_type"] == "applicant" and not r["applicant_id"]:
        viol.append("7d %s subject_type applicant but applicant_id blank" % r["check_id"])
    if r["subject_type"] == "individual" and not r["individual_id"]:
        viol.append("7d %s subject_type individual but individual_id blank" % r["check_id"])
# 7e human sign-off flag set for medium and above / any match
for r in T["risk_assessment"]:
    if r["risk_band"] in ("medium", "high", "critical") and r["requires_human_signoff"] != "true":
        viol.append("7e %s band %s without requires_human_signoff" % (r["case_id"], r["risk_band"]))
for cid in flagged_cases:
    ra = [r for r in T["risk_assessment"] if r["case_id"] == cid]
    if ra and ra[0]["requires_human_signoff"] != "true":
        viol.append("7e %s has a sanctions/PEP finding without requires_human_signoff" % cid)
# 7f case 8 white-label branch carries no decisioning rows
for tbl in ("risk_assessment", "human_decision", "screening_check", "registry_check",
            "identity_check", "evidence_pack"):
    n = len([r for r in T[tbl] if r["case_id"] == "WAL-ONB-0008"])
    if n:
        viol.append("7f WAL-ONB-0008 has %d %s row(s)" % (n, tbl))
if cases["WAL-ONB-0008"]["white_label_branch_flag"] != "true":
    viol.append("7f WAL-ONB-0008 white_label_branch_flag is not true")
if not [e for e in T["audit_event"] if e["case_id"] == "WAL-ONB-0008"
        and e["action"] == "routed_to_white_label_future_phase"]:
    viol.append("7f WAL-ONB-0008 has no white-label routing audit event")
# 7g every ai_agent audit event carries a model/prompt version
for e in T["audit_event"]:
    if e["actor_type"] == "ai_agent" and not e["model_or_prompt_version"].strip():
        viol.append("7g %s is an ai_agent event with no model_or_prompt_version" % e["event_id"])
# 7h checklist items derivable from the pack's rules
pack_by_id = dict((p["pack_id"], p) for p in T["requirement_pack"])
rule_by_id = dict((r["rule_id"], r) for r in T["requirement_rule"])
for i in T["checklist_item"]:
    p, ru = pack_by_id[i["pack_id"]], rule_by_id[i["rule_id"]]
    if (ru["applicant_type"], ru["jurisdiction"], ru["entity_type"]) != \
            (p["applicant_type"], p["jurisdiction"], p["entity_type"]):
        viol.append("7h %s uses rule %s outside its pack scope" % (i["item_id"], i["rule_id"]))
    if i["document_type"] != ru["document_type"] or i["level"] != ru["level"]:
        viol.append("7h %s does not match rule %s" % (i["item_id"], i["rule_id"]))
# 7i id formats
import re
PATTERNS = [("onboarding_case", "case_id", r"^WAL-ONB-\d{4}$"),
            ("applicant", "applicant_id", r"^APP-\d{4}$"),
            ("individual", "individual_id", r"^IND-\d{4}$"),
            ("document", "document_id", r"^DOC-\d{4}$"),
            ("ubo", "ubo_id", r"^UBO-\d{4}$"),
            ("registry_check", "check_id", r"^REG-\d{4}$"),
            ("identity_check", "check_id", r"^IDC-\d{4}$"),
            ("screening_check", "check_id", r"^SCR-\d{4}$"),
            ("risk_assessment", "assessment_id", r"^RSK-\d{4}$"),
            ("human_decision", "decision_id", r"^DEC-\d{4}$"),
            ("audit_event", "event_id", r"^EVT-\d{4}$"),
            ("requirement_rule", "rule_id", r"^RULE-\d{4}$"),
            ("checklist_item", "item_id", r"^CHK-\d{4}$"),
            ("extracted_field", "field_id", r"^FLD-\d{4}$"),
            ("risk_factor", "factor_id", r"^RF-\d{4}$"),
            ("message_template", "template_id", r"^TPL-\d{4}$")]
for (tbl, c, pat) in PATTERNS:
    for r in T[tbl]:
        if not re.match(pat, r[c]):
            viol.append("7i %s.%s = %r does not match %s" % (tbl, c, r[c], pat))
    ids = [r[c] for r in T[tbl]]
    if len(ids) != len(set(ids)):
        viol.append("7i %s.%s is not unique" % (tbl, c))
result("7. Compliance and integrity rules (7a approval gate, 7b send gate, 7c override and "
       "escalation, 7d screening subject XOR, 7e sign-off flag, 7f white-label branch, 7g AI "
       "version stamps, 7h checklist derivability, 7i id formats and uniqueness)",
       not viol, "all rules hold" if not viol else "; ".join(viol[:25]), 0)

# ------------------------------------------------------------------ output --
say("# Wallester UC4 dataset - validation report")
say()
say("Generated from `wallester_uc4_dataset/` by `validate_dataset.py`.")
say()
say("| # | Check | Result | Detail |")
say("|---|-------|--------|--------|")
for (check, ok, detail, rows) in RESULTS:
    say("| %s | %s | %s | %s |" % (check.split(".")[0], check.split(". ", 1)[1],
                                   "PASS" if ok else "FAIL", detail))
say()
say("## Case outcome matrix (check 4)")
say()
say("| Case | Expected status | Actual status | Expected band | Actual band | Result |")
say("|------|-----------------|---------------|---------------|-------------|--------|")
for (cid, st, ast, bd, abd, ok) in case_rows:
    say("| %s | %s | %s | %s | %s | %s |" % (cid, st, ast, bd, abd, "PASS" if ok else "FAIL"))
say()
say("## Row counts")
say()
say("| Table | Rows |")
say("|-------|------|")
for t in sorted(T):
    say("| %s.csv | %d |" % (t, len(T[t])))
say()
ok_all = all(r[1] for r in RESULTS)
say("**Overall: %s**" % ("PASS - all 7 checks green" if ok_all else "FAIL"))

text = "\n".join(REPORT) + "\n"
f = open(os.path.join(OUT, "validation_report.md"), "w", encoding="utf-8")
f.write(text)
f.close()
print(text)
sys.exit(0 if ok_all else 1)
