"""
Wallester UC4 demo screens (brief Section 10).

    streamlit run app/main.py

This app reads the database freely and writes to it never. Every action on every
screen calls the orchestrator's own function, so a refusal you see here - "cannot
approve while holds are open", "compliance role required" - is the real rule
refusing, not a message this app invented.

Clarity over styling: plain tables, plain words, and the refusal text shown
verbatim so the reason is the one the system actually gave.
"""

import json
import sys
from pathlib import Path

import streamlit as st

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from app import data                                                    # noqa: E402
from app.customer_view import customer_view, leaks                      # noqa: E402

SCREENS = ["Operations dashboard", "Case detail", "Customer view",
           "Agent reuse", "Audit export"]


# ---------------------------------------------------------------------------

def sidebar():
    st.sidebar.title("Wallester UC4")

    mode = data.mode_badge()
    if mode == "MOCK":
        st.sidebar.success("**MOCK** - scripted answers, no API calls")
    else:
        st.sidebar.warning(f"**{mode}** - live provider calls")
    st.sidebar.radio(
        "AI and provider mode", ["Mock", "Live - pending API access"],
        index=0, disabled=True, key="mode",
        help="Live mode is written but untested against the real API; see "
             "README.md, 'Live mode (pending)'.")

    st.sidebar.divider()
    role = st.sidebar.radio("Your role", ["analyst", "compliance"], index=0, key="role",
                            help="Sets reviewer_role. The taxonomy decides what each "
                                 "role may do; this switch does not override it.")
    reviewer = st.sidebar.text_input("Your name", value=f"{role}.demo", key="reviewer")

    st.sidebar.divider()
    if st.sidebar.button("Reset demo", width="stretch"):
        with st.spinner("Rebuilding to the demo start state..."):
            data.reset_demo()
        st.sidebar.success("Reset. Every case is at its first human action.")
        st.cache_resource.clear()
        st.rerun()
    st.sidebar.caption("Reset runs all 14 cases up to their first human action "
                       "and stops, so the releases and decisions can be made here.")
    return role, reviewer


@st.cache_resource
def get_conn():
    if not data.DB_PATH.exists():
        data.reset_demo()
    return data.connect()


# ---------------------------------------------------------------------------

def screen_dashboard(conn):
    st.header("Operations dashboard")
    rows = data.dashboard(conn)

    open_cases = [r for r in rows if r["status"] not in
                  ("approved", "rejected", "closed_withdrawn")]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cases", len(rows))
    c2.metric("Open", len(open_cases))
    c3.metric("With holds", sum(1 for r in rows if r["open_holds"]))
    c4.metric("Restricted findings", sum(1 for r in rows if r["restricted"]))

    statuses = sorted({r["status"] for r in rows})
    owners = sorted({r["owner"] for r in rows if r["owner"]})
    f1, f2 = st.columns(2)
    chosen_status = f1.multiselect("Status", statuses, default=statuses)
    chosen_owner = f2.multiselect("Next action owner", owners, default=owners)

    shown = [r for r in rows
             if r["status"] in chosen_status and r["owner"] in chosen_owner]
    st.caption(f"{len(shown)} of {len(rows)} cases")
    st.dataframe(
        [{"Case": r["case_id"], "Applicant": r["applicant"], "Type": r["applicant_type"],
          "Status": r["status"], "Owner": r["owner"], "Band": r["risk_band"],
          "Score": r["risk_score"], "Holds": r["open_holds"],
          "Hold detail": r["hold_detail"], "Age (days)": r["ageing_days"]}
         for r in shown],
        width="stretch", hide_index=True)

    if any(r["restricted"] for r in shown):
        st.info("Cases with a restricted finding get generic customer wording only. "
                "The finding itself is never shown to the applicant.")


