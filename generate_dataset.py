# -*- coding: utf-8 -*-
"""
Wallester UC4 - KYC/KYB Onboarding Orchestration POC
Correlation-first synthetic dataset generator.

Deterministic: no randomness. Every row belongs to one of 10 scripted cases.
Run:  python generate_dataset.py
"""
import csv, os, re, zipfile
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "wallester_uc4_dataset")
if not os.path.isdir(OUT):
    os.makedirs(OUT)

KB_VERSION = "WAL-KB-2026.09"
RISK_MATRIX = "WAL-RM-2026.03"
V_INTAKE = "wallester-uc4-intake-prompt-v0.9"
V_QUALITY = "doc-quality-classifier-v1.4"
V_OCR = "ocr-extract-v2.3"
V_RISK = "risk-explainer-v1.6"
V_PACK = "evidence-pack-writer-v1.2"
V_COMMS = "comms-drafter-v1.3"

SCHEMA = {
 "onboarding_case": ["case_id", "applicant_id", "applicant_type", "jurisdiction_path", "entity_scope",
    "source_channel", "status", "assigned_owner", "next_action_owner", "white_label_branch_flag",
    "created_at", "updated_at"],
 "applicant": ["applicant_id", "legal_name", "trading_name", "registration_number", "entity_type", "country",
    "business_activity", "expected_usage", "vat_registered", "risk_segment"],
 "individual": ["individual_id", "applicant_id", "role", "full_name", "date_of_birth", "nationality",
    "residence_country", "id_document_id", "relationship_to_entity"],
 "ubo": ["ubo_id", "applicant_id", "individual_id", "ownership_percentage", "ownership_chain_percentages", "control_type", "ownership_path",
    "verification_status"],
 "document": ["document_id", "case_id", "subject_individual_id", "document_type", "file_name", "upload_time",
    "quality_status", "quality_flags", "expiry_date", "issue_country", "resubmission_required",
    "resubmission_reasons"],
 "registry_check": ["check_id", "case_id", "applicant_id", "provider_name", "company_status",
    "registry_legal_name", "registry_number", "registry_address", "registry_directors",
    "name_match", "number_match", "address_match", "director_match",
    "ubo_supported_by_registry", "high_risk_jurisdiction_or_industry", "confidence", "result"],
 "identity_check": ["check_id", "case_id", "individual_id", "provider_name", "document_result",
    "liveness_result", "biometric_result", "address_result", "name_dob_match", "document_expired",
    "duplicate_individual_detected", "result"],
 "screening_check": ["check_id", "case_id", "subject_type", "applicant_id", "individual_id",
    "sanctions_result", "pep_result", "adverse_media_result", "severity", "evidence_refs"],
 "risk_assessment": ["assessment_id", "case_id", "risk_score", "risk_band", "recommended_action", "confidence",
    "insufficient_evidence_flag", "requires_human_signoff", "risk_matrix_version"],
 "communication": ["communication_id", "case_id", "template_id", "audience", "message_type", "approval_status",
    "sent_status", "rendered_text", "created_at"],
 "human_decision": ["decision_id", "case_id", "reviewer", "reviewer_role", "decision", "reason_code",
    "rationale", "evidence_relied_on", "override_flag", "override_reason", "escalation_target",
    "customer_template_id", "timestamp"],
 "audit_event": ["event_id", "case_id", "actor_type", "actor_id", "action", "payload_summary",
    "model_or_prompt_version", "timestamp"],
 "requirement_rule": ["rule_id", "applicant_type", "jurisdiction", "entity_type", "requirement_area",
    "document_type", "level", "condition", "condition_key", "applies_per_individual_role",
    "max_age_days"],
 "requirement_pack": ["pack_id", "case_id", "applicant_type", "jurisdiction", "entity_type", "kb_version"],
 "checklist_item": ["item_id", "pack_id", "rule_id", "subject_individual_id", "document_type", "level",
    "status", "resubmission_attempts"],
 "evidence_pack": ["evidence_pack_id", "case_id", "assessment_id", "decision_id", "generated_at",
    "applicant_summary", "missing_or_conflicting_evidence", "recommended_next_action",
    "draft_compliance_narrative"],
 "extracted_field": ["field_id", "document_id", "name", "value", "confidence", "source_page",
    "corrected_by_analyst"],
 "risk_scoring_matrix": ["factor_id", "factor", "source", "condition", "points",
    "description", "weight_status"],
 "risk_bands": ["band_id", "band", "min_score", "max_score", "hard_floor_condition",
    "recommended_action", "description"],
 "risk_factor": ["factor_id", "assessment_id", "factor", "weight", "explanation", "evidence_refs"],
 "checklist_item_document": ["item_id", "document_id"],
 "message_template": ["template_id", "message_type", "audience", "template_text", "version",
    "approval_status"],
}
ROWS = dict((t, []) for t in SCHEMA)
_ctr = {}


def nid(prefix, width=4):
    _ctr[prefix] = _ctr.get(prefix, 0) + 1
    return "%s-%0*d" % (prefix, width, _ctr[prefix])


def fmt(v):
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "|".join(str(x) for x in v)
    return str(v)


def add(table, **kw):
    cols = SCHEMA[table]
    row = dict((c, "") for c in cols)
    for k, v in kw.items():
        if k not in cols:
            raise KeyError("%s has no column %s" % (table, k))
        row[k] = fmt(v)
    ROWS[table].append(row)
    return row


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock(object):
    def __init__(self, dt):
        self.dt = dt

    def tick(self, minutes):
        self.dt = self.dt + timedelta(minutes=minutes)
        return iso(self.dt)

    def now(self):
        return iso(self.dt)


def audit(case_id, actor_type, actor_id, action, summary, ts, version=""):
    add("audit_event", event_id=nid("EVT"), case_id=case_id, actor_type=actor_type, actor_id=actor_id,
        action=action, payload_summary=summary, model_or_prompt_version=version, timestamp=ts)


# ---------------------------------------------------------------------------
# Requirement rules (knowledge pack: document requirement matrix, brief s.7)
# ---------------------------------------------------------------------------
RULES = []

# The condition column is written for a human reader. condition_key is the same
# condition as a stable machine token, so an orchestrator can look the answer up
# in the application's flags instead of string-matching an English sentence.
CONDITION_KEYS = {
    "remote onboarding without in-person verification": "remote_onboarding",
    "company is VAT registered": "vat_registered",
    "authorised signatory is not a registered director": "signatory_not_director",
    "expected monthly card spend above 50000 EUR": "spend_above_50k",
    "nominee or trust entity present in the ownership chain": "nominee_or_trust_in_chain",
    "UBO holds 25 percent or more through an indirect ownership chain":
        "ubo_indirect_25pct_or_more",
    "registered with a national trade or business register": "registered_with_trade_register",
}


def rule(applicant_type, jurisdiction, entity_type, area, doc_type, level,
         condition="", roles="", max_age=""):
    rid = nid("RULE")
    if condition and condition not in CONDITION_KEYS:
        raise KeyError("no condition_key defined for %r" % condition)
    condition_key = CONDITION_KEYS[condition] if condition else ""
    RULES.append({"rule_id": rid, "applicant_type": applicant_type, "jurisdiction": jurisdiction,
                  "entity_type": entity_type, "requirement_area": area, "document_type": doc_type,
                  "level": level, "condition": condition, "condition_key": condition_key,
                  "applies_per_individual_role": roles, "max_age_days": max_age})
    add("requirement_rule", rule_id=rid, applicant_type=applicant_type, jurisdiction=jurisdiction,
        entity_type=entity_type, requirement_area=area, document_type=doc_type, level=level,
        condition=condition, condition_key=condition_key, applies_per_individual_role=roles,
        max_age_days=max_age)
    return rid


POA_AGE = {"EE": 90, "UK": 120}


def freelancer_rules(j):
    et = "sole_trader"
    rule("freelancer_sole_trader", j, et, "entity_proof", "registry_extract",
         "required" if j == "EE" else "conditional",
         "" if j == "EE" else "registered with a national trade or business register", "", 180)
    rule("freelancer_sole_trader", j, et, "individual_identity", "id_document", "required", "",
         "sole_trader", "")
    rule("freelancer_sole_trader", j, et, "individual_identity", "proof_of_address", "required", "",
         "sole_trader", POA_AGE[j])
    rule("freelancer_sole_trader", j, et, "individual_identity", "liveness_selfie", "conditional",
         "remote onboarding without in-person verification", "sole_trader", "")
    rule("freelancer_sole_trader", j, et, "source_of_funds", "source_of_funds_declaration", "required",
         "", "", 365)
    rule("freelancer_sole_trader", j, et, "business_model", "business_activity_description", "required",
         "", "", "")
    rule("freelancer_sole_trader", j, et, "risk_specific", "website_or_platform_details", "optional",
         "", "", "")


def corporate_core_rules(at, j):
    et = "private_limited_company"
    rule(at, j, et, "entity_proof", "certificate_of_incorporation", "required", "", "", "")
    rule(at, j, et, "entity_proof", "registry_extract", "required", "", "", 90)
    rule(at, j, et, "entity_proof", "tax_registration_certificate",
         "required" if j == "EE" else "conditional",
         "" if j == "EE" else "company is VAT registered", "", "")
    rule(at, j, et, "directors_officers", "director_register", "required", "", "", 90)
    rule(at, j, et, "directors_officers", "authorised_signatory_list", "required", "", "", "")
    rule(at, j, et, "directors_officers", "board_resolution", "conditional",
         "authorised signatory is not a registered director", "", "")
    rule(at, j, et, "ownership_ubo", "ubo_declaration", "required", "", "", 365)
    rule(at, j, et, "individual_identity", "id_document", "required", "",
         "director|ubo|authorised_signatory", "")
    rule(at, j, et, "individual_identity", "proof_of_address", "required", "", "director|ubo", POA_AGE[j])
    rule(at, j, et, "individual_identity", "liveness_selfie", "conditional",
         "remote onboarding without in-person verification", "director", "")
    rule(at, j, et, "source_of_funds", "source_of_funds_declaration", "required", "", "", 365)
    rule(at, j, et, "business_model", "business_activity_description", "required", "", "", "")
    rule(at, j, et, "risk_specific", "bank_statement", "conditional",
         "expected monthly card spend above 50000 EUR", "", 90)


def complex_rules(j):
    at = "complex_corporate_ubo"
    et = "private_limited_company"
    corporate_core_rules(at, j)
    rule(at, j, et, "ownership_ubo", "ownership_chart", "required", "", "", 365)
    rule(at, j, et, "ownership_ubo", "shareholder_register", "required", "", "", 90)
    rule(at, j, et, "ownership_ubo", "nominee_trust_explanation", "conditional",
         "nominee or trust entity present in the ownership chain", "", "")
    rule(at, j, et, "risk_specific", "source_of_wealth_statement", "conditional",
         "UBO holds 25 percent or more through an indirect ownership chain", "", 365)


def white_label_rules(j):
    at = "white_label_partner"
    et = "private_limited_company"
    rule(at, j, et, "entity_proof", "certificate_of_incorporation", "required", "", "", "")
    rule(at, j, et, "entity_proof", "registry_extract", "required", "", "", 90)
    rule(at, j, et, "directors_officers", "director_register", "required", "", "", 90)
    rule(at, j, et, "ownership_ubo", "ubo_declaration", "required", "", "", 365)
    rule(at, j, et, "business_model", "programme_business_plan", "required", "", "", "")
    rule(at, j, et, "risk_specific", "website_or_platform_details", "required", "", "", "")


for _j in ("EE", "UK"):
    freelancer_rules(_j)
    corporate_core_rules("sme_corporate", _j)
    complex_rules(_j)
    white_label_rules(_j)


# ---------------------------------------------------------------------------
# Compliance-approved message library
# ---------------------------------------------------------------------------
TEMPLATES = [
 ("TPL-0001", "resubmission_request", "applicant",
  "Hello {{contact_name}}, thank you for your application for {{applicant_name}} (reference {{case_id}}). "
  "To continue we need the following items: {{item_list}}. Please upload them in the onboarding portal. "
  "If anything is unclear, reply to this message and our onboarding team will help.", "1.2", "approved"),
 ("TPL-0002", "clarification_request", "applicant",
  "Hello {{contact_name}}, we are completing the company checks for {{applicant_name}} (reference "
  "{{case_id}}). The registered address held on the official company register does not appear to be the "
  "same as the address on the documents you submitted. Please confirm the current registered address and "
  "upload a document that shows it.", "1.1", "approved"),
 ("TPL-0003", "additional_information_request", "applicant",
  "Hello {{contact_name}}, to complete the company verification for {{applicant_name}} (reference "
  "{{case_id}}) we need a clearer picture of the ownership structure. Please upload a complete ownership "
  "chart showing every intermediate company and the individuals who ultimately own or control "
  "{{applicant_name}}, together with {{item_list}}.", "1.3", "approved"),
 ("TPL-0004", "manual_review_underway", "applicant",
  "Hello {{contact_name}}, your application for {{applicant_name}} (reference {{case_id}}) is with our "
  "onboarding team for an additional manual review step. No further documents are needed from you at this "
  "time. We will contact you as soon as this step is complete.", "1.4", "approved"),
 ("TPL-0005", "status_update", "applicant",
  "Hello {{contact_name}}, we have received your application for {{applicant_name}} and all of the "
  "documents we asked for. Your case reference is {{case_id}}. Your application is now with our onboarding "
  "team and we will let you know as soon as the review is complete.", "1.1", "approved"),
 ("TPL-0006", "verification_delay", "applicant",
  "Hello {{contact_name}}, the checks on your application for {{applicant_name}} (reference {{case_id}}) "
  "are taking a little longer than usual. No action is needed from you. We will update you again within "
  "three working days.", "1.0", "approved"),
 ("TPL-0007", "approval_notification", "applicant",
  "Hello {{contact_name}}, good news. The Wallester Business account for {{applicant_name}} (reference "
  "{{case_id}}) has been approved. You can sign in to the portal to order your first cards and invite "
  "users.", "1.2", "approved"),
 ("TPL-0008", "internal_case_summary", "analyst",
  "Internal case summary for {{case_id}} ({{applicant_name}}). Prepared by the orchestration layer for "
  "analyst review. Contains evidence references, outstanding items and the recommended next action. Not "
  "for disclosure to the applicant.", "1.5", "approved"),
 ("TPL-0009", "internal_escalation_note", "compliance",
  "Internal escalation note for {{case_id}} ({{applicant_name}}). Routed to {{escalation_target}}. "
  "Automated progression is blocked pending compliance review. Not for disclosure to the applicant.",
  "1.1", "approved"),
 ("TPL-0010", "white_label_intake_acknowledgement", "applicant",
  "Hello {{contact_name}}, thank you for the partner programme enquiry for {{applicant_name}} (reference "
  "{{case_id}}). Your company documents have been received. Partner programme onboarding is handled by our "
  "programme delivery team, who will contact you about the next steps.", "1.0", "approved"),
 # Neither of these may state a reason that would disclose a check result: a
 # decline says what the applicant can act on and nothing more.
 ("TPL-0011", "application_declined", "applicant",
  "Hello {{contact_name}}, thank you for your application for {{applicant_name}} (reference "
  "{{case_id}}). We are not able to open an account at this time. If your circumstances change you "
  "are welcome to apply again.", "1.0", "approved"),
 ("TPL-0012", "case_closed", "applicant",
  "Hello {{contact_name}}, we have not heard back about the documents we asked for, so we have closed "
  "your application for {{applicant_name}} (reference {{case_id}}) for now. Nothing is held against "
  "you and you are welcome to apply again whenever it suits you.", "1.0", "approved"),
]
for _t in TEMPLATES:
    add("message_template", template_id=_t[0], message_type=_t[1], audience=_t[2], template_text=_t[3],
        version=_t[4], approval_status=_t[5])
TEMPLATE_BY_ID = dict((t[0], t) for t in TEMPLATES)


# ---------------------------------------------------------------------------
# Case driver
# ---------------------------------------------------------------------------
QUALITY_TO_ITEM = {
    "accepted_for_checks": "accepted",
    "resubmission_required": "resubmission_requested",
    "manual_review_required": "manual_review",
    "pending": "received",
}
QUALITY_SEVERITY = ["accepted_for_checks", "pending", "manual_review_required", "resubmission_required"]


def find_rule(applicant_type, jurisdiction, document_type):
    for r in RULES:
        if (r["applicant_type"] == applicant_type and r["jurisdiction"] == jurisdiction
                and r["document_type"] == document_type):
            return r["rule_id"]
    raise KeyError("no rule for %s/%s/%s" % (applicant_type, jurisdiction, document_type))


def resolve_tokens(start_idx, refmap):
    """Replace symbolic references such as {DOC:id_document@IND-0002} with real ids."""
    for table, rows in ROWS.items():
        for row in rows[start_idx[table]:]:
            for col, val in list(row.items()):
                if "{" not in val:
                    continue
                out = val
                for key, real in refmap.items():
                    out = out.replace("{" + key + "}", real)
                if "{" in out:
                    raise ValueError("unresolved reference in %s.%s: %s" % (table, col, out))
                row[col] = out


def emit_extra_audit(spec, case_id, clk, key):
    for extra in spec.get(key, []):
        t = clk.tick(extra.get("after", 2))
        audit(case_id, extra["actor_type"], extra["actor_id"], extra["action"], extra["summary"], t,
              extra.get("version", ""))


def emit_communications(spec, case_id, clk):
    for c in spec.get("communications", []):
        comm_id = nid("COM")
        created = clk.tick(4)
        add("communication", communication_id=comm_id, case_id=case_id, template_id=c["template_id"],
            audience=c["audience"], message_type=c["message_type"],
            approval_status=c["approval_status"], sent_status=c["sent_status"],
            rendered_text=c["rendered_text"], created_at=created)
        audit(case_id, "ai_agent", "communication-agent", "communication_drafted",
              "%s drafted from template %s for audience %s"
              % (comm_id, c["template_id"], c["audience"]), created, V_COMMS)
        if c["approval_status"] == "approved":
            t = clk.tick(2)
            audit(case_id, c.get("approver_role", "analyst"),
                  c.get("approver", spec["assigned_owner"]), "communication_approved",
                  "%s approved for release under template %s" % (comm_id, c["template_id"]), t)
        else:
            t = clk.tick(2)
            audit(case_id, "system", "communication-gate", "communication_held",
                  "%s held: approval_status is %s, so it has not been sent"
                  % (comm_id, c["approval_status"]), t)
        if c["sent_status"] == "sent":
            t = clk.tick(1)
            audit(case_id, "system", "notification-service", "communication_sent",
                  "%s sent to %s" % (comm_id, c["audience"]), t)


def emit_decisions(spec, case_id, clk):
    decision_id = ""
    for hd in spec.get("decisions", []):
        decision_id = nid("DEC")
        t = clk.tick(35)
        add("human_decision", decision_id=decision_id, case_id=case_id, reviewer=hd["reviewer"],
            reviewer_role=hd["reviewer_role"], decision=hd["decision"], reason_code=hd["reason_code"],
            rationale=hd["rationale"], evidence_relied_on=hd["evidence_relied_on"],
            override_flag=hd["override_flag"], override_reason=hd.get("override_reason", ""),
            escalation_target=hd.get("escalation_target", ""),
            customer_template_id=hd.get("customer_template_id", ""), timestamp=t)
        audit(case_id, hd["reviewer_role"], hd["reviewer"], "human_decision_recorded",
              "%s -> %s (reason %s)" % (decision_id, hd["decision"], hd["reason_code"]), t)
    return decision_id


