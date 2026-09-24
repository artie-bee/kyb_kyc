# Wallester UC4 - architecture

A KYB/KYC onboarding pipeline. Eight steps, each one small, each reading and
writing named tables, with every rule that could reasonably change living in a
CSV rather than in code.

Two ideas hold it together:

- **The knowledge base decides, the code applies.** Which documents a case
  needs, what a quality flag means, what a risk factor is worth, who may take
  which decision - all of it is data in `kb/`. Changing a rule is a CSV edit and
  a version bump, not a release.
- **A step may stop a case but not clear it.** Steps place *holds*; only the
  step that placed one, or a named human, may lift it. Case status is derived
  from the open holds, never set by a step that has finished its own work.

---

## The eight steps

| Step | Module | Reads | Writes |
|---|---|---|---|
| 1 Intake | `steps/intake.py` | the application; `applicant_type_rules`, `jurisdiction_routing` | `applicant`, `onboarding_case`, `individual`, `ubo` |
| 2 Requirement pack | `steps/requirement_pack.py` | the case; `requirement_rule` | `requirement_pack`, `checklist_item` |
| 3 Document quality | `steps/document_quality.py` | uploaded documents; `document_quality_rules` | `document`, `checklist_item_document`, `case_hold` |
| 4 Extraction | `steps/extraction.py` | **accepted documents only**; `extraction_fields` | `extracted_field`, `case_hold` |
| 5 Verification | `steps/verification.py` | extracted fields, providers; `registry_rules`, `ubo_policy` | `registry_check`, `identity_check`, `ubo`, `finding`, `case_hold` |
| 6 Screening | `steps/screening.py` | subjects, providers; `screening_rules`, `adverse_media_categories` | `screening_check`, `finding`, `onboarding_case` flags, `case_hold` |
| 7 Risk + pack | `steps/risk_assessment.py`, `steps/evidence_pack.py` | `finding` and case data; `risk_scoring_matrix`, `risk_bands` | `risk_assessment`, `risk_factor`, `evidence_pack` |
| 8 Comms + decision | `steps/communication.py`, `steps/decision.py` | `message_template`, `communication_rules`, `analyst_decision_taxonomy` | `communication`, `outbox`, `compliance_task`, `human_decision` |

`steps/analyst_review.py` sits alongside Step 3: it is how a named analyst
releases a document the quality screen held.

### The boundaries that matter

- **Step 4 reads documents only through `document_quality.accepted_documents()`.**
  A file that failed the screen cannot be extracted.
- **Step 5 opens with a gate.** It refuses to run unless every required
  checklist item is accepted. It is the first step that spends money, and a case
  still owing a document would only have to repeat the calls.
- **Steps 6 and 7 run even when the case is held**, so an analyst opens one
  queue item with the registry, identity, screening and risk picture together.
- **Step 8 approves nothing over an open hold**, and nothing is ever approved
  automatically - a low band *recommends* approval and a person still makes it.

---

## Holds

`orchestrator/holds.py`. One table, `case_hold`, and four functions.

```
place(conn, case_id, step, code, reason, owner)     any step may place
release(conn, hold_id, who, reason, by_step=None)   only the placer, or a human
open_holds(conn, case_id)                           what is outstanding
apply_status(conn, case_id, clear_status, owner)    derive the case status
```

A step lifting its own hold passes `by_step` and it must match. A human release
leaves it out and must name a person; a step trying to pass as one is rejected.

Precedence when several are open - the worst owns the case:

```
compliance  >  analyst  >  insufficient evidence  >  customer
```

Reason codes carry the category: `sanctions_escalation`, `eligibility`,
`manual_review`, `identity_failed`, `insufficient_evidence`, `resubmission`.
`insufficient_evidence` means *could not be determined* - a provider that did
not answer, or evidence still outstanding. A failed identity check is a result,
not an absence of one, so it has its own code and scores as a risk factor.

---

## The knowledge base

