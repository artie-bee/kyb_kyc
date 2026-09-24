"""
Wallester UC4 schema: every table, column and enum in the dataset and in
the orchestrator database.

Generated to match both exactly. tests/test_schema_sync.py fails if a column
or an enum value appears in the data but not here, so the two cannot drift
apart silently.

  TABLES     dataset CSV -> columns, in file order
  ENUMS      (table, column) -> allowed values
  DB_TABLES  orchestrator SQLite table -> columns
  DB_ENUMS   enums that exist only in the database
"""

TABLES = {
    "applicant": [
        "applicant_id", "legal_name", "trading_name", "registration_number", "entity_type",
        "country", "business_activity", "expected_usage", "vat_registered", "risk_segment",
    ],
    "audit_event": [
        "event_id", "case_id", "actor_type", "actor_id", "action", "payload_summary",
        "model_or_prompt_version", "timestamp",
    ],
    "case_map": [
        "case_id", "case_label", "brief_section", "applicant_id", "applicant_name",
        "applicant_type", "jurisdiction_path", "entity_scope", "source_channel",
        "expected_status", "expected_risk_band", "next_action_owner", "what_it_demonstrates",
        "key_evidence_ids",
    ],
    "checklist_item": [
        "item_id", "pack_id", "rule_id", "subject_individual_id", "document_type", "level",
        "status", "resubmission_attempts",
    ],
    "checklist_item_document": [
        "item_id", "document_id",
    ],
    "communication": [
        "communication_id", "case_id", "template_id", "audience", "message_type",
        "approval_status", "sent_status", "rendered_text", "created_at",
    ],
    "document": [
        "document_id", "case_id", "subject_individual_id", "document_type", "file_name",
        "upload_time", "quality_status", "quality_flags", "expiry_date", "issue_country",
        "resubmission_required", "resubmission_reasons",
    ],
    "evidence_pack": [
        "evidence_pack_id", "case_id", "assessment_id", "decision_id", "generated_at",
        "applicant_summary", "missing_or_conflicting_evidence", "recommended_next_action",
        "draft_compliance_narrative",
    ],
    "extracted_field": [
        "field_id", "document_id", "name", "value", "confidence", "source_page",
        "corrected_by_analyst",
    ],
    "human_decision": [
        "decision_id", "case_id", "reviewer", "reviewer_role", "decision", "reason_code",
        "rationale", "evidence_relied_on", "override_flag", "override_reason",
        "override_direction", "escalation_target", "customer_template_id", "timestamp",
    ],
    "identity_check": [
        "check_id", "case_id", "individual_id", "provider_name", "document_result",
        "liveness_result", "biometric_result", "address_result", "name_dob_match",
        "document_expired", "duplicate_individual_detected", "result",
    ],
    "individual": [
        "individual_id", "applicant_id", "role", "full_name", "date_of_birth", "nationality",
        "residence_country", "id_document_id", "relationship_to_entity",
    ],
    "message_template": [
        "template_id", "message_type", "audience", "template_text", "version",
        "approval_status",
    ],
    "onboarding_case": [
        "case_id", "applicant_id", "applicant_type", "jurisdiction_path", "entity_scope",
        "source_channel", "status", "assigned_owner", "next_action_owner",
        "white_label_branch_flag", "created_at", "updated_at",
    ],
    "registry_check": [
        "check_id", "case_id", "applicant_id", "provider_name", "company_status",
        "registry_legal_name", "registry_number", "registry_address", "registry_directors",
        "name_match", "number_match", "address_match", "director_match",
        "ubo_supported_by_registry", "high_risk_jurisdiction_or_industry", "confidence",
        "result",
    ],
    "requirement_pack": [
        "pack_id", "case_id", "applicant_type", "jurisdiction", "entity_type", "kb_version",
    ],
    "requirement_rule": [
        "rule_id", "applicant_type", "jurisdiction", "entity_type", "requirement_area",
        "document_type", "level", "condition", "condition_key", "applies_per_individual_role",
        "max_age_days",
    ],
    "risk_assessment": [
        "assessment_id", "case_id", "risk_score", "risk_band", "recommended_action",
        "confidence", "insufficient_evidence_flag", "requires_human_signoff",
        "risk_matrix_version",
    ],
    "risk_bands": [
        "band_id", "band", "min_score", "max_score", "hard_floor_condition",
        "recommended_action", "description",
    ],
    "risk_factor": [
        "factor_id", "assessment_id", "factor", "weight", "explanation", "evidence_refs",
    ],
    "risk_scoring_matrix": [
        "factor_id", "factor", "source", "condition", "points", "description", "weight_status",
    ],
    "screening_check": [
        "check_id", "case_id", "subject_type", "applicant_id", "individual_id",
        "sanctions_result", "pep_result", "adverse_media_result", "severity", "evidence_refs",
    ],
    "ubo": [
        "ubo_id", "applicant_id", "individual_id", "ownership_percentage",
        "ownership_chain_percentages", "control_type", "ownership_path", "verification_status",
    ],
}

