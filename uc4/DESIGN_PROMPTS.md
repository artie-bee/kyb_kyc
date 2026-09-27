# Claude design prompts — Wallester UC4 app

Paste these into Claude one at a time, in order. Each is self-contained and
describes what is actually in `app/main.py`, so the design that comes back can be
wired to the real components instead of being redrawn afterwards.

Prompt 0 sets the design system. Run it first and keep the artifact link — every
later prompt says "use the design system from the previous artifact."

**Paste the quality bar below at the end of every prompt**, including Prompt 0.
It is what keeps the output looking like a bank's internal system rather than a
consumer SaaS landing page, and it is the part that gets forgotten.

---

## The quality bar — paste this at the end of every prompt

> **Craft requirements.** This is a regulated-finance console shown to a bank's
> compliance team, not a consumer app. Hold it to that standard:
> - No emoji, no gradients, no drop shadows, no pill-shaped everything, no
>   decorative illustration, no purple-to-blue SaaS look.
> - Depth comes from 1px hairline borders and background steps, not shadows.
> - Every number is set in tabular figures and right-aligned in tables.
> - Corner radius is 4–6px and used consistently. Nothing is fully rounded except
>   the status dot.
> - Full keyboard operability: visible focus rings, logical tab order, Escape
>   closes an expanded row.
> - Text contrast meets WCAG AA at every size, including the muted caption grey.
> - Nothing is conveyed by colour alone — a status chip carries a word, and the
>   mismatch row carries an icon as well as a tint.
> - Realistic density. This is a screen someone reads for six hours.

---

## Prompt 0 — Design system

> Design a design system for an internal compliance-operations console. It is used
> by KYB/KYC analysts and compliance officers at a card issuer, on a desktop, for
> hours at a time, alongside a register lookup and a case queue.
>
> **Art direction.** Restrained, editorial, high-density. The reference points are
> the Stripe Dashboard and Linear for craft and spacing, and a trading terminal for
> density and tabular discipline — but sober rather than dark-glamorous. It should
> look like a system of record: quiet by default, so that the three or four things
> that are genuinely wrong on a case are the only things that carry colour. An
> analyst should be able to glance at a screen and find the problem without reading
> it. Legibility and information density beat visual interest everywhere they
> conflict.
>
> **Typography.** A neutral grotesque for the interface — Inter, IBM Plex Sans or
> similar — with **tabular figures enabled on every number**. A separate monospace
> family for identifiers and raw values. Base size 13–14px with a tight scale;
> headings carry weight, not size. No more than three weights in total.
>
> **Grid and density.** An 8px spacing scale on a 4px sub-grid. Table rows at
> 32–36px. Generous gutters between regions, tight leading inside them. A 1px
> hairline border system carries all structure — hairlines and background steps do
> the work that shadows would do in a consumer product.
>
> **Colour.** A ten-step neutral ramp as the foundation, warm-grey or blue-grey,
> and a single restrained accent used only for interactive affordances. The four
> semantic colours are **desaturated and serious** — the amber of a compliance
> flag, not a browser warning; the red of a ledger, not an error toast. They appear
> as a tinted background plus a coloured left rule plus a coloured icon, never as
> coloured body text.
>
> The semantics are fixed by the app and must map one to one:
> - **warning / amber** — an open hold, or a decision the risk band does not allow
> - **error / red** — a restricted finding, a value below the confidence floor, an
>   "INTERNAL — NOT FOR CUSTOMER" label, and any refusal the backend returns
> - **success / green** — a hold cleared, a field corrected by an analyst, a
>   verified beneficial owner, an action that completed
> - **info / blue** — neutral status and explanatory captions
>
> **Deliver as components, with every state drawn** (rest, hover, focus, active,
> disabled, loading, empty, error):
> - Data table — sticky header, sortable header, hairline row separators, no zebra
>   striping, right-aligned numerics, a truncation-with-tooltip rule for long cells,
>   a selected row, and a genuinely designed empty state
> - Status chip — a small dot plus a word, in the four semantics plus neutral
> - Banner / callout — the four semantics, with a left rule, an icon, a title and
>   a body that can hold a bulleted list
> - Tabs — underline style, not boxed
> - Buttons — primary, secondary, tertiary, and a destructive variant; regular and
>   small
> - Form field — label, input, help text, error text; text input, textarea, select,
>   multi-select
> - Expander / disclosure row, collapsed and open
> - Metric tile — label, large tabular value, and a "not yet available" state that
>   is visibly different from a zero
> - Monospace ID token, inline and in a table cell
>
> **Identifiers** appear constantly and must be scannable at a glance: cases
> `WAL-ONB-0004`, holds `HLD-0012`, fields `FLD-0256`, documents `DOC-0038`,
> screening checks `SCR-0008`. Give them a distinct but unobtrusive treatment —
> monospace, slightly tighter tracking, a faint background — and make sure a column
> of them aligns.
>
> **Environment badge**, persistent in the sidebar, two states: **MOCK** (green,
> "scripted answers, no API calls") and a live state (amber). MOCK is the only
> state that is ever active today, and the badge must be impossible to miss without
> being loud — this is how the room knows no real customer data is on screen.
>
> Provide light and dark themes. Dark is a genuine dark theme with its own neutral
> ramp, not an inverted light one.