def build_case(spec):
    case_id = spec["case_id"]
    app_id = spec["applicant_id"]
    clk = Clock(spec["start"])
    created_at = clk.now()
    start_idx = dict((t, len(ROWS[t])) for t in ROWS)
    refmap = {}

    # ---- applicant + individuals + declared UBOs ---------------------------
    a = spec["applicant"]
    add("applicant", applicant_id=app_id, legal_name=a["legal_name"],
        trading_name=a.get("trading_name", ""), registration_number=a["registration_number"],
        entity_type=a["entity_type"], country=a["country"], business_activity=a["business_activity"],
        expected_usage=a["expected_usage"],
        # Declared on the application form, not inferred from the documents supplied.
        vat_registered=str(a["vat_registered"]).lower(), risk_segment=a["risk_segment"])

    inds = spec.get("individuals", [])
    for ind in inds:
        add("individual", individual_id=ind["id"], applicant_id=app_id, role=ind["role"],
            full_name=ind["full_name"], date_of_birth=ind["dob"], nationality=ind["nationality"],
            residence_country=ind["residence"], id_document_id="",
            relationship_to_entity=ind["relationship"])

    for u in spec.get("ubos", []):
        add("ubo", ubo_id=u["id"], applicant_id=app_id, individual_id=u["individual_id"],
            ownership_percentage=u["pct"],
            ownership_chain_percentages="|".join(
                str(c) for c in u.get("chain", [u["pct"]])), control_type=u["control_type"],
            ownership_path=u["ownership_path"], verification_status=u["verification_status"])

    audit(case_id, "system", "portal-intake-service", "case_created",
          "Onboarding case created from %s intake for %s" % (spec["source_channel"], a["legal_name"]),
          created_at)
    t = clk.tick(3)
    audit(case_id, "ai_agent", "applicant-classifier", "applicant_type_classified",
          "Classified as %s; jurisdiction path %s; entity scope %s"
          % (spec["applicant_type"], spec["jurisdiction"], spec["entity_scope"]), t, V_INTAKE)

    # ---- requirement pack --------------------------------------------------
    pack_id = nid("PACK")
    refmap["PACK"] = pack_id
    t = clk.tick(2)
    add("requirement_pack", pack_id=pack_id, case_id=case_id, applicant_type=spec["applicant_type"],
        jurisdiction=spec["jurisdiction"], entity_type=a["entity_type"], kb_version=KB_VERSION)
    audit(case_id, "ai_agent", "requirement-pack-builder", "requirement_pack_generated",
          "Requirement pack %s generated from %s for %s / %s"
          % (pack_id, KB_VERSION, spec["applicant_type"], spec["jurisdiction"]), t, V_INTAKE)

    # ---- documents ---------------------------------------------------------
    docs = spec.get("documents", [])
    clk.tick(25)
    for d in docs:
        d["_id"] = nid("DOC")
        d["_upload"] = clk.tick(2)
        key = "DOC:" + d["type"] + ("@" + d["subject"] if d.get("subject") else "")
        refmap.setdefault(key, d["_id"])
    if docs:
        audit(case_id, "applicant", app_id, "documents_uploaded",
              "%d file(s) received via %s" % (len(docs), spec["source_channel"]), clk.tick(1))

    for d in docs:
        add("document", document_id=d["_id"], case_id=case_id,
            subject_individual_id=d.get("subject", ""), document_type=d["type"],
            file_name=d["file_name"], upload_time=d["_upload"], quality_status=d["quality"],
            quality_flags=d.get("flags", ""), expiry_date=d.get("expiry", ""),
            issue_country=d.get("issue_country", ""),
            resubmission_required=(d["quality"] == "resubmission_required"),
            resubmission_reasons=d.get("reasons", ""))

    for ind in inds:
        for d in docs:
            if (d["type"] == "id_document" and d.get("subject") == ind["id"]
                    and d["quality"] == "accepted_for_checks"):
                for r in ROWS["individual"]:
                    if r["individual_id"] == ind["id"]:
                        r["id_document_id"] = d["_id"]

    if docs:
        t = clk.tick(4)
        bad = [d for d in docs if d["quality"] != "accepted_for_checks"]
        audit(case_id, "ai_agent", "document-quality-agent", "document_quality_prescreen_completed",
              "%d document(s) screened; %d accepted for checks; %d flagged"
              % (len(docs), len(docs) - len(bad), len(bad)), t, V_QUALITY)
        for d in bad:
            t = clk.tick(1)
            audit(case_id, "ai_agent", "document-quality-agent", "document_flagged",
                  "%s (%s) -> %s; flags %s; reasons %s"
                  % (d["_id"], d["type"], d["quality"], d.get("flags", ""), d.get("reasons", "")),
                  t, V_QUALITY)

    # ---- OCR / structured extraction ---------------------------------------
    n_fields, corrections, low = 0, [], 0
    for d in docs:
        for f in d.get("fields", []):
            fid = nid("FLD")
            n_fields += 1
            if float(f[2]) < 0.70:
                low += 1
            add("extracted_field", field_id=fid, document_id=d["_id"], name=f[0], value=f[1],
                confidence=f[2], source_page=f[3], corrected_by_analyst=f[4])
            if f[4]:
                corrections.append((fid, d["_id"], f[0]))
    if n_fields:
        t = clk.tick(3)
        audit(case_id, "ai_agent", "ocr-extraction-agent", "fields_extracted",
              "%d field(s) extracted from %d document(s); %d below the 0.70 confidence threshold"
              % (n_fields, len([d for d in docs if d.get("fields")]), low), t, V_OCR)
    for (fid, did, name) in corrections:
        t = clk.tick(6)
        audit(case_id, "analyst", spec["assigned_owner"], "extracted_field_corrected",
              "Analyst corrected low-confidence field %s (%s) on document %s" % (fid, name, did), t)

    # ---- checklist items, derived from requirement_rule --------------------
    triggered = set(spec.get("triggered_conditionals", []))
    applicable = [r for r in RULES
                  if r["applicant_type"] == spec["applicant_type"]
                  and r["jurisdiction"] == spec["jurisdiction"]
                  and r["entity_type"] == a["entity_type"]]
    for r in applicable:
        if r["applies_per_individual_role"]:
            roles = r["applies_per_individual_role"].split("|")
            subjects = [i["id"] for i in inds if i["role"] in roles]
        else:
            subjects = [""]
        for subj in subjects:
            matched = [d for d in docs
                       if d["type"] == r["document_type"] and d.get("subject", "") == subj]
            item_id = nid("CHK")
            if matched:
                worst = matched[0]
                for m in matched:
                    if QUALITY_SEVERITY.index(m["quality"]) > QUALITY_SEVERITY.index(worst["quality"]):
                        worst = m
                status = QUALITY_TO_ITEM[worst["quality"]]
                attempts = 1 if status == "resubmission_requested" else 0
            else:
                if r["level"] == "required" or r["rule_id"] in triggered:
                    status = "pending"
                else:
                    status = "waived"
                attempts = 0
            add("checklist_item", item_id=item_id, pack_id=pack_id, rule_id=r["rule_id"],
                subject_individual_id=subj, document_type=r["document_type"], level=r["level"],
                status=status, resubmission_attempts=attempts)
            for m in matched:
                add("checklist_item_document", item_id=item_id, document_id=m["_id"])

    emit_extra_audit(spec, case_id, clk, "audit_after_quality")

    # ---- provider checks ---------------------------------------------------
    reg = spec.get("registry")
    if reg:
        t = clk.tick(5)
        cid = nid("REG")
        refmap["REG"] = cid
        def _field(dtype, fname):
            for d in docs:
                if d["type"] == dtype:
                    for f in d.get("fields", []):
                        if f[0] == fname:
                            return f[1]
            return ""
        directors = [v for v in (_field("director_register", "director_name"),
                                 _field("director_register", "director_name_2")) if v]
        if not directors:
            directors = [i["full_name"] for i in inds if i["role"] in ("director", "sole_trader")]
        add("registry_check", check_id=cid, case_id=case_id, applicant_id=app_id,
            provider_name=reg["provider"], company_status=reg["company_status"],
            registry_legal_name=reg.get("legal_name", a["legal_name"]),
            registry_number=reg.get("number", a["registration_number"]),
            registry_address=reg.get("address", _field("registry_extract", "registered_address")),
            registry_directors="|".join(reg.get("directors", directors)),
            name_match=reg["name_match"], number_match=reg["number_match"],
            address_match=reg["address_match"], director_match=reg["director_match"],
            ubo_supported_by_registry=reg["ubo_supported"],
            high_risk_jurisdiction_or_industry=reg["high_risk"], confidence=reg["confidence"],
            result=reg["result"])
        audit(case_id, "external_provider", reg["provider"], "registry_check_completed",
              "%s -> company_status %s, name %s, number %s, address %s, director %s, result %s"
              % (cid, reg["company_status"], reg["name_match"], reg["number_match"],
                 reg["address_match"], reg["director_match"], reg["result"]), t)

    if spec.get("identity_checks"):
        for ic in spec["identity_checks"]:
            cid = nid("IDC")
            ic["_id"] = cid
            refmap["IDC:" + ic["individual_id"]] = cid
            add("identity_check", check_id=cid, case_id=case_id, individual_id=ic["individual_id"],
                provider_name=ic["provider"], document_result=ic["document_result"],
                liveness_result=ic["liveness_result"], biometric_result=ic["biometric_result"],
                address_result=ic["address_result"], name_dob_match=ic["name_dob_match"],
                document_expired=ic["document_expired"],
                duplicate_individual_detected=ic["duplicate"], result=ic["result"])
        t = clk.tick(6)
        audit(case_id, "external_provider", spec["identity_checks"][0]["provider"],
              "identity_checks_completed",
              "%d individual check(s) returned: %s"
              % (len(spec["identity_checks"]),
                 ", ".join("%s %s=%s" % (i["_id"], i["individual_id"], i["result"])
                           for i in spec["identity_checks"])), t)

    if spec.get("screenings"):
        for sc in spec["screenings"]:
            cid = nid("SCR")
            sc["_id"] = cid
            subj_key = sc["individual_id"] if sc["subject_type"] == "individual" else "applicant"
            refmap["SCR:" + subj_key] = cid
            add("screening_check", check_id=cid, case_id=case_id, subject_type=sc["subject_type"],
                applicant_id=(app_id if sc["subject_type"] == "applicant" else ""),
                individual_id=(sc["individual_id"] if sc["subject_type"] == "individual" else ""),
                sanctions_result=sc["sanctions"], pep_result=sc["pep"],
                adverse_media_result=sc["adverse_media"], severity=sc["severity"],
                evidence_refs=sc.get("evidence_refs", ""))
        t = clk.tick(7)
        audit(case_id, "external_provider", "ScreenMock Global", "screening_checks_completed",
              "%d subject(s) screened: %s"
              % (len(spec["screenings"]),
                 ", ".join("%s sanctions=%s pep=%s adverse_media=%s severity=%s"
                           % (s["_id"], s["sanctions"], s["pep"], s["adverse_media"], s["severity"])
                           for s in spec["screenings"])), t)

    # ---- risk assessment ---------------------------------------------------
    assessment_id = ""
    risk = spec.get("risk")
    if risk:
        assessment_id = nid("RSK")
        refmap["RSK"] = assessment_id
        t = clk.tick(4)

        # ---- facts, read off this case ------------------------------------
        screens = spec.get("screenings", [])
        idchecks = spec.get("identity_checks", [])
        reg = spec.get("registry") or {}
        ubo_specs = spec.get("ubos", [])
        ind_by_id = dict((i["id"], i) for i in inds)
        spend = [int(m) for m in re.findall(r"(\d{4,})\s*(?:EUR|GBP)",
                                            a.get("expected_usage", ""))]
        all_fields = [f for d in docs for f in d.get("fields", [])]

        facts = {
          "ubo_resident_outside_applicant_country": any(
              ind_by_id.get(u["individual_id"], {}).get("residence") != a["country"]
              for u in ubo_specs),
          "high_risk_industry": bool(reg.get("high_risk")),
          "ownership_layers_gt_1": any(len(u.get("chain", [u["pct"]])) > 1 for u in ubo_specs),
          "ubo_not_supported_by_registry": reg.get("ubo_supported") is False,
          "registry_address_mismatch": reg.get("address_match") == "mismatch",
          "registry_director_mismatch": reg.get("director_match") == "mismatch",
          "entity_not_active": bool(reg) and reg.get("company_status") != "active",
          "identity_check_failed": any(c["result"] in ("fail", "review") for c in idchecks),
          "identity_duplicate": any(c["duplicate"] for c in idchecks),
          "low_confidence_accepted_as_read": any(
              float(f[2]) < 0.70 and not f[4] for f in all_fields),
          "field_corrected_by_analyst": any(f[4] for f in all_fields),
          "expected_monthly_spend_above_50k": bool(spend) and max(spend) > 50000,
          "pep_match": any(s["pep"] in ("pep_match", "close_associate_family") for s in screens),
          "adverse_media_moderate": any(s["adverse_media"] == "moderate" for s in screens),
          "adverse_media_serious": any(s["adverse_media"] == "serious" for s in screens),
          "sanctions_possible_match": any(s["sanctions"] == "possible_match" for s in screens),
          "sanctions_clear_match": any(s["sanctions"] == "clear_match" for s in screens),
          "document_resubmission_required": any(
              d["quality"] == "resubmission_required" for d in docs),
          "document_manual_review": any(d["quality"] == "manual_review_required" for d in docs),
        }
        # Not enough to go on: a document never replaced, a provider that did not
        # answer, or an identity check that failed. The case is not scored.
        facts["insufficient_evidence_hold"] = (
            facts["document_resubmission_required"]
            or facts["identity_check_failed"]
            or any(s["adverse_media"] == "unavailable" or s["sanctions"] == "unavailable"
                   for s in screens))

        # ---- score, then floors -------------------------------------------
        fired = [FACTOR_BY_CONDITION[c] for c in
                 [f[3] for f in RISK_FACTORS] if facts.get(c)]
        score = sum(f[4] for f in fired)
        band = apply_floors(band_for_score(score), facts)
        insufficient = band == "insufficient_evidence"
        action = BAND_ACTION[band]
        human = band in ("medium", "high", "critical") or facts["pep_match"] \
            or facts["adverse_media_moderate"] or facts["adverse_media_serious"] \
            or facts["sanctions_possible_match"] or facts["sanctions_clear_match"] \
            or insufficient

        # Every factor points at rows that actually exist for this case.
        evidence_for = {
          "registry_check": "{REG}", "finding": "{REG}",
          "screening_check": "{SCR:applicant}",
          "identity_check": "{PACK}", "document": "{PACK}", "extracted_field": "{PACK}",
          "applicant": "{PACK}", "ubo": "{DOC:ubo_declaration}",
          "individual": "{DOC:ubo_declaration}",
        }

        add("risk_assessment", assessment_id=assessment_id, case_id=case_id,
            risk_score="" if insufficient else score, risk_band=band,
            recommended_action=action, confidence=risk.get("confidence", 0.9),
            insufficient_evidence_flag=insufficient,
            requires_human_signoff=human, risk_matrix_version=RISK_MATRIX)
        for f in fired:
            add("risk_factor", factor_id=nid("RF"), assessment_id=assessment_id, factor=f[1],
                weight=f[4], explanation=f[5], evidence_refs=evidence_for.get(f[2], "{PACK}"))
        audit(case_id, "ai_agent", "risk-classification-agent", "risk_assessment_completed",
              "%s -> band %s, score %s, recommended action %s, requires_human_signoff %s"
              % (assessment_id, band, "" if insufficient else score, action, fmt(human)),
              t, V_RISK)

    # ---- evidence pack -----------------------------------------------------
    ev = spec.get("evidence")
    ev_generated = None
    if ev:
        ev_generated = clk.tick(3)
        audit(case_id, "ai_agent", "evidence-pack-agent", "evidence_pack_generated",
              "Evidence pack prepared for human review; recommended next action %s"
              % ev["recommended_next_action"], ev_generated, V_PACK)

    # ---- human decision and customer communication -------------------------
    if spec.get("decisions_before_comms"):
        decision_id = emit_decisions(spec, case_id, clk)
        emit_communications(spec, case_id, clk)
    else:
        emit_communications(spec, case_id, clk)
        decision_id = emit_decisions(spec, case_id, clk)

    if ev:
        add("evidence_pack", evidence_pack_id=nid("EVP"), case_id=case_id,
            assessment_id=assessment_id, decision_id=decision_id, generated_at=ev_generated,
            applicant_summary=ev["applicant_summary"],
            missing_or_conflicting_evidence=ev["missing_or_conflicting_evidence"],
            recommended_next_action=ev["recommended_next_action"],
            draft_compliance_narrative=ev["draft_compliance_narrative"])

    emit_extra_audit(spec, case_id, clk, "extra_audit")

    t = clk.tick(1)
    audit(case_id, "system", "case-state-machine", "status_changed",
          "Case status set to %s; next action owner %s"
          % (spec["status"], spec["next_action_owner"]), t)

    add("onboarding_case", case_id=case_id, applicant_id=app_id,
        applicant_type=spec["applicant_type"], jurisdiction_path=spec["jurisdiction"],
        entity_scope=spec["entity_scope"], source_channel=spec["source_channel"],
        status=spec["status"], assigned_owner=spec["assigned_owner"],
        next_action_owner=spec["next_action_owner"],
        white_label_branch_flag=spec.get("white_label", False), created_at=created_at, updated_at=t)

    resolve_tokens(start_idx, refmap)


# ---------------------------------------------------------------------------
# Scripted cases - 8 demo cases from section 8.1 plus 2 controls
# ---------------------------------------------------------------------------
# The document.quality_flags vocabulary. wrong_document_type and document_too_old
# were split out of unsupported_file_type and expired: a legible file that is the
# wrong document is not an unreadable format, and a document that is merely older
# than the rule's max_age_days has not expired.
QUALITY_FLAGS = ("blurred_unreadable", "cut_off_pages", "expired", "missing_pages",
                 "screenshot_not_original", "name_mismatch", "tampering_indicator",
                 "unsupported_file_type", "wrong_document_type", "document_too_old")



# ---------------------------------------------------------------------------
# Risk scoring matrix (brief Section 5.8)
#
# The brief does not state weights, so every one of these is a placeholder for
# Wallester to confirm. They are recorded as data rather than buried in code so
# that changing a weight is a KB edit and the dataset and the orchestrator move
# together - the same arrangement as the requirement matrix.
#
# (factor_id, factor, source, condition, points, description)
RISK_FACTORS = [
 ("RS-01", "jurisdiction_risk", "individual", "ubo_resident_outside_applicant_country", 8,
  "A beneficial owner lives outside the country the applicant is registered in"),
 ("RS-02", "industry_risk", "registry_check", "high_risk_industry", 12,
  "The register or the declared activity marks this as a higher-risk industry"),
 ("RS-03", "ownership_complexity", "ubo", "ownership_layers_gt_1", 15,
  "Ownership runs through at least one intermediate company"),
 ("RS-04", "ownership_opacity", "finding", "ubo_not_supported_by_registry", 25,
  "The register does not corroborate the declared beneficial ownership"),
 ("RS-05", "registry_address_mismatch", "finding", "registry_address_mismatch", 30,
  "The registered address on file does not match the register"),
 ("RS-06", "registry_director_mismatch", "finding", "registry_director_mismatch", 12,
  "The declared directors do not match the register"),
 ("RS-07", "entity_status", "finding", "entity_not_active", 60,
  "The register does not show the entity as active"),
 ("RS-08", "identity_verification", "identity_check", "identity_check_failed", 25,
  "An identity check returned fail or review for a director, owner or signatory"),
 ("RS-09", "identity_verification", "identity_check", "identity_duplicate", 25,
  "An individual appears to duplicate someone already known to us"),
 ("RS-10", "extraction_confidence", "extracted_field", "low_confidence_accepted_as_read", 6,
  "A value was read below the confidence floor and accepted as read by an analyst"),
 ("RS-11", "extraction_confidence", "extracted_field", "field_corrected_by_analyst", 5,
  "An analyst had to correct a value the system could not read reliably"),
 ("RS-12", "usage_volume", "applicant", "expected_monthly_spend_above_50k", 8,
  "Expected monthly card spend is above the 50000 threshold"),
 ("RS-13", "pep_exposure", "screening_check", "pep_match", 30,
  "A subject is a politically exposed person or a close associate"),
 ("RS-14", "adverse_media_severity", "screening_check", "adverse_media_moderate", 20,
  "Moderate adverse media of relevance to a subject; allegations, not established fact"),
 ("RS-15", "adverse_media_severity", "screening_check", "adverse_media_serious", 40,
  "Serious adverse media of relevance to a subject; allegations, not established fact"),
 ("RS-16", "sanctions_exposure", "screening_check", "sanctions_possible_match", 60,
  "A possible sanctions match that only a human can resolve"),
 ("RS-17", "sanctions_exposure", "screening_check", "sanctions_clear_match", 70,
  "A confirmed sanctions match"),
 ("RS-18", "document_quality", "document", "document_resubmission_required", 10,
  "A document failed the quality screen and had to be sent back"),
 ("RS-19", "document_quality", "document", "document_manual_review", 15,
  "A document was held for an analyst at the quality screen"),
]
WEIGHT_STATUS = "poc_placeholder - Wallester to confirm"
for _f in RISK_FACTORS:
    add("risk_scoring_matrix", factor_id=_f[0], factor=_f[1], source=_f[2], condition=_f[3],
        points=_f[4], description=_f[5], weight_status=WEIGHT_STATUS)

