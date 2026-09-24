# Wallester UC4 — KYC/KYB Onboarding Orchestration POC: synthetic dataset

Correlated synthetic data for the POC Scope Brief *"Onboarding KYC/KYB Orchestration Assessment for
Wallester"*, covering all eight demo cases in section 8.1, the scenario patterns in section 14, and
two control cases.

Everything here is fictional. No real person, company, registration number, registry, identity
provider, screening provider or sanctioned entity appears in the data. All provider names
(`MockRegistryHub EE`, `MockRegistryHub UK`, `VerifyMock ID`, `ScreenMock Global`) are invented.

## What is in the box

20 data tables plus a scenario map and the validation report. 1 060 data rows in total, every one of
which belongs to one of the ten scripted cases.

| File | Rows | Cases that use it |
|------|------|-------------------|
| `onboarding_case.csv` | 10 | 1–10 |
| `applicant.csv` | 10 | 1–10 |
| `individual.csv` | 23 | 1–10 |
| `ubo.csv` | 8 | 3–7, 9, 10 |
| `document.csv` | 124 | 1–10 |
| `extracted_field.csv` | 188 | 1–10 |
| `requirement_rule.csv` | 86 | reference data — 4 applicant types × EE and UK |
| `requirement_pack.csv` | 10 | 1–10 |
| `checklist_item.csv` | 161 | 1–10 |
| `checklist_item_document.csv` | 124 | 1–10 |
| `registry_check.csv` | 8 | 1, 3–7, 9, 10 |
| `identity_check.csv` | 20 | 1, 3–7, 9, 10 |
| `screening_check.csv` | 28 | 1, 3–7, 9, 10 |
| `risk_assessment.csv` | 9 | 1–7, 9, 10 |
| `risk_factor.csv` | 42 | 1–7, 9, 10 |
| `evidence_pack.csv` | 9 | 1–7, 9, 10 |
| `human_decision.csv` | 6 | 3–7, 9 |
| `message_template.csv` | 10 | reference data — the compliance-approved message library |
| `communication.csv` | 15 | 1–10 |
| `audit_event.csv` | 169 | 1–10 |
| `case_map.csv` | 10 | the demo answer key: case → scenario, expected outcome, key evidence ids |
| `validation_report.md` | — | output of `validate_dataset.py` |

Cases 2 and 8 deliberately have **no** provider-check rows, and case 8 has no risk assessment,
decision or evidence pack. Those absences are the point of those two scenarios, not gaps.

## The ten cases

| Case | Story | Status | Risk band | Next action |
|------|-------|--------|-----------|-------------|
| WAL-ONB-0001 | Low-risk EE freelancer, complete documents | `ready_for_decision` | `low` | analyst |
| WAL-ONB-0002 | UK SME, unreadable director ID + missing UBO declaration, stopped before paid checks | `resubmission_required` | `insufficient_evidence` | customer |
| WAL-ONB-0003 | UK corporate, registry address mismatch | `analyst_review_required` | `medium` | customer |
| WAL-ONB-0004 | EE complex corporate, unsupported indirect ownership chain | `enhanced_due_diligence` | `high` | compliance |
| WAL-ONB-0005 | UK SME, PEP match + moderate adverse media on the 55 % owner | `enhanced_due_diligence` | `high` | compliance |
| WAL-ONB-0006 | EE SME, possible sanctions match on a director | `analyst_review_required` | `critical` | compliance |
| WAL-ONB-0007 | UK SME, serious adverse media on the sole director | `enhanced_due_diligence` | `high` | compliance |
| WAL-ONB-0008 | EE white-label partner, KYB intake only, routed to the future-phase branch | `submitted` | — | system |
| WAL-ONB-0009 | Control: clean EE SME, approved by an analyst | `approved` | `low` | system |
| WAL-ONB-0010 | Control: clean UK corporate on the complex-ownership path | `ready_for_decision` | `low` | analyst |

Jurisdiction mix: EE 5 cases (4 `wallester_as`, 1 `undetermined`), UK 5 cases (`wallester_uk_ltd`).
Intake channel mix: 8 portal, 2 email.

