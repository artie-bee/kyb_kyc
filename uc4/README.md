# Wallester UC4 - First KB items and first orchestration layers

## What's here
kb/                         Knowledge base (read-only CSV, versioned in kb_manifest.json)
  applicant_type_rules.csv  Which applicant type a case is (Section 7: applicant-type rules)
  jurisdiction_routing.csv  Country -> EE/UK rule set and Wallester AS / UK Ltd (Section 6.2)
  requirement_rule.csv      Document requirement matrix (Section 5.2) - the 86-rule matrix
                            from the scripted dataset
  requirement_rule_sample.csv  The original 36-rule sample matrix, kept for reference
  document_quality_rules.csv   Quality checks per document type (Section 5.3)
  extraction_fields.csv        Fields each document type must yield (Section 5.4)
  registry_rules.csv           Registry outcomes and what blocks (Section 5.5)
  ubo_policy.csv               Beneficial-ownership threshold and findings (Section 5.6)
  screening_rules.csv          Sanctions and PEP outcomes (Section 5.7)
  adverse_media_categories.csv Media relevance categories (Section 5.7)
  risk_scoring_matrix.csv      Risk factors and weights (Section 5.8)
                               EVERY weight is a POC placeholder for Wallester to confirm
  risk_bands.csv               Score thresholds and the hard floors that override them
  message_template.csv         The approved message library (Section 5.9)
  communication_rules.csv      Which template for which situation (Sections 5.9, 11.3)
  analyst_decision_taxonomy.csv Decisions, roles and resulting status (5.10, 10.7)
  audit_log_standard.csv       Audit actions every case must carry (Section 10.8)
  communication_schedule.csv   Reminder and closure timing
orchestrator/
  orchestrator.py           The orchestration layer: runs steps in order, stops cleanly
  kb.py                     Loads the KB + tiny rule engine
  db.py                     SQLite tables the orchestrator writes; audit table is append-only
  steps/intake.py           Step 1: case creation, completeness, classification, routing
  steps/requirement_pack.py Step 2: builds the per-case checklist from the KB
  steps/document_quality.py Step 3: screens each uploaded document (Section 5.3)
  steps/analyst_review.py   Analyst release of a document held at Step 3
  steps/extraction.py       Step 4: OCR and structured extraction (Section 5.4)
  steps/verification.py     Step 5: registry, identity and UBO (Sections 5.5, 5.6)
  steps/screening.py        Step 6: sanctions, PEP and adverse media (Section 5.7)
  providers.py              Registry, identity and screening providers (mock / live stubs)
  steps/risk_assessment.py  Step 7: scoring and hard floors (Section 5.8)
  steps/evidence_pack.py    Step 7b: the analyst pack (Section 5.11)
  media_relevance.py        AI half of Step 6 (mock / Claude stub)
  narrator.py               Written parts of Steps 7-8 (mock / Claude stub)
  steps/communication.py    Step 8a: customer messages, templates only (5.9, 11.3)
  steps/decision.py         Step 8b: the human decision (5.10, 10.7, 18)
  holds.py                  Case holds - the one place a case is stopped or released
  quality_checker.py        AI half of Step 3 (mock / Claude Vision stub)
  extractor.py              AI half of Step 4 (mock / Claude stub)
sample_applications/        6 test applications (normal, complex, branch, and failure cases)
  from_dataset/             10 applications rebuilt from the scripted dataset (generated)
tools/
  dataset_to_applications.py  Dataset CSVs -> application JSON; prints every assumption
  compare_to_dataset.py       Runs the orchestrator and scores it against the dataset
  run_demo.py                 All 14 cases through Steps 1-8, scripted humans replayed
  export_case.py              Audit bundle per case: rows, trail, versions (10.8)
tests/test_first_layers.py    4 unit tests
tests/test_against_dataset.py 65 tests - full match against the 14-case dataset:
                              70/70 case fields, 168/168 documents, 256/256 extracted
                              fields, 50/50 verification, 39/39 screening,
                              33/33 risk and evidence pack
tests/test_step8_end_to_end.py 14 tests - all 14 cases through Steps 1-8:
                              14/14 final statuses, 14/14 applicant communications
tests/test_schema_sync.py     6 tests - fails if the dataset or database gains a
                              column or enum value the schema file does not describe