ENUMS = {
    ("applicant", "entity_type"): [
        "sole_trader", "private_limited_company",
    ],
    ("applicant", "risk_segment"): [
        "low", "standard", "elevated", "unassessed",
    ],
    ("applicant", "vat_registered"): [
        "true", "false",
    ],
    ("audit_event", "actor_type"): [
        "system", "ai_agent", "analyst", "compliance", "applicant", "external_provider",
    ],
    ("checklist_item", "level"): [
        "required", "optional", "conditional",
    ],
    ("checklist_item", "status"): [
        "pending", "received", "accepted", "resubmission_requested", "manual_review", "waived",
    ],
    ("communication", "approval_status"): [
        "approved", "pending_approval", "rejected",
    ],
    ("communication", "audience"): [
        "applicant", "analyst", "compliance",
    ],
    ("communication", "sent_status"): [
        "sent", "not_sent", "failed",
    ],
    ("document", "quality_flags"): [
        "blurred_unreadable", "cut_off_pages", "expired", "missing_pages",
        "screenshot_not_original", "name_mismatch", "tampering_indicator",
        "unsupported_file_type", "wrong_document_type", "document_too_old",
    ],
    ("document", "quality_status"): [
        "pending", "accepted_for_checks", "resubmission_required", "manual_review_required",
    ],
    ("document", "resubmission_reasons"): [
        "document_unreadable", "document_expired", "ubo_declaration_missing",
        "registered_address_mismatch", "director_identity_missing",
        "source_of_funds_clarification", "ownership_structure_unclear",
        "proof_of_address_too_old",
    ],
    ("document", "resubmission_required"): [
        "true", "false",
    ],
    ("evidence_pack", "recommended_next_action"): [
        "approve", "conditional_approve", "enhanced_due_diligence", "reject",
        "request_more_information", "escalate", "withdrawn", "insufficient_evidence",
    ],
    ("extracted_field", "corrected_by_analyst"): [
        "true", "false",
    ],
    ("human_decision", "decision"): [
        "approve", "conditional_approve", "enhanced_due_diligence", "reject",
        "request_more_information", "escalate", "withdrawn", "insufficient_evidence",
    ],
    ("human_decision", "override_flag"): [
        "true", "false",
    ],
    ("human_decision", "reviewer_role"): [
        "analyst", "compliance",
    ],
    ("identity_check", "address_result"): [
        "pass", "fail", "review", "unavailable",
    ],
    ("identity_check", "biometric_result"): [
        "pass", "fail", "review", "unavailable",
    ],
    ("identity_check", "document_expired"): [
        "true", "false",
    ],
    ("identity_check", "document_result"): [
        "pass", "fail", "review", "unavailable",
    ],
    ("identity_check", "duplicate_individual_detected"): [
        "true", "false",
    ],
    ("identity_check", "liveness_result"): [
        "pass", "fail", "review", "unavailable",
    ],
    ("identity_check", "name_dob_match"): [
        "match", "mismatch", "unavailable",
    ],
    ("identity_check", "result"): [
        "pass", "fail", "review", "unavailable",
    ],
    ("individual", "role"): [
        "director", "ubo", "authorised_signatory", "authorised_user", "sole_trader",
    ],
    ("message_template", "approval_status"): [
        "approved", "pending_approval", "rejected",
    ],
    ("message_template", "audience"): [
        "applicant", "analyst", "compliance",
    ],
    ("onboarding_case", "applicant_type"): [
        "freelancer_sole_trader", "sme_corporate", "complex_corporate_ubo",
        "white_label_partner",
    ],
    ("onboarding_case", "entity_scope"): [
        "wallester_as", "wallester_uk_ltd", "undetermined",
    ],
    ("onboarding_case", "jurisdiction_path"): [
        "EE", "UK",
    ],
    ("onboarding_case", "next_action_owner"): [
        "customer", "analyst", "compliance", "system", "external_provider",
    ],
    ("onboarding_case", "source_channel"): [
        "portal", "email",
    ],
    ("onboarding_case", "status"): [
        "submitted", "document_quality_review", "resubmission_required",
        "verification_in_progress", "analyst_review_required", "enhanced_due_diligence",
        "ready_for_decision", "approved", "rejected", "closed_withdrawn",
    ],
    ("onboarding_case", "white_label_branch_flag"): [
        "true", "false",
    ],
    ("registry_check", "address_match"): [
        "match", "mismatch", "unavailable",
    ],
    ("registry_check", "company_status"): [
        "active", "dissolved", "struck_off", "suspended", "not_found", "unavailable",
    ],
    ("registry_check", "director_match"): [
        "match", "mismatch", "unavailable",
    ],
    ("registry_check", "high_risk_jurisdiction_or_industry"): [
        "true", "false",
    ],
    ("registry_check", "name_match"): [
        "match", "mismatch", "unavailable",
    ],
    ("registry_check", "number_match"): [
        "match", "mismatch", "unavailable",
    ],
    ("registry_check", "result"): [
        "pass", "fail", "review", "unavailable",
    ],
    ("registry_check", "ubo_supported_by_registry"): [
        "true", "false",
    ],
    ("requirement_pack", "applicant_type"): [
        "freelancer_sole_trader", "sme_corporate", "complex_corporate_ubo",
        "white_label_partner",
    ],
    ("requirement_pack", "jurisdiction"): [
        "EE", "UK",
    ],
    ("requirement_rule", "applicant_type"): [
        "freelancer_sole_trader", "sme_corporate", "complex_corporate_ubo",
        "white_label_partner",
    ],
    ("requirement_rule", "jurisdiction"): [
        "EE", "UK",
    ],
    ("requirement_rule", "level"): [
        "required", "optional", "conditional",
    ],
    ("risk_assessment", "insufficient_evidence_flag"): [
        "true", "false",
    ],
    ("risk_assessment", "recommended_action"): [
        "approve", "conditional_approve", "enhanced_due_diligence", "reject",
        "request_more_information", "escalate", "withdrawn", "insufficient_evidence",
    ],
    ("risk_assessment", "requires_human_signoff"): [
        "true", "false",
    ],
    ("risk_assessment", "risk_band"): [
        "low", "medium", "high", "critical", "insufficient_evidence",
    ],
    ("screening_check", "adverse_media_result"): [
        "none", "low_relevance", "moderate", "serious", "unavailable",
    ],
    ("screening_check", "pep_result"): [
        "no_match", "pep_match", "close_associate_family", "unavailable",
    ],
    ("screening_check", "sanctions_result"): [
        "no_match", "possible_match", "clear_match", "unavailable",
    ],
    ("screening_check", "severity"): [
        "none", "low", "medium", "high", "critical",
    ],
    ("screening_check", "subject_type"): [
        "applicant", "individual",
    ],
    ("ubo", "control_type"): [
        "direct_shareholding", "indirect_shareholding", "voting_rights", "other_control",
    ],
    ("ubo", "verification_status"): [
        "verified", "unverified", "pending",
    ],
}