---

## Prompt 1 — App shell, sidebar and routing

> Using the design system from the previous artifact, design the app shell.
>
> **Left sidebar, top to bottom:**
> 1. Title "Wallester UC4"
> 2. The environment badge, showing **MOCK**
> 3. Radio "AI and provider mode" with two options — `Mock` and
>    `Live - pending API access`. The whole control is **disabled**; the live
>    option is visible but unreachable. Help text: "Live mode is written but
>    untested against the real API."
> 4. Divider
> 5. Radio "Your role" — `analyst` / `compliance`. This sets who is acting; it
>    does **not** grant permission. The backend still checks.
> 6. Text input "Your name", prefilled `analyst.demo`
> 7. Divider
> 8. Full-width button **Reset demo**, with a caption explaining it rebuilds all
>    14 cases to their first human action
> 9. Radio "Screen" — the five screens below
> 10. Dropdown "Case" — 14 case IDs
>
> **Routing:** the Screen radio is the only navigation. Five screens:
> `Operations dashboard`, `Case detail`, `Customer view`, `Agent reuse`,
> `Audit export`. The **Case dropdown is hidden** on `Operations dashboard` and
> `Agent reuse`, because neither is about one case. Show both sidebar states.
>
> There is no top nav, no breadcrumb and no modal. Every screen is a full-width
> page under its header.

---

## Prompt 2 — Operations dashboard

> Using the same design system, design the **Operations dashboard**.
>
> - Page header "Operations dashboard"
> - Four metrics in a row: **Cases** 14 · **Open** 11 · **With holds** 11 ·
>   **Restricted findings** 3
> - Two multi-select filters side by side, "Status" and "Next action owner", both
>   defaulting to everything selected
> - A caption "14 of 14 cases"
> - One dense table, ten columns: Case · Applicant · Type · Status · Owner ·
>   Band · Score · Holds · Hold detail · Age (days)
>
> Real rows to lay out:
>
> | Case | Applicant | Type | Status | Owner | Band | Score | Holds | Hold detail |
> |---|---|---|---|---|---|---|---|---|
> | WAL-ONB-0001 | Kaari Mets | freelancer_sole_trader | ready_for_decision | analyst | low | 0 | 0 | |
> | WAL-ONB-0002 | Northbridge Craft Supplies Ltd | sme_corporate | resubmission_required | customer | | | 1 | resubmission (customer) |
> | WAL-ONB-0004 | Vestmark Nordic OU | complex_corporate_ubo | analyst_review_required | analyst | | | 2 | manual_review (analyst); insufficient_evidence (analyst) |
> | WAL-ONB-0006 | Saarvik Metall OU | sme_corporate | analyst_review_required | analyst | critical | 60 | 1 | manual_review (analyst) |
> | WAL-ONB-0013 | Ellcott Haulage Ltd | sme_corporate | analyst_review_required | analyst | insufficient_evidence | | 2 | identity_failed (analyst); insufficient_evidence (analyst) |
>
> Two things to get right. **Band and Score are blank whenever a case has not
> reached risk scoring** — blank means "not scored yet", not zero, and must not
> read as a missing value. Give it a deliberate treatment, an em dash in muted
> grey, so it reads as a state rather than as data that failed to load. And
> `insufficient_evidence` is itself a band, and a long one; make sure the column
> survives it without wrapping the row.
>
> **Table craft.** Status and Band are status chips, not coloured text. Owner is a
> chip too — `analyst`, `compliance`, `customer`, `system` are four distinct
> populations and the eye should sort them without reading. Holds is a count with a
> subtle badge, and the Hold detail column is the one place long text is allowed to
> truncate with a tooltip. Age (days) is right-aligned tabular, with a quiet
> escalation as it grows — this is the ageing signal an operations lead scans for.
> Sticky header, hairline separators, no zebra.
>
> Below the table, an info note: cases with a restricted finding get generic
> customer wording only.
>
> Clicking a row opens that case. Show the hover, focus and selected states, and
> the filtered-to-nothing empty state.

