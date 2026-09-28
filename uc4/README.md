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
  quality_checker.py        AI half of Step 3 (mock / live vision)
  extractor.py              AI half of Step 4 (mock / live)
sample_applications/        6 test applications (normal, complex, branch, and failure cases)
  from_dataset/             10 applications rebuilt from the scripted dataset (generated)
tools/
  dataset_to_applications.py  Dataset CSVs -> application JSON; prints every assumption
  compare_to_dataset.py       Runs the orchestrator and scores it against the dataset
  run_demo.py                 All 14 cases through Steps 1-8, scripted humans replayed
  export_case.py              Audit bundle per case: rows, trail, versions (10.8)
  make_sample_documents.py    Demo document files for cases 1, 2, 3, 4 and 6
  evaluate_live.py            Live model vs the scripted answers -> eval_report.md
prompts/                    Versioned prompt files; the version is audited per call
sample_documents/           Generated demo files (git-ignored)
ARCHITECTURE.md             The 8 steps, holds, the KB, and mock vs live
tests/test_first_layers.py    4 unit tests
tests/test_against_dataset.py 65 tests - full match against the 14-case dataset:
                              70/70 case fields, 168/168 documents, 256/256 extracted
                              fields, 50/50 verification, 39/39 screening,
                              33/33 risk and evidence pack
tests/test_step8_end_to_end.py 17 tests - all 14 cases through Steps 1-8:
                              14/14 final statuses, 14/14 applicant communications
tests/test_sample_documents.py 5 tests - printed values match extracted_field
tests/test_live_mode.py       10 tests - live mode stays opt-in and never passes on failure
tests/test_llm_providers.py   provider layer - three providers, one set of guarantees
tests/test_app.py             12 tests - no SQL writes in app/ or web/, the customer
                              view leaks nothing, reuse points at real KB files, and
                              a human action carries the case forward
tests/test_web.py             the console - every screen and tab renders, actions
                              redirect, and the backend's refusals reach the screen
tests/test_portal.py          the customer portal - one test per customer journey
                              (cases 2, 5, 6, 8, 11, 12, 13, 14), uploads with
                              JavaScript off, no SQL writes, no console routes
tests/test_schema_sync.py     6 tests - fails if the dataset or database gains a
                              column or enum value the schema file does not describe

## Demo screens (brief Section 10)

```
pip install -r requirements.txt
python tools/serve.py      # http://127.0.0.1:8700/, opens a browser
```

Screens: operations dashboard, case detail (timeline, checklist, documents and
extraction, people and ownership, checks, risk and evidence pack, decision,
communications), a separate customer view, an agent reuse table (10.9) and the
audit export. A white-label case also shows its future-phase panel.

A human action on screen carries the case forward: releasing a document or
correcting a field clears the hold and the orchestrator resumes from there, so
case 4 reaches band high / 69 and case 3 medium / 43 as they do in the pipeline.

The role (analyst / compliance) is chosen on the decision form, and the backend
checks it. The sidebar carries a "Reset demo" button that rebuilds the database
to the demo start state - every case run as far as the pipeline can take it
alone, with the releases and decisions left to make on screen. A badge appears
only when a live provider is selected; mock mode shows none.

**The app never writes to the database.** Every action calls the orchestrator's
own function, so a refusal you see - "cannot approve while holds are open",
"compliance role required" - is the real rule refusing. `tests/test_app.py`
scans `app/` and `web/` for SQL writes and fails if it finds any.

## Customer portal (port 8701)

The applicant's side, on the same stack as the console: plain `http.server`,
HTML built in Python, plain CSS, and one small optional script. It serves none
of the console's routes. Run the two side by side, in two terminals:

```
python tools/serve.py           # console   http://127.0.0.1:8700/
python tools/serve_portal.py    # portal    http://127.0.0.1:8701/demo
```

Both read and act on the same `onboarding.db`, so a file uploaded in the portal
is waiting in the console's Documents tab for an analyst. "Reset demo" lives in
the console only; the portal never resets anything.

Screens: **My application** (five customer steps, one plain line each),
**Documents** (each item with Not yet uploaded / Received / Under review /
Accepted / Resubmission needed, a plain reason for resubmissions only, an upload
form per outstanding item, and the upload history), **Messages** (everything
sent to the applicant, in order), and **/demo**, a case selector that opens the
portal as any of the 14 customers. `--no-demo` turns the selector off; a
customer then needs an access link (`/access/<token>`).