DB_TABLES = {
    "applicant": [
        "applicant_id", "legal_name", "trading_name", "registration_number", "entity_type",
        "country", "business_activity", "expected_usage", "risk_segment",
    ],
    "audit_event": [
        "event_id", "case_id", "actor_type", "actor_id", "action", "payload_summary",
        "model_or_prompt_version", "timestamp",
    ],
    "case_hold": [
        "hold_id", "case_id", "placed_by_step", "reason", "owner", "placed_at", "released_by",
        "release_reason", "released_at",
    ],
    "checklist_item": [
        "item_id", "pack_id", "rule_id", "subject_individual_id", "document_type", "level",
        "status", "resubmission_attempts", "note",
    ],
    "checklist_item_document": [
        "item_id", "document_id",
    ],
    "communication": [
        "communication_id", "case_id", "template_id", "audience", "message_type", "situation",
        "approval_status", "approved_by", "sent_status", "rendered_text", "created_at",
    ],
    "compliance_task": [
        "task_id", "case_id", "task", "reason", "owner", "created_at", "completed_by",
        "completed_at",
    ],
    "document": [
        "document_id", "case_id", "subject_individual_id", "document_type", "file_name",
        "upload_time", "quality_status", "quality_status_at_screen", "quality_flags",
        "expiry_date", "document_date", "issue_country", "resubmission_required",
        "resubmission_reasons", "released_by", "release_reason",
    ],
    "evidence_pack": [
        "evidence_pack_id", "case_id", "assessment_id", "applicant_summary", "entity_details",
        "individuals", "ubos", "checklist_completeness", "provider_results", "risk_factors",
        "open_holds", "missing_or_conflicting_evidence", "recommended_next_action",
        "draft_compliance_narrative", "evidence_refs", "created_at",
    ],
    "extracted_field": [
        "field_id", "document_id", "name", "value", "confidence", "source_page",
        "corrected_by_analyst", "needs_analyst_correction",
    ],
    "finding": [
        "finding_id", "case_id", "source", "rule_id", "summary", "evidence_refs", "blocking",
        "created_at",
    ],
    "human_decision": [
        "decision_id", "case_id", "reviewer", "reviewer_role", "decision", "reason_code",
        "rationale", "evidence_relied_on", "override_flag", "override_reason",
        "override_direction", "escalation_target", "customer_template_id", "timestamp",
    ],
    "identity_check": [
        "check_id", "case_id", "individual_id", "provider_name", "document_result",
        "liveness_result", "biometric_result", "address_result", "name_dob_match",
        "document_expired", "duplicate_individual_detected", "result",
    ],
    "individual": [
        "individual_id", "applicant_id", "role", "full_name", "date_of_birth", "nationality",
        "residence_country", "id_document_id", "relationship_to_entity",
    ],
    "onboarding_case": [
        "case_id", "applicant_id", "applicant_type", "jurisdiction_path", "entity_scope",
        "source_channel", "status", "assigned_owner", "next_action_owner",
        "white_label_branch_flag", "restricted_finding", "requires_human_signoff", "created_at",
        "updated_at",
    ],
    "outbox": [
        "outbox_id", "communication_id", "case_id", "audience", "body", "sent_at",
    ],
    "registry_check": [
        "check_id", "case_id", "applicant_id", "provider_name", "company_status",
        "registry_legal_name", "registry_number", "registry_address", "registry_directors",
        "name_match", "number_match", "address_match", "director_match",
        "ubo_supported_by_registry", "high_risk_jurisdiction_or_industry", "confidence",
        "result", "attempts",
    ],
    "requirement_pack": [
        "pack_id", "case_id", "applicant_type", "jurisdiction", "entity_type", "kb_version",
    ],
    "risk_assessment": [
        "assessment_id", "case_id", "risk_score", "risk_band", "recommended_action",
        "confidence", "insufficient_evidence_flag", "requires_human_signoff",
        "risk_matrix_version",
    ],
    "risk_factor": [
        "factor_id", "assessment_id", "factor", "weight", "explanation", "evidence_refs",
    ],
    "screening_check": [
        "check_id", "case_id", "subject_type", "applicant_id", "individual_id", "provider_name",
        "sanctions_result", "pep_result", "adverse_media_result", "severity", "evidence_refs",
        "attempts",
    ],
    "ubo": [
        "ubo_id", "applicant_id", "individual_id", "ownership_percentage",
        "ownership_chain_percentages", "control_type", "ownership_path", "verification_status",
    ],
}