# Bands, plus the floors that override a score outright. A floor exists because
# some findings are not a matter of degree: a confirmed sanctions match is
# critical whatever else the file looks like.
RISK_BANDS = [
 ("RB-01", "low", 0, 24, "", "approve",
  "Nothing outstanding. Still requires a human decision; there is no automatic approval"),
 ("RB-02", "medium", 25, 49, "", "conditional_approve",
  "Something to resolve, but nothing that stops the application"),
 ("RB-03", "high", 50, 79, "", "enhanced_due_diligence",
  "Enhanced due diligence before any decision"),
 ("RB-04", "critical", 80, 100, "", "escalate",
  "Compliance decision required"),
 ("RB-05", "critical", "", "", "sanctions_clear_match", "escalate",
  "A confirmed sanctions match is critical whatever the score"),
 ("RB-06", "critical", "", "", "sanctions_possible_match", "escalate",
  "An unresolved possible sanctions match is critical whatever the score"),
 ("RB-07", "high", "", "", "pep_match", "enhanced_due_diligence",
  "A PEP match is at least high whatever the score"),
 ("RB-08", "high", "", "", "adverse_media_serious", "enhanced_due_diligence",
  "Serious adverse media is at least high whatever the score"),
 ("RB-09", "insufficient_evidence", "", "", "insufficient_evidence_hold",
  "insufficient_evidence",
  "An unresolved gap in the evidence. The case is not scored: there is not enough to score"),
]
for _b in RISK_BANDS:
    add("risk_bands", band_id=_b[0], band=_b[1], min_score=_b[2], max_score=_b[3],
        hard_floor_condition=_b[4], recommended_action=_b[5], description=_b[6])

POINTS = dict((f[3], f[4]) for f in RISK_FACTORS)
FACTOR_BY_CONDITION = dict((f[3], f) for f in RISK_FACTORS)
# Floors in the order they are applied: the first one whose condition holds wins.
FLOORS = [(b[4], b[1]) for b in RISK_BANDS if b[4]]
BAND_ACTION = dict((b[1], b[5]) for b in RISK_BANDS)
SCORE_BANDS = [(b[1], b[2], b[3]) for b in RISK_BANDS if b[2] != ""]


def band_for_score(score):
    for band, lo, hi in SCORE_BANDS:
        if lo <= score <= hi:
            return band
    return "critical"


def apply_floors(band, facts):
    """A floor raises the band; it never lowers it."""
    order = ["low", "medium", "high", "critical"]
    for condition, floor_band in FLOORS:
        if not facts.get(condition):
            continue
        if floor_band == "insufficient_evidence":
            return "insufficient_evidence"
        if band == "insufficient_evidence":
            continue
        if order.index(floor_band) > order.index(band):
            band = floor_band
    return band


def doc(dtype, file_name, quality="accepted_for_checks", subject="", fields=None, flags="",
        reasons="", expiry="", issue_country=""):
    for f in flags.split("|") if flags else []:
        if f not in QUALITY_FLAGS:
            raise ValueError("unknown quality flag %r on %s" % (f, file_name))
    return {"type": dtype, "file_name": file_name, "quality": quality, "subject": subject,
            "fields": fields or [], "flags": flags, "reasons": reasons, "expiry": expiry,
            "issue_country": issue_country}


def idcheck(individual_id, provider="VerifyMock ID", document_result="pass", liveness="pass",
            biometric="pass", address="pass", name_dob="match", expired=False, duplicate=False,
            result="pass"):
    return {"individual_id": individual_id, "provider": provider, "document_result": document_result,
            "liveness_result": liveness, "biometric_result": biometric, "address_result": address,
            "name_dob_match": name_dob, "document_expired": expired, "duplicate": duplicate,
            "result": result}


def screen(subject_type, individual_id="", sanctions="no_match", pep="no_match",
           adverse_media="none", severity="none", evidence_refs=""):
    return {"subject_type": subject_type, "individual_id": individual_id, "sanctions": sanctions,
            "pep": pep, "adverse_media": adverse_media, "severity": severity,
            "evidence_refs": evidence_refs}


CASES = []

# ---------------------------------------------------------------- Case 1 ----
CASES.append({
 "case_id": "WAL-ONB-0001", "applicant_id": "APP-0001",
 "start": datetime(2026, 9, 1, 8, 15, 0),
 "applicant_type": "freelancer_sole_trader", "jurisdiction": "EE", "entity_scope": "wallester_as",
 "source_channel": "portal", "status": "ready_for_decision", "next_action_owner": "analyst",
 "assigned_owner": "ops.tiina.kask",
 "applicant": {"legal_name": "Kaari Mets", "trading_name": "Mets Design Studio",
   "registration_number": "EE-FIE-4410932", "entity_type": "sole_trader", "country": "EE",
   "business_activity": "Freelance graphic design and brand identity services",
   "expected_usage": "One card; up to 4000 EUR per month on software and travel",
   "vat_registered": False,
   "risk_segment": "low"},
 "individuals": [
   {"id": "IND-0001", "role": "sole_trader", "full_name": "Kaari Mets", "dob": "1991-03-14",
    "nationality": "EE", "residence": "EE",
    "relationship": "Registered sole trader and sole beneficial owner"}],
 "documents": [
   doc("registry_extract", "ee_fie_registry_extract_mets.pdf", issue_country="EE", fields=[
     ("company_name", "Kaari Mets", 0.97, 1, False),
     ("registration_number", "EE-FIE-4410932", 0.96, 1, False),
     ("registered_address", "Kastani 12-4, 51006 Tartu, Estonia", 0.93, 1, False),
     ("entity_status", "active", 0.95, 1, False)]),
   doc("id_document", "passport_mets_k.jpg", subject="IND-0001", expiry="2031-04-18",
       issue_country="EE", fields=[
     ("full_name", "Kaari Mets", 0.98, 1, False),
     ("date_of_birth", "1991-03-14", 0.97, 1, False),
     ("document_number", "EE-P-3391842", 0.94, 1, False),
     ("expiry_date", "2031-04-18", 0.96, 1, False)]),
   doc("proof_of_address", "utility_bill_aug2026_mets.pdf", subject="IND-0001", issue_country="EE",
       fields=[
     ("full_name", "Kaari Mets", 0.95, 1, False),
     ("address", "Kastani 12-4, 51006 Tartu, Estonia", 0.92, 1, False),
     ("document_date", "2026-08-12", 0.94, 1, False),
     ("issuer", "Tartu Energia AS", 0.91, 1, False)]),
   doc("liveness_selfie", "liveness_capture_20260901_mets.jpg", subject="IND-0001"),
   doc("source_of_funds_declaration", "source_of_funds_mets.pdf", fields=[
     ("declared_source", "Client invoices from retained design work", 0.89, 1, False),
     ("expected_monthly_volume", "4000 EUR", 0.88, 1, False)]),
   doc("business_activity_description", "business_activity_mets.pdf", fields=[
     ("declared_industry", "Graphic design services", 0.90, 1, False),
     ("expected_card_usage", "Software subscriptions, print suppliers, travel", 0.87, 1, False)]),
 ],
 "triggered_conditionals": [find_rule("freelancer_sole_trader", "EE", "liveness_selfie")],
 "registry": {"provider": "MockRegistryHub EE", "company_status": "active", "name_match": "match",
   "number_match": "match", "address_match": "match", "director_match": "match",
   "ubo_supported": True, "high_risk": False, "confidence": 0.96, "result": "pass"},
 "identity_checks": [idcheck("IND-0001")],
 "screenings": [screen("applicant"), screen("individual", "IND-0001")],
 "risk": {"score": 12, "band": "low", "recommended_action": "approve", "confidence": 0.91,
   "insufficient_evidence_flag": False, "requires_human_signoff": False, "factors": [
     ("jurisdiction_risk", 0.05,
      "Estonia is a home-market jurisdiction under Wallester AS with no elevated country weighting "
      "in the configured risk matrix.", "{REG}"),
     ("industry_risk", 0.10,
      "Freelance graphic design is a low-risk service activity with no restricted merchant category.",
      "{DOC:business_activity_description}"),
     ("document_quality", 0.00,
      "All six submitted documents passed the pre-screen at the first attempt.", "{PACK}"),
     ("screening_outcome", 0.00,
      "No sanctions, PEP or adverse-media findings for the entity or the sole trader.",
      "{SCR:applicant}|{SCR:IND-0001}"),
     ("source_of_funds_clarity", 0.10,
      "Declared source of funds is client invoicing, consistent with the declared activity and the "
      "4000 EUR expected monthly spend.", "{DOC:source_of_funds_declaration}")]},
 "evidence": {
   "applicant_summary": "Kaari Mets trading as Mets Design Studio, Estonian sole trader "
     "EE-FIE-4410932, freelance graphic design, one card requested, expected spend 4000 EUR per "
     "month. Sole trader is the only individual associated with the application.",
   "missing_or_conflicting_evidence": "",
   "recommended_next_action": "approve",
   "draft_compliance_narrative": "All mandatory requirement-pack items are satisfied at first "
     "submission. Registry confirms an active sole-trader registration with matching name, number "
     "and address. Identity verification returned pass on document, liveness, biometric and address "
     "components with a name and date-of-birth match. Screening returned no sanctions, PEP or "
     "adverse-media findings for the entity or the individual. Risk band low at score 12. No factor "
     "in the assessment requires enhanced due diligence. Recommended for approval under the "
     "low-risk path, subject to the reviewer confirming Wallester policy on sole-trader approval "
     "authority."},
 "communications": [
   {"template_id": "TPL-0005", "audience": "applicant", "message_type": "status_update",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "analyst",
    "rendered_text": "Hello Kaari Mets, we have received your application for Mets Design Studio "
      "and all of the documents we asked for. Your case reference is WAL-ONB-0001. Your "
      "application is now with our onboarding team and we will let you know as soon as the review "
      "is complete."}],
})