| File | What it decides |
|---|---|
| `applicant_type_rules.csv` | freelancer / SME / complex / white-label |
| `jurisdiction_routing.csv` | country -> EE or UK rule set and legal entity |
| `requirement_rule.csv` | which documents each case type needs |
| `document_quality_rules.csv` | what each quality flag means and who it goes to |
| `extraction_fields.csv` | which fields each document type must yield |
| `registry_rules.csv` | which registry outcomes block and which are findings |
| `ubo_policy.csv` | the 25% threshold and how chains are multiplied |
| `screening_rules.csv` | sanctions and PEP outcomes |
| `adverse_media_categories.csv` | media relevance categories |
| `risk_scoring_matrix.csv` | risk factors and weights - **all placeholders** |
| `risk_bands.csv` | score thresholds and the hard floors that override them |
| `message_template.csv` | the approved message library |
| `communication_rules.csv` | which template for which situation |
| `analyst_decision_taxonomy.csv` | who may decide what, at which band, and how strict each outcome is |
| `audit_log_standard.csv` | the audit actions every case must carry |
| `communication_schedule.csv` | reminder and closure timing |

`kb_manifest.json` carries the version written into every audit row.

**Every risk weight is marked `poc_placeholder - Wallester to confirm`.** The
brief does not state them, and a number nobody has agreed should not look like
one that has.

---

## Mock and live

Each place the pipeline would call a model or a provider has an interface with
two implementations. **Mock is the default everywhere**; nothing calls an API
unless the mode is changed explicitly.

| Concern | Interface | Mock | Live |
|---|---|---|---|
| Document quality | `quality_checker.py` | replays the scripted verdict | `ClaudeVisionQualityChecker` |
| Extraction | `extractor.py` | replays scripted fields | `ClaudeExtractor` |
| Registry / identity | `providers.py` | replays the dataset rows | stubs |
| Screening | `providers.py` | replays the dataset rows | stub |
| Media relevance | `media_relevance.py` | replays the category | stub |
| Risk / pack narrative | `narrator.py` | assembles from the pack | stub |
| Template choice | `steps/communication.py` | first allowed template | stub |

**Live mode is a placeholder.** `LIVE_MODE_READY` in
`orchestrator/live_mode.py` is `False`, and selecting a live implementation
raises `LiveModeNotConfigured` with instructions rather than attempting a call.
The Claude integration for Steps 3 and 4 is written and unit-tested but has
never met the real API; see the *Live mode (pending)* section of README.md for
what is built, what is unproven, and how to turn it on.

Modes are set at the top of `orchestrator/orchestrator.py`:
`QUALITY_CHECKER_MODE`, `EXTRACTOR_MODE`, `PROVIDER_MODE`,
`MEDIA_ASSESSOR_MODE`, `NARRATOR_MODE`.

Whatever is behind an interface, three rules hold:

1. **A model may not invent a value.** Flags, field names, media categories and
   templates are all checked against the KB, and an unrecognised one is
   rejected rather than stored.
2. **A failed call is never a pass.** A provider that does not answer, or a
   model whose output will not parse, produces insufficient evidence and a
   hold - not a clean result.
3. **Prompts are versioned files in `prompts/`**, and the version goes into the
   audit row for every call.

---

## The demo app

`app/` is a Streamlit front end and nothing more. It reads the database directly
but **never writes to it**: every action calls the orchestrator function that
owns the rule, so holds, role checks and the sanctions rules apply on screen
exactly as they do in the pipeline. `app/customer_view.py` is a plain function
rather than part of the page, so a test can render what the applicant would see
and check every word of it.

A human action on a screen clears a hold and then calls
`orchestrator.resume()`, which carries the case on from wherever it now
stands. Without that the case would be unblocked and going nowhere, which is
not what the pipeline does. Extraction skips documents it has already read, so
resuming cannot duplicate values.

## Running it

```
python tools/dataset_to_applications.py   # dataset -> application JSON
python tools/run_demo.py                  # all 14 cases, Steps 1-8
python tools/compare_to_dataset.py        # score against the dataset
python tools/export_case.py               # audit bundle per case
python tools/make_sample_documents.py     # demo document files
python tools/evaluate_live.py             # live vs mock, needs an API key
streamlit run app/main.py                 # the demo screens
python -m pytest tests -q
```