## Run
# On Windows PowerShell the shell does not expand the glob, so expand it explicitly:
python -m orchestrator.orchestrator (Get-ChildItem sample_applications\from_dataset\*.json | ForEach-Object FullName)
python tools/dataset_to_applications.py     # rebuild the 10 applications
python tools/compare_to_dataset.py          # print the comparison table
python -m pytest tests -q

## Flow so far
application -> [Step 1 intake] --primary--> [Step 2 requirement pack] -> [Step 3 document quality]
                               --white_label--> [Step 2] -> [Step 3] -> stop (KYB intake only)
                               --unknown country / no rule--> analyst_review_required
                               --form incomplete--> next_action_owner = customer

[Step 3 document quality] --any manual review--> analyst_review_required (analyst)
                          --any resubmission---> resubmission_required (customer)
                          --all required accepted--> [Step 4 extraction]
                          --required items outstanding--> document_quality_review (customer)
                          --white-label--> stop (KYB intake only)

[Step 4 extraction] --all required fields above the floor--> [Step 5 verification]
                    --missing / low confidence / date conflict--> analyst_review_required
                    --required checklist item outstanding--> stays with the customer

[Step 5 verification] --> [Step 6 screening] either way; a blocking outcome sets
                          analyst_review_required so the analyst sees both together

[Step 6 screening] --> [Step 7 risk assessment + evidence pack] --> decision
                   holds placed for: clear match (compliance), possible match
                   (analyst), silent provider (analyst). Findings alone travel on.

[Step 7] low / medium -> ready_for_decision    high -> enhanced_due_diligence
         critical -> analyst_review_required   insufficient_evidence -> analyst
         ...unless a hold is open, in which case the hold decides.

[Step 8] a person decides; the taxonomy says who may decide what at which band,
         the resulting status comes from the taxonomy, and the customer message
         comes from the template library. Nothing is approved over an open hold.

## Case holds
One mechanism, in orchestrator/holds.py. Any step may PLACE a hold; only the step
that placed it, or a named human, may RELEASE it. Screening finding nothing does
not release a hold verification placed. Case status and next_action_owner are
DERIVED from the open holds, worst first: compliance > analyst > insufficient
evidence > customer. Every place and release is audited.

A document held at Step 3 waits for a named analyst: steps/analyst_review.py
release_document(document_id, analyst_id, decision, reason) with decision
accept or request_resubmission. A reason is required. The release re-routes the
case, so clearing the last held document moves it on by itself. quality_status
moves; quality_status_at_screen keeps what Step 3 decided, so an override never
erases the original verdict.

## Step 4 - extraction (Section 5.4)
Documents enter only through document_quality.accepted_documents().
kb/extraction_fields.csv says which fields each document type must yield.
A required field that is missing, or any value below 0.70 confidence, is marked
needs_analyst_correction - the value is never guessed. An analyst supplies it with
extraction.correct_field(), and the corrected value is what every later step reads.
The date rules QR-02/QR-03 run again on the dates OCR read; if they disagree with
what the quality checker read off the page, the document goes to manual review
rather than one reading being preferred.
  mock    replays the dataset's extracted_field rows - the default, calls no API
  claude  STUB, raises; see orchestrator/extractor.py
Set the mode with EXTRACTOR_MODE in orchestrator/orchestrator.py.

## Step 5 - verification (Sections 5.5, 5.6)
The paid-check boundary. verification.gate() refuses to run unless every REQUIRED
checklist item is accepted, so a case still owing a document is never billed for
provider calls it would have to repeat.
  registry  the provider returns what the register HOLDS (name, number, address,
            directors); the match results are COMPUTED here against the extracted
            fields, an analyst's correction included. RG-09: a provider that does
            not answer is retried once and then stops the case - never a pass.
  identity  one call per director, UBO and signatory. fail / review / duplicate
            stop the case; an expired ID goes back to the customer as a
            resubmission of that one document.
  ubo       effective ownership is multiplied along the chain (70% x 45% = 31.5%)
            and compared with the declared total; owners at or above the 25%
            threshold must be verified.
Blocking outcomes stop at analyst_review_required. Non-blocking findings are rows
in the finding table with evidence references, and the case goes on to screening.
  mock  replays registry_check.csv / identity_check.csv - the default, calls no API
  live  STUBS, raise; see orchestrator/providers.py
Set the mode with PROVIDER_MODE in orchestrator/orchestrator.py.