# ---------------------------------------------------------------- Case 2 ----
CASES.append({
 "case_id": "WAL-ONB-0002", "applicant_id": "APP-0002",
 "start": datetime(2026, 9, 2, 9, 40, 0),
 "applicant_type": "sme_corporate", "jurisdiction": "UK", "entity_scope": "wallester_uk_ltd",
 "source_channel": "portal", "status": "resubmission_required", "next_action_owner": "customer",
 "assigned_owner": "ops.james.corrin",
 "applicant": {"legal_name": "Northbridge Craft Supplies Ltd", "trading_name": "Northbridge Crafts",
   "registration_number": "UK-99010288", "entity_type": "private_limited_company", "country": "GB",
   "business_activity": "Wholesale of craft and hobby supplies to independent retailers",
   "expected_usage": "Up to 12 cards; around 25000 GBP per month on stock and logistics",
   "vat_registered": False,
   "risk_segment": "unassessed"},
 "individuals": [
   {"id": "IND-0002", "role": "director", "full_name": "Denton Halliwell", "dob": "1978-06-02",
    "nationality": "GB", "residence": "GB",
    "relationship": "Registered director and primary application contact"},
   {"id": "IND-0003", "role": "director", "full_name": "Priya Nankivell", "dob": "1985-11-19",
    "nationality": "GB", "residence": "GB",
    "relationship": "Registered director and authorised signatory"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_northbridge.pdf", issue_country="GB",
       fields=[
     ("company_name", "Northbridge Craft Supplies Ltd", 0.96, 1, False),
     ("registration_number", "UK-99010288", 0.95, 1, False),
     ("incorporation_date", "2019-04-11", 0.93, 1, False)]),
   doc("registry_extract", "registry_extract_northbridge.pdf", issue_country="GB", fields=[
     ("company_name", "Northbridge Craft Supplies Ltd", 0.95, 1, False),
     ("registered_address", "Suite 3, 88 Fettlers Row, Sheffield S3 8PQ, United Kingdom", 0.91, 1,
      False),
     ("entity_status", "active", 0.94, 1, False),
     ("registration_number", "UK-99010288", 0.95, 1, False)]),
   doc("director_register", "director_register_northbridge.pdf", fields=[
     ("director_name", "Denton Halliwell", 0.92, 1, False),
     ("director_name_2", "Priya Nankivell", 0.90, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_northbridge.pdf", fields=[
     ("signatory_name", "Priya Nankivell", 0.89, 1, False)]),
   doc("id_document", "director_id_halliwell_scan.jpg", quality="resubmission_required",
       subject="IND-0002", flags="blurred_unreadable", reasons="document_unreadable"),
   doc("id_document", "passport_nankivell_p.jpg", subject="IND-0003", expiry="2029-09-30",
       issue_country="GB", fields=[
     ("full_name", "Priya Nankivell", 0.96, 1, False),
     ("date_of_birth", "1985-11-19", 0.95, 1, False),
     ("expiry_date", "2029-09-30", 0.94, 1, False)]),
   doc("proof_of_address", "poa_halliwell_jul2026.pdf", subject="IND-0002", issue_country="GB",
       fields=[("address", "12 Sowerby Lane, Sheffield S7 2LT, United Kingdom", 0.90, 1, False),
               ("document_date", "2026-07-28", 0.92, 1, False)]),
   doc("proof_of_address", "poa_nankivell_aug2026.pdf", subject="IND-0003", issue_country="GB",
       fields=[("address", "4 Carrowbrook Court, Sheffield S11 9DD, United Kingdom", 0.91, 1, False),
               ("document_date", "2026-08-05", 0.93, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_northbridge.pdf", fields=[
     ("declared_source", "Trading revenue from wholesale supply contracts", 0.88, 1, False)]),
   doc("business_activity_description", "business_activity_northbridge.pdf", fields=[
     ("declared_industry", "Wholesale of craft and hobby goods", 0.90, 1, False)]),
 ],
 "triggered_conditionals": [],
 "audit_after_quality": [
   {"actor_type": "ai_agent", "actor_id": "missing-item-agent", "action": "resubmission_reasons_determined",
    "summary": "Outstanding items for WAL-ONB-0002 mapped to approved reason codes: "
      "document_unreadable (director identity document for IND-0002) and ubo_declaration_missing "
      "(no beneficial ownership declaration received for the company). Reason codes drive the "
      "customer-facing template; no other reason is disclosed.",
    "version": V_QUALITY, "after": 2},
   {"actor_type": "ai_agent", "actor_id": "orchestration-controller", "action": "paid_checks_suppressed",
    "summary": "Registry, identity and screening provider calls were not commissioned for "
      "WAL-ONB-0002. Mandatory evidence is incomplete: the director identity document for IND-0002 "
      "failed the quality pre-screen and no UBO declaration has been received. Checks will be "
      "commissioned once the outstanding items are accepted.",
    "version": V_INTAKE, "after": 3}],
 "risk": {"band": "insufficient_evidence", "recommended_action": "request_more_information",
   "confidence": 0.40, "insufficient_evidence_flag": True, "requires_human_signoff": False,
   "factors": [
     ("document_quality", 0.40,
      "The director identity document was rejected at pre-screen as unreadable, so no usable "
      "identity evidence exists for IND-0002.", "{DOC:id_document@IND-0002}"),
     ("ownership_transparency", 0.35,
      "No UBO declaration has been submitted, so beneficial ownership of the applicant is "
      "undetermined.", "{PACK}"),
     ("evidence_completeness", 0.25,
      "No registry, identity or screening evidence exists because provider calls were suppressed "
      "until the mandatory document set is complete.", "{PACK}")]},
 "evidence": {
   "applicant_summary": "Northbridge Craft Supplies Ltd, UK private limited company UK-99010288, "
     "wholesale craft supplies, two registered directors, up to 12 cards requested.",
   "missing_or_conflicting_evidence": "Director identity document for IND-0002 ({DOC:id_document@IND-0002}) "
     "rejected at pre-screen as unreadable; UBO declaration not submitted; no registry, identity or "
     "screening evidence available because provider checks were suppressed.",
   "recommended_next_action": "request_more_information",
   "draft_compliance_narrative": "The case cannot be assessed. Two mandatory requirement-pack items "
     "are outstanding: a readable identity document for director IND-0002 and the beneficial "
     "ownership declaration for the company. In line with the configured policy, no paid registry, "
     "identity or screening calls were commissioned while mandatory evidence is incomplete. The "
     "risk band is recorded as insufficient_evidence rather than low; the absence of adverse "
     "findings here reflects the absence of checks, not a clean result. A resubmission request has "
     "been issued to the applicant and the case is held with the customer."},
 "communications": [
   {"template_id": "TPL-0001", "audience": "applicant", "message_type": "resubmission_request",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "analyst",
    "rendered_text": "Hello Denton Halliwell, thank you for your application for Northbridge Craft "
      "Supplies Ltd (reference WAL-ONB-0002). To continue we need the following items: a clear "
      "colour scan or photograph of the passport or national identity card for Denton Halliwell, "
      "because the copy we received could not be read; and the completed beneficial ownership "
      "declaration for the company. Please upload them in the onboarding portal. If anything is "
      "unclear, reply to this message and our onboarding team will help."}],
})

# ---------------------------------------------------------------- Case 3 ----
CASES.append({
 "case_id": "WAL-ONB-0003", "applicant_id": "APP-0003",
 "start": datetime(2026, 9, 3, 11, 5, 0),
 "applicant_type": "sme_corporate", "jurisdiction": "UK", "entity_scope": "wallester_uk_ltd",
 "source_channel": "email", "status": "analyst_review_required", "next_action_owner": "customer",
 "assigned_owner": "analyst.r.toome", "decisions_before_comms": True,
 "applicant": {"legal_name": "Calderwick Logistics Ltd", "trading_name": "Calderwick Freight",
   "registration_number": "UK-99014477", "entity_type": "private_limited_company", "country": "GB",
   "business_activity": "Regional road freight and contract haulage",
   "expected_usage": "Up to 30 fuel cards; around 60000 GBP per month",
   "vat_registered": True,
   "risk_segment": "standard"},
 "individuals": [
   {"id": "IND-0004", "role": "director", "full_name": "Marcus Ellersby", "dob": "1974-01-27",
    "nationality": "GB", "residence": "GB", "relationship": "Managing director"},
   {"id": "IND-0005", "role": "director", "full_name": "Roisin Delamere", "dob": "1982-05-09",
    "nationality": "IE", "residence": "GB", "relationship": "Operations director"},
   {"id": "IND-0006", "role": "ubo", "full_name": "Torvald Kellingray", "dob": "1969-08-22",
    "nationality": "GB", "residence": "GB",
    "relationship": "Holds 60 percent of the issued share capital directly"}],
 "ubos": [{"id": "UBO-0001", "individual_id": "IND-0006", "pct": 60.0,
   "control_type": "direct_shareholding", "ownership_path": "Calderwick Logistics Ltd",
   "verification_status": "verified"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_calderwick.pdf", issue_country="GB",
       fields=[
     ("company_name", "Calderwick Logistics Ltd", 0.96, 1, False),
     ("registration_number", "UK-99014477", 0.95, 1, False),
     ("registered_address", "14 Marlow Court, Leeds LS11 9RB, United Kingdom", 0.91, 1, False)]),
   doc("registry_extract", "registry_extract_calderwick.pdf", issue_country="GB", fields=[
     ("company_name", "Calderwick Logistics Ltd", 0.94, 1, False),
     ("registration_number", "UK-99014477", 0.95, 1, False),
     ("registered_address", "Unit 7 Calderwick Way, Leeds LS12 4QT, United Kingdom", 0.58, 1, True),
     ("entity_status", "active", 0.93, 1, False)]),
   doc("tax_registration_certificate", "vat_certificate_calderwick.pdf", fields=[
     ("vat_number", "GB-VAT-771902845", 0.90, 1, False)]),
   doc("director_register", "director_register_calderwick.pdf", fields=[
     ("director_name", "Marcus Ellersby", 0.93, 1, False),
     ("director_name_2", "Roisin Delamere", 0.92, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_calderwick.pdf", fields=[
     ("signatory_name", "Marcus Ellersby", 0.91, 1, False)]),
   doc("ubo_declaration", "ubo_declaration_calderwick.pdf", fields=[
     ("ubo_name", "Torvald Kellingray", 0.93, 1, False),
     ("ownership_percentage", "60.0", 0.92, 1, False),
     ("control_basis", "direct shareholding", 0.89, 1, False)]),
   doc("id_document", "passport_ellersby_m.jpg", subject="IND-0004", expiry="2030-02-14",
       issue_country="GB", fields=[("full_name", "Marcus Ellersby", 0.96, 1, False),
                                   ("expiry_date", "2030-02-14", 0.94, 1, False)]),
   doc("id_document", "passport_delamere_r.jpg", subject="IND-0005", expiry="2032-07-06",
       issue_country="IE", fields=[("full_name", "Roisin Delamere", 0.95, 1, False),
                                   ("expiry_date", "2032-07-06", 0.93, 1, False)]),
   doc("id_document", "passport_kellingray_t.jpg", subject="IND-0006", expiry="2028-11-30",
       issue_country="GB", fields=[("full_name", "Torvald Kellingray", 0.94, 1, False),
                                   ("expiry_date", "2028-11-30", 0.92, 1, False)]),
   doc("proof_of_address", "poa_ellersby_aug2026.pdf", subject="IND-0004", issue_country="GB",
       fields=[("document_date", "2026-08-19", 0.92, 1, False)]),
   doc("proof_of_address", "poa_delamere_aug2026.pdf", subject="IND-0005", issue_country="GB",
       fields=[("document_date", "2026-08-22", 0.91, 1, False)]),
   doc("proof_of_address", "poa_kellingray_jul2026.pdf", subject="IND-0006", issue_country="GB",
       fields=[("document_date", "2026-07-30", 0.90, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_calderwick.pdf", fields=[
     ("declared_source", "Haulage contract revenue from three national retail clients", 0.88, 1,
      False)]),
   doc("business_activity_description", "business_activity_calderwick.pdf", fields=[
     ("declared_industry", "Road freight transport", 0.91, 1, False)]),
   doc("bank_statement", "bank_statement_calderwick_aug2026.pdf", fields=[
     ("account_holder", "Calderwick Logistics Ltd", 0.89, 1, False)]),
 ],
 "triggered_conditionals": [find_rule("sme_corporate", "UK", "tax_registration_certificate"),
                            find_rule("sme_corporate", "UK", "bank_statement")],
 "registry": {"provider": "MockRegistryHub UK", "company_status": "active", "name_match": "match",
   "address": "Enterprise House, 14 Bell Lane, Leeds LS11 9PT, United Kingdom",
   "number_match": "match", "address_match": "mismatch", "director_match": "match",
   "ubo_supported": True, "high_risk": False, "confidence": 0.88, "result": "review"},
 "identity_checks": [idcheck("IND-0004"), idcheck("IND-0005"), idcheck("IND-0006")],
 "screenings": [screen("applicant"), screen("individual", "IND-0004"),
                screen("individual", "IND-0005"), screen("individual", "IND-0006")],
 "risk": {"score": 38, "band": "medium", "recommended_action": "request_more_information",
   "confidence": 0.82, "insufficient_evidence_flag": False, "requires_human_signoff": True,
   "factors": [
     ("registry_address_mismatch", 0.60,
      "The registered address held on the company register differs from the address on the "
      "certificate of incorporation supplied by the applicant.",
      "{REG}|{DOC:registry_extract}|{DOC:certificate_of_incorporation}"),
     ("extraction_confidence", 0.15,
      "The registered-address field was extracted at 0.58 confidence and was corrected by an "
      "analyst before the comparison was relied on.", "{DOC:registry_extract}"),
     ("jurisdiction_risk", 0.05,
      "United Kingdom under Wallester UK Ltd; no elevated country weighting.", "{REG}"),
     ("industry_risk", 0.15,
      "Road freight is a standard-risk industry with routine cross-border fuel spend.",
      "{DOC:business_activity_description}"),
     ("screening_outcome", 0.00,
      "No sanctions, PEP or adverse-media findings for the entity, either director or the "
      "beneficial owner.",
      "{SCR:applicant}|{SCR:IND-0004}|{SCR:IND-0005}|{SCR:IND-0006}")]},
 "evidence": {
   "applicant_summary": "Calderwick Logistics Ltd, UK private limited company UK-99014477, road "
     "freight, two directors, one 60 percent direct beneficial owner, 30 fuel cards requested at "
     "around 60000 GBP per month.",
   "missing_or_conflicting_evidence": "Registered address on the company register "
     "(Unit 7 Calderwick Way, Leeds LS12 4QT) conflicts with the address on the certificate of "
     "incorporation supplied by the applicant (14 Marlow Court, Leeds LS11 9RB). Source field "
     "extracted at 0.58 confidence and corrected by an analyst.",
   "recommended_next_action": "request_more_information",
   "draft_compliance_narrative": "Entity existence, registration number, directors and beneficial "
     "ownership are all corroborated by the registry record. One conflict remains: the registered "
     "address on the register does not match the address on the applicant's certificate of "
     "incorporation. The underlying field was extracted at low confidence and was corrected by an "
     "analyst before comparison, so the mismatch is a genuine data conflict rather than an "
     "extraction artefact. Identity checks passed for all three individuals and screening returned "
     "no findings. Risk band medium at score 38, driven by the unresolved address conflict alone. "
     "Recommended action is to request written confirmation of the current registered address with "
     "supporting evidence before the case proceeds to decision."},
 "decisions": [
   {"reviewer": "R. Toome", "reviewer_role": "analyst", "decision": "request_more_information",
    "reason_code": "registered_address_mismatch",
    "rationale": "Registry and applicant documents disagree on the registered address. Everything "
      "else corroborates. Asking the applicant to confirm the current registered address and "
      "provide a document that shows it, rather than escalating, because no screening or identity "
      "issue is present.",
    "evidence_relied_on": "{REG}|{DOC:registry_extract}|{DOC:certificate_of_incorporation}|{RSK}",
    "override_flag": False, "customer_template_id": "TPL-0002"}],
 "communications": [
   {"template_id": "TPL-0002", "audience": "applicant", "message_type": "clarification_request",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "analyst",
    "rendered_text": "Hello Marcus Ellersby, we are completing the company checks for Calderwick "
      "Logistics Ltd (reference WAL-ONB-0003). The registered address held on the official company "
      "register does not appear to be the same as the address on the documents you submitted. "
      "Please confirm the current registered address and upload a document that shows it."},
   {"template_id": "TPL-0008", "audience": "analyst", "message_type": "internal_case_summary",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "analyst",
    "rendered_text": "Internal case summary for WAL-ONB-0003 (Calderwick Logistics Ltd). Registry "
      "returned result review: number and director match, address mismatch. Risk band medium, score "
      "38. Outstanding item: written confirmation of the registered address. Clarification request "
      "issued under TPL-0002. Not for disclosure to the applicant."}],
})

# ---------------------------------------------------------------- Case 4 ----
CASES.append({
 "case_id": "WAL-ONB-0004", "applicant_id": "APP-0004",
 "start": datetime(2026, 9, 7, 10, 0, 0),
 "applicant_type": "complex_corporate_ubo", "jurisdiction": "EE", "entity_scope": "wallester_as",
 "source_channel": "portal", "status": "enhanced_due_diligence", "next_action_owner": "compliance",
 "assigned_owner": "analyst.m.sild", "decisions_before_comms": True,
 "applicant": {"legal_name": "Vestmark Nordic OU", "trading_name": "Vestmark",
   "registration_number": "EE-90012345", "entity_type": "private_limited_company", "country": "EE",
   "business_activity": "Import and distribution of marine equipment",
   "expected_usage": "Up to 15 cards; around 35000 EUR per month",
   "vat_registered": True,
   "risk_segment": "elevated"},
 "individuals": [
   {"id": "IND-0007", "role": "director", "full_name": "Lembit Vaher", "dob": "1971-02-11",
    "nationality": "EE", "residence": "EE", "relationship": "Managing director"},
   {"id": "IND-0008", "role": "director", "full_name": "Anneli Sormus", "dob": "1980-07-30",
    "nationality": "EE", "residence": "EE",
    "relationship": "Director and holder of 24.5 percent of shares directly"},
   {"id": "IND-0009", "role": "ubo", "full_name": "Ruben Halvorsen", "dob": "1966-12-05",
    "nationality": "NO", "residence": "NO",
    "relationship": "Declared indirect beneficial owner through two intermediate companies"}],
 "ubos": [
   {"id": "UBO-0002", "individual_id": "IND-0008", "pct": 24.5,
    "control_type": "direct_shareholding", "ownership_path": "Vestmark Nordic OU",
    "verification_status": "verified"},
   {"id": "UBO-0003", "individual_id": "IND-0009", "pct": 31.5, "chain": [70, 45],
    "control_type": "indirect_shareholding",
    "ownership_path": "Harboe Holdings OU > Lindval Mid Holdings SA > Vestmark Nordic OU",
    "verification_status": "unverified"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_vestmark.pdf", issue_country="EE",
       fields=[("company_name", "Vestmark Nordic OU", 0.95, 1, False),
               ("registration_number", "EE-90012345", 0.94, 1, False)]),
   doc("registry_extract", "registry_extract_vestmark.pdf", issue_country="EE", fields=[
     ("company_name", "Vestmark Nordic OU", 0.95, 1, False),
     ("registration_number", "EE-90012345", 0.94, 1, False),
     ("registered_address", "Sadama 14, 10111 Tallinn, Estonia", 0.92, 1, False),
     ("entity_status", "active", 0.94, 1, False),
     ("registered_shareholder", "Lindval Mid Holdings SA", 0.86, 2, False)]),
   doc("tax_registration_certificate", "tax_registration_vestmark.pdf", fields=[
     ("tax_number", "EE-TAX-90012345", 0.91, 1, False)]),
   doc("director_register", "director_register_vestmark.pdf", fields=[
     ("director_name", "Lembit Vaher", 0.93, 1, False),
     ("director_name_2", "Anneli Sormus", 0.91, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_vestmark.pdf", fields=[
     ("signatory_name", "Lembit Vaher", 0.90, 1, False)]),
   doc("ubo_declaration", "ubo_declaration_vestmark.pdf", fields=[
     ("ubo_name", "Ruben Halvorsen", 0.88, 1, False),
     ("ownership_percentage", "31.5", 0.74, 1, False),
     ("indirect_ownership_path",
      "Harboe Holdings OU > Lindval Mid Holdings SA > Vestmark Nordic OU", 0.41, 2, False),
     ("ubo_name_2", "Anneli Sormus", 0.87, 1, False)]),
   doc("ownership_chart", "ownership_chart_vestmark.pdf", quality="manual_review_required",
       flags="missing_pages", reasons="ownership_structure_unclear", fields=[
     ("intermediate_entity", "Lindval Mid Holdings SA", 0.55, 1, False)]),
   doc("shareholder_register", "shareholder_register_vestmark.pdf", fields=[
     ("shareholder_name", "Lindval Mid Holdings SA", 0.87, 1, False),
     ("shareholder_name_2", "Anneli Sormus", 0.89, 1, False)]),
   doc("id_document", "id_card_vaher_l.jpg", subject="IND-0007", expiry="2029-05-22",
       issue_country="EE", fields=[("full_name", "Lembit Vaher", 0.95, 1, False)]),
   doc("id_document", "id_card_sormus_a.jpg", subject="IND-0008", expiry="2031-01-09",
       issue_country="EE", fields=[("full_name", "Anneli Sormus", 0.94, 1, False)]),
   doc("id_document", "passport_halvorsen_r.jpg", subject="IND-0009", expiry="2030-08-17",
       issue_country="NO", fields=[("full_name", "Ruben Halvorsen", 0.93, 1, False)]),
   doc("proof_of_address", "poa_vaher_aug2026.pdf", subject="IND-0007", issue_country="EE",
       fields=[("document_date", "2026-08-25", 0.92, 1, False)]),
   doc("proof_of_address", "poa_sormus_aug2026.pdf", subject="IND-0008", issue_country="EE",
       fields=[("document_date", "2026-08-27", 0.91, 1, False)]),
   doc("proof_of_address", "poa_halvorsen_jul2026.pdf", subject="IND-0009", issue_country="NO",
       fields=[("document_date", "2026-07-15", 0.88, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_vestmark.pdf", fields=[
     ("declared_source", "Distribution margin on marine equipment imports", 0.86, 1, False)]),
   doc("business_activity_description", "business_activity_vestmark.pdf", fields=[
     ("declared_industry", "Wholesale of marine equipment", 0.90, 1, False)]),
 ],
 "triggered_conditionals": [find_rule("complex_corporate_ubo", "EE", "source_of_wealth_statement")],
 # The ownership chart failed the quality screen and went to an analyst. The case
 # only reaches the paid provider checks because the analyst released it - without
 # this event the jump from document_flagged to registry_check_completed is
 # unexplained, and nothing downstream could show who authorised it.
 "audit_after_quality": [
   {"actor_type": "analyst", "actor_id": "analyst.m.sild", "action": "document_released_after_review",
    "summary": "DOC-0038 (ownership_chart) released by analyst.m.sild; decision accept; reason: "
               "the missing annex lists dormant subsidiaries only and does not affect the "
               "beneficial ownership chain, which is legible on the pages supplied"},
   # Two values came off the page too faintly to act on unread. The analyst read
   # the originals, found them correct as extracted and accepted them rather than
   # retyping them - recorded so the low confidence is not silently ignored.
   {"actor_type": "analyst", "actor_id": "analyst.m.sild",
    "action": "extracted_field_accepted_as_read",
    "summary": "indirect_ownership_path on ubo_declaration_vestmark.pdf accepted as read by "
               "analyst.m.sild; reason: checked against the original declaration, the chain is "
               "transcribed correctly despite the low OCR confidence"},
   {"actor_type": "analyst", "actor_id": "analyst.m.sild",
    "action": "extracted_field_accepted_as_read",
    "summary": "intermediate_entity on ownership_chart_vestmark.pdf accepted as read by "
               "analyst.m.sild; reason: the entity name matches the shareholder register and the "
               "UBO declaration, so the faint scan is corroborated"}],
 "registry": {"provider": "MockRegistryHub EE", "company_status": "active", "name_match": "match",
   "number_match": "match", "address_match": "match", "director_match": "match",
   "ubo_supported": False, "high_risk": False, "confidence": 0.52, "result": "review"},
 "identity_checks": [idcheck("IND-0007"), idcheck("IND-0008"), idcheck("IND-0009")],
 "screenings": [screen("applicant"), screen("individual", "IND-0007"),
                screen("individual", "IND-0008"), screen("individual", "IND-0009")],
 "risk": {"score": 66, "band": "high", "recommended_action": "enhanced_due_diligence",
   "confidence": 0.69, "insufficient_evidence_flag": False, "requires_human_signoff": True,
   "factors": [
     ("ownership_complexity", 0.55,
      "Beneficial ownership reaches the applicant through a two-layer chain: Harboe Holdings OU > "
      "Lindval Mid Holdings SA > Vestmark Nordic OU.",
      "{DOC:ubo_declaration}|{DOC:ownership_chart}"),
     ("ubo_opacity", 0.50,
      "The declared 31.5 percent indirect holding is not supported by the registry record, which "
      "shows only the immediate corporate shareholder.", "{REG}"),
     ("document_quality", 0.30,
      "The ownership chart was routed to manual review because pages of the structure are missing.",
      "{DOC:ownership_chart}"),
     ("extraction_confidence", 0.20,
      "The indirect ownership path was extracted at 0.41 confidence and is not corroborated by a "
      "second source.", "{DOC:ubo_declaration}"),
     ("screening_outcome", 0.00,
      "No sanctions, PEP or adverse-media findings for the entity or any listed individual.",
      "{SCR:applicant}|{SCR:IND-0009}")]},
 "evidence": {
   "applicant_summary": "Vestmark Nordic OU, Estonian private limited company EE-90012345, marine "
     "equipment distribution, two directors, one verified 24.5 percent direct holding and one "
     "declared 31.5 percent indirect holding through two intermediate companies.",
   "missing_or_conflicting_evidence": "Ownership chart ({DOC:ownership_chart}) incomplete: pages "
     "missing, routed to manual review. Registry does not support the declared indirect beneficial "
     "ownership ({REG}). Source-of-wealth statement required by the triggered conditional rule has "
     "not been received. Indirect ownership path extracted at 0.41 confidence.",
   "recommended_next_action": "enhanced_due_diligence",
   "draft_compliance_narrative": "The entity itself is corroborated: active status, matching name, "
     "number, address and directors. Beneficial ownership is not. The declaration states a 31.5 "
     "percent indirect holding through Harboe Holdings OU and Lindval Mid Holdings SA, but the "
     "registry shows only the immediate corporate shareholder and does not support the chain. The "
     "ownership chart submitted to evidence the chain is incomplete and has been routed to manual "
     "review, and the path itself was extracted at 0.41 confidence. Screening and identity checks "
     "returned no findings, so the risk here is structural opacity rather than an adverse finding. "
     "Risk band high at score 66. Enhanced due diligence is recommended: a complete ownership "
     "chart, a source-of-wealth statement for the indirect owner, and confirmation of the "
     "ownership percentage at each layer."},
 "decisions": [
   {"reviewer": "M. Sild", "reviewer_role": "analyst", "decision": "enhanced_due_diligence",
    "reason_code": "ownership_structure_unclear",
    "rationale": "Declared indirect ownership is not supported by the registry and the supporting "
      "chart is incomplete. Routing to enhanced due diligence and asking the applicant for a "
      "complete chart and a source-of-wealth statement. No adverse screening finding is present, "
      "so this is an evidence-quality escalation, not a financial-crime referral.",
    "evidence_relied_on": "{REG}|{DOC:ubo_declaration}|{DOC:ownership_chart}|{RSK}",
    "override_flag": False, "customer_template_id": "TPL-0003"}],
 "communications": [
   {"template_id": "TPL-0003", "audience": "applicant",
    "message_type": "additional_information_request", "approval_status": "approved",
    "sent_status": "sent", "approver_role": "analyst",
    "rendered_text": "Hello Lembit Vaher, to complete the company verification for Vestmark Nordic "
      "OU (reference WAL-ONB-0004) we need a clearer picture of the ownership structure. Please "
      "upload a complete ownership chart showing every intermediate company and the individuals who "
      "ultimately own or control Vestmark Nordic OU, together with a source-of-wealth statement for "
      "the individual holding shares through those companies."},
   {"template_id": "TPL-0006", "audience": "applicant", "message_type": "verification_delay",
    "approval_status": "pending_approval", "sent_status": "not_sent",
    "rendered_text": "Hello Lembit Vaher, the checks on your application for Vestmark Nordic OU "
      "(reference WAL-ONB-0004) are taking a little longer than usual. No action is needed from "
      "you. We will update you again within three working days."}],
})

# ---------------------------------------------------------------- Case 5 ----
CASES.append({
 "case_id": "WAL-ONB-0005", "applicant_id": "APP-0005",
 "start": datetime(2026, 9, 10, 8, 50, 0),
 # Single-layer, directly held ownership: this is an SME corporate. The case is
 # interesting because of the PEP/adverse-media finding on the owner, which is a
 # risk signal, not an applicant-type signal.
 "applicant_type": "sme_corporate", "jurisdiction": "UK", "entity_scope": "wallester_uk_ltd",
 "source_channel": "portal", "status": "enhanced_due_diligence", "next_action_owner": "compliance",
 "assigned_owner": "compliance.h.laur",
 "applicant": {"legal_name": "Quillon Marine Services Ltd", "trading_name": "Quillon Marine",
   "registration_number": "UK-99017733", "entity_type": "private_limited_company", "country": "GB",
   "business_activity": "Offshore vessel chartering and crew logistics",
   "expected_usage": "Up to 40 cards; around 120000 GBP per month on fuel, port fees and crew travel",
   "vat_registered": True,
   "risk_segment": "elevated"},
 "individuals": [
   {"id": "IND-0010", "role": "director", "full_name": "Gareth Pennyfold", "dob": "1975-09-17",
    "nationality": "GB", "residence": "GB", "relationship": "Managing director"},
   {"id": "IND-0011", "role": "director", "full_name": "Ines Carrasco-Bell", "dob": "1983-04-03",
    "nationality": "ES", "residence": "GB", "relationship": "Finance director"},
   {"id": "IND-0012", "role": "ubo", "full_name": "Aurelio Vantano", "dob": "1962-11-28",
    "nationality": "IT", "residence": "MT",
    "relationship": "Holds 55 percent of the issued share capital directly"}],
 "ubos": [{"id": "UBO-0004", "individual_id": "IND-0012", "pct": 55.0,
   "control_type": "direct_shareholding", "ownership_path": "Quillon Marine Services Ltd",
   "verification_status": "verified"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_quillon.pdf", issue_country="GB",
       fields=[("company_name", "Quillon Marine Services Ltd", 0.96, 1, False),
               ("registration_number", "UK-99017733", 0.95, 1, False)]),
   doc("registry_extract", "registry_extract_quillon.pdf", issue_country="GB", fields=[
     ("company_name", "Quillon Marine Services Ltd", 0.95, 1, False),
     ("registration_number", "UK-99017733", 0.94, 1, False),
     ("registered_address", "Harbour House, 2 Dockside Walk, Aberdeen AB11 5QT, United Kingdom",
      0.92, 1, False),
     ("entity_status", "active", 0.94, 1, False)]),
   doc("tax_registration_certificate", "vat_certificate_quillon.pdf", fields=[
     ("vat_number", "GB-VAT-882014577", 0.91, 1, False)]),
   doc("director_register", "director_register_quillon.pdf", fields=[
     ("director_name", "Gareth Pennyfold", 0.93, 1, False),
     ("director_name_2", "Ines Carrasco-Bell", 0.92, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_quillon.pdf", fields=[
     ("signatory_name", "Gareth Pennyfold", 0.90, 1, False)]),
   doc("ubo_declaration", "ubo_declaration_quillon.pdf", fields=[
     ("ubo_name", "Aurelio Vantano", 0.92, 1, False),
     ("ownership_percentage", "55.0", 0.91, 1, False),
     ("control_basis", "direct shareholding", 0.89, 1, False)]),
   doc("id_document", "passport_pennyfold_g.jpg", subject="IND-0010", expiry="2029-12-04",
       issue_country="GB", fields=[("full_name", "Gareth Pennyfold", 0.96, 1, False)]),
   doc("id_document", "passport_carrasco_bell_i.jpg", subject="IND-0011", expiry="2031-03-21",
       issue_country="ES", fields=[("full_name", "Ines Carrasco-Bell", 0.94, 1, False)]),
   doc("id_document", "passport_vantano_a.jpg", subject="IND-0012", expiry="2028-06-19",
       issue_country="IT", fields=[("full_name", "Aurelio Vantano", 0.93, 1, False)]),
   doc("proof_of_address", "poa_pennyfold_aug2026.pdf", subject="IND-0010", issue_country="GB",
       fields=[("document_date", "2026-08-30", 0.92, 1, False)]),
   doc("proof_of_address", "poa_carrasco_bell_aug2026.pdf", subject="IND-0011", issue_country="GB",
       fields=[("document_date", "2026-08-28", 0.91, 1, False)]),
   doc("proof_of_address", "poa_vantano_aug2026.pdf", subject="IND-0012", issue_country="MT",
       fields=[("document_date", "2026-08-11", 0.87, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_quillon.pdf", fields=[
     ("declared_source", "Charter revenue under long-term vessel contracts", 0.88, 1, False)]),
   doc("business_activity_description", "business_activity_quillon.pdf", fields=[
     ("declared_industry", "Offshore marine chartering", 0.90, 1, False)]),
   doc("bank_statement", "bank_statement_quillon_aug2026.pdf", fields=[
     ("account_holder", "Quillon Marine Services Ltd", 0.89, 1, False)]),
 ],
 "triggered_conditionals": [find_rule("sme_corporate", "UK", "tax_registration_certificate"),
                            find_rule("sme_corporate", "UK", "bank_statement")],
 "registry": {"provider": "MockRegistryHub UK", "company_status": "active", "name_match": "match",
   "number_match": "match", "address_match": "match", "director_match": "match",
   "ubo_supported": True, "high_risk": False, "confidence": 0.93, "result": "pass"},
 "identity_checks": [idcheck("IND-0010"), idcheck("IND-0011"), idcheck("IND-0012")],
 "screenings": [screen("applicant"), screen("individual", "IND-0010"),
                screen("individual", "IND-0011"),
                screen("individual", "IND-0012", sanctions="no_match", pep="pep_match",
                       adverse_media="moderate", severity="high",
                       evidence_refs="SCREEN-REF-PEP-4471|SCREEN-REF-MEDIA-9920")],
 "risk": {"score": 72, "band": "high", "recommended_action": "enhanced_due_diligence",
   "confidence": 0.78, "insufficient_evidence_flag": False, "requires_human_signoff": True,
   "factors": [
     ("pep_exposure", 0.70,
      "The 55 percent beneficial owner returned a PEP match. Configured policy routes PEP findings "
      "to enhanced due diligence; PEP status alone is not a rejection reason.", "{SCR:IND-0012}"),
     ("adverse_media_severity", 0.45,
      "Moderate-relevance adverse media was returned for the same individual. The findings are "
      "unverified reporting and are not treated as established fact.", "{SCR:IND-0012}"),
     ("industry_risk", 0.30,
      "Offshore chartering with cross-border crew payments carries an elevated industry weighting.",
      "{DOC:business_activity_description}"),
     ("jurisdiction_risk", 0.20,
      "The beneficial owner is resident outside the applicant jurisdiction, which adds a "
      "cross-border factor.", "{DOC:ubo_declaration}"),
     ("document_quality", 0.00,
      "All fifteen documents passed the pre-screen at the first attempt.", "{PACK}")]},
 "evidence": {
   "applicant_summary": "Quillon Marine Services Ltd, UK private limited company UK-99017733, "
     "offshore vessel chartering, two directors, one 55 percent direct beneficial owner resident in "
     "Malta, 40 cards requested at around 120000 GBP per month.",
   "missing_or_conflicting_evidence": "No missing documents. Open question: the source and current "
     "status of the beneficial owner's public position, and whether the moderate adverse-media "
     "reporting relates to the same individual.",
   "recommended_next_action": "enhanced_due_diligence",
   "draft_compliance_narrative": "Internal compliance narrative, not for disclosure to the "
     "applicant. Entity verification is clean: registry confirms active status with name, number, "
     "address and director matches, and beneficial ownership is supported by the register. "
     "Identity verification passed for all three individuals. Screening on the 55 percent "
     "beneficial owner IND-0012 returned a PEP match together with moderate-relevance adverse "
     "media; sanctions screening returned no match. Provider evidence references are "
     "SCREEN-REF-PEP-4471 and SCREEN-REF-MEDIA-9920. Under the configured matrix this produces "
     "risk band high at score 72 and mandatory human sign-off. PEP status does not by itself "
     "warrant rejection. Enhanced due diligence should establish the nature and currency of the "
     "position held, the source of wealth behind the 55 percent holding, and whether the "
     "adverse-media reporting concerns this individual. The orchestration layer has not cleared or "
     "dismissed either finding. Customer-facing communication is limited to the generic "
     "manual-review template."},
 "decisions": [
   {"reviewer": "H. Laur", "reviewer_role": "compliance", "decision": "enhanced_due_diligence",
    "reason_code": "pep_match_edd_required",
    "rationale": "PEP match on the majority beneficial owner with moderate adverse media on the "
      "same individual. Opening enhanced due diligence to establish the position held, the source "
      "of wealth and the relevance of the media reporting. Not a rejection: PEP status alone does "
      "not disqualify under Wallester policy.",
    "evidence_relied_on": "{SCR:IND-0012}|{IDC:IND-0012}|{DOC:ubo_declaration}|{RSK}",
    "override_flag": False, "customer_template_id": "TPL-0004"}],
 "communications": [
   {"template_id": "TPL-0004", "audience": "applicant", "message_type": "manual_review_underway",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "compliance",
    "approver": "compliance.h.laur",
    "rendered_text": "Hello Gareth Pennyfold, your application for Quillon Marine Services Ltd "
      "(reference WAL-ONB-0005) is with our onboarding team for an additional manual review step. "
      "No further documents are needed from you at this time. We will contact you as soon as this "
      "step is complete."},
   {"template_id": "TPL-0008", "audience": "analyst", "message_type": "internal_case_summary",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "compliance",
    "approver": "compliance.h.laur",
    "rendered_text": "Internal case summary for WAL-ONB-0005 (Quillon Marine Services Ltd). Risk "
      "band high, score 72. Screening on the 55 percent beneficial owner returned a PEP match with "
      "moderate adverse media; sanctions no match. Enhanced due diligence opened by compliance. "
      "Applicant contact is limited to the generic manual-review template. Not for disclosure to "
      "the applicant."}],
})

# ---------------------------------------------------------------- Case 6 ----
CASES.append({
 "case_id": "WAL-ONB-0006", "applicant_id": "APP-0006",
 "start": datetime(2026, 9, 14, 9, 20, 0),
 "applicant_type": "sme_corporate", "jurisdiction": "EE", "entity_scope": "wallester_as",
 "source_channel": "portal", "status": "analyst_review_required", "next_action_owner": "compliance",
 "assigned_owner": "compliance.k.ohtla",
 "applicant": {"legal_name": "Saarvik Metall OU", "trading_name": "Saarvik",
   "registration_number": "EE-90014782", "entity_type": "private_limited_company", "country": "EE",
   "business_activity": "Metal fabrication and industrial subcontracting",
   "expected_usage": "Up to 20 cards; around 45000 EUR per month on materials and fuel",
   "vat_registered": True,
   "risk_segment": "elevated"},
 "individuals": [
   {"id": "IND-0013", "role": "director", "full_name": "Rasmus Kalda", "dob": "1977-05-21",
    "nationality": "EE", "residence": "EE",
    "relationship": "Registered director with sole signing authority"},
   {"id": "IND-0014", "role": "director", "full_name": "Triin Lepik", "dob": "1986-10-08",
    "nationality": "EE", "residence": "EE", "relationship": "Registered director"},
   {"id": "IND-0015", "role": "ubo", "full_name": "Oskar Magiste", "dob": "1970-03-16",
    "nationality": "EE", "residence": "EE",
    "relationship": "Holds 80 percent of the issued share capital directly"}],
 "ubos": [{"id": "UBO-0005", "individual_id": "IND-0015", "pct": 80.0,
   "control_type": "direct_shareholding", "ownership_path": "Saarvik Metall OU",
   "verification_status": "verified"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_saarvik.pdf", issue_country="EE",
       fields=[("company_name", "Saarvik Metall OU", 0.95, 1, False),
               ("registration_number", "EE-90014782", 0.94, 1, False)]),
   doc("registry_extract", "registry_extract_saarvik.pdf", issue_country="EE", fields=[
     ("company_name", "Saarvik Metall OU", 0.95, 1, False),
     ("registration_number", "EE-90014782", 0.94, 1, False),
     ("registered_address", "Tehnika 27, 76505 Saue, Estonia", 0.93, 1, False),
     ("entity_status", "active", 0.95, 1, False)]),
   doc("tax_registration_certificate", "tax_registration_saarvik.pdf", fields=[
     ("tax_number", "EE-TAX-90014782", 0.92, 1, False)]),
   doc("director_register", "director_register_saarvik.pdf", fields=[
     ("director_name", "Rasmus Kalda", 0.94, 1, False),
     ("director_name_2", "Triin Lepik", 0.92, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_saarvik.pdf", fields=[
     ("signatory_name", "Rasmus Kalda", 0.91, 1, False)]),
   doc("ubo_declaration", "ubo_declaration_saarvik.pdf", fields=[
     ("ubo_name", "Oskar Magiste", 0.93, 1, False),
     ("ownership_percentage", "80.0", 0.92, 1, False)]),
   doc("id_document", "id_card_kalda_r.jpg", subject="IND-0013", expiry="2030-04-02",
       issue_country="EE", fields=[("full_name", "Rasmus Kalda", 0.95, 1, False),
                                   ("date_of_birth", "1977-05-21", 0.94, 1, False)]),
   doc("id_document", "id_card_lepik_t.jpg", subject="IND-0014", expiry="2029-08-14",
       issue_country="EE", fields=[("full_name", "Triin Lepik", 0.94, 1, False)]),
   doc("id_document", "id_card_magiste_o.jpg", subject="IND-0015", expiry="2031-06-25",
       issue_country="EE", fields=[("full_name", "Oskar Magiste", 0.93, 1, False)]),
   doc("proof_of_address", "poa_kalda_sep2026.pdf", subject="IND-0013", issue_country="EE",
       fields=[("document_date", "2026-09-02", 0.92, 1, False)]),
   doc("proof_of_address", "poa_lepik_aug2026.pdf", subject="IND-0014", issue_country="EE",
       fields=[("document_date", "2026-08-21", 0.91, 1, False)]),
   doc("proof_of_address", "poa_magiste_aug2026.pdf", subject="IND-0015", issue_country="EE",
       fields=[("document_date", "2026-08-18", 0.90, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_saarvik.pdf", fields=[
     ("declared_source", "Subcontracting revenue from industrial clients", 0.88, 1, False)]),
   doc("business_activity_description", "business_activity_saarvik.pdf", fields=[
     ("declared_industry", "Metal fabrication", 0.91, 1, False)]),
 ],
 "triggered_conditionals": [],
 "registry": {"provider": "MockRegistryHub EE", "company_status": "active", "name_match": "match",
   "number_match": "match", "address_match": "match", "director_match": "match",
   "ubo_supported": True, "high_risk": False, "confidence": 0.94, "result": "pass"},
 "identity_checks": [idcheck("IND-0013"), idcheck("IND-0014"), idcheck("IND-0015")],
 "screenings": [screen("applicant"), screen("individual", "IND-0014"),
                screen("individual", "IND-0015"),
                screen("individual", "IND-0013", sanctions="possible_match", pep="no_match",
                       adverse_media="none", severity="critical",
                       evidence_refs="SCREEN-REF-SAN-2281")],
 "risk": {"score": 88, "band": "critical", "recommended_action": "escalate", "confidence": 0.85,
   "insufficient_evidence_flag": False, "requires_human_signoff": True, "factors": [
     ("sanctions_screening_outcome", 0.90,
      "A possible sanctions match was returned for a registered director. Automated progression is "
      "blocked and the orchestration layer may not clear or dismiss the match.", "{SCR:IND-0013}"),
     ("control_exposure", 0.35,
      "The subject of the possible match is a registered director with sole signing authority.",
      "{DOC:director_register}|{DOC:authorised_signatory_list}"),
     ("jurisdiction_risk", 0.05,
      "Estonia under Wallester AS; no elevated country weighting.", "{REG}"),
     ("document_quality", 0.00,
      "All fourteen documents passed the pre-screen at the first attempt.", "{PACK}")]},
 "evidence": {
   "applicant_summary": "Saarvik Metall OU, Estonian private limited company EE-90014782, metal "
     "fabrication, two registered directors, one 80 percent direct beneficial owner, 20 cards "
     "requested at around 45000 EUR per month.",
   "missing_or_conflicting_evidence": "No missing documents. Open item: the possible sanctions "
     "match on director IND-0013 has not been resolved as either a true match or a false positive.",
   "recommended_next_action": "escalate",
   "draft_compliance_narrative": "Internal compliance narrative, not for disclosure to the "
     "applicant. Entity and identity evidence are complete and consistent: registry returned active "
     "status with name, number, address and director matches, beneficial ownership is supported by "
     "the register, and all three individual identity checks passed. Screening returned a possible "
     "sanctions match on registered director IND-0013 with severity critical; provider evidence "
     "reference SCREEN-REF-SAN-2281. Sanctions screening on the entity and on the other two "
     "individuals returned no match. Under the configured matrix this produces risk band critical "
     "at score 88 with mandatory human sign-off. The orchestration layer has neither cleared nor "
     "dismissed the match and has stopped automated progression. The case requires compliance "
     "review to determine whether this is a true match or a false positive against the screened "
     "name and date of birth. Customer-facing communication has been restricted to the generic "
     "manual-review template with no reference to the reason for review."},
 "decisions": [
   {"reviewer": "K. Ohtla", "reviewer_role": "compliance", "decision": "escalate",
    "reason_code": "possible_sanctions_match",
    "rationale": "Possible sanctions match on a registered director with sole signing authority. "
      "Escalating to the MLRO queue for adjudication. No automated clearance and no customer "
      "disclosure of the reason for review.",
    "evidence_relied_on": "{SCR:IND-0013}|{IDC:IND-0013}|{DOC:director_register}|{RSK}",
    "override_flag": False, "escalation_target": "MLRO queue - Wallester AS",
    "customer_template_id": "TPL-0004"}],
 "communications": [
   {"template_id": "TPL-0004", "audience": "applicant", "message_type": "manual_review_underway",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "compliance",
    "approver": "compliance.k.ohtla",
    "rendered_text": "Hello Rasmus Kalda, your application for Saarvik Metall OU (reference "
      "WAL-ONB-0006) is with our onboarding team for an additional manual review step. No further "
      "documents are needed from you at this time. We will contact you as soon as this step is "
      "complete."},
   {"template_id": "TPL-0009", "audience": "compliance", "message_type": "internal_escalation_note",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "compliance",
    "approver": "compliance.k.ohtla",
    "rendered_text": "Internal escalation note for WAL-ONB-0006 (Saarvik Metall OU). Routed to MLRO "
      "queue - Wallester AS. Automated progression is blocked pending compliance review. Not for "
      "disclosure to the applicant."}],
})

# ---------------------------------------------------------------- Case 7 ----
CASES.append({
 "case_id": "WAL-ONB-0007", "applicant_id": "APP-0007",
 "start": datetime(2026, 9, 16, 13, 30, 0),
 "applicant_type": "sme_corporate", "jurisdiction": "UK", "entity_scope": "wallester_uk_ltd",
 "source_channel": "email", "status": "enhanced_due_diligence", "next_action_owner": "compliance",
 "assigned_owner": "compliance.d.ferreira",
 "applicant": {"legal_name": "Bramforth Aggregates Ltd", "trading_name": "Bramforth",
   "registration_number": "UK-99021560", "entity_type": "private_limited_company", "country": "GB",
   "business_activity": "Supply of construction aggregates and tipper haulage",
   "expected_usage": "Up to 25 cards; around 55000 GBP per month on fuel and plant hire",
   "vat_registered": True,
   "risk_segment": "elevated"},
 "individuals": [
   {"id": "IND-0016", "role": "director", "full_name": "Nigel Braithwaite-Cole", "dob": "1968-04-12",
    "nationality": "GB", "residence": "GB",
    "relationship": "Sole registered director and authorised signatory"},
   {"id": "IND-0017", "role": "ubo", "full_name": "Fenella Quist", "dob": "1979-01-25",
    "nationality": "GB", "residence": "GB",
    "relationship": "Holds 45 percent of the issued share capital directly"}],
 "ubos": [{"id": "UBO-0006", "individual_id": "IND-0017", "pct": 45.0,
   "control_type": "direct_shareholding", "ownership_path": "Bramforth Aggregates Ltd",
   "verification_status": "verified"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_bramforth.pdf", issue_country="GB",
       fields=[("company_name", "Bramforth Aggregates Ltd", 0.95, 1, False),
               ("registration_number", "UK-99021560", 0.94, 1, False)]),
   doc("registry_extract", "registry_extract_bramforth.pdf", issue_country="GB", fields=[
     ("company_name", "Bramforth Aggregates Ltd", 0.95, 1, False),
     ("registration_number", "UK-99021560", 0.94, 1, False),
     ("registered_address", "Bramforth Yard, Pinfold Lane, Doncaster DN4 6RS, United Kingdom",
      0.90, 1, False),
     ("entity_status", "active", 0.93, 1, False)]),
   doc("tax_registration_certificate", "vat_certificate_bramforth.pdf", fields=[
     ("vat_number", "GB-VAT-664120933", 0.89, 1, False)]),
   doc("director_register", "director_register_bramforth_scan.pdf", fields=[
     ("director_name", "Nigel Braithwaite-Cole", 0.64, 1, False),
     ("appointment_date", "2015-02-03", 0.81, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_bramforth.pdf", fields=[
     ("signatory_name", "Nigel Braithwaite-Cole", 0.88, 1, False)]),
   doc("ubo_declaration", "ubo_declaration_bramforth.pdf", fields=[
     ("ubo_name", "Fenella Quist", 0.91, 1, False),
     ("ownership_percentage", "45.0", 0.90, 1, False)]),
   doc("id_document", "passport_braithwaite_cole_n.jpg", subject="IND-0016", expiry="2030-10-08",
       issue_country="GB", fields=[("full_name", "Nigel Braithwaite-Cole", 0.93, 1, False),
                                   ("date_of_birth", "1968-04-12", 0.92, 1, False)]),
   doc("id_document", "passport_quist_f.jpg", subject="IND-0017", expiry="2032-01-30",
       issue_country="GB", fields=[("full_name", "Fenella Quist", 0.94, 1, False)]),
   doc("proof_of_address", "poa_braithwaite_cole_aug2026.pdf", subject="IND-0016",
       issue_country="GB", fields=[("document_date", "2026-08-09", 0.90, 1, False)]),
   doc("proof_of_address", "poa_quist_aug2026.pdf", subject="IND-0017", issue_country="GB",
       fields=[("document_date", "2026-08-15", 0.91, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_bramforth.pdf", fields=[
     ("declared_source", "Aggregate supply contracts with regional construction firms", 0.87, 1,
      False)]),
   doc("business_activity_description", "business_activity_bramforth.pdf", fields=[
     ("declared_industry", "Construction aggregates and haulage", 0.90, 1, False)]),
   doc("bank_statement", "bank_statement_bramforth_aug2026.pdf", fields=[
     ("account_holder", "Bramforth Aggregates Ltd", 0.88, 1, False)]),
 ],
 "triggered_conditionals": [find_rule("sme_corporate", "UK", "tax_registration_certificate"),
                            find_rule("sme_corporate", "UK", "bank_statement")],
 "audit_after_quality": [
   {"actor_type": "analyst", "actor_id": "compliance.d.ferreira",
    "action": "extracted_field_accepted_as_read",
    "summary": "director_name on director_register_bramforth_scan.pdf accepted as read by "
               "compliance.d.ferreira; reason: the name matches the signatory list and the "
               "director's passport, so the faint scan is corroborated"}],
 "registry": {"provider": "MockRegistryHub UK", "company_status": "active", "name_match": "match",
   "number_match": "match", "address_match": "match", "director_match": "match",
   "ubo_supported": True, "high_risk": False, "confidence": 0.90, "result": "pass"},
 "identity_checks": [idcheck("IND-0016"), idcheck("IND-0017")],
 "screenings": [screen("applicant"), screen("individual", "IND-0017"),
                screen("individual", "IND-0016", sanctions="no_match", pep="no_match",
                       adverse_media="serious", severity="high",
                       evidence_refs="SCREEN-REF-MEDIA-5514|SCREEN-REF-MEDIA-5517")],
 "risk": {"score": 74, "band": "high", "recommended_action": "enhanced_due_diligence",
   "confidence": 0.80, "insufficient_evidence_flag": False, "requires_human_signoff": True,
   "factors": [
     ("adverse_media_severity", 0.75,
      "Serious adverse media was returned for the sole registered director, concerning alleged "
      "conduct in the applicant's own sector. The reporting is unverified allegation and is not "
      "treated as established fact.", "{SCR:IND-0016}"),
     ("control_exposure", 0.35,
      "The subject of the media findings is the sole registered director and the only authorised "
      "signatory, so no separation of control exists.",
      "{DOC:director_register}|{DOC:authorised_signatory_list}"),
     ("extraction_confidence", 0.10,
      "The director name was extracted from a scanned register at 0.64 confidence and was matched "
      "to the identity document rather than relied on alone.", "{DOC:director_register}"),
     ("industry_risk", 0.20,
      "Aggregates supply and haulage carries a moderately elevated industry weighting.",
      "{DOC:business_activity_description}"),
     ("screening_outcome", 0.00,
      "No sanctions or PEP findings for the entity, the director or the beneficial owner.",
      "{SCR:applicant}|{SCR:IND-0017}")]},
 "evidence": {
   "applicant_summary": "Bramforth Aggregates Ltd, UK private limited company UK-99021560, "
     "construction aggregates and haulage, one sole registered director, one 45 percent direct "
     "beneficial owner, 25 cards requested at around 55000 GBP per month.",
   "missing_or_conflicting_evidence": "No missing documents. The director name on the scanned "
     "register was extracted at 0.64 confidence; it was corroborated against the identity document "
     "rather than corrected. Open item: relevance and currency of the adverse-media reporting.",
   "recommended_next_action": "escalate",
   "draft_compliance_narrative": "Internal compliance narrative, not for disclosure to the "
     "applicant. Entity verification is clean and both identity checks passed. Screening on the "
     "sole registered director IND-0016 returned serious adverse media at severity high; sanctions "
     "and PEP screening returned no match for any subject. Provider evidence references are "
     "SCREEN-REF-MEDIA-5514 and SCREEN-REF-MEDIA-5517. The reporting concerns alleged conduct in "
     "the applicant's own sector and, because the same individual is the only director and the only "
     "authorised signatory, there is no separation of control that would mitigate it. The findings "
     "are allegations and are recorded as such; the orchestration layer has not treated them as "
     "proven and has not dismissed them. Risk band high at score 74. The model recommended enhanced "
     "due diligence; the reviewer escalated instead, and the override reason is recorded on the "
     "decision. Customer contact is limited to the generic manual-review template."},
 "decisions": [
   {"reviewer": "D. Ferreira", "reviewer_role": "compliance", "decision": "escalate",
    "reason_code": "serious_adverse_media",
    "rationale": "Serious adverse media on the sole director and sole signatory, in the applicant's "
      "own sector, with no separation of control. Referring to the financial crime review team "
      "rather than handling in standard enhanced due diligence.",
    "evidence_relied_on": "{SCR:IND-0016}|{IDC:IND-0016}|{DOC:director_register}|{RSK}",
    "override_flag": True,
    "override_reason": "The risk assessment recommended enhanced_due_diligence. The reviewer "
      "escalated because the media findings concern the only individual with control of the "
      "company and relate directly to the declared business activity, which the scoring matrix "
      "does not weight separately.",
    "escalation_target": "Financial crime review team - Wallester UK Ltd",
    "customer_template_id": "TPL-0004"}],
 "communications": [
   {"template_id": "TPL-0004", "audience": "applicant", "message_type": "manual_review_underway",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "compliance",
    "approver": "compliance.d.ferreira",
    "rendered_text": "Hello Nigel Braithwaite-Cole, your application for Bramforth Aggregates Ltd "
      "(reference WAL-ONB-0007) is with our onboarding team for an additional manual review step. "
      "No further documents are needed from you at this time. We will contact you as soon as this "
      "step is complete."},
   {"template_id": "TPL-0009", "audience": "compliance", "message_type": "internal_escalation_note",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "compliance",
    "approver": "compliance.d.ferreira",
    "rendered_text": "Internal escalation note for WAL-ONB-0007 (Bramforth Aggregates Ltd). Routed "
      "to Financial crime review team - Wallester UK Ltd. Automated progression is blocked pending "
      "compliance review. Not for disclosure to the applicant."}],
})

# ---------------------------------------------------------------- Case 8 ----
CASES.append({
 "case_id": "WAL-ONB-0008", "applicant_id": "APP-0008",
 "start": datetime(2026, 9, 18, 10, 10, 0),
 "applicant_type": "white_label_partner", "jurisdiction": "EE", "entity_scope": "undetermined",
 "source_channel": "portal", "status": "submitted", "next_action_owner": "system",
 "assigned_owner": "partner.delivery.queue", "white_label": True,
 "applicant": {"legal_name": "Kestrel Pay Partners OU", "trading_name": "KestrelPay",
   "registration_number": "EE-90016004", "entity_type": "private_limited_company", "country": "EE",
   "business_activity": "Payments software platform seeking a white-label card programme",
   "expected_usage": "Programme level; not assessed in this phase",
   "vat_registered": True,
   "risk_segment": "unassessed"},
 "individuals": [
   {"id": "IND-0018", "role": "director", "full_name": "Marek Tonisson", "dob": "1981-08-04",
    "nationality": "EE", "residence": "EE",
    "relationship": "Registered director and programme contact"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_kestrel.pdf", issue_country="EE"),
   doc("registry_extract", "registry_extract_kestrel.pdf", issue_country="EE"),
   doc("director_register", "director_register_kestrel.pdf"),
   doc("ubo_declaration", "ubo_declaration_kestrel.pdf"),
   doc("programme_business_plan", "programme_business_plan_kestrel.pdf"),
   doc("website_or_platform_details", "platform_details_kestrel.pdf"),
 ],
 "triggered_conditionals": [],
 "audit_after_quality": [
   {"actor_type": "ai_agent", "actor_id": "applicant-router",
    "action": "routed_to_white_label_future_phase",
    "summary": "Applicant type white_label_partner. Case routed to the white-label programme "
      "onboarding branch, which is a future phase and outside the primary POC decision path. "
      "Future-phase steps: partner KYB tracking, API integration checklist, Visa co-brand approval, "
      "BIN and 3DS configuration, go-live testing readiness. No risk assessment, registry, "
      "identity, screening or decision records are produced for this branch.",
    "version": V_INTAKE, "after": 3},
   {"actor_type": "system", "actor_id": "case-state-machine", "action": "entity_scope_deferred",
    "summary": "Entity scope left as undetermined: the Wallester AS or Wallester UK Ltd perimeter "
      "for a partner programme is decided during programme design, not at KYB intake.",
    "after": 2}],
 "communications": [
   {"template_id": "TPL-0010", "audience": "applicant",
    "message_type": "white_label_intake_acknowledgement", "approval_status": "approved",
    "sent_status": "sent", "approver_role": "analyst", "approver": "partner.delivery.queue",
    "rendered_text": "Hello Marek Tonisson, thank you for the partner programme enquiry for Kestrel "
      "Pay Partners OU (reference WAL-ONB-0008). Your company documents have been received. Partner "
      "programme onboarding is handled by our programme delivery team, who will contact you about "
      "the next steps."}],
})

# ---------------------------------------------------------------- Case 9 ----
CASES.append({
 "case_id": "WAL-ONB-0009", "applicant_id": "APP-0009",
 "start": datetime(2026, 9, 19, 9, 0, 0),
 "applicant_type": "sme_corporate", "jurisdiction": "EE", "entity_scope": "wallester_as",
 "source_channel": "portal", "status": "approved", "next_action_owner": "system",
 "assigned_owner": "analyst.r.toome", "decisions_before_comms": True,
 "applicant": {"legal_name": "Parnu Kohviubade OU", "trading_name": "Parnu Coffee Roasters",
   "registration_number": "EE-90018321", "entity_type": "private_limited_company", "country": "EE",
   "business_activity": "Coffee roasting and wholesale supply to cafes",
   "expected_usage": "Up to 8 cards; around 9000 EUR per month on green coffee and packaging",
   "vat_registered": True,
   "risk_segment": "low"},
 "individuals": [
   {"id": "IND-0019", "role": "director", "full_name": "Katrin Ilves", "dob": "1984-02-19",
    "nationality": "EE", "residence": "EE",
    "relationship": "Managing director and authorised signatory"},
   {"id": "IND-0020", "role": "ubo", "full_name": "Mihkel Roosaar", "dob": "1976-06-11",
    "nationality": "EE", "residence": "EE",
    "relationship": "Holds 70 percent of the issued share capital directly"}],
 "ubos": [{"id": "UBO-0007", "individual_id": "IND-0020", "pct": 70.0,
   "control_type": "direct_shareholding", "ownership_path": "Parnu Kohviubade OU",
   "verification_status": "verified"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_parnu.pdf", issue_country="EE",
       fields=[("company_name", "Parnu Kohviubade OU", 0.96, 1, False),
               ("registration_number", "EE-90018321", 0.95, 1, False)]),
   doc("registry_extract", "registry_extract_parnu.pdf", issue_country="EE", fields=[
     ("company_name", "Parnu Kohviubade OU", 0.95, 1, False),
     ("registration_number", "EE-90018321", 0.94, 1, False),
     ("registered_address", "Ringi 42, 80010 Parnu, Estonia", 0.94, 1, False),
     ("entity_status", "active", 0.96, 1, False)]),
   doc("tax_registration_certificate", "tax_registration_parnu.pdf", fields=[
     ("tax_number", "EE-TAX-90018321", 0.93, 1, False)]),
   doc("director_register", "director_register_parnu.pdf", fields=[
     ("director_name", "Katrin Ilves", 0.95, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_parnu.pdf", fields=[
     ("signatory_name", "Katrin Ilves", 0.93, 1, False)]),
   doc("ubo_declaration", "ubo_declaration_parnu.pdf", fields=[
     ("ubo_name", "Mihkel Roosaar", 0.94, 1, False),
     ("ownership_percentage", "70.0", 0.93, 1, False)]),
   doc("id_document", "id_card_ilves_k.jpg", subject="IND-0019", expiry="2030-09-12",
       issue_country="EE", fields=[("full_name", "Katrin Ilves", 0.96, 1, False)]),
   doc("id_document", "id_card_roosaar_m.jpg", subject="IND-0020", expiry="2029-11-03",
       issue_country="EE", fields=[("full_name", "Mihkel Roosaar", 0.95, 1, False)]),
   doc("proof_of_address", "poa_ilves_sep2026.pdf", subject="IND-0019", issue_country="EE",
       fields=[("document_date", "2026-09-05", 0.93, 1, False)]),
   doc("proof_of_address", "poa_roosaar_aug2026.pdf", subject="IND-0020", issue_country="EE",
       fields=[("document_date", "2026-08-29", 0.92, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_parnu.pdf", fields=[
     ("declared_source", "Wholesale coffee sales to cafe customers", 0.90, 1, False)]),
   doc("business_activity_description", "business_activity_parnu.pdf", fields=[
     ("declared_industry", "Coffee roasting and wholesale", 0.92, 1, False)]),
 ],
 "triggered_conditionals": [],
 "registry": {"provider": "MockRegistryHub EE", "company_status": "active", "name_match": "match",
   "number_match": "match", "address_match": "match", "director_match": "match",
   "ubo_supported": True, "high_risk": False, "confidence": 0.97, "result": "pass"},
 "identity_checks": [idcheck("IND-0019"), idcheck("IND-0020")],
 "screenings": [screen("applicant"), screen("individual", "IND-0019"),
                screen("individual", "IND-0020")],
 "risk": {"score": 15, "band": "low", "recommended_action": "approve", "confidence": 0.93,
   "insufficient_evidence_flag": False, "requires_human_signoff": False, "factors": [
     ("jurisdiction_risk", 0.05, "Estonia under Wallester AS; no elevated country weighting.",
      "{REG}"),
     ("industry_risk", 0.10, "Food and beverage wholesale is a low-risk activity.",
      "{DOC:business_activity_description}"),
     ("ownership_transparency", 0.05,
      "A single 70 percent direct beneficial owner, supported by the registry record.",
      "{REG}|{DOC:ubo_declaration}"),
     ("document_quality", 0.00,
      "All twelve documents passed the pre-screen at the first attempt.", "{PACK}"),
     ("screening_outcome", 0.00,
      "No sanctions, PEP or adverse-media findings for the entity, the director or the beneficial "
      "owner.", "{SCR:applicant}|{SCR:IND-0019}|{SCR:IND-0020}")]},
 "evidence": {
   "applicant_summary": "Parnu Kohviubade OU, Estonian private limited company EE-90018321, coffee "
     "roasting and wholesale, one director, one 70 percent direct beneficial owner, 8 cards "
     "requested at around 9000 EUR per month.",
   "missing_or_conflicting_evidence": "",
   "recommended_next_action": "approve",
   "draft_compliance_narrative": "Control case with no exceptions. Every mandatory requirement-pack "
     "item was satisfied at first submission and no conditional rule was triggered. Registry "
     "returned active status with name, number, address and director matches and supports the "
     "declared beneficial ownership. Both identity checks passed on all components. Screening "
     "returned no sanctions, PEP or adverse-media findings for any subject. Risk band low at score "
     "15. Approved by the assigned analyst; the decision is recorded even though the configured "
     "matrix did not mandate human sign-off at this band."},
 "decisions": [
   {"reviewer": "R. Toome", "reviewer_role": "analyst", "decision": "approve",
    "reason_code": "all_mandatory_checks_passed",
    "rationale": "Complete document set at first submission, registry corroboration on every "
      "compared field, identity checks passed and no screening findings. Approving under the "
      "low-risk path.",
    "evidence_relied_on": "{REG}|{IDC:IND-0019}|{IDC:IND-0020}|{SCR:applicant}|{RSK}",
    "override_flag": False, "customer_template_id": "TPL-0007"}],
 "communications": [
   {"template_id": "TPL-0007", "audience": "applicant", "message_type": "approval_notification",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "analyst",
    "rendered_text": "Hello Katrin Ilves, good news. The Wallester Business account for Parnu "
      "Kohviubade OU (reference WAL-ONB-0009) has been approved. You can sign in to the portal to "
      "order your first cards and invite users."}],
})

# --------------------------------------------------------------- Case 10 ----
CASES.append({
 "case_id": "WAL-ONB-0010", "applicant_id": "APP-0010",
 "start": datetime(2026, 9, 21, 14, 0, 0),
 "applicant_type": "complex_corporate_ubo", "jurisdiction": "UK", "entity_scope": "wallester_uk_ltd",
 "source_channel": "portal", "status": "ready_for_decision", "next_action_owner": "analyst",
 "assigned_owner": "analyst.j.okoro",
 "applicant": {"legal_name": "Thornbury Analytics Ltd", "trading_name": "Thornbury",
   "registration_number": "UK-99025819", "entity_type": "private_limited_company", "country": "GB",
   "business_activity": "Data analytics consultancy for the retail sector",
   "expected_usage": "Up to 18 cards; around 22000 GBP per month on software and travel",
   "vat_registered": False,
   "risk_segment": "low"},
 "individuals": [
   {"id": "IND-0021", "role": "director", "full_name": "Imogen Skellow", "dob": "1987-12-01",
    "nationality": "GB", "residence": "GB", "relationship": "Managing director"},
   {"id": "IND-0022", "role": "director", "full_name": "Callum Pryde-Nash", "dob": "1979-07-23",
    "nationality": "GB", "residence": "GB", "relationship": "Technical director"},
   {"id": "IND-0023", "role": "ubo", "full_name": "Helena Marchbank", "dob": "1973-10-14",
    "nationality": "GB", "residence": "GB",
    "relationship": "Holds 70 percent through Marchbank Holdings Ltd, a UK holding company "
      "recorded on the register"}],
 # Two-layer but fully transparent: one UK holding company, both tiers on the
 # register. The negative control for WAL-ONB-0004's unsupported indirect chain.
 "ubos": [{"id": "UBO-0008", "individual_id": "IND-0023", "pct": 70.0,
   "chain": [100, 70],
   "control_type": "indirect_shareholding",
   "ownership_path": "Marchbank Holdings Ltd > Thornbury Analytics Ltd",
   "verification_status": "verified"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_thornbury.pdf", issue_country="GB",
       fields=[("company_name", "Thornbury Analytics Ltd", 0.96, 1, False),
               ("registration_number", "UK-99025819", 0.95, 1, False)]),
   doc("registry_extract", "registry_extract_thornbury.pdf", issue_country="GB", fields=[
     ("company_name", "Thornbury Analytics Ltd", 0.95, 1, False),
     ("registration_number", "UK-99025819", 0.94, 1, False),
     ("registered_address", "5 Wraysbury Mews, Bristol BS1 6TT, United Kingdom", 0.93, 1, False),
     ("entity_status", "active", 0.95, 1, False)]),
   doc("director_register", "director_register_thornbury.pdf", fields=[
     ("director_name", "Imogen Skellow", 0.94, 1, False),
     ("director_name_2", "Callum Pryde-Nash", 0.93, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_thornbury.pdf", fields=[
     ("signatory_name", "Imogen Skellow", 0.92, 1, False)]),
   doc("ubo_declaration", "ubo_declaration_thornbury.pdf", fields=[
     ("ubo_name", "Helena Marchbank", 0.94, 1, False),
     ("ownership_percentage", "70.0", 0.93, 1, False),
     ("control_basis", "indirect shareholding through Marchbank Holdings Ltd", 0.91, 1, False)]),
   doc("ownership_chart", "ownership_chart_thornbury.pdf", fields=[
     ("chart_summary", "Two layers: Helena Marchbank 100 percent of Marchbank Holdings Ltd "
      "(UK-99026440), which holds 70 percent of Thornbury Analytics Ltd; two directors hold "
      "15 percent each directly", 0.90, 1, False),
     ("intermediate_entity", "Marchbank Holdings Ltd (UK-99026440), active on the UK register",
      0.92, 1, False)]),
   doc("shareholder_register", "shareholder_register_thornbury.pdf", fields=[
     ("shareholder_name", "Marchbank Holdings Ltd", 0.92, 1, False),
     ("shareholder_name_2", "Helena Marchbank (sole shareholder of Marchbank Holdings Ltd)",
      0.91, 1, False)]),
   doc("id_document", "passport_skellow_i.jpg", subject="IND-0021", expiry="2031-05-16",
       issue_country="GB", fields=[("full_name", "Imogen Skellow", 0.96, 1, False)]),
   doc("id_document", "passport_pryde_nash_c.jpg", subject="IND-0022", expiry="2029-04-27",
       issue_country="GB", fields=[("full_name", "Callum Pryde-Nash", 0.95, 1, False)]),
   doc("id_document", "passport_marchbank_h.jpg", subject="IND-0023", expiry="2030-02-09",
       issue_country="GB", fields=[("full_name", "Helena Marchbank", 0.94, 1, False)]),
   doc("proof_of_address", "poa_skellow_sep2026.pdf", subject="IND-0021", issue_country="GB",
       fields=[("document_date", "2026-09-08", 0.93, 1, False)]),
   doc("proof_of_address", "poa_pryde_nash_aug2026.pdf", subject="IND-0022", issue_country="GB",
       fields=[("document_date", "2026-08-31", 0.92, 1, False)]),
   doc("proof_of_address", "poa_marchbank_sep2026.pdf", subject="IND-0023", issue_country="GB",
       fields=[("document_date", "2026-09-02", 0.92, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_thornbury.pdf", fields=[
     ("declared_source", "Consultancy fees under annual retainer contracts", 0.90, 1, False)]),
   doc("business_activity_description", "business_activity_thornbury.pdf", fields=[
     ("declared_industry", "Management and data analytics consultancy", 0.92, 1, False)]),
   doc("source_of_wealth_statement", "source_of_wealth_marchbank.pdf", fields=[
     ("declared_source_of_wealth",
      "Proceeds of the founder's earlier consultancy sale, held through Marchbank Holdings Ltd",
      0.91, 1, False)]),
 ],
 # The 70 percent holding is indirect, so the source-of-wealth conditional applies
 # here exactly as it does on case 4. The difference is that here it was supplied
 # and accepted, and the registry supports the chain.
 "triggered_conditionals": [find_rule("complex_corporate_ubo", "UK", "source_of_wealth_statement")],
 "registry": {"provider": "MockRegistryHub UK", "company_status": "active", "name_match": "match",
   "number_match": "match", "address_match": "match", "director_match": "match",
   "ubo_supported": True, "high_risk": False, "confidence": 0.95, "result": "pass"},
 "identity_checks": [idcheck("IND-0021"), idcheck("IND-0022"), idcheck("IND-0023")],
 "screenings": [screen("applicant"), screen("individual", "IND-0021"),
                screen("individual", "IND-0022"), screen("individual", "IND-0023")],
 "risk": {"score": 22, "band": "low", "recommended_action": "approve", "confidence": 0.90,
   "insufficient_evidence_flag": False, "requires_human_signoff": False, "factors": [
     ("ownership_transparency", 0.10,
      "The applicant is held through one intermediate UK holding company, Marchbank Holdings Ltd, "
      "which is itself wholly owned by the 70 percent beneficial owner. Both tiers appear on the "
      "register and the chain is fully supported by the registry record, so the additional layer "
      "does not add opacity.", "{REG}|{DOC:ownership_chart}|{DOC:shareholder_register}"),
     ("jurisdiction_risk", 0.05,
      "United Kingdom under Wallester UK Ltd; no elevated country weighting.", "{REG}"),
     ("industry_risk", 0.10, "Consultancy services carry a low industry weighting.",
      "{DOC:business_activity_description}"),
     ("document_quality", 0.00,
      "All sixteen documents passed the pre-screen at the first attempt.", "{PACK}"),
     ("screening_outcome", 0.00,
      "No sanctions, PEP or adverse-media findings for the entity or any of the three individuals.",
      "{SCR:applicant}|{SCR:IND-0021}|{SCR:IND-0022}|{SCR:IND-0023}")]},
 "evidence": {
   "applicant_summary": "Thornbury Analytics Ltd, UK private limited company UK-99025819, data "
     "analytics consultancy, two directors, one 70 percent beneficial owner holding through a "
     "single UK holding company, 18 cards requested at around 22000 GBP per month.",
   "missing_or_conflicting_evidence": "",
   "recommended_next_action": "approve",
   "draft_compliance_narrative": "Control case for the complex-corporate path. The applicant is "
     "held through one intermediate company, so it takes the complex corporate requirement pack "
     "and an ownership chart and shareholder register were required; both were supplied and "
     "accepted. The chain is two layers deep but entirely transparent: Marchbank Holdings Ltd is "
     "active on the UK register and wholly owned by the 70 percent beneficial owner, and the "
     "registry check supports the beneficial ownership. This is the contrast against "
     "WAL-ONB-0004, where an indirect chain of the same shape was not supported by the register. "
     "Registry, identity and screening results are clean throughout. Risk band low at score 22, "
     "ready for the analyst decision."},
 "communications": [
   {"template_id": "TPL-0005", "audience": "applicant", "message_type": "status_update",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "analyst",
    "rendered_text": "Hello Imogen Skellow, we have received your application for Thornbury "
      "Analytics Ltd and all of the documents we asked for. Your case reference is WAL-ONB-0010. "
      "Your application is now with our onboarding team and we will let you know as soon as the "
      "review is complete."}],
})

# --------------------------------------------------------------- Case 11 ----
# Eligibility failure rather than a risk finding: the register says the company
# no longer exists, which no amount of clean paperwork can cure.
CASES.append({
 "case_id": "WAL-ONB-0011", "applicant_id": "APP-0011",
 "start": datetime(2026, 9, 23, 9, 10, 0),
 "applicant_type": "sme_corporate", "jurisdiction": "UK", "entity_scope": "wallester_uk_ltd",
 "source_channel": "portal", "status": "rejected", "next_action_owner": "system",
 "assigned_owner": "analyst.j.okoro",
 "applicant": {"legal_name": "Ardenhall Joinery Ltd", "trading_name": "Ardenhall",
   "registration_number": "UK-99031147", "entity_type": "private_limited_company", "country": "GB",
   "business_activity": "Bespoke joinery and shopfitting for retail interiors",
   "expected_usage": "Up to 10 cards; around 18000 GBP per month on timber and fixings",
   "vat_registered": True,
   "risk_segment": "unassessed"},
 "individuals": [
   {"id": "IND-0024", "role": "director", "full_name": "Rowan Feathersby", "dob": "1972-03-08",
    "nationality": "GB", "residence": "GB", "relationship": "Managing director"},
   {"id": "IND-0025", "role": "director", "full_name": "Delia Ormsby-Crane", "dob": "1980-11-26",
    "nationality": "GB", "residence": "GB", "relationship": "Finance director"},
   {"id": "IND-0026", "role": "ubo", "full_name": "Wilhelmina Tarrant", "dob": "1965-07-14",
    "nationality": "GB", "residence": "GB",
    "relationship": "Holds 65 percent of the issued share capital directly"}],
 "ubos": [{"id": "UBO-0009", "individual_id": "IND-0026", "pct": 65.0,
   "control_type": "direct_shareholding", "ownership_path": "Ardenhall Joinery Ltd",
   "verification_status": "verified"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_ardenhall.pdf", issue_country="GB",
       fields=[("company_name", "Ardenhall Joinery Ltd", 0.95, 1, False),
               ("registration_number", "UK-99031147", 0.94, 1, False)]),
   doc("registry_extract", "registry_extract_ardenhall.pdf", issue_country="GB", fields=[
     ("company_name", "Ardenhall Joinery Ltd", 0.94, 1, False),
     ("registration_number", "UK-99031147", 0.93, 1, False),
     ("registered_address", "Ardenhall Works, 6 Kiln Row, Derby DE1 3QF, United Kingdom",
      0.92, 1, False),
     ("entity_status", "active", 0.71, 1, False)]),
   doc("tax_registration_certificate", "vat_certificate_ardenhall.pdf", fields=[
     ("vat_number", "GB-VAT-771903442", 0.92, 1, False)]),
   doc("director_register", "director_register_ardenhall.pdf", fields=[
     ("director_name", "Rowan Feathersby", 0.94, 1, False),
     ("director_name_2", "Delia Ormsby-Crane", 0.93, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_ardenhall.pdf", fields=[
     ("signatory_name", "Rowan Feathersby", 0.91, 1, False)]),
   doc("ubo_declaration", "ubo_declaration_ardenhall.pdf", fields=[
     ("ubo_name", "Wilhelmina Tarrant", 0.93, 1, False),
     ("ownership_percentage", "65.0", 0.92, 1, False)]),
   doc("id_document", "passport_feathersby_r.jpg", subject="IND-0024", expiry="2030-06-30",
       issue_country="GB", fields=[("full_name", "Rowan Feathersby", 0.95, 1, False)]),
   doc("id_document", "passport_ormsby_crane_d.jpg", subject="IND-0025", expiry="2029-09-19",
       issue_country="GB", fields=[("full_name", "Delia Ormsby-Crane", 0.94, 1, False)]),
   doc("id_document", "passport_tarrant_w.jpg", subject="IND-0026", expiry="2031-01-22",
       issue_country="GB", fields=[("full_name", "Wilhelmina Tarrant", 0.93, 1, False)]),
   doc("proof_of_address", "poa_feathersby_sep2026.pdf", subject="IND-0024", issue_country="GB",
       fields=[("document_date", "2026-09-04", 0.92, 1, False)]),
   doc("proof_of_address", "poa_ormsby_crane_sep2026.pdf", subject="IND-0025", issue_country="GB",
       fields=[("document_date", "2026-09-02", 0.91, 1, False)]),
   doc("proof_of_address", "poa_tarrant_aug2026.pdf", subject="IND-0026", issue_country="GB",
       fields=[("document_date", "2026-08-27", 0.90, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_ardenhall.pdf", fields=[
     ("declared_source", "Contract joinery revenue from retail fit-out clients", 0.89, 1, False)]),
   doc("business_activity_description", "business_activity_ardenhall.pdf", fields=[
     ("declared_industry", "Joinery and shopfitting", 0.91, 1, False)]),
 ],
 "triggered_conditionals": [find_rule("sme_corporate", "UK", "tax_registration_certificate")],
 "registry": {"provider": "MockRegistryHub UK", "company_status": "dissolved",
   "name_match": "match", "number_match": "match", "address_match": "match",
   "director_match": "match", "ubo_supported": True, "high_risk": False, "confidence": 0.97,
   "result": "fail"},
 "identity_checks": [idcheck("IND-0024"), idcheck("IND-0025"), idcheck("IND-0026")],
 "screenings": [screen("applicant"), screen("individual", "IND-0024"),
                screen("individual", "IND-0025"), screen("individual", "IND-0026")],
 "risk": {"score": 88, "band": "high", "recommended_action": "reject", "confidence": 0.95,
   "insufficient_evidence_flag": False, "requires_human_signoff": True, "factors": [
     ("entity_status", 0.95,
      "The register records the company as dissolved. A dissolved entity cannot hold a card "
      "programme, and no document supplied by the applicant can change that.", "{REG}"),
     ("document_quality", 0.00,
      "All fourteen documents passed the pre-screen at the first attempt.", "{PACK}"),
     ("screening_outcome", 0.00,
      "No sanctions, PEP or adverse-media findings for the entity or any of the three "
      "individuals.",
      "{SCR:applicant}|{SCR:IND-0024}|{SCR:IND-0025}|{SCR:IND-0026}")]},
 "evidence": {
   "applicant_summary": "Ardenhall Joinery Ltd, UK private limited company UK-99031147, bespoke "
     "joinery, two directors, one 65 percent direct beneficial owner, 10 cards requested at "
     "around 18000 GBP per month.",
   "missing_or_conflicting_evidence": "The registry extract supplied by the applicant shows the "
     "entity as active; the live register returns dissolved. The applicant's copy is the older "
     "record.",
   "recommended_next_action": "reject",
   "draft_compliance_narrative": "The paperwork is complete and internally consistent: name, "
     "number, address and directors all match the register, identity verification passed for all "
     "three individuals and screening returned nothing. The blocker is eligibility rather than "
     "risk. The live register records Ardenhall Joinery Ltd as dissolved, while the extract the "
     "applicant supplied shows it as active, which indicates the applicant is working from an "
     "older copy. A dissolved entity cannot be onboarded, so the recommendation is to reject and "
     "invite a fresh application if the company is restored to the register.",
 },
 "decisions": [
   {"reviewer": "J. Okoro", "reviewer_role": "analyst", "decision": "reject",
    "reason_code": "entity_not_active_on_register",
    "rationale": "The register records the applicant as dissolved. This is an eligibility failure "
      "and not a judgement about the people involved: identity and screening were clean "
      "throughout. Rejecting with the door left open to a new application if the company is "
      "restored.",
    "evidence_relied_on": "{REG}|{DOC:registry_extract}|{RSK}",
    "override_flag": True,
    "override_reason": "The scored recommendation was enhanced due diligence. A dissolved "
      "entity is an eligibility failure rather than a due-diligence question, so no amount of "
      "further diligence would change the answer; rejecting instead.",
    "customer_template_id": "TPL-0011"}],
 "communications": [
   {"template_id": "TPL-0011", "audience": "applicant", "message_type": "application_declined",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "analyst",
    "approver": "analyst.j.okoro",
    "rendered_text": "Hello Rowan Feathersby, thank you for your application for Ardenhall "
      "Joinery Ltd. We are not able to open an account at this time because the company register "
      "shows the company as dissolved. If the company is restored to the register, please do "
      "apply again and we will be glad to look at it afresh."}],
})

# --------------------------------------------------------------- Case 12 ----
# The one unambiguous sanctions hit in the set. Nothing automated may clear it:
# it goes to compliance and waits for a person.
CASES.append({
 "case_id": "WAL-ONB-0012", "applicant_id": "APP-0012",
 "start": datetime(2026, 9, 24, 8, 30, 0),
 "applicant_type": "sme_corporate", "jurisdiction": "EE", "entity_scope": "wallester_as",
 "source_channel": "portal", "status": "analyst_review_required", "next_action_owner": "compliance",
 "assigned_owner": "compliance.k.ohtla",
 "applicant": {"legal_name": "Solvarn Baltic OU", "trading_name": "Solvarn",
   "registration_number": "EE-90021455", "entity_type": "private_limited_company", "country": "EE",
   "business_activity": "Industrial valve distribution and maintenance",
   "expected_usage": "Up to 16 cards; around 30000 EUR per month on parts and travel",
   "vat_registered": True,
   "risk_segment": "elevated"},
 "individuals": [
   {"id": "IND-0027", "role": "director", "full_name": "Taavi Ruumet", "dob": "1979-05-30",
    "nationality": "EE", "residence": "EE", "relationship": "Managing director"},
   {"id": "IND-0028", "role": "ubo", "full_name": "Ivar Kolmsaar", "dob": "1968-02-17",
    "nationality": "EE", "residence": "EE",
    "relationship": "Holds 80 percent of the issued share capital directly"}],
 "ubos": [{"id": "UBO-0010", "individual_id": "IND-0028", "pct": 80.0,
   "control_type": "direct_shareholding", "ownership_path": "Solvarn Baltic OU",
   "verification_status": "verified"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_solvarn.pdf", issue_country="EE",
       fields=[("company_name", "Solvarn Baltic OU", 0.96, 1, False),
               ("registration_number", "EE-90021455", 0.95, 1, False)]),
   doc("registry_extract", "registry_extract_solvarn.pdf", issue_country="EE", fields=[
     ("company_name", "Solvarn Baltic OU", 0.95, 1, False),
     ("registration_number", "EE-90021455", 0.94, 1, False),
     ("registered_address", "Punane 18, 13619 Tallinn, Estonia", 0.93, 1, False),
     ("entity_status", "active", 0.95, 1, False)]),
   doc("tax_registration_certificate", "tax_registration_solvarn.pdf", fields=[
     ("tax_number", "EE-TAX-90021455", 0.92, 1, False)]),
   doc("director_register", "director_register_solvarn.pdf", fields=[
     ("director_name", "Taavi Ruumet", 0.94, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_solvarn.pdf", fields=[
     ("signatory_name", "Taavi Ruumet", 0.92, 1, False)]),
   doc("ubo_declaration", "ubo_declaration_solvarn.pdf", fields=[
     ("ubo_name", "Ivar Kolmsaar", 0.93, 1, False),
     ("ownership_percentage", "80.0", 0.92, 1, False)]),
   doc("id_document", "id_card_ruumet_t.jpg", subject="IND-0027", expiry="2030-04-11",
       issue_country="EE", fields=[("full_name", "Taavi Ruumet", 0.95, 1, False)]),
   doc("id_document", "id_card_kolmsaar_i.jpg", subject="IND-0028", expiry="2029-08-05",
       issue_country="EE", fields=[("full_name", "Ivar Kolmsaar", 0.94, 1, False)]),
   doc("proof_of_address", "poa_ruumet_sep2026.pdf", subject="IND-0027", issue_country="EE",
       fields=[("document_date", "2026-09-06", 0.93, 1, False)]),
   doc("proof_of_address", "poa_kolmsaar_sep2026.pdf", subject="IND-0028", issue_country="EE",
       fields=[("document_date", "2026-09-01", 0.92, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_solvarn.pdf", fields=[
     ("declared_source", "Distribution margin on industrial valve sales", 0.90, 1, False)]),
   doc("business_activity_description", "business_activity_solvarn.pdf", fields=[
     ("declared_industry", "Industrial equipment distribution", 0.91, 1, False)]),
 ],
 "triggered_conditionals": [],
 "registry": {"provider": "MockRegistryHub EE", "company_status": "active", "name_match": "match",
   "number_match": "match", "address_match": "match", "director_match": "match",
   "ubo_supported": True, "high_risk": False, "confidence": 0.94, "result": "pass"},
 "identity_checks": [idcheck("IND-0027"), idcheck("IND-0028")],
 "screenings": [screen("applicant"), screen("individual", "IND-0027"),
                screen("individual", "IND-0028", sanctions="clear_match", pep="no_match",
                       adverse_media="none", severity="critical",
                       evidence_refs="SCREEN-REF-SAN-7714")],
 "risk": {"score": 96, "band": "critical", "recommended_action": "escalate", "confidence": 0.91,
   "insufficient_evidence_flag": False, "requires_human_signoff": True, "factors": [
     ("sanctions_exposure", 0.95,
      "The 80 percent beneficial owner returned a confirmed sanctions match. Configured policy "
      "routes this to compliance without any automated clearance, and the orchestration layer has "
      "not cleared or downgraded the finding.", "{SCR:IND-0028}"),
     ("ownership_concentration", 0.30,
      "A single owner holds 80 percent, so the finding attaches to effective control of the "
      "applicant rather than to a minority holding.", "{DOC:ubo_declaration}"),
     ("document_quality", 0.00,
      "All twelve documents passed the pre-screen at the first attempt.", "{PACK}")]},
 "evidence": {
   "applicant_summary": "Solvarn Baltic OU, Estonian private limited company EE-90021455, "
     "industrial valve distribution, one director, one 80 percent direct beneficial owner, 16 "
     "cards requested at around 30000 EUR per month.",
   "missing_or_conflicting_evidence": "None on the documents. The open question is the sanctions "
     "match on the beneficial owner, which only a compliance officer can resolve.",
   "recommended_next_action": "escalate",
   "draft_compliance_narrative": "Internal compliance narrative, not for disclosure to the "
     "applicant. Entity verification is clean: the register returns active status with name, "
     "number, address and director matches and supports the declared ownership. Both identity "
     "checks passed. Screening on the 80 percent beneficial owner IND-0028 returned a confirmed "
     "sanctions match under provider reference SCREEN-REF-SAN-7714. Under the configured matrix "
     "this is critical and mandates compliance sign-off. No automated step has cleared, "
     "downgraded or re-run the match, and none may: only a compliance decision can resolve it. "
     "The case is escalated to the MLRO queue. Customer-facing communication is limited to the "
     "generic manual-review template and states no reason.",
 },
 "decisions": [
   {"reviewer": "K. Ohtla", "reviewer_role": "compliance", "decision": "escalate",
    "reason_code": "confirmed_sanctions_match_on_ubo",
    "rationale": "Confirmed sanctions match on the majority beneficial owner. This is outside the "
      "analyst mandate and is referred to the MLRO for a reporting decision. No customer contact "
      "beyond the generic acknowledgement until that decision is taken.",
    "evidence_relied_on": "{SCR:IND-0028}|{IDC:IND-0028}|{DOC:ubo_declaration}|{RSK}",
    "escalation_target": "mlro.queue",
    "override_flag": False, "customer_template_id": "TPL-0004"}],
 "communications": [
   {"template_id": "TPL-0004", "audience": "applicant", "message_type": "manual_review_underway",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "compliance",
    "approver": "compliance.k.ohtla",
    "rendered_text": "Hello Taavi Ruumet, your application for Solvarn Baltic OU (reference "
      "WAL-ONB-0012) is with our onboarding team for an additional manual review step. No further "
      "documents are needed from you at this time. We will contact you as soon as this step is "
      "complete."},
   {"template_id": "TPL-0008", "audience": "compliance", "message_type": "internal_case_summary",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "compliance",
    "approver": "compliance.k.ohtla",
    "rendered_text": "Internal case summary for WAL-ONB-0012 (Solvarn Baltic OU). Confirmed "
      "sanctions match on the 80 percent beneficial owner, provider reference "
      "SCREEN-REF-SAN-7714. Escalated to the MLRO queue. No automated clearance was applied. "
      "Applicant contact is limited to the generic manual-review template. Not for disclosure to "
      "the applicant."}],
})

# --------------------------------------------------------------- Case 13 ----
# Two independent gaps at once: an identity check that failed outright and a
# media provider that did not answer. Neither is an adverse finding; together
# they mean the file simply does not support a decision.
CASES.append({
 "case_id": "WAL-ONB-0013", "applicant_id": "APP-0013",
 "start": datetime(2026, 9, 25, 10, 5, 0),
 "applicant_type": "sme_corporate", "jurisdiction": "UK", "entity_scope": "wallester_uk_ltd",
 "source_channel": "email", "status": "analyst_review_required", "next_action_owner": "analyst",
 "assigned_owner": "analyst.r.toome",
 "applicant": {"legal_name": "Pemberly Tooling Ltd", "trading_name": "Pemberly",
   "registration_number": "UK-99034820", "entity_type": "private_limited_company", "country": "GB",
   "business_activity": "Precision tooling and machine parts for light manufacturing",
   "expected_usage": "Up to 14 cards; around 21000 GBP per month on materials and freight",
   "vat_registered": True,
   "risk_segment": "unassessed"},
 "individuals": [
   {"id": "IND-0029", "role": "director", "full_name": "Hadley Winstock", "dob": "1976-10-03",
    "nationality": "GB", "residence": "GB", "relationship": "Managing director"},
   {"id": "IND-0030", "role": "director", "full_name": "Marguerite Ellcott", "dob": "1983-06-21",
    "nationality": "GB", "residence": "GB", "relationship": "Operations director"},
   {"id": "IND-0031", "role": "ubo", "full_name": "Oswin Prattley", "dob": "1970-12-09",
    "nationality": "GB", "residence": "GB",
    "relationship": "Holds 55 percent of the issued share capital directly"}],
 "ubos": [{"id": "UBO-0011", "individual_id": "IND-0031", "pct": 55.0,
   "control_type": "direct_shareholding", "ownership_path": "Pemberly Tooling Ltd",
   "verification_status": "verified"}],
 "documents": [
   doc("certificate_of_incorporation", "cert_incorporation_pemberly.pdf", issue_country="GB",
       fields=[("company_name", "Pemberly Tooling Ltd", 0.95, 1, False),
               ("registration_number", "UK-99034820", 0.94, 1, False)]),
   doc("registry_extract", "registry_extract_pemberly.pdf", issue_country="GB", fields=[
     ("company_name", "Pemberly Tooling Ltd", 0.94, 1, False),
     ("registration_number", "UK-99034820", 0.93, 1, False),
     ("registered_address", "Unit 12 Pemberly Trading Estate, Coventry CV6 5NN, United Kingdom",
      0.92, 1, False),
     ("entity_status", "active", 0.94, 1, False)]),
   doc("tax_registration_certificate", "vat_certificate_pemberly.pdf", fields=[
     ("vat_number", "GB-VAT-903155721", 0.91, 1, False)]),
   doc("director_register", "director_register_pemberly.pdf", fields=[
     ("director_name", "Hadley Winstock", 0.93, 1, False),
     ("director_name_2", "Marguerite Ellcott", 0.92, 1, False)]),
   doc("authorised_signatory_list", "signatory_list_pemberly.pdf", fields=[
     ("signatory_name", "Hadley Winstock", 0.90, 1, False)]),
   doc("ubo_declaration", "ubo_declaration_pemberly.pdf", fields=[
     ("ubo_name", "Oswin Prattley", 0.92, 1, False),
     ("ownership_percentage", "55.0", 0.91, 1, False)]),
   doc("id_document", "passport_winstock_h.jpg", subject="IND-0029", expiry="2029-02-14",
       issue_country="GB", fields=[("full_name", "Hadley Winstock", 0.88, 1, False)]),
   doc("id_document", "passport_ellcott_m.jpg", subject="IND-0030", expiry="2031-07-08",
       issue_country="GB", fields=[("full_name", "Marguerite Ellcott", 0.94, 1, False)]),
   doc("id_document", "passport_prattley_o.jpg", subject="IND-0031", expiry="2030-05-26",
       issue_country="GB", fields=[("full_name", "Oswin Prattley", 0.93, 1, False)]),
   doc("proof_of_address", "poa_winstock_sep2026.pdf", subject="IND-0029", issue_country="GB",
       fields=[("document_date", "2026-09-07", 0.91, 1, False)]),
   doc("proof_of_address", "poa_ellcott_sep2026.pdf", subject="IND-0030", issue_country="GB",
       fields=[("document_date", "2026-09-03", 0.92, 1, False)]),
   doc("proof_of_address", "poa_prattley_aug2026.pdf", subject="IND-0031", issue_country="GB",
       fields=[("document_date", "2026-08-30", 0.90, 1, False)]),
   doc("source_of_funds_declaration", "source_of_funds_pemberly.pdf", fields=[
     ("declared_source", "Tooling contracts with light manufacturing customers", 0.89, 1, False)]),
   doc("business_activity_description", "business_activity_pemberly.pdf", fields=[
     ("declared_industry", "Precision engineering and tooling", 0.90, 1, False)]),
 ],
 "triggered_conditionals": [find_rule("sme_corporate", "UK", "tax_registration_certificate")],
 "registry": {"provider": "MockRegistryHub UK", "company_status": "active", "name_match": "match",
   "number_match": "match", "address_match": "match", "director_match": "match",
   "ubo_supported": True, "high_risk": False, "confidence": 0.91, "result": "pass"},
 "identity_checks": [
   idcheck("IND-0029", document_result="fail", liveness="pass", biometric="fail",
           address="pass", name_dob="mismatch", result="fail"),
   idcheck("IND-0030"), idcheck("IND-0031")],
 "screenings": [screen("applicant"), screen("individual", "IND-0030"),
                screen("individual", "IND-0031"),
                screen("individual", "IND-0029", sanctions="no_match", pep="no_match",
                       adverse_media="unavailable", severity="medium",
                       evidence_refs="SCREEN-REF-MEDIA-UNAVAILABLE-3301")],
 "risk": {"score": 0, "band": "insufficient_evidence",
   "recommended_action": "insufficient_evidence", "confidence": 0.42,
   "insufficient_evidence_flag": True, "requires_human_signoff": True, "factors": [
     ("identity_verification", 0.00,
      "The identity check on director IND-0029 failed on the document and biometric components "
      "and the name and date of birth did not match. That is a gap in the evidence, not a finding "
      "about the person.", "{IDC:IND-0029}"),
     ("media_coverage", 0.00,
      "The adverse-media provider did not return a result for the same director, so the file "
      "cannot be said to be clear on that point either.", "{SCR:IND-0029}"),
     ("document_quality", 0.00,
      "All fourteen documents passed the pre-screen at the first attempt.", "{PACK}")]},
 "evidence": {
   "applicant_summary": "Pemberly Tooling Ltd, UK private limited company UK-99034820, precision "
     "tooling, two directors, one 55 percent direct beneficial owner, 14 cards requested at "
     "around 21000 GBP per month.",
   "missing_or_conflicting_evidence": "Identity verification for director IND-0029 failed on the "
     "document and biometric components with a name and date-of-birth mismatch, and the "
     "adverse-media provider returned no result for the same individual. Neither gap has been "
     "filled, so no risk band can be assigned.",
   "recommended_next_action": "insufficient_evidence",
   "draft_compliance_narrative": "Internal compliance narrative, not for disclosure to the "
     "applicant. This case is scored as insufficient evidence rather than high risk, and the "
     "distinction matters: nothing adverse was found about anyone. The register corroborates the "
     "entity on every compared field and two of the three identity checks passed cleanly. The "
     "identity check on director IND-0029 failed on the document and biometric components with a "
     "name and date-of-birth mismatch, which most often means a poor or mismatched identity "
     "document rather than an impersonation attempt. Separately the adverse-media provider did "
     "not answer for that individual under reference SCREEN-REF-MEDIA-UNAVAILABLE-3301, so an "
     "absence of findings cannot be relied on. A provider non-answer is not a pass. The case "
     "needs a fresh identity document from that director and a re-run of the media check before "
     "any decision is possible.",
 },
 "decisions": [
   {"reviewer": "R. Toome", "reviewer_role": "analyst", "decision": "insufficient_evidence",
    "reason_code": "identity_failure_and_provider_non_answer",
    "rationale": "One identity check failed and one media check did not return. Neither is an "
      "adverse finding and neither can be treated as a pass. Holding the case as insufficient "
      "evidence and requesting a current identity document from the director concerned.",
    "evidence_relied_on": "{IDC:IND-0029}|{SCR:IND-0029}|{REG}|{RSK}",
    "override_flag": False, "customer_template_id": "TPL-0002"}],
 "communications": [
   {"template_id": "TPL-0002", "audience": "applicant", "message_type": "document_request",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "analyst",
    "approver": "analyst.r.toome",
    "rendered_text": "Hello Hadley Winstock, thank you for your application for Pemberly Tooling "
      "Ltd. To continue we need a current identity document for you. A passport or photocard "
      "driving licence works well, photographed in good light with all four corners visible. "
      "Once we have it we will pick the review straight back up."}],
})

# --------------------------------------------------------------- Case 14 ----
# The quiet ending: a resubmission was asked for, chased once, and never came.
# The case closes without any judgement being made about the applicant.
CASES.append({
 "case_id": "WAL-ONB-0014", "applicant_id": "APP-0014",
 "start": datetime(2026, 9, 26, 11, 40, 0),
 "applicant_type": "freelancer_sole_trader", "jurisdiction": "EE", "entity_scope": "wallester_as",
 "source_channel": "portal", "status": "closed_withdrawn", "next_action_owner": "system",
 "assigned_owner": "ops.tiina.kask",
 "applicant": {"legal_name": "Elise Kaarma", "trading_name": "Kaarma Ceramics",
   "registration_number": "EE-FIE-4471208", "entity_type": "sole_trader", "country": "EE",
   "business_activity": "Studio ceramics and small-batch tableware",
   "expected_usage": "One card; up to 1500 EUR per month on clay, glazes and kiln hire",
   "vat_registered": False,
   "risk_segment": "unassessed"},
 "individuals": [
   {"id": "IND-0032", "role": "sole_trader", "full_name": "Elise Kaarma", "dob": "1994-08-23",
    "nationality": "EE", "residence": "EE",
    "relationship": "Registered sole trader and sole beneficial owner"}],
 "ubos": [],
 "documents": [
   doc("registry_extract", "ee_fie_registry_extract_kaarma.pdf", issue_country="EE", fields=[
     ("company_name", "Elise Kaarma", 0.96, 1, False),
     ("registration_number", "EE-FIE-4471208", 0.95, 1, False),
     ("registered_address", "Vabriku 31-2, 10411 Tallinn, Estonia", 0.93, 1, False),
     ("entity_status", "active", 0.94, 1, False)]),
   doc("id_document", "id_card_kaarma_e.jpg", subject="IND-0032", expiry="2032-03-19",
       issue_country="EE", fields=[("full_name", "Elise Kaarma", 0.95, 1, False)]),
   doc("proof_of_address", "poa_kaarma_screenshot.jpg", quality="resubmission_required",
       subject="IND-0032", flags="screenshot_not_original", reasons="document_unreadable",
       issue_country="EE"),
   doc("source_of_funds_declaration", "source_of_funds_kaarma.pdf", fields=[
     ("declared_source", "Studio sales and commissioned tableware", 0.90, 1, False)]),
   doc("business_activity_description", "business_activity_kaarma.pdf", fields=[
     ("declared_industry", "Studio ceramics", 0.92, 1, False)]),
 ],
 "triggered_conditionals": [],
 "audit_after_quality": [
   {"actor_type": "system", "actor_id": "step.document_quality", "action": "resubmission_requested",
    "summary": "Proof of address returned as a screenshot rather than the original document; "
               "resubmission requested from the applicant"},
   {"actor_type": "system", "actor_id": "step.communication", "action": "applicant_chased",
    "summary": "No response after 14 days; single reminder sent under the approved template"},
   {"actor_type": "system", "actor_id": "step.communication", "action": "case_closed_no_response",
    "summary": "No response 30 days after the original request; case closed as withdrawn with no "
               "assessment made and no provider checks commissioned"}],
 "risk": {"score": 0, "band": "insufficient_evidence",
   "recommended_action": "withdrawn", "confidence": 0.30,
   "insufficient_evidence_flag": True, "requires_human_signoff": False, "factors": [
     ("document_completeness", 0.00,
      "The proof of address was a screenshot rather than the original and was never replaced, so "
      "the mandatory checklist was never completed.", "{PACK}"),
     ("applicant_engagement", 0.00,
      "One request and one reminder were sent and neither was answered within 30 days.",
      "{PACK}")]},
 "evidence": {
   "applicant_summary": "Elise Kaarma, Estonian sole trader EE-FIE-4471208, studio ceramics, one "
     "card requested at up to 1500 EUR per month.",
   "missing_or_conflicting_evidence": "Proof of address was supplied as a screenshot and was "
     "never replaced. No other item was outstanding.",
   "recommended_next_action": "withdrawn",
   "draft_compliance_narrative": "The case closed for want of one document. The proof of address "
     "was a screenshot of an online billing page rather than the original statement, which the "
     "pre-screen returned for resubmission. One request and one reminder were sent under approved "
     "templates and neither was answered. No registry, identity or screening check was ever "
     "commissioned, so no money was spent and nothing was assessed about the applicant. The case "
     "is closed as withdrawn and carries no adverse inference; a fresh application would start "
     "clean.",
 },
 "decisions": [
   {"reviewer": "T. Kask", "reviewer_role": "analyst", "decision": "withdrawn",
    "reason_code": "no_response_to_resubmission_request",
    "rationale": "The outstanding proof of address was requested and chased and did not arrive "
      "within 30 days. Closing as withdrawn. No assessment was made about the applicant and no "
      "provider checks were commissioned.",
    "evidence_relied_on": "{PACK}",
    "override_flag": False, "customer_template_id": "TPL-0012"}],
 "communications": [
   {"template_id": "TPL-0002", "audience": "applicant", "message_type": "document_request",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "analyst",
    "approver": "ops.tiina.kask",
    "rendered_text": "Hello Elise Kaarma, thank you for your application. The proof of address "
      "you sent is a screenshot, and we need the original document itself. A PDF downloaded from "
      "your bank or utility provider, or a photograph of the paper copy with all four corners "
      "visible, works well."},
   {"template_id": "TPL-0012", "audience": "applicant", "message_type": "case_closed",
    "approval_status": "approved", "sent_status": "sent", "approver_role": "analyst",
    "approver": "ops.tiina.kask",
    "rendered_text": "Hello Elise Kaarma, we have not heard back about the proof of address we "
      "asked for, so we have closed your application for now. Nothing is held against you and you "
      "are very welcome to apply again whenever it suits you."}],
})

for _c in CASES:
    build_case(_c)


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------
def write_csvs():
    for table in sorted(SCHEMA):
        path = os.path.join(OUT, table + ".csv")
        f = open(path, "w", newline="", encoding="utf-8")
        w = csv.DictWriter(f, fieldnames=SCHEMA[table], lineterminator="\n")
        w.writeheader()
        for row in ROWS[table]:
            w.writerow(row)
        f.close()
    print("Wrote %d CSV files to %s" % (len(SCHEMA), OUT))
    for table in sorted(SCHEMA):
        print("  %-28s %4d rows" % (table + ".csv", len(ROWS[table])))


if __name__ == "__main__":
    write_csvs()
