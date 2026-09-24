# Wallester UC4 — Demo Rehearsal Script

A step-by-step walkthrough of the seven demo scenarios in Section 14 of the POC brief, showing what to click in the app, what to say, what the audience should see, and which Section 15 success criterion each moment proves.

Total running time: about 35 minutes (5 min opening, 25 min scenarios, 5 min close). Rehearse it at least twice end to end, because button labels in the app may differ slightly from the wording here.

---

## Before you start

**Setup checklist (10 minutes before the demo):**

- [ ] Open the orchestrator folder in VS Code and run `streamlit run app/main.py`.
- [ ] Click **Reset demo** in the sidebar. All 14 cases should stop at their first human action (11 open holds, 0 decisions, 0 communications).
- [ ] Check the sidebar shows the **MOCK** badge, and the role is set to **analyst**.
- [ ] Close every other browser tab and notification. Zoom the browser to 110–125% so the room can read it.
- [ ] Have the sample documents folder open in a second window, in case a preview fails.
- [ ] Fallback: if the app breaks, stop it, check out the tag `v0.1-mock-complete`, and restart.

---

## Opening (5 minutes)

**What to say.** Frame the POC the way the brief requires, and avoid the claims in Section 18:

> "Wallester already runs a strict onboarding model. This POC doesn't change that policy or replace any of your providers. It shows how orchestration can take the avoidable manual work out: chasing documents, re-keying data, and unclear case status, while every judgement that matters stays with your analysts and compliance team."

Then show the **operations dashboard** (10.1).

**Point out:** every case has a status, an owner and a next action, and ageing is visible at a glance. Mention the **MOCK** badge: everything today runs on synthetic data, with no live customer or identity data.

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
3. Switch the sidebar role to **compliance**. Record **escalate**, with an escalation target and reason.
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
| Synthetic-data readiness | Opening — MOCK badge |
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