## Step 6 - screening (Section 5.7)
Subjects are the entity plus every director, beneficial owner and authorised
signatory. Sanctions and PEP are provider list results. Adverse-media relevance -
is this article about this person, and how serious - is the AI part and sits behind
media_relevance.py.
Four things this step will not do:
  1. no code path turns a sanctions possible_match or clear_match into no_match;
     the database refuses the update, and only a human decision resolves one;
  2. a provider that did not answer is not a pass (SC-05..SC-07 retry once, then
     record insufficient evidence);
  3. any human_required outcome sets requires_human_signoff and blocks approval;
  4. findings are internal - they never reach a field the customer could be shown.
restricted_finding is set on the case whenever there is any sanctions, PEP or
adverse-media finding, ready for Step 8 to force generic customer wording.
  mock    replays screening_check.csv - the default, calls no API
  live    STUBS, raise; see orchestrator/providers.py and media_relevance.py
Set the modes with PROVIDER_MODE and MEDIA_ASSESSOR_MODE in orchestrator.py.

## Step 7 - risk assessment and evidence pack (Sections 5.8, 5.11)
The score is arithmetic, computed in code from the findings table and the case
data using kb/risk_scoring_matrix.csv. The dataset generator reads the same
matrix, so the scripted scores and the computed ones cannot drift apart.
EVERY weight is marked "poc_placeholder - Wallester to confirm": the brief does
not state them, and a number nobody has agreed should not look like one that has.
Hard floors override the score - a confirmed sanctions match is critical whatever
else the file looks like, and a case with unresolved gaps is not scored at all.
Nothing is approved automatically (Section 18): a low band RECOMMENDS approval
and a person still has to make it.
The evidence pack is assembled from the database only. The one written part is
the compliance narrative, which may cite nothing that is not already a row here -
an invented reference is rejected, not stored - and is internal: it is never
copied into any field a customer could be shown.
  mock    replays / assembles deterministically - the default, calls no API
  claude  STUB, raises; see orchestrator/narrator.py
Set the mode with NARRATOR_MODE in orchestrator/orchestrator.py.

## Step 8 - communications and decisions (Sections 5.9, 5.10, 11.3, 18)
Everything a customer receives comes from kb/message_template.csv. The model may
choose among the templates kb/communication_rules.csv allows for the situation and
fill their declared placeholders; it writes no sentences. Three gates:
  1. a case with restricted_finding gets only the generic templates;
  2. a confirmed sanctions match sends nothing at all - a compliance task
     "decide customer communication" is raised instead;
  3. the rendered text is scanned for restricted wording before any send, and a
     hit blocks it and is audited. A send writes to the outbox table; no mail
     leaves this POC.
Decisions go through decision.record_decision(), which refuses to approve over an
open hold, enforces the role the taxonomy requires, COMPUTES override_flag by
comparing the decision with the recommendation, and requires every cited
evidence id to exist.
The resubmission loop runs on a Clock (real, or fake for the tests): one reminder
after 14 days, closed_withdrawn after 30. A re-upload re-runs Step 3 for that
checklist item only.

## Step 3 - document quality (Section 5.3)
Deterministic checks (file type, expiry, proof-of-address age, page count) run in code.
Judgement calls run through a QualityChecker:
  mock           replays the scripted verdict - the default, calls no API
  claude_vision  STUB, raises; see orchestrator/quality_checker.py
Set the mode with QUALITY_CHECKER_MODE in orchestrator/orchestrator.py.
A checker may only return flags in ALLOWED_FLAGS; anything else is rejected, not trusted.
A document that fails quality never reaches a later step: document_quality.accepted_documents()
is the only supported way to ask what a downstream step may read, and the case only moves to
extraction when every required checklist item is accepted. Three failed attempts on one item
send it to an analyst instead of back to the customer.

## Conditional rules
requirement_rule.csv carries both a human-readable `condition` sentence and a machine-readable
`condition_key`. Step 2 looks the key up in the application's `flags`:
  answered true  -> item added, status pending
  answered false -> item added, status waived (recorded, never silently dropped)
  not answered   -> item added, status pending, with a note for the analyst

## Assumptions to confirm
- All EEA countries use the Estonian (Wallester AS) rule set; brief Section 19 leaves scope open.
- Classification is rule-based from form fields (no LLM needed at this step).
- A conditional rule whose question isn't answered on the form is still added, with a note
  for the analyst, rather than silently dropped.
