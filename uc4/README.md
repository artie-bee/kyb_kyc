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
orchestrator/
  orchestrator.py           The orchestration layer: runs steps in order, stops cleanly
  kb.py                     Loads the KB + tiny rule engine
  db.py                     SQLite tables the orchestrator writes; audit table is append-only
  steps/intake.py           Step 1: case creation, completeness, classification, routing
  steps/requirement_pack.py Step 2: builds the per-case checklist from the KB
  steps/document_quality.py Step 3: screens each uploaded document (Section 5.3)
  steps/analyst_review.py   Analyst release of a document held at Step 3
  steps/extraction.py       Step 4: OCR and structured extraction (Section 5.4)
  quality_checker.py        AI half of Step 3 (mock / Claude Vision stub)
  extractor.py              AI half of Step 4 (mock / Claude stub)
sample_applications/        6 test applications (normal, complex, branch, and failure cases)
  from_dataset/             10 applications rebuilt from the scripted dataset (generated)
tools/
  dataset_to_applications.py  Dataset CSVs -> application JSON; prints every assumption
  compare_to_dataset.py       Runs the orchestrator and scores it against the dataset
tests/test_first_layers.py    4 unit tests
tests/test_against_dataset.py 26 tests - full match against the dataset:
                              50/50 case fields, 123/123 documents, 161/161 extracted fields

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

[Step 4 extraction] --all required fields above the floor--> verification
                    --missing / low confidence / date conflict--> analyst_review_required

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
