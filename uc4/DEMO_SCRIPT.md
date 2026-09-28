# Wallester UC4 — Demo Rehearsal Script

A step-by-step walkthrough of the seven demo scenarios in Section 14 of the POC brief, showing what to click in the app, what to say, what the audience should see, and which Section 15 success criterion each moment proves.

Total running time: about 35 minutes (5 min opening, 25 min scenarios, 5 min close). Rehearse it at least twice end to end, because button labels in the app may differ slightly from the wording here.

---

## Before you start

**Setup checklist (10 minutes before the demo):**

- [ ] Open the orchestrator folder in VS Code and run `python tools/serve.py` (it opens http://127.0.0.1:8700/).
- [ ] For Scenarios 2b and 8: in a second terminal run `python tools/serve_portal.py` (port 8701), and run `python tools/make_sample_documents.py` **on the demo day**. That makes the Scenario 2b files and the Scenario 8 demo upload pack, whose proof of address is dated from the day it is generated.
- [ ] Close any old terminal still running a server: on Windows two servers can share a port, and your browser may reach the old one.
- [ ] Click **Reset demo** in the sidebar. All 14 cases should stop at their first human action (11 open holds, 0 decisions, 0 communications).
- [ ] Check no LIVE badge shows in the sidebar (mock mode shows none), and the decision form's role is set to **analyst**.
- [ ] Close every other browser tab and notification. Zoom the browser to 110–125% so the room can read it.
- [ ] Have the sample documents folder open in a second window, in case a preview fails.
- [ ] Fallback: if the app breaks, stop it, check out the tag `v0.1-mock-complete`, and restart.

---

## Opening (5 minutes)

**What to say.** Frame the POC the way the brief requires, and avoid the claims in Section 18:

> "Wallester already runs a strict onboarding model. This POC doesn't change that policy or replace any of your providers. It shows how orchestration can take the avoidable manual work out: chasing documents, re-keying data, and unclear case status, while every judgement that matters stays with your analysts and compliance team."

Then show the **operations dashboard** (10.1).

**Point out:** every case has a status, an owner and a next action, and ageing is visible at a glance. Mention that it runs in mock mode: everything today runs on synthetic data, with no live customer or identity data.

**Proves:** *Better status visibility* · *Synthetic-data readiness* · *No weak-compliance implication*

Briefly open the **Agent reuse** screen (10.9), headed "Agent reuse (10.9)": "This maps which parts follow the generic KYC/KYB agent pattern and which are Wallester-specific overrides: your applicant types, your requirement packs, your templates, your risk matrix."

Be precise here. This POC is a standalone build that *represents* the reuse split; it wasn't built on top of an actual existing agent. Section 15 accepts "reused or represented," and Section 18 warns against implying the generic agent fits Wallester without adaptation, so say "represents" and "maps," not "is running."

**Proves:** *Clear reuse of existing agent* · *Wallester-specific variant is standalone*

---

## Scenario 1 — Fast-track clean freelancer (14.1) · Case WAL-ONB-0001 · 3 min

**Story:** an Estonian freelancer submits complete documents.

1. Open Case 1 from the dashboard.
2. **Documents tab:** every document is accepted, with no flags. Extracted fields all show high confidence.
3. **Checks tab:** identity pass; sanctions, PEP and adverse media all clear.
4. **Risk tab:** band **low**, score **0**. No risk factors fired at all: the table reads *No factors fired.* Point out the recommended action is *approve*, but a person still has to make the decision.
5. **Decision tab:** as **analyst**, record **approve** with a reason.
6. **Customer view:** show the approval message the applicant receives.

**What to say:** "Even the cleanest case ends with a human decision. The POC doesn't assume low-risk auto-approval, because that's a Wallester policy decision, not ours."

**Proves:** *Reduced manual chasing* (no follow-up needed) · *Auditability* (the decision is timestamped in the audit trail)

---

## Scenario 2 — SME with poor document quality (14.2) · Case WAL-ONB-0002 · 4 min

**Story:** an SME uploads a blurred director ID and hasn't provided the UBO declaration.

1. Open Case 2. Status: **resubmission required**; owner: **customer**.
2. **Documents tab:** open the director ID preview. The audience can see it's genuinely unreadable. The verdict shows the flag *blurred / unreadable*.
3. **Checklist:** the UBO declaration item is still *pending*.
4. **Checks tab:** it's **empty**. Pause here; this is the key moment.
5. **Communications tab:** nothing has been drafted yet. Under **Send a message**, choose situation `resubmission` and click **Draft and approve as me**. The request appears and names exactly what's missing, in plain language: *ubo declaration, id document*.
6. **Customer view:** the applicant sees a clear status and knows exactly what to do next.

**What to say:** "The system caught the problem *before* spending money on any paid verification. No identity check, no registry lookup, no screening call was made for a document that was never going to pass. And the customer got a precise request instead of a vague 'please resend your documents.' No one typed that message; it came from the template library and the checklist."

**Proves:** *Reduced manual chasing* · *Better status visibility* (cost avoidance is the headline demo value in 14.2)

---

## Scenario 2b (optional): the customer fixes it · Case WAL-ONB-0002 · 5 min

**Story:** the same customer receives the request, opens the portal, and sends a clear director ID and the missing ownership declaration. Run this straight after Scenario 2, with the customer portal open in a second browser tab.

**You need:** the portal running (`python tools/serve_portal.py`, port 8701) and the two upload files, made by `python tools/make_sample_documents.py`:
- `sample_documents/scenario_2b/halliwell_id_clear.jpg`, the director ID from Scenario 2, drawn clearly this time;
- `sample_documents/scenario_2b/northbridge_ownership_declaration.pdf`, the missing ownership (UBO) declaration.

Both are synthetic specimens. Never upload a real document.

1. **Console, Case 2:** click **Copy customer link** under the case header, then **Copy**. The link opens this one application only. It carries no case number and expires after 14 days.
2. **Portal:** paste the link into the second tab. Open **Documents needed**. The header reads **9 accepted · 0 under review · 2 still needed**.
   - *Identity document (passport or ID card) - Denton Halliwell* shows **Resubmission needed**, the reason *"We could not read this document clearly…"*, and *Previous upload: director_id_halliwell_scan.jpg (replaced when you upload a new one)*.
   - *Declaration of beneficial owners* shows **Not uploaded yet**.
3. **Portal:** upload `halliwell_id_clear.jpg` against the identity document and `northbridge_ownership_declaration.pdf` against the declaration. Each upload answers *"Thank you. We have your file and it is now under review."* Both rows now say **Under review**.
4. **Console, Case 2, Documents tab:** both files are marked **uploaded by applicant**, and the blurred scan is still listed as **superseded**. The case header shows the hold *"visual check not run in mock mode: 2 uploaded document(s) waiting for an analyst to look at them"*.
5. **Console:** on each of the two uploads, open the file, type a reason (*Clear copy, matches the director*) and click **Accept**. The hold changes to *"fields not read automatically in mock mode"*, because in mock mode nothing reads an uploaded file, so a person types in what it says.
6. **Console, Documents tab:** type the fields exactly as printed on the files, then **Save the fields**.

   | Director ID (`halliwell_id_clear.jpg`) | Type |
   |---|---|
   | full name | `Denton Halliwell` |
   | expiry date | `2030-09-18` |
   | date of birth | `1978-06-02` |
   | document number | `GB-DH-5159942` |

   | Ownership declaration (`northbridge_ownership_declaration.pdf`) | Type |
   |---|---|
   | ubo name | `Denton Halliwell` |
   | ownership percentage | `60` |
   | control basis | `Direct shareholding` |
   | ubo name 2 | `Priya Nankivell` |
   | indirect ownership path | *(leave blank)* |

   Each value is recorded as **entered by an analyst**, never as extracted.
7. **Console, Checks tab:** the case has run on into verification. Step 5 made its calls: registry (2 attempts), identity for both directors, and screening for all three subjects.
   - *Expect this:* the dataset scripts no provider answers for case 2, so the mock providers report no answer. The registry shows **unavailable**, the Risk tab reads **insufficient evidence, not scored**, and the case is held for an analyst.
8. **Portal:** refresh. **11 accepted · 0 under review · 0 still needed**. *My application* shows *Documents reviewed: done* and *Verification and review: "Further review is needed before we can finish…"*. There is no mention of providers, holds or evidence.

**What to say:** "The customer fixed it themselves, in their own words and their own time. Nobody chased them by email. The blurred copy isn't thrown away; it's kept, marked as replaced. And when the paid checks finally ran and a provider didn't answer, the system didn't wave the case through. It stopped and put it on an analyst's desk. The customer, meanwhile, sees nothing but a calm 'further review'."

**Proves:** *Reduced manual chasing* · *Auditability* (every upload, release and typed value is in the audit trail under a name) · *No weak-compliance implication* (a silent provider is never a pass)

---

## Scenario 3 — Company registry mismatch (14.3) · Case WAL-ONB-0003 · 4 min

**Story:** a UK company's registration number matches the register, but its registered address doesn't.

1. Open Case 3. After Reset demo it is **held**: one field needs an analyst, and the registry check hasn't run yet, because the paid check waits for the field to be confirmed.
2. **Documents tab:** on `registry_extract_calderwick.pdf`, the `registered_address` field was read at low confidence (**0.58**) and is highlighted. The value shown is already right; the analyst is confirming a faint reading, not fixing a wrong one. As **analyst**, click **Correct** and enter exactly:
   `Unit 7 Calderwick Way, Leeds LS12 4QT, United Kingdom`
3. The case now carries on by itself through the remaining checks. **Checks tab:** registry results show the two addresses **side by side**:
   - Register holds: *Enterprise House, 14 Bell Lane, Leeds LS11 9PT, United Kingdom*
   - Extracted (confirmed): *Unit 7 Calderwick Way, Leeds LS12 4QT, United Kingdom*

   Number: match. Address: **mismatch**. Because a human confirmed the reading first, the mismatch is a real conflict, not an OCR error.
4. **Risk tab:** band **medium**, score **43**, with the address mismatch as a cited factor. Status: *ready for decision*.
5. **Decision tab:** the recommendation is *conditional approve*. As **analyst**, choose **request more information**. Fill in **both** *Rationale* and *Override reason*: the decision differs from the recommendation, so the backend requires the second one.
6. Point out the decision is recorded as an override in the **stricter** direction.
7. **Communications tab / Customer view:** the clarification request to the customer.

**What to say:** "The system didn't trust a faint reading. It asked a person to confirm it before spending money on the registry check. Then every flag points to its evidence. And when the analyst chooses to be more cautious than the system, that's recorded too, as a stricter override, so compliance can see exactly where people and the system disagree."

**Proves:** *Evidence-pack quality* · *Auditability*

---

## Scenario 4 — Complex UBO structure (14.4) · Case WAL-ONB-0004 · 5 min

**Story:** a corporate applicant owns through an indirect chain of holding companies.

1. Open Case 4. After Reset demo it has **no risk score yet**, because it's held. Show there are **two open holds** at once: one from the document quality step (the ownership chart) and one from extraction (**one** low-confidence field).
2. **Documents tab:** as **analyst**, release the ownership chart with a reason. Show the case **does not move on**, because extraction's hold is still open. Then accept the field that's waiting (`indirect_ownership_path`) as read — and look again: reading the released chart has turned up a second one, `intermediate_entity` at 0.55. Accept that too. The case now carries on by itself.
3. **People tab:** effective ownership is **calculated, not copied**: the app shows `70% x 45% = 31.5%`, above the 25% UBO threshold. On the **Checks tab**, the registry section says the register does **not** corroborate the declared beneficial ownership.
4. **Risk tab:** band **high**, score **69** → enhanced due diligence route.
5. **Contrast with the control:** open **Case 10**. Same applicant type and requirement pack, but a transparent, registry-supported chain. It scores **15** against Case 4's **69**.

**What to say:** "This is why orchestration matters beyond OCR. Reading the chart is the easy part. Working out who really owns the company, noticing the registry doesn't back it up, and making sure one step can't quietly release another step's hold — that's the orchestration. And Case 10 proves the difference comes from the evidence, not from the type of company."

**Proves:** *Compliance-safe behaviour* (ambiguous ownership goes to a human) · *Evidence-pack quality*

---

## Scenario 5 — PEP and adverse media (14.5) · Case WAL-ONB-0005 · 4 min

**Story:** a UBO is a politically exposed person, with a moderate adverse-media finding.

1. Open Case 5. **Checks tab:** PEP match on the UBO; adverse media *moderate*.
2. **Risk tab:** the **hard floor** applies: a PEP is never scored below high, whatever the points say. Point out the floor is shown explicitly.
3. **Evidence pack:** open the draft compliance narrative, labelled **INTERNAL - NOT FOR CUSTOMER**. It names every factor and its weight. For the evidence IDs behind them, point at the **Risk factors** table just above it: the PEP and media factors both cite `SCR-0006`.
4. **Customer view:** nothing about PEP or media appears; only a neutral status.

**What to say:** "The system does the preparation (the pack, the narrative, the evidence links) so the analyst starts from a complete picture instead of a blank page. The judgement stays with them."

**Proves:** *Compliance-safe behaviour* · *Evidence-pack quality*

---

## Scenario 6 — Possible sanctions match (14.6) · Case WAL-ONB-0006 · 5 min

**Story:** a director returns a possible sanctions match. This is the most important scenario; slow down.

1. Open Case 6. Status: **analyst review required**; band **critical** (the sanctions floor, not the score).
2. **Decision tab, as analyst:** there is no **approve** to attempt. Point at the panel headed *Not offered at band `critical`*, which lists approve and conditional approve and the bands that do allow them. Then choose **escalate** and record it as **analyst**. The app shows the backend's refusal — *"analyst.demo is analyst, but the band is critical, so this decision is compliance's to take"*. Read it aloud.
3. Set the role on the decision form to **compliance**. Record **escalate**, with an escalation target and reason.
4. **Customer view:** the escalation you just recorded sent the applicant one message, and it is generic — their application is with the onboarding team for an additional manual review step, and no further documents are needed. That is everything they have ever been sent. Nothing mentions sanctions, screening or matches.
5. Optional: mention that the database itself blocks any attempt to change a sanctions match to "no match." Only a human decision can resolve it.

**What to say:** "The AI can't clear a sanctions match, and neither can an analyst without the right role. The system won't even let the attempt through. And the customer isn't tipped off: the message they get is identical to any other case under review."

**Proves:** *Compliance-safe behaviour* · *Auditability* (refusal and decision are both in the audit trail)

---

## Scenario 7 — White-label branch (14.7) · Case WAL-ONB-0008 · 2 min

**Story:** a white-label programme partner applies.

1. Open Case 8. It's recognised as **white-label** and routed to a separate branch. Entity scope is *undetermined*: that's decided in the programme phase.
2. Show the KYB intake was done (the checklist and document quality), then the case stopped deliberately.
3. Show the **Future phase** panel: KYB, API integration, Visa co-brand approval, BIN and 3DS configuration, go-live testing. The panel also shows the entity scope, *undetermined*.

**What to say:** "We understand Wallester runs two onboarding workflows. Direct business and freelancer onboarding is the first build; white-label programme onboarding is shown, recognised, and kept as a later phase, so the POC isn't over-scoped."

**Proves:** *White-label not over-scoped*

---

## Scenario 8 — A brand-new customer, start to finish · WAL-DEMO-0001 · about 7 min

**Story:** a new Estonian company, Lumen Harbour OU, applies through the customer portal. Its one director, Kristiina Vaher, owns all of it. She types in the form, uploads her documents (including one blurred ID the system sends back), and the case arrives in the console ready for a decision. Nobody chased her, and nobody re-keyed anything.

**You need:** both servers running, and the demo upload pack made by `python tools/make_sample_documents.py` on the demo day. It is written to `sample_documents/demo_pack/`: 12 fictional files marked SPECIMEN, `FORM_VALUES.md`, and `manifest.json`. Open `FORM_VALUES.md` beside the browser: it lists every value to type and which file goes with which checklist item.

1. **Portal** (http://127.0.0.1:8701/demo): click **Start a new demo application** and type the values from `FORM_VALUES.md`. It takes five steps of **Continue**, then **Send application**:
   - *Your business:* Lumen Harbour OU · Private limited company · EE-16550321 · Estonia · Narva mnt 7, 10117 Tallinn · Online sales of handmade ceramics · 8000 EUR;
   - *Contact:* Kristiina Vaher · kristiina@lumen-harbour.example;
   - *People:* Director 1: Kristiina Vaher · 1988-04-14 · Estonia · Estonia;
   - *Owners:* Owner 1: Kristiina Vaher · Directly · 100 (date of birth not needed; she is the director);
   - *A few facts:* No · No · No · Yes · No;
   - tick *Every detail here is made up for the demo*.

   Point out that nothing on the form asks what *kind* of applicant this is. The system decides that from the facts (here, *SME company*).
2. **Portal:** the customer lands on *My application*, signed in to the new case (reference **WAL-DEMO-0001**). Click **Documents needed**: **0 accepted · 0 under review · 11 still needed**, one row per document, each with its own upload area.
3. **Portal, the refusal:** upload `07_identity_document_BLURRED.jpg` against *Identity document (passport or ID card) - Kristiina Vaher*. It is refused in plain words: *"We could not accept this file. We could not read this document clearly. Please upload a sharp, complete copy of the original."* The row now shows **Resubmission needed**.
4. **Portal:** upload `08_identity_document_clear.jpg` against the same row, then the ten other files, each against the item named in `FORM_VALUES.md`. Each one answers *"…now under review"*: nothing is read until the whole checklist is in. The last file sets off the run and answers *"Your file has been accepted"*. The header reads **11 accepted · 0 under review · 0 still needed**.
5. **Console** (http://127.0.0.1:8700/): open **WAL-DEMO-0001** from the dashboard.
   - **Status: ready_for_decision**, owner analyst, type *sme_corporate*, no open holds.
   - **Documents tab:** all 12 uploads are marked *uploaded by applicant*. The blurred ID is still in the history. Every document and every field reads **mock: recognised demo sample file**, and there are no typed-in fields.
   - **Checks tab:** every result is a *Simulated provider response*. The registry comparison matches on name, number, address and directors: the values read off the files agree with what the customer typed.
   - **Risk tab:** band **low**, score **10**. The 10 points are the blurred ID the customer had to send again. Recommended action: approve, and a person still makes the decision.
6. **Portal:** *My application* now reads *"The checks are complete and your application is with us for a final look."*

**What to say:** "That's a customer we'd never seen before, onboarded from a blank form to a decision-ready case in a few minutes. The system asked for exactly the documents her facts required. It turned back the blurred ID straight away, with a reason she could act on. It read everything else and checked it against the register. The only thing left for a person is the decision itself."

**Be precise about mock mode:** the pack's files are *recognised by their contents* and their verdicts and fields are replayed from a manifest, which is why every one of them is labelled *mock: recognised demo sample file*. Any other file goes through the ordinary mock path: a visual-check hold, and an analyst types the fields in. In live mode the real checker and extractor read every file, and the manifest is ignored.

**Proves:** *Reduced manual chasing* · *Better status visibility* · *Auditability* (every upload, refusal and replayed result is in the audit trail) · *Synthetic-data readiness*

**Timing:** in the dry run after Reset demo, the system's own work took 1.1 seconds end to end (Reset 0.3 s, form 0.05 s, 12 uploads including the full run 0.6 s). The presenter's typing and clicking takes about 7 minutes: 3 for the form, 3 for the uploads, 1 in the console.

---

## Close (5 minutes)

1. **Audit export (10.8):** download the JSON bundle for Case 6. Show that it carries every row, the full audit trail, and the KB and model/prompt versions used.
2. **Dashboard again:** show the statuses have moved on after today's decisions.

**What to say:**

> "Every AI output and every human decision is timestamped and traceable to the rules version it used. Nothing today changes Wallester's policy; the rules live in versioned files your team controls. The next step is a discovery session to confirm the risk-matrix weights, jurisdiction scope and templates, all of which are marked as placeholders today."

**Proves:** *Auditability* · *No weak-compliance implication*

---

## Coverage check — every Section 15 criterion is demonstrated

| Success criterion (Section 15) | Where it's shown |
|---|---|
| Clear reuse of existing agent | Opening — Agent reuse screen (10.9) |
| Wallester-specific variant is standalone | Opening — Agent reuse screen; applicant types and templates throughout |
| Reduced manual chasing | Scenarios 1 and 2 |
| Better status visibility | Opening dashboard; Scenario 2 customer view |
| Compliance-safe behaviour | Scenarios 4, 5 and 6 |
| Evidence-pack quality | Scenarios 3, 4 and 5 |
| No weak-compliance implication | Opening and close wording |
| White-label not over-scoped | Scenario 7 |
| Synthetic-data readiness | Opening — mock mode |
| Auditability | Scenarios 1, 3 and 6; close — audit export |

---

## Backup cases (only if asked)

| Question from the audience | Show |
|---|---|
| "What about a confirmed sanctions hit?" | **Case 12** — clear match, critical, no automatic customer message; compliance decides what (if anything) the customer is told. |
| "What if the company doesn't exist any more?" | **Case 11** — dissolved on the register; eligibility floor recommends reject; analyst agrees. |
| "What if a provider is down?" | **Case 13** — unavailable media check never counts as a pass; the case is held as insufficient evidence. |
| "What if the customer just stops replying?" | **Case 14** — held for the customer, awaiting a proof of address. The reminder schedule is `kb/communication_schedule.csv`; the demo database stops at the hold rather than fast-forwarding a month, and `python tools/run_demo.py` plays the thirty days out and closes the case as withdrawn. |

---

## Likely questions — safe answers

**"Is the AI making the onboarding decision?"** No. It prepares evidence, drafts narratives and routes cases. Every decision is recorded by a named person with the right role.

**"Would this replace our identity or screening provider?"** No. Providers sit behind a configurable interface; today they're mocked, and in production they'd be Wallester's existing providers.

**"Where do the risk weights come from?"** They're POC placeholders, each labelled "Wallester to confirm." Changing them means editing a versioned rule file, not code.

**"Does it use real customer data?"** No. Every person, company and document is synthetic, and the ID cards are generic designs watermarked as specimens.

**"Is the AI live today?"** The demo runs in mock mode with scripted AI outputs, so results are repeatable. The live Claude integration for document checks and extraction is built but not yet tested against the real API; that's the next technical step.

**Words to avoid** (Section 18): "auto-approve," "replace analysts," "clears sanctions," "your onboarding is weak," "Companies House is enough," "proves you need this."