# Columns the orchestrator keeps that the dataset has no column for.
DB_ONLY = {
    ("checklist_item", "note"): "analyst-facing note from Step 2 or 3",
    ("document", "quality_status_at_screen"): "the Step 3 verdict, kept when an analyst release moves quality_status",
    ("document", "release_reason"): "why it was released",
    ("document", "released_by"): "analyst who released a held document",
    ("extracted_field", "needs_analyst_correction"): "value missing or below the confidence floor",
    ("registry_check", "attempts"): "provider calls made, for the RG-09 retry",
}

DB_ENUMS = {
    ("document", "quality_status_at_screen"): ["pending", "accepted_for_checks", "resubmission_required", "manual_review_required"],
    ("finding", "source"): ["registry", "identity", "ubo"],
}



def json_schema() -> dict:
    """Export the same thing as JSON Schema, one object definition per table."""
    def table_schema(cols, table, enum_source):
        props = {}
        for c in cols:
            prop = {"type": ["string", "null"]}
            values = enum_source.get((table, c))
            if values:
                prop["enum"] = sorted(values) + [""]
            props[c] = prop
        return {"type": "object", "properties": props,
                "required": list(cols), "additionalProperties": False}

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Wallester UC4 dataset and orchestrator database",
        "type": "object",
        "properties": {
            "dataset": {
                "type": "object",
                "properties": {t: {"type": "array",
                                   "items": table_schema(cols, t, ENUMS)}
                               for t, cols in TABLES.items()},
                "additionalProperties": False,
            },
            "database": {
                "type": "object",
                "properties": {t: {"type": "array",
                                   "items": table_schema(cols, t, {**ENUMS, **DB_ENUMS})}
                               for t, cols in DB_TABLES.items()},
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


if __name__ == "__main__":
    import json as _json
    import pathlib as _pathlib
    out = _pathlib.Path(__file__).with_suffix(".json")
    out.write_text(_json.dumps(json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} - {len(TABLES)} dataset tables, {len(DB_TABLES)} database tables, "
          f"{len(ENUMS) + len(DB_ENUMS)} enum columns")