Case 10 is the negative control for case 4: the same `complex_corporate_ubo` requirement pack, the
same ownership-chart, shareholder-register and source-of-wealth requirements, and an indirect chain
of the same shape — but one UK holding company that is active on the register and wholly owned by
the beneficial owner, so the registry supports the chain end to end. The difference in outcome comes
from the evidence, not from the applicant type.

Applicant type is decided by structure alone: a chain with an intermediate company is
`complex_corporate_ubo` (cases 4 and 10), a directly held company is `sme_corporate`. Case 5 is
directly held and therefore an SME; what makes it interesting is the PEP finding on its owner, which
is a risk signal rather than an applicant-type signal.

## Join keys

Every case is reconstructable end to end from `case_id`. The full FK map is in `validation_report.md`
check 1 (31 relationships, all verified). The chains that matter for a demo:

```
onboarding_case.case_id
   -> requirement_pack.case_id -> checklist_item.pack_id -> checklist_item.rule_id -> requirement_rule
                                                         -> checklist_item_document -> document
   -> document.case_id -> extracted_field.document_id
   -> registry_check.case_id / identity_check.case_id / screening_check.case_id
   -> risk_assessment.case_id -> risk_factor.assessment_id
   -> evidence_pack.case_id (-> assessment_id, -> decision_id)
   -> communication.case_id -> message_template.template_id
   -> human_decision.case_id -> customer_template_id -> message_template
   -> audit_event.case_id
applicant.applicant_id -> individual.applicant_id -> ubo.individual_id
```

`risk_factor.evidence_refs`, `human_decision.evidence_relied_on` and the free-text evidence-pack
fields carry real ids from the other tables (pipe-separated), so a reviewer can follow a stated
reason back to the row it came from.

## Reproducing

```
python generate_dataset.py     # writes every CSV into wallester_uc4_dataset/
python make_case_map.py        # writes case_map.csv
python validate_dataset.py     # runs the 7 checks, writes validation_report.md, exits 1 on failure
```

The generator is fully deterministic — no random number generator is used anywhere, so a rerun
reproduces byte-identical files. Cases are scripted as data in `generate_dataset.py` (`CASES`), and
checklist items are **derived** from `requirement_rule` at generation time rather than written by
hand, so the requirement matrix and the checklists cannot drift apart.

## Compliance properties the data holds

Verified mechanically by `validate_dataset.py` check 7:

- No case at risk band medium, high or critical, and no case with a sanctions or PEP finding,
  reaches status `approved` without a `human_decision` row.
- No applicant-facing communication has `sent_status = sent` unless `approval_status = approved`.
  Case 4 carries a deliberate counter-example: a `verification_delay` draft sitting at
  `pending_approval` / `not_sent`.
- No applicant-facing `rendered_text` contains sanctions, screening, AML, PEP, adverse-media,
  financial-crime, escalation, match or risk-scoring wording. Cases 5, 6 and 7 all use the generic
  `manual_review_underway` template (TPL-0004) towards the customer while the internal narrative
  states the finding in full.
- `override_flag = true` always carries an `override_reason` (one case: WAL-ONB-0007), and every
  `escalate` decision carries an `escalation_target` (WAL-ONB-0006, WAL-ONB-0007).
- Every `ai_agent` audit event carries a `model_or_prompt_version`; human steps are logged with
  `actor_type` `analyst` or `compliance`.
- `screening_check` fills exactly one of `applicant_id` / `individual_id`, matching `subject_type`.
- Case 8 (white-label) has zero rows in `risk_assessment`, `human_decision`, `screening_check`,
  `registry_check`, `identity_check` and `evidence_pack`, and carries a
  `routed_to_white_label_future_phase` audit event naming the future-phase steps.

## Deliberate OCR imperfections (brief section 8)

`extracted_field` carries four values below the 0.70 confidence threshold, each one load-bearing for
its scenario rather than decorative:

| Field | Case | Confidence | Corrected | Why it is there |
|-------|------|-----------|-----------|-----------------|
| `registered_address` (FLD-0040) | 3 | 0.58 | **yes** | The address mismatch had to survive an analyst correction to count as a real data conflict rather than an OCR artefact |
| `indirect_ownership_path` (FLD-0072) | 4 | 0.41 | no | The ownership chain is asserted at low confidence and uncorroborated — one of the reasons the case goes to EDD |
| `intermediate_entity` (FLD-0074) | 4 | 0.55 | no | Read from the incomplete ownership chart |
| `director_name` (FLD-0131) | 7 | 0.64 | no | Read from a scanned register; corroborated against the ID document instead of corrected |

## Assumptions made where the brief is silent

The brief specifies the canonical entities and their key fields but not every vocabulary. Where a
value set was not stated, it was chosen here and is listed so a reviewer can challenge it:

1. **`jurisdiction_path` = `EE` | `UK`.** The brief calls for a jurisdiction-aware path but does not
   give its values. Two-letter codes were used so `onboarding_case.jurisdiction_path` joins directly
   to `requirement_rule.jurisdiction` and `requirement_pack.jurisdiction`.
2. **`entity_type` = `sole_trader` | `private_limited_company`.** Needed as the third selector for
   requirement rules. Kept deliberately small.
3. **`communication.audience` = `applicant` | `analyst` | `compliance`**;
   **`approval_status` = `approved` | `pending_approval` | `rejected`**;
   **`sent_status` = `sent` | `not_sent` | `failed`**. Not enumerated in the brief.
4. **`screening_check.subject_type` = `applicant` | `individual`**, matching the two nullable subject
   columns that replace the ER diagram's polymorphic `subject_id`.
5. **`ubo.control_type` / `verification_status`** and **`applicant.risk_segment`** are free-text in
   the brief; controlled vocabularies were invented for them.
6. **Case 2 has no registry check either.** The brief says bad documents are stopped "before paid
   checks"; registry validation is a paid provider call, so no registry, identity or screening rows
   were produced for that case, not just none for the director with the bad ID. The suppression is
   recorded as its own audit event (`paid_checks_suppressed`).
7. **Case 6 risk band.** The brief fixes the status (`analyst_review_required`) and the human
   sign-off requirement but not the band. A possible sanctions match is scored `critical` here.
8. **Case 7 status.** Not specified in the brief beyond "analyst escalation"; scored `high` and set
   to `enhanced_due_diligence`, consistent with the section 5.8 mapping of high risk to EDD with
   analyst sign-off.
9. **Case 8 `entity_scope` is `undetermined`.** A partner programme's AS/UK Ltd perimeter is decided
   during programme design, not at KYB intake. This also exercises the third `entity_scope` value.
10. **`evidence_pack.decision_id` is populated after the decision it references.** The pack is
    generated before the reviewer decides (its `generated_at` proves this) and the row is updated
    with the decision id once taken — the CSV shows the final state of the row.
11. **Case 9 records an analyst approval even though `requires_human_signoff` is `false`.** Low-risk
    auto-approval is called out in brief section 18 as a claim to avoid, so no case in this dataset
    reaches `approved` without a human decision.
12. **All values are ASCII.** Estonian company suffixes are written `OU` rather than `OÜ` so the CSVs
    open cleanly in any tool regardless of encoding settings.

## Enum values not exercised

Scripted from the ten cases rather than padded for coverage, so some allowed values do not appear.
They are listed here so the omission is visible rather than accidental: statuses
`document_quality_review`, `verification_in_progress`, `rejected`, `closed_withdrawn`;
`next_action_owner` `external_provider`; quality flags `cut_off_pages`, `expired`,
`screenshot_not_original`, `name_mismatch`, `tampering_indicator`, `unsupported_file_type`;
`quality_status` `pending`; `company_status` other than `active`; `unavailable` on any provider
field; `sanctions_result` `clear_match`; `pep_result` `close_associate_family`;
`adverse_media_result` `low_relevance`; severity `low` and `medium`; decisions
`conditional_approve`, `reject`, `withdrawn`, `insufficient_evidence`; checklist status `received`.

Adding them would mean inventing cases the brief does not ask for. If the demo needs them — a
dissolved company, an expired ID, a provider timeout, a rejection — say which, and they can be added
as further scripted cases rather than as loose rows.