---

## Prompt 3 — Case detail: header, holds and tabs

> Using the same design system, design the **Case detail** frame.
>
> - Header: `WAL-ONB-0004 - Vestmark Nordic OU`
> - Four metrics: Status `analyst_review_required` · Owner `analyst` ·
>   Type `complex_corporate_ubo` · Age (days) `24`
> - An **open-holds banner** in amber, headed "Open holds — the case cannot be
>   approved while any of these stand", listing each as:
>   - `HLD-0003` **manual_review** (analyst), placed by step.document_quality:
>     1 document(s) flagged for an analyst at the quality screen
>   - `HLD-0004` **insufficient_evidence** (analyst), placed by step.extraction:
>     1 extracted field(s) cannot be relied on as read
>
>   Design the empty state too: a green "No open holds."
> - A red banner for a **restricted finding**: customer messages limited to
>   generic templates.
> - Eight tabs, in this order, with these exact labels:
>   `Timeline` · `Checklist` · `Documents` · `People` · `Checks` · `Risk` ·
>   `Decision` · `Communications`
>
> **The hold banner is the most important element on the page.** A hold names the
> step that placed it, and only that step or a named human can release it. Make it
> read as a lock with an owner, not as an error message. Treat it as a designed
> object rather than an alert box: each hold is a row carrying its ID, a code chip,
> an owner chip and the reason, so two holds read as two separate locks rather than
> as one long paragraph of amber. It should be obvious at a glance how many there
> are and who each one belongs to.
>
> The header row is a page header, not a page title: the case ID in monospace, the
> legal name in the display weight, and the four metrics on the same optical line
> as a compact stat strip rather than four large cards.
>
> Also design the white-label **Future phase** panel — a bordered container that
> appears above the tabs on partner cases only. It shows "Entity scope:
> `undetermined`" and five greyed-out future steps, each captioned "future phase,
> not in this POC": KYB · API integration · Visa co-brand approval · BIN and 3DS
> configuration · Go-live testing.

---

## Prompt 4 — Documents tab

> Using the same design system, design the **Documents** tab. This is the most
> interactive screen in the app.
>
> A list of collapsible document cards. Each header carries the file name, the
> quality verdict and any flags:
> `ownership_chart_vestmark.pdf - manual_review_required  [missing_pages]`
> Held documents are **open by default**; accepted ones are collapsed.
>
> Inside an open card, two columns:
> - **Left, narrow:** an image preview for `.jpg` / `.png`, or for a PDF a caption
>   and an **Open the file** button.
> - **Right, wide:** "Screen verdict", "Reasons", and if the document is held, an
>   amber note "Held for an analyst. Releasing it is a decision on the record.",
>   a **Reason** text input, and two buttons side by side: **Accept** and
>   **Request resubmission**. Once released, a blue note replaces them:
>   "Released by analyst.demo: Chain confirmed."
>
> Below that, an **Extracted fields** list. Each row has four columns: field name
> in monospace · value · confidence to two decimals · status. Three statuses:
> nothing, a green "corrected by an analyst", or a red "below the confidence
> floor". The floor is **0.70**.
>
> A field below the floor expands into an inline form: the caption
> `FLD-0256: accept the reading as it stands, or replace it.`, a "Corrected value"
> input **prefilled with the current value**, a "Reason" input, and two submit
> buttons — **Accept as read** and **Correct**.
>
> Real data for the layout:
>
> | field | value | conf | state |
> |---|---|---|---|
> | `registered_address` | Unit 7 Calderwick Way, Leeds LS12 4QT, United Kingdom | 0.58 | below the floor |
> | `intermediate_entity` | Lindval Mid Holdings SA | 0.55 | below the floor |
> | `company_name` | Calderwick Logistics Ltd | 0.97 | fine |
>
> The distinction the design has to carry: **Accept as read** means a human looked
> at the original and the faint reading was right — the value does not change.
> **Correct** replaces it. Both are recorded against a named person. Neither is
> "dismiss".

