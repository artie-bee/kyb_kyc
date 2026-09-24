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
  media_relevance.py        AI half of Step 6 (mock / Claude stub)
  quality_checker.py        AI half of Step 3 (mock / Claude Vision stub)
  extractor.py              AI half of Step 4 (mock / Claude stub)
sample_applications/        6 test applications (normal, complex, branch, and failure cases)
  from_dataset/             10 applications rebuilt from the scripted dataset (generated)
tools/
  dataset_to_applications.py  Dataset CSVs -> application JSON; prints every assumption
  compare_to_dataset.py       Runs the orchestrator and scores it against the dataset
tests/test_first_layers.py    4 unit tests
tests/test_against_dataset.py 50 tests - full match against the 14-case dataset:
                              70/70 case fields, 168/168 documents, 256/256 extracted
                              fields, 50/50 verification, 39/39 screening
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

[Step 6 screening] --clear sanctions match / serious media--> compliance
                   --possible sanctions match--> analyst
                   --PEP / moderate media--> risk_assessment, findings attached
                   --provider silent after a retry--> insufficient evidence, analyst
                   --nothing found--> risk_assessment (unless an earlier step held it)

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