The rules it keeps:
- every upload goes through `document_quality.receive_upload()`, which refuses a
  wrong type, an empty file, a file over 10 MB, or bytes that do not match the
  extension, before anything is stored or counted against the customer;
- in mock mode an upload has no scripted verdict to replay, so it goes to an
  analyst as **Under review** rather than being accepted unseen;
- uploads are taken only at the document stage. Once any paid check has run,
  the onboarding team asks for anything further by message;
- only one new table, `portal_token`, holding only a hash of each token.
  Everything else is read from the tables the console reads;
- every page passes through `customer_view.leaks()` before it is sent, and a
  page carrying restricted wording is refused rather than shown;
- every page works with JavaScript off. Uploads are plain multipart form posts,
  parsed with the standard library, so no new package was needed.

Uploaded files go to `uploads/<case_id>/` (git-ignored).

### Phase 2: new demo applications (demo only, synthetic data)

**Start a new demo application** on `/demo` opens a six-step form: business,
contact, people, owners (with percentages, held directly or through a company),
a few yes/no facts, then check and send. It works with JavaScript off. Answers
travel between steps as hidden fields, so nothing is stored until it is sent.

- **The customer never chooses an applicant type.** Sending calls the existing
  intake (`process_application`), and Step 1 classifies from the facts with the
  AT rules. A form field that tries to name a type is dropped on arrival.
- **Cases are `WAL-DEMO-0001`, `WAL-DEMO-0002`, ...** with source channel
  `portal`, numbered apart from the dataset's `WAL-ONB-` series. Every query in
  `tools/compare_to_dataset.py` leaves them out, and the scores are unchanged.
  The submitted application is saved to `portal_applications/<case_id>.json`
  (git-ignored), the same shape as a dataset application file. **Reset demo**
  deletes the cases, those files and every portal upload.
- **Reading uploads.** In live mode the real checker and extractor read the
  file. In mock mode the deterministic quality rules run for real, and then
  Step 4 holds the case: *"fields not read automatically in mock mode"*. The
  console's Documents tab shows a form where the analyst types the fields in
  from the file. Each value is recorded as `entry_method = entered_by_analyst`,
  never as extracted and never as a correction. Typed dates go through the
  expiry and age rules. The customer sees **Received** until the file is read.
  The typed values then go through the existing checks against the customer's
  entered details.
- **Simulated providers.** A demo case's registry, identity and screening
  results mirror what the customer entered, and every one is labelled
  *"Simulated provider response"*. The label is the provider name, so it
  appears on every check row, as the actor on every provider audit event, and
  in the evidence pack. The console's **Demo scenario** control (on the case
  page, console only) picks clean / address mismatch / PEP match / possible
  sanctions match. The choice is an audit event and is read back from the audit
  trail. It is fixed once the providers have answered.
- **No new tables.** `extracted_field` gains one column, `entry_method`, added
  in place to an existing database.

`tests/test_portal_phase2.py` covers each rule above.

### The upload space and the dynamic checklist

The portal's **Documents needed** page shows what `customer_view.customer_checklist()`
returns, read fresh from the `checklist_item` rows on every request. The portal
names no document type and counts nothing itself.

- **Shown:** required items; optional items, labelled *optional* and left out of
  "X of Y still needed"; items an analyst adds later (console, Checklist tab,
  *Request another document*), which appear at once and are named generically
  on a case with a restricted finding. **Not shown:** waived items, and
  conditions the form did not answer until an analyst confirms they apply
  (Checklist tab, *Applies / Does not apply*).
- **Four statuses only:** Accepted, Under review (any hold, including the mock
  visual check), Resubmission needed (with the approved reason from
  `kb/resubmission_reason_text.csv`), Not uploaded yet.
- **`document_quality.receive_upload()`** is the only way in. It refuses a
  closed case, an item not asked for, an item already accepted, the wrong type,
  an empty file, a file over the limit (`WALLESTER_UC4_MAX_UPLOAD_MB`, default
  10) and bytes that do not match the extension. It virus-scans the file
  (`orchestrator/virus_scanner.py`: mock reports clean, live is a stub), stores
  it under a random name with its SHA-256 in the audit trail, marks any earlier
  upload on the item `superseded` (kept), and runs the **same Step 3 `run()`**
  every scripted document goes through. In mock mode that means the
  deterministic rules for real, then a hold *"visual check not run in mock
  mode"*, released in the console with the existing *Release document*. After
  that, the phase 2 field-typing step applies. A successful upload stops
  reminders about that item.