---

## Prompt 5 — Decision tab, and how refusals look

> Using the same design system, design the **Decision** tab.
>
> At the top, any decision already recorded, as a green card: **escalate** by
> compliance.demo (compliance) — demo_decision, with the rationale beneath, and an
> amber sub-note if it was an override — "Override (stricter): …".
>
> Then, in order:
> 1. A blue note when holds are open: "2 hold(s) open. A decision that would close
>    the case is refused while any stands."
> 2. An **amber panel headed "Not offered at band `critical`"**, listing the
>    decisions this band does not allow and the bands that do:
>    **approve** (allowed at low, medium) · **conditional_approve** (low, medium) ·
>    **request_more_information** (low, medium, high, insufficient_evidence)
> 3. A caption: the dropdown is a CSV read at this band, and the role is checked by
>    the backend when you record.
> 4. The form: a **Decision** dropdown, "Reason code" prefilled `demo_decision`,
>    a "Rationale" textarea, "Override reason", "Escalation target", and a submit
>    button labelled **Record as analyst** — the label carries the current role.
>
> **Design the refusal state.** Every action calls the backend, and the backend's
> refusal is shown verbatim; the app never invents a message. A refusal renders as
> a red block with the exception name and the message:
>
> > **DecisionRefused**
> > analyst.demo is analyst, but the band is critical, so this decision is
> > compliance's to take
>
> > **DecisionRefused**
> > the recommendation was 'conditional_approve' and the decision is
> > 'request_more_information'. That is an override and needs a reason
>
> These refusals are the product, not an error state to hide — they are the proof
> that the rules hold. Make them look authoritative rather than like a form
> validation failure: a titled block with the rule name set as a label, the
> sentence set in the reading size rather than in small error text, and enough
> padding that it reads as a considered response from the system. It is closer to a
> returned filing than to a red toast. Do not let it shake, flash or auto-dismiss.
>
> The success state is a plain, quiet confirmation — this console is used for hours
> and a celebration on every action would be exhausting.

---

## Prompt 6 — Checks and Risk tabs

> Using the same design system, design two tabs.
>
> **Checks tab**, three sections.
>
> *Registry* — a line "**MockRegistryHub UK** — company status **active**, result
> **review** (attempts: 1)", then a green or amber note on whether the register
> corroborates the declared beneficial ownership, then a comparison table with the
> columns **Compared · The register holds · Extracted from documents · Result**:
>
> | legal name | Calderwick Logistics Ltd | Calderwick Logistics Ltd | match |
> |---|---|---|---|
> | registration number | UK-99014477 | UK-99014477 | match |
> | registered address | Enterprise House, 14 Bell Lane, Leeds LS11 9PT | Unit 7 Calderwick Way, Leeds LS12 4QT | **mismatch** |
> | directors | Marcus Ellersby, Roisin Delamere | Marcus Ellersby, Roisin Delamere | match |
>
> The side-by-side comparison is the whole point of this screen. Make the mismatch
> row unmissable without making the matching rows noisy. Empty state: "No registry
> check was run: the case did not reach the paid step."
>
> *Identity* — a table: Check · Subject · Document · Liveness · Biometric ·
> Name/DOB · Expired · Duplicate · Result.
>
> *Screening* — a table: Check · Subject · Sanctions · PEP · Adverse media ·
> Severity · Provider refs, captioned "Internal only. None of this may be repeated
> to the applicant." Values include `no_match`, `possible_match`, `clear_match`,
> `unavailable`; severity `none` / `medium` / `high` / `critical`.
>
> **Risk tab.**
> - Three metrics: Band `high` · Score `69` · Recommended `enhanced_due_diligence`
> - A blue note when human sign-off is required
> - An amber **"Hard floors applied — these override the score"** panel:
>   `pep_match` forces at least **high**, `sanctions_possible_match` forces at
>   least **critical**. A floor beats the arithmetic; design it so that is obvious.
> - A **Risk factors** table: Factor · Points · Why · Evidence, where Evidence is a
>   list of IDs like `SCR-0006`. Rows: `ownership_opacity` 25 — the register does
>   not corroborate the declared beneficial ownership; `pep_exposure` 30;
>   `adverse_media_severity` 20.
> - A caption that every weight is a POC placeholder for Wallester to confirm
> - An **Evidence pack** section: a plain summary, an amber "Missing or conflicting"
>   note, a red **INTERNAL — NOT FOR CUSTOMER** label, and a read-only textarea
>   holding the draft compliance narrative
>
> Empty state for the tab: "No risk assessment yet: the case has not reached
> Step 7."