def screen_case(conn, case_id, role, reviewer):
    detail = data.case(conn, case_id)
    case, applicant = detail["case"], detail["applicant"]

    st.header(f"{case_id} - {applicant['legal_name']}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", case["status"])
    c2.metric("Owner", case["next_action_owner"] or "-")
    c3.metric("Type", case["applicant_type"] or "-")
    c4.metric("Age (days)", data.ageing_days(case["created_at"]))

    open_now = data.open_holds(conn, case_id)
    if open_now:
        st.warning("**Open holds** - the case cannot be approved while any of these stand:\n\n"
                   + "\n".join(f"- `{h.hold_id}` **{h.code}** ({h.owner}), placed by "
                               f"{h.placed_by_step}: {h.reason.split(': ', 1)[-1]}"
                               for h in open_now))
    else:
        st.success("No open holds.")
    if case["restricted_finding"]:
        st.error("**Restricted finding on this case.** Customer messages are limited to "
                 "generic templates. Nothing about the finding may reach the applicant.")

    if data.is_white_label(conn, case_id):
        with st.container(border=True):
            st.subheader("Future phase")
            st.caption("This is a white-label partner. The POC does the KYB intake and "
                       "stops; everything below belongs to the programme phase.")
            for name, detail in data.FUTURE_PHASE_STEPS:
                st.write(f"- **{name}** - {detail}")
                st.caption("future phase, not in this POC")

    tabs = st.tabs(["Timeline", "Checklist", "Documents", "People", "Checks",
                    "Risk", "Decision", "Communications"])
    with tabs[0]:
        tab_timeline(conn, case_id)
    with tabs[1]:
        tab_checklist(conn, case_id)
    with tabs[2]:
        tab_documents(conn, case_id, reviewer)
    with tabs[3]:
        tab_people(conn, case_id)
    with tabs[4]:
        tab_checks(conn, case_id)
    with tabs[5]:
        tab_risk(conn, case_id)
    with tabs[6]:
        tab_decision(conn, case_id, role, reviewer)
    with tabs[7]:
        tab_communications(conn, case_id, reviewer)


def tab_timeline(conn, case_id):
    st.subheader("Audit trail")
    events = data.audit_trail(conn, case_id)
    st.caption(f"{len(events)} events, oldest first")
    st.dataframe(
        [{"Event": e["event_id"], "When": e["timestamp"], "Actor": f"{e['actor_type']}: "
          f"{e['actor_id']}", "Action": e["action"], "Detail": e["payload_summary"],
          "Version": e["model_or_prompt_version"] or ""} for e in events],
        width="stretch", hide_index=True, height=420)


def tab_checklist(conn, case_id):
    st.subheader("Requirement checklist")
    items = data.checklist(conn, case_id)
    required = [i for i in items if i["level"] == "required"]
    accepted = [i for i in required if i["status"] == "accepted"]
    st.caption(f"{len(accepted)} of {len(required)} required items accepted "
               f"({len(items)} items in total)")
    st.dataframe(
        [{"Item": i["item_id"], "Rule": i["rule_id"], "Document": i["document_type"],
          "Level": i["level"], "Status": i["status"], "Attempts": i["resubmission_attempts"],
          "File": i["file_name"] or "", "Note": i["note"] or ""} for i in items],
        width="stretch", hide_index=True)


def tab_documents(conn, case_id, reviewer):
    st.subheader("Documents and extraction")
    for doc in data.documents(conn, case_id):
        held = doc["quality_status"] == "manual_review_required"
        label = (f"{doc['file_name']} - {doc['quality_status']}"
                 + (f"  [{doc['quality_flags']}]" if doc["quality_flags"] else ""))
        with st.expander(label, expanded=held):
            left, right = st.columns([1, 2])
            with left:
                if doc["sample_path"]:
                    if doc["sample_path"].suffix.lower() in (".jpg", ".jpeg", ".png"):
                        st.image(str(doc["sample_path"]), width="stretch")
                    else:
                        st.caption(f"PDF: {doc['sample_path'].name}")
                        st.download_button("Open the file",
                                           doc["sample_path"].read_bytes(),
                                           file_name=doc["sample_path"].name,
                                           key=f"dl_{doc['document_id']}")
                else:
                    st.caption("No sample file generated for this document.")
            with right:
                st.write(f"**Screen verdict:** {doc['quality_status_at_screen'] or '-'}")
                if doc["resubmission_reasons"]:
                    st.write(f"**Reasons:** {doc['resubmission_reasons']}")
                if doc["released_by"]:
                    st.info(f"Released by {doc['released_by']}: {doc['release_reason']}")

                if held:
                    st.warning("Held for an analyst. Releasing it is a decision on the "
                               "record.")
                    reason = st.text_input("Reason", key=f"rr_{doc['document_id']}")
                    b1, b2 = st.columns(2)
                    if b1.button("Accept", key=f"acc_{doc['document_id']}"):
                        _do(lambda: data.release_document(
                            conn, doc["document_id"], reviewer, "accept", reason))
                    if b2.button("Request resubmission", key=f"req_{doc['document_id']}"):
                        _do(lambda: data.release_document(
                            conn, doc["document_id"], reviewer, "request_resubmission", reason))

            if doc["fields"]:
                st.write("**Extracted fields**")
                for f in doc["fields"]:
                    low = f["needs_analyst_correction"] and not f["corrected_by_analyst"]
                    cols = st.columns([2, 3, 1, 3])
                    cols[0].write(f"`{f['name']}`")
                    cols[1].write(f["value"] if f["value"] is not None else "_(not read)_")
                    cols[2].write(f"{float(f['confidence']):.2f}")
                    if f["corrected_by_analyst"]:
                        cols[3].success("corrected by an analyst")
                    elif low:
                        cols[3].error("below the confidence floor")
                    if low:
                        with st.form(key=f"fix_{f['field_id']}"):
                            st.caption(f"{f['field_id']}: accept the reading as it stands, "
                                       f"or replace it.")
                            new_value = st.text_input("Corrected value",
                                                      value=f["value"] or "")
                            why = st.text_input("Reason")
                            a, b = st.columns(2)
                            if a.form_submit_button("Accept as read"):
                                _do(lambda: data.accept_field_as_read(
                                    conn, f["field_id"], reviewer, why))
                            if b.form_submit_button("Correct"):
                                _do(lambda: data.correct_field(
                                    conn, f["field_id"], reviewer, new_value, why))


