# Wallester UC4 dataset - validation report

Generated from `wallester_uc4_dataset/` by `validate_dataset.py`.

| # | Check | Result | Detail |
|---|-------|--------|--------|
| 1 | Referential integrity (31 FK relationships, 1981 non-null values) | PASS | no orphans |
| 2 | Enum values (60 enum columns, 2441 values incl. pipe-list members) | PASS | all values in range; no N/A placeholders |
| 3 | Timestamp ordering (14 cases, 456 timestamps) | PASS | every case ordered: created -> upload -> quality/extraction -> provider checks -> risk -> evidence pack -> communication/decision -> updated_at |
| 4 | Case outcomes (14 cases) | PASS | every case reaches its scripted status and risk band |
| 5 | Case 2 stopped before paid checks (bad-ID director IND-0002) | PASS | no registry, identity or screening rows exist for WAL-ONB-0002; UBO declaration checklist item is pending |
| 6 | No restricted wording in applicant-facing text (16 applicant messages, 1 on case 6) | PASS | no message mentions sanctions, screening, AML, PEP, adverse media, matches, escalation or risk scoring |
| 7 | Compliance and integrity rules (7a approval gate, 7b send gate, 7c override and escalation, 7d screening subject XOR, 7e sign-off flag, 7f white-label branch, 7g AI version stamps, 7h checklist derivability, 7i id formats and uniqueness) | PASS | all rules hold |

## Case outcome matrix (check 4)

| Case | Expected status | Actual status | Expected band | Actual band | Result |
|------|-----------------|---------------|---------------|-------------|--------|
| WAL-ONB-0001 | ready_for_decision | ready_for_decision | low | low | PASS |
| WAL-ONB-0002 | resubmission_required | resubmission_required | insufficient_evidence | insufficient_evidence | PASS |
| WAL-ONB-0003 | analyst_review_required | analyst_review_required | medium | medium | PASS |
| WAL-ONB-0004 | enhanced_due_diligence | enhanced_due_diligence | high | high | PASS |
| WAL-ONB-0005 | enhanced_due_diligence | enhanced_due_diligence | high | high | PASS |
| WAL-ONB-0006 | analyst_review_required | analyst_review_required | critical | critical | PASS |
| WAL-ONB-0007 | analyst_review_required | analyst_review_required | high | high | PASS |
| WAL-ONB-0008 | submitted | submitted | (none) | (none) | PASS |
| WAL-ONB-0009 | approved | approved | low | low | PASS |
| WAL-ONB-0010 | ready_for_decision | ready_for_decision | low | low | PASS |
| WAL-ONB-0011 | rejected | rejected | high | high | PASS |
| WAL-ONB-0012 | analyst_review_required | analyst_review_required | critical | critical | PASS |
| WAL-ONB-0013 | analyst_review_required | analyst_review_required | insufficient_evidence | insufficient_evidence | PASS |
| WAL-ONB-0014 | closed_withdrawn | closed_withdrawn | insufficient_evidence | insufficient_evidence | PASS |

## Row counts

| Table | Rows |
|-------|------|
| applicant.csv | 14 |
| audit_event.csv | 244 |
| case_map.csv | 14 |
| checklist_item.csv | 215 |
| checklist_item_document.csv | 168 |
| communication.csv | 21 |
| document.csv | 168 |
| evidence_pack.csv | 13 |
| extracted_field.csv | 256 |
| human_decision.csv | 10 |
| identity_check.csv | 28 |
| individual.csv | 32 |
| message_template.csv | 12 |
| onboarding_case.csv | 14 |
| registry_check.csv | 11 |
| requirement_pack.csv | 14 |
| requirement_rule.csv | 86 |
| risk_assessment.csv | 13 |
| risk_bands.csv | 10 |
| risk_factor.csv | 22 |
| risk_scoring_matrix.csv | 19 |
| screening_check.csv | 39 |
| ubo.csv | 11 |

**Overall: PASS - all 7 checks green**