---

## Prompt 7 — Customer view

> Using the same design system, design the **Customer view**. This is the one
> screen an applicant would see, and the one screen where a leak matters.
>
> - Header "Customer view", caption "Exactly what the applicant sees. Nothing
>   internal appears on this page."
> - The applicant's name as a heading
> - A line: **Reference** WAL-ONB-0006 · **Applied** 2026-09-01
> - A blue status box with one plain sentence: "Your application is with our
>   onboarding team. There is nothing you need to do at the moment."
> - "Messages we have sent you" — bordered cards, each with a timestamp and the
>   message text. Empty state: "No messages yet."
>
> Design this deliberately **plainer and calmer** than the internal screens; it
> should read as a different register entirely. No IDs, no badges, no colour-coded
> severity, no tables.
>
> Also design the **refusal state**: if restricted wording ever reaches this page,
> it renders nothing but a red block — "This page refuses to render: restricted
> wording reached customer-facing content. That is a bug, not a display problem."
> It fails closed. Design that as a deliberate stop, not a crash.

---

## Prompt 8 — Agent reuse and Audit export

> Using the same design system, design the last two screens.
>
> **Agent reuse (10.9).** Header "Agent reuse (10.9)", a blue caption, a line
> "Knowledge base in force: **kb-2026.09-poc-v7**", then a four-column table —
> Component · Generic agent · Wallester variant override · Implemented by:
>
> | Document quality rules | Reused | Wallester thresholds and accepted document types | kb/document_quality_rules.csv |
> |---|---|---|---|
> | OCR extraction | Reused | Wallester field map and required fields | kb/extraction_fields.csv |
> | Registry validation | Reused pattern | Configurable registry providers, not Companies House-only | kb/registry_rules.csv, kb/ubo_policy.csv |
> | Risk scoring | Reused pattern | Wallester-specific policy matrix | kb/risk_scoring_matrix.csv |
> | Customer communications | Partially reused | Wallester-approved templates required | kb/message_template.csv |
> | Audit summary | Reused | Wallester case fields and decision taxonomy | kb/audit_log_standard.csv |
>
> Below, one expander per component showing the generic and override wording, a
> note, each file with its version and rule count, and the raw CSV in a code block.
>
> **Audit export.** Header, caption, three metrics — Rows `90` · Audit events `57`
> · KB version `kb-2026.09-poc-v7` — a line listing the model and prompt versions
> used, a two-column Table/Rows breakdown, and a **Download the bundle (JSON)**
> button.

---

## Prompt 9 — Interaction and state

> Using the same design system, produce an interaction map for this console as a
> single page: what each button does, and what changes on screen afterwards.
>
> The rule that governs all of it: **the UI never writes to the database.** Every
> button calls a backend function, and whatever the backend returns — including a
> refusal — is what the screen shows.
>
> | Button | What it calls | On success | On refusal |
> |---|---|---|---|
> | Reset demo | rebuild all 14 cases | every case back to its first human action; 11 open holds | — |
> | Accept / Request resubmission | release a held document | the document-quality hold clears; other holds may remain | red refusal block |
> | Accept as read | keep the faint value, unblock it | the extraction hold is recomputed | a reason is required |
> | Correct | replace the value | the field is marked "corrected by an analyst" | a reason is required |
> | Record as analyst / compliance | record a decision | a green decision card; a customer message may send automatically | role, band or override refusal |
> | Draft and approve as me | draft and send one message | it appears in Communications and on Customer view | restricted-wording refusal |
> | Open the file / Download the bundle (JSON) | download | — | — |
>
> **The state rule to make visible:** clearing one hold does not move the case. A
> case moves only when *every* hold is clear, and then it runs forward on its own
> through the remaining steps — which can surface a **new** hold. Design the
> "cleared one, still held" state and the "carried on by itself" transition, because
> that sequence is the thing this console exists to show.