def tab_people(conn, case_id):
    st.subheader("People")
    st.dataframe(
        [{"Id": p["individual_id"], "Name": p["full_name"], "Role": p["role"],
          "Date of birth": p["date_of_birth"] or "", "Nationality": p["nationality"] or "",
          "Resident in": p["residence_country"] or ""} for p in data.people(conn, case_id)],
        width="stretch", hide_index=True)

    st.subheader("Beneficial ownership")
    owners = data.ubos(conn, case_id)
    if not owners:
        st.caption("No beneficial owners declared.")
        return
    threshold = data.kb().ubo_threshold
    for u in owners:
        cols = st.columns([3, 3, 2, 2])
        cols[0].write(f"**{u['full_name']}**  \n`{u['ubo_id']}` {u['control_type']}")
        cols[1].write(f"Effective ownership  \n**{u['working']}**")
        cols[2].write(f"Declared  \n{float(u['ownership_percentage']):g}%")
        if u["verification_status"] == "verified":
            cols[3].success("verified")
        else:
            cols[3].error(u["verification_status"])
        if u["effective"] >= threshold and u["verification_status"] != "verified":
            st.warning(f"{u['full_name']} holds {u['effective']:g}%, at or above the "
                       f"{threshold:g}% threshold, and is not verified.")
    st.caption(f"Ownership through a company is multiplied along the chain. "
               f"The threshold is {threshold:g}%.")


def tab_checks(conn, case_id):
    result = data.checks(conn, case_id)

    st.subheader("Registry")
    if not result["registry"]:
        st.caption("No registry check was run: the case did not reach the paid step.")
    else:
        reg = result["registry"]
        st.write(f"**{reg['provider_name']}** - company status **{reg['company_status']}**, "
                 f"result **{reg['result']}** "
                 f"(attempt{'s' if reg['attempts'] > 1 else ''}: {reg['attempts']})")
        st.dataframe(
            [{"Compared": name, "The register holds": held or "-",
              "Extracted from documents": got or "-", "Result": outcome}
             for name, held, got, outcome in result["comparisons"]],
            width="stretch", hide_index=True)
        st.caption("Match results are computed here by comparing the two columns, "
                   "corrections included - not taken from the provider.")

    st.subheader("Identity")
    if not result["identity"]:
        st.caption("No identity checks were run.")
    else:
        st.dataframe(
            [{"Check": r["check_id"], "Subject": r["full_name"], "Document": r["document_result"],
              "Liveness": r["liveness_result"], "Biometric": r["biometric_result"],
              "Name/DOB": r["name_dob_match"], "Expired": r["document_expired"],
              "Duplicate": r["duplicate_individual_detected"], "Result": r["result"]}
             for r in result["identity"]], width="stretch", hide_index=True)

    st.subheader("Screening")
    if not result["screening"]:
        st.caption("No screening was run.")
    else:
        st.dataframe(
            [{"Check": r["check_id"], "Subject": r["full_name"] or "the applicant entity",
              "Sanctions": r["sanctions_result"], "PEP": r["pep_result"],
              "Adverse media": r["adverse_media_result"], "Severity": r["severity"],
              "Provider refs": r["evidence_refs"] or ""} for r in result["screening"]],
            width="stretch", hide_index=True)
        st.caption("Internal only. None of this may be repeated to the applicant.")