- **A document that arrives after the paid checks** (one added during enhanced
  due diligence, say) is screened and read, but `resume()` does not re-run the
  providers. The case goes back to the status its risk band implies.
- **Access:** *Copy customer link* on every console case page makes a fresh
  link, shown once. Tokens expire (`WALLESTER_UC4_PORTAL_LINK_DAYS`, default
  14), only their hash is kept, each opens one case, and no case id appears in
  any portal URL.

`tests/test_portal_uploads.py` covers each rule above.

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

## Live mode

**Mock is the default everywhere and needs no key, no network and no
configuration.** The demo runs in mock mode. Live mode is opt-in: with
`LIVE_MODE_READY = False` in `orchestrator/live_mode.py`, selecting a live
implementation stops with a message rather than failing part-way through a case.

### Which model answers

`LLM_PROVIDER` selects the provider. The default is `anthropic`, so an
environment that sets nothing behaves as it always did.

| `LLM_PROVIDER` | Key | Model default | Documents | Tested against the real API |
|---|---|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5` | PDFs read natively | **no** |
| `xai` | `XAI_API_KEY` | `grok-4.7` | images only, PDFs rasterised | **no** |
| `groq` | `GROQ_API_KEY` | `qwen/qwen3.8-27b` | images only, max 3 per request | **yes** |

Groq is not xAI. The names are one letter apart and both serve an
OpenAI-compatible endpoint, but they are separate companies with separate keys,
and a key for one is rejected by the other.

### What is built

- `orchestrator/llm_client.py` - the provider interface. `LLMClient.ask` owns the
  retry budget, the strict-JSON rule and the `Call` record, so a provider
  supplies a transport and cannot relax a guarantee. `AnthropicClient` uses the
  anthropic SDK; `XaiClient` and `GroqClient` share `OpenAICompatibleClient`.
- `prompts/quality_check_v1.txt` and `prompts/extraction_v1.txt` - the prompt
  text as versioned files, never inline, so the audit trail can say what was
  asked.
- `ClaudeVisionQualityChecker` and `ClaudeExtractor` - both validate the reply
  against the KB and raise rather than return something unusable.
- Steps 3 and 4 hold the document when a call fails: a failed call is never a
  pass.
- `tools/evaluate_live.py` - runs the live checkers over `sample_documents/`,
  compares with the scripted answers and writes `eval_report.md`.

Every call is audited with the provider, the model, the prompt version, the
temperature, the attempt count, the latency, the token counts and how the file
was transported.

### Two things that are not cosmetic

**Temperature is 0 by default.** `WALLESTER_UC4_TEMPERATURE` overrides it, and
the value used is written into every audit row: a verdict reached at 0.7 is a
different claim from one reached at 0. This matters more than it sounds. The
first live runs were made before temperature was pinned, and two identical runs
over the same two documents disagreed with each other - one flagged a document
the other called clean. An evaluation at a sampled temperature measures the
sampler as much as the model.

**PDFs are rasterised for the OpenAI-compatible providers**, which take jpg and
png only. The conversion is recorded on the call, because "the model read the
PDF" and "the model read a picture of the PDF" are different claims. A document
with more pages than one request can carry is **refused, not truncated**: the
caller turns that into a manual-review hold saying the document could not be
fully assessed, rather than reaching a verdict on page one and presenting it as
a verdict on the document. Every sample PDF in this repository is one page, so
the limit does not bite today - but the guard is what stops it biting silently
later.

### Rate limits

A 429 is backpressure, not an answer. It is waited out - honouring the
provider's own `retry-after` - without spending the format-retry budget, and
after `RATE_LIMIT_ATTEMPTS` the document goes to an analyst. The Groq account
used for the evaluation allowed **8,000 tokens per minute**, and each vision
call costs about 2,350 (a Groq image is a flat 2,048 tokens regardless of
content), so roughly three calls fit in a minute. The full 61-document run is
122 calls and about 282,000 tokens; budget the best part of an hour.

### Turning it on

1. set `LLM_PROVIDER` and the matching key in the environment
2. confirm the network or proxy allows that provider's host
3. `pip install` the SDK it needs: `anthropic`, or `openai` for xai and groq
   (`pypdfium2` as well, for rasterising PDFs)
4. set `LIVE_MODE_READY = True` in `orchestrator/live_mode.py`
5. run `python tools/evaluate_live.py --limit 2` first, then the full run, and
   read `eval_report.md` before trusting any of it

A prompt that needs changing gets a new file - `quality_check_v2.txt` - never an
edit to v1, or the audit trail stops meaning anything.

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