def tab_risk(conn, case_id):
    result = data.risk(conn, case_id)
    if not result["assessment"]:
        st.caption("No risk assessment yet: the case has not reached Step 7.")
        return

    a = result["assessment"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Band", a["risk_band"])
    c2.metric("Score", "not scored" if a["risk_score"] is None else a["risk_score"])
    c3.metric("Recommended", a["recommended_action"])
    if a["requires_human_signoff"]:
        st.info("Human sign-off is required. The system cannot decide this case.")

    if result["floors"]:
        st.warning("**Hard floors applied** - these override the score:\n\n"
                   + "\n".join(f"- `{condition}` forces at least **{band}**"
                               for condition, band in result["floors"]))

    st.subheader("Risk factors")
    if not result["factors"]:
        st.caption("No factors fired.")
    else:
        st.dataframe(
            [{"Factor": f["factor"], "Points": f["weight"], "Why": f["explanation"],
              "Evidence": (f["evidence_refs"] or "").replace("|", ", ")}
             for f in result["factors"]], width="stretch", hide_index=True)
    st.caption("Every weight in kb/risk_scoring_matrix.csv is a POC placeholder for "
               "Wallester to confirm; the brief does not state them.")

    if result["pack"]:
        st.subheader("Evidence pack")
        pack = result["pack"]
        st.write(pack["applicant_summary"])
        if pack["missing_or_conflicting_evidence"]:
            st.warning(f"**Missing or conflicting:** {pack['missing_or_conflicting_evidence']}")
        st.error("**INTERNAL - NOT FOR CUSTOMER**")
        st.text_area("Draft compliance narrative", pack["draft_compliance_narrative"] or "",
                     height=220, disabled=True, label_visibility="collapsed")


def tab_decision(conn, case_id, role, reviewer):
    st.subheader("Decision")
    already = data.decisions(conn, case_id)
    if already:
        for d in already:
            st.success(f"**{d['decision']}** by {d['reviewer']} ({d['reviewer_role']}) - "
                       f"{d['reason_code']}")
            st.caption(d["rationale"])
            if d["override_flag"]:
                st.warning(f"Override ({d['override_direction']}): {d['override_reason']}")

    open_now = data.open_holds(conn, case_id)
    if open_now:
        st.info(f"{len(open_now)} hold(s) open. Approving is refused while any stands - "
                f"try it and the backend will say so.")

    options = data.available_decisions(conn, case_id)
    if not options:
        st.caption("No decision is available at this band.")
        return

    with st.form("decision"):
        choice = st.selectbox("Decision", options)
        reason_code = st.text_input("Reason code", value="demo_decision")
        rationale = st.text_area("Rationale")
        override_reason = st.text_input(
            "Override reason", help="Required only if the decision differs from the "
                                    "recommendation; the backend computes that, not you.")
        escalation_target = st.text_input("Escalation target",
                                          help="Required for 'escalate'.")
        if st.form_submit_button(f"Record as {role}"):
            _do(lambda: data.record_decision(
                conn, case_id, reviewer, role, choice, reason_code, rationale,
                override_reason=override_reason or None,
                escalation_target=escalation_target or None))


def tab_communications(conn, case_id, reviewer):
    st.subheader("Communications")
    tasks = data.compliance_tasks(conn, case_id)
    for t in tasks:
        st.error(f"**Compliance task {t['task_id']}: {t['task']}** - {t['reason']}")

    messages = data.communications(conn, case_id)
    if not messages:
        st.caption("Nothing drafted yet.")
    for m in messages:
        icon = {"sent": "✓", "not_sent": "·"}.get(m["sent_status"], "·")
        with st.expander(f"{icon} {m['template_id']} to {m['audience']} - "
                         f"{m['approval_status']}, {m['sent_status']}"):
            st.write(m["rendered_text"])
            st.caption(f"{m['communication_id']} - {m['message_type']}, "
                       f"situation {m['situation'] or '-'}"
                       + (f", approved by {m['approved_by']}" if m["approved_by"] else ""))

    st.divider()
    st.write("**Send a message**")
    situations = sorted({r["situation"] for r in data.kb().communication_rules})
    with st.form("send"):
        situation = st.selectbox("Situation", situations)
        if st.form_submit_button("Draft and approve as me"):
            _do(lambda: data.send_message(conn, case_id, situation, reviewer))


def screen_customer(conn, case_id):
    st.header("Customer view")
    st.caption("Exactly what the applicant sees. Nothing internal appears on this page.")

    view = customer_view(conn, case_id)
    found = leaks(view)
    if found:
        st.error(f"This page refuses to render: restricted wording {found} reached "
                 f"customer-facing content. That is a bug, not a display problem.")
        return

    st.subheader(view["applicant_name"])
    st.write(f"**Reference** {view['case_id']}  ·  **Applied** {view['applied_on']}")
    st.info(view["status_text"])

    st.subheader("Messages we have sent you")
    if not view["messages"]:
        st.caption("No messages yet.")
    for m in view["messages"]:
        with st.container(border=True):
            st.caption(m["sent_at"])
            st.write(m["text"])


def screen_reuse():
    st.header("Agent reuse (10.9)")
    st.info(data.REUSE_CAPTION)
    st.caption("The two columns below are the brief's own wording. The notes under "
               "each component are this POC's commentary on how the override is "
               "realised, and are not from the brief.")

    table = data.reuse_table()
    st.write(f"Knowledge base in force: **{table['kb_version']}**")

    st.dataframe(
        [{"Component": row["component"],
          "Generic agent": row["generic"],
          "Wallester variant override": row["override"],
          "Implemented by": ", ".join(f"{f['file']} (v{f['version']})"
                                      for f in row["files"])}
         for row in table["rows"]],
        width="stretch", hide_index=True)

    st.subheader("The files behind each override")
    for row in table["rows"]:
        with st.expander(f"{row['component']} - "
                         f"{', '.join(f['file'] for f in row['files'])}"):
            st.write(f"**Generic agent:** {row['generic']}")
            st.write(f"**Wallester variant override:** {row['override']}")
            st.caption(f"How this POC realises it: {row['notes']}")
            for f in row["files"]:
                cols = st.columns([3, 1, 1])
                cols[0].write(f"`{f['file']}`")
                cols[1].write(f"version **{f['version']}**")
                cols[2].write(f"{f['rules']} rules")
                if not f["exists"]:
                    st.error(f"{f['file']} is named here but missing from the repository.")
                    continue
                st.code(f["path"].read_text(encoding="utf-8"), language="csv")
    st.caption("Read-only. Changing a rule is a CSV edit and a version bump in "
               "kb/kb_manifest.json - no release.")


def screen_export(conn, case_id):
    st.header("Audit export")
    st.caption("Every row on the case, the full audit trail, the KB versions in force "
               "and every model or prompt version used.")
    bundle = data.export_bundle(conn, case_id)

    c1, c2, c3 = st.columns(3)
    c1.metric("Rows", sum(bundle["row_counts"].values()))
    c2.metric("Audit events", len(bundle["audit_trail"]))
    c3.metric("KB version", bundle["kb_version"])
    st.write("**Model and prompt versions used:** "
             + (", ".join(f"`{v}`" for v in bundle["model_and_prompt_versions"]) or "none"))
    st.dataframe([{"Table": k, "Rows": v} for k, v in bundle["row_counts"].items()],
                 width="stretch", hide_index=True)
    st.download_button("Download the bundle (JSON)",
                       json.dumps(bundle, indent=2, ensure_ascii=False),
                       file_name=f"{case_id}.json", mime="application/json")


def _do(action):
    """Run an action and show whatever the backend says, refusal included."""
    try:
        action()
    except Exception as e:                      # the backend's refusals are the message
        st.error(f"**{type(e).__name__}**\n\n{e}")
    else:
        st.success("Done.")
        st.rerun()


def main():
    st.set_page_config(page_title="Wallester UC4 demo", layout="wide")
    role, reviewer = sidebar()
    conn = get_conn()

    screen = st.sidebar.radio("Screen", SCREENS, key="screen")
    case_ids = [r["case_id"] for r in data.dashboard(conn)]
    case_id = None
    if screen not in ("Operations dashboard", "Agent reuse"):
        case_id = st.sidebar.selectbox("Case", case_ids, key="case")

    if screen == "Operations dashboard":
        screen_dashboard(conn)
    elif screen == "Case detail":
        screen_case(conn, case_id, role, reviewer)
    elif screen == "Customer view":
        screen_customer(conn, case_id)
    elif screen == "Agent reuse":
        screen_reuse()
    else:
        screen_export(conn, case_id)


if __name__ == "__main__":
    main()
