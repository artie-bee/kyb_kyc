"""
Every screen, as HTML.

Pure functions: each takes a connection and returns a string. Nothing here opens
a database, runs an action or holds state, which is what lets a test render any
screen for any case and read the result.

Reads and actions both go through app/data.py - the same functions the Streamlit
app calls. This module never writes SQL, and tests/test_app.py scans it to make
sure. Two front ends over one set of rules is fine; two implementations of the
rules is not.

Values are computed before they reach an f-string rather than nested inside one:
this has to parse on 3.11, where a backslash or a reused quote inside an
interpolation is a syntax error.
"""

import html
import sys
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from app import data                                                  # noqa: E402
from app.customer_view import customer_view, leaks                    # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"

SCREENS = [("Operations dashboard", "/"), ("Case detail", "/case/"),
           ("Customer view", "/customer/"), ("Agent reuse", "/reuse"),
           ("Audit export", "/export/")]

TABS = ["Timeline", "Checklist", "Documents", "People", "Checks",
        "Risk", "Decision", "Communications"]

STATUS_TONE = {
    "submitted": "neutral", "document_quality_review": "info",
    "resubmission_required": "warn", "verification_in_progress": "info",
    "analyst_review_required": "warn", "enhanced_due_diligence": "warn",
    "ready_for_decision": "info", "approved": "ok", "rejected": "bad",
    "closed_withdrawn": "neutral",
}
BAND_TONE = {"low": "ok", "medium": "info", "high": "warn", "critical": "bad",
             "insufficient_evidence": "warn"}
OWNER_TONE = {"analyst": "info", "compliance": "bad", "customer": "warn",
              "system": "neutral"}
RESULT_TONE = {"match": "ok", "pass": "ok", "no_match": "ok", "none": "ok",
               "verified": "ok", "true": "ok",
               "mismatch": "bad", "fail": "bad", "clear_match": "bad",
               "critical": "bad", "false": "bad",
               "possible_match": "warn", "pep_match": "warn", "review": "warn",
               "unavailable": "warn", "moderate": "warn", "medium": "warn",
               "high": "warn"}
CLOSED = ("approved", "rejected", "closed_withdrawn")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def e(value) -> str:
    return html.escape("" if value is None else str(value))


def chip(value, tone_map=None, tone=None) -> str:
    if value in (None, ""):
        return em_dash()
    key = str(value)
    tone = tone or (tone_map or {}).get(key, "neutral")
    return ('<span class="chip chip--' + tone + '"><span class="chip__dot"></span>'
            + e(value) + "</span>")


def em_dash(title: str = "") -> str:
    attr = ' title="' + e(title) + '"' if title else ""
    return "<span class=\"muted\"" + attr + ">&mdash;</span>"


def idtag(value) -> str:
    return '<span class="id">' + e(value) + "</span>" if value else ""


def cell(value, title="") -> str:
    return em_dash(title) if value in (None, "") else e(value)


def note(body, tone="info", title="") -> str:
    head = '<span class="note__title">' + title + "</span>" if title else ""
    return '<div class="note note--' + tone + '">' + head + body + "</div>"


def table(headers, rows, empty="Nothing to show.") -> str:
    if not rows:
        return '<div class="tablewrap"><p class="empty">' + e(empty) + "</p></div>"
    head = "".join('<th scope="col" data-sort="text">' + h + "</th>" for h in headers)
    body = "".join("<tr>" + "".join("<td>" + c + "</td>" for c in r) + "</tr>"
                   for r in rows)
    return ('<div class="tablewrap"><table><thead><tr>' + head
            + "</tr></thead><tbody>" + body + "</tbody></table></div>")


def _back(active, case_id):
    href = dict(SCREENS).get(active, "/")
    return href + case_id if href.endswith("/") and case_id else href


# ---------------------------------------------------------------------------
# Chrome
# ---------------------------------------------------------------------------

def page(title, body, active, conn=None, case_id=None, role="analyst",
         reviewer="analyst.demo", flash=None) -> str:
    mode = data.mode_badge()
    live = mode != "MOCK"
    badge_class = "badge badge--live" if live else "badge"
    badge_text = "live provider calls" if live else "scripted answers, no API calls"
    badge = ('<div class="' + badge_class + '"><span class="badge__dot"></span>'
             "<div><strong>" + e(mode) + "</strong><span>" + badge_text
             + "</span></div></div>")

    cases = [r["case_id"] for r in data.dashboard(conn)] if conn is not None else []
    nav = []
    for name, href in SCREENS:
        if href.endswith("/"):
            target = href + (case_id or (cases[0] if cases else ""))
        else:
            target = href
        on = " navitem--on" if name == active else ""
        nav.append('<a class="navitem' + on + '" href="' + e(target) + '">'
                   + e(name) + "</a>")

    picker = ""
    if cases and active in ("Case detail", "Customer view", "Audit export"):
        base = dict(SCREENS)[active]
        options = "".join(
            '<option value="' + e(c) + '"'
            + (" selected" if c == case_id else "") + ">" + e(c) + "</option>"
            for c in cases)
        picker = ('<div><span class="rail__label">Case</span>'
                  '<select data-base="' + e(base) + '" id="casepick">'
                  + options + "</select></div>")

    flash_html = ""
    if flash:
        kind, text = flash
        flash_html = note(e(text), "bad" if kind == "err" else "ok",
                          "Refused" if kind == "err" else "Done")

    role_a = " selected" if role == "analyst" else ""
    role_c = " selected" if role == "compliance" else ""

    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>" + e(title) + " &middot; Wallester UC4</title>\n"
        '<link rel="stylesheet" href="/static/app.css">\n</head>\n<body>\n'
        '<div class="shell">\n'
        '  <aside class="rail">\n'
        '    <div class="rail__brand">Wallester UC4</div>\n'
        "    " + badge + "\n"
        '    <form method="post" action="/action/settings">\n'
        '      <span class="rail__label">Your role</span>\n'
        '      <select name="role">'
        '<option value="analyst"' + role_a + ">analyst</option>"
        '<option value="compliance"' + role_c + ">compliance</option></select>\n"
        '      <div style="height:8px"></div>\n'
        '      <span class="rail__label">Your name</span>\n'
        '      <input type="text" name="reviewer" value="' + e(reviewer) + '">\n'
        '      <input type="hidden" name="back" value="' + e(_back(active, case_id)) + '">\n'
        '      <div style="height:8px"></div>\n'
        '      <button class="btn btn--wide" type="submit">Apply</button>\n'
        "    </form>\n"
        '    <nav class="rail__nav"><span class="rail__label">Screen</span>'
        + "".join(nav) + "</nav>\n"
        "    " + picker + "\n"
        '    <form method="post" action="/action/reset">\n'
        '      <input type="hidden" name="back" value="/">\n'
        '      <button class="btn btn--wide" type="submit">Reset demo</button>\n'
        "    </form>\n"
        '    <div class="rail__foot">\n'
        '      <div class="kv"><span>Knowledge base</span><code>'
        + e(data.kb().version) + "</code></div>\n"
        '      <button class="btn" type="button" id="theme">Toggle theme</button>\n'
        "    </div>\n"
        "  </aside>\n"
        '  <main class="main">\n' + flash_html + body + "\n  </main>\n"
        "</div>\n"
        '<script src="/static/app.js"></script>\n</body>\n</html>\n')


# ---------------------------------------------------------------------------
# 10.1 Operations dashboard
# ---------------------------------------------------------------------------

def dashboard(conn, **kw) -> str:
    rows = data.dashboard(conn)
    open_cases = [r for r in rows if r["status"] not in CLOSED]
    statuses = sorted({r["status"] for r in rows})
    owners = sorted({r["owner"] for r in rows if r["owner"]})

    body = []
    for r in rows:
        age = r["ageing_days"]
        step = "age--old" if age >= 21 else "age--due" if age >= 14 else ""
        flag = ('<span class="restricted" title="Customer messages are limited '
                'to generic templates">restricted</span>') if r["restricted"] else ""
        band = chip(r["risk_band"], BAND_TONE) if r["risk_band"] \
            else em_dash("not scored yet")
        score = cell(r["risk_score"], "not scored yet")
        holds = ('<span class="count">' + str(r["open_holds"]) + "</span>") \
            if r["open_holds"] else '<span class="muted">0</span>'
        detail = ('<span class="detail" title="' + e(r["hold_detail"]) + '">'
                  + cell(r["hold_detail"]) + "</span>")
        body.append(
            '<tr class="rowlink" data-href="/case/' + e(r["case_id"]) + '"'
            ' data-status="' + e(r["status"]) + '"'
            ' data-owner="' + e(r["owner"]) + '">'
            '<td class="cell-id"><a href="/case/' + e(r["case_id"]) + '">'
            + idtag(r["case_id"]) + "</a>" + flag + "</td>"
            '<td class="cell-name">' + e(r["applicant"]) + "</td>"
            '<td class="cell-type"><span class="type">' + e(r["applicant_type"]) + "</span></td>"
            "<td>" + chip(r["status"], STATUS_TONE) + "</td>"
            "<td>" + chip(r["owner"], OWNER_TONE) + "</td>"
            "<td>" + band + "</td>"
            '<td class="ta-r">' + score + "</td>"
            '<td class="ta-r">' + holds + "</td>"
            '<td class="cell-detail">' + detail + "</td>"
            '<td class="ta-r"><span class="age ' + step + '">' + str(age) + "</span></td>"
            "</tr>")

    def boxes(name, values):
        return "".join(
            '<label class="opt"><input type="checkbox" name="' + name + '" value="'
            + e(v) + '" checked><span>' + e(v) + "</span></label>" for v in values)

    head = ('<th scope="col" data-sort="text">Case</th>'
            '<th scope="col" data-sort="text">Applicant</th>'
            '<th scope="col" data-sort="text">Type</th>'
            '<th scope="col" data-sort="text">Status</th>'
            '<th scope="col" data-sort="text">Owner</th>'
            '<th scope="col" data-sort="text">Band</th>'
            '<th scope="col" data-sort="num" class="ta-r">Score</th>'
            '<th scope="col" data-sort="num" class="ta-r">Holds</th>'
            '<th scope="col">Hold detail</th>'
            '<th scope="col" data-sort="num" class="ta-r">Age&nbsp;(days)</th>')

    n = len(rows)
    return (
        '<header class="page"><h1>Operations dashboard</h1>'
        '<p class="sub">Every case, its owner and what it is waiting for.</p></header>'
        '<section class="stats">'
        '<div class="stat"><span class="stat__k">Cases</span>'
        '<span class="stat__v num">' + str(n) + "</span></div>"
        '<div class="stat"><span class="stat__k">Open</span>'
        '<span class="stat__v num">' + str(len(open_cases)) + "</span></div>"
        '<div class="stat"><span class="stat__k">With holds</span>'
        '<span class="stat__v num">' + str(sum(1 for r in rows if r["open_holds"])) + "</span></div>"
        '<div class="stat"><span class="stat__k">Restricted findings</span>'
        '<span class="stat__v num">' + str(sum(1 for r in rows if r["restricted"])) + "</span></div>"
        "</section>"
        '<section class="filters">'
        "<fieldset><legend>Status</legend>"
        '<div class="opts">' + boxes("status", statuses) + "</div></fieldset>"
        "<fieldset><legend>Next action owner</legend>"
        '<div class="opts">' + boxes("owner", owners) + "</div></fieldset>"
        "</section>"
        '<p class="count-line"><span id="shown">' + str(n) + "</span> of "
        + str(n) + " cases</p>"
        '<div class="tablewrap"><table id="cases"><thead><tr>' + head
        + "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
        '<p class="empty" id="empty" hidden>No case matches these filters.</p></div>'
        + note("A case carrying a restricted finding gets generic customer wording "
               "only. The finding itself is never shown to the applicant.",
               "info", "Restricted findings")
        + '<p class="foot">A dash in Band or Score means the case has not reached '
          "risk scoring &mdash; it is not a score of zero.</p>")


# ---------------------------------------------------------------------------
# 10.2-10.7 Case detail
# ---------------------------------------------------------------------------

def case_detail(conn, case_id, tab="Timeline", role="analyst",
                reviewer="analyst.demo", **kw) -> str:
    detail = data.case(conn, case_id)
    case, applicant = detail["case"], detail["applicant"]
    open_now = data.open_holds(conn, case_id)

    if open_now:
        rows = []
        for h in open_now:
            why = h.reason.split(": ", 1)[-1]
            rows.append('<div class="hold">' + idtag(h.hold_id)
                        + chip(h.code, tone="warn") + chip(h.owner, OWNER_TONE)
                        + '<span class="hold__why">' + e(why) + "</span>"
                        + '<span class="hold__by">' + e(h.placed_by_step) + "</span></div>")
        holds_html = note('<div class="holds">' + "".join(rows) + "</div>", "warn",
                          "Open holds &mdash; the case cannot be approved while any "
                          "of these stand")
    else:
        holds_html = note("No open holds.", "ok")

    restricted = ""
    if case["restricted_finding"]:
        restricted = note("Customer messages are limited to generic templates. "
                          "Nothing about the finding may reach the applicant.",
                          "bad", "Restricted finding on this case")

    future = ""
    if data.is_white_label(conn, case_id):
        steps = "".join("<li><strong>" + e(n) + "</strong> &mdash; " + e(d)
                        + ' <span class="muted">(future phase, not in this POC)</span></li>'
                        for n, d in data.FUTURE_PHASE_STEPS)
        scope = case["entity_scope"] or "undetermined"
        extra = (" &mdash; which Wallester entity contracts with the partner is "
                 "settled in the programme phase, not here."
                 if scope == "undetermined" else "")
        future = note("This is a white-label partner. The POC does the KYB intake "
                      "and stops; everything below belongs to the programme phase."
                      "<p><strong>Entity scope:</strong> <code>" + e(scope)
                      + "</code>" + extra + "</p><ul>" + steps + "</ul>",
                      "info", "Future phase")

    tabs = "".join('<a class="tab' + (" tab--on" if t == tab else "") + '" href="/case/'
                   + e(case_id) + "?tab=" + e(t) + '">' + e(t) + "</a>"
                   for t in TABS)

    renderers = {
        "Timeline": _tab_timeline, "Checklist": _tab_checklist,
        "Documents": _tab_documents, "People": _tab_people,
        "Checks": _tab_checks, "Risk": _tab_risk,
        "Decision": _tab_decision, "Communications": _tab_comms,
    }
    inner = renderers[tab](conn, case_id, role=role, reviewer=reviewer)

    return (
        '<header class="page"><h1>' + idtag(case_id) + " " + e(applicant["legal_name"])
        + "</h1></header>"
        '<section class="stats">'
        '<div class="stat"><span class="stat__k">Status</span>'
        '<span class="stat__v stat__v--sm">' + chip(case["status"], STATUS_TONE) + "</span></div>"
        '<div class="stat"><span class="stat__k">Owner</span>'
        '<span class="stat__v stat__v--sm">' + chip(case["next_action_owner"], OWNER_TONE) + "</span></div>"
        '<div class="stat"><span class="stat__k">Type</span>'
        '<span class="stat__v stat__v--sm"><span class="type">'
        + e(case["applicant_type"] or "-") + "</span></span></div>"
        '<div class="stat"><span class="stat__k">Age (days)</span>'
        '<span class="stat__v num">' + str(data.ageing_days(case["created_at"])) + "</span></div>"
        "</section>"
        + holds_html + restricted + future
        + '<nav class="tabs">' + tabs + "</nav>" + inner)


def _tab_timeline(conn, case_id, **kw):
    events = data.audit_trail(conn, case_id)
    rows = [[idtag(ev["event_id"]),
             '<span class="type">' + e(ev["timestamp"]) + "</span>",
             e(ev["actor_type"]) + ": " + e(ev["actor_id"]),
             chip(ev["action"], tone="neutral"),
             '<span class="detail" title="' + e(ev["payload_summary"]) + '">'
             + cell(ev["payload_summary"]) + "</span>",
             '<span class="type">' + e(ev["model_or_prompt_version"] or "") + "</span>"]
            for ev in events]
    return ("<h2>Audit trail</h2>"
            '<p class="count-line">' + str(len(events)) + " events, oldest first</p>"
            + table(["Event", "When", "Actor", "Action", "Detail", "Version"], rows,
                    "No audit events."))


def _tab_checklist(conn, case_id, **kw):
    items = data.checklist(conn, case_id)
    required = [i for i in items if i["level"] == "required"]
    accepted = [i for i in required if i["status"] == "accepted"]
    tone = {"accepted": "ok", "pending": "warn", "waived": "neutral",
            "resubmission_requested": "bad", "manual_review": "warn"}
    rows = []
    for i in items:
        level_tone = "info" if i["level"] == "required" else "neutral"
        rows.append([idtag(i["item_id"]),
                     '<span class="type">' + e(i["rule_id"]) + "</span>",
                     e(i["document_type"]), chip(i["level"], tone=level_tone),
                     chip(i["status"], tone), str(i["resubmission_attempts"]),
                     cell(i["file_name"])])
    return ("<h2>Requirement checklist</h2>"
            '<p class="count-line">' + str(len(accepted)) + " of " + str(len(required))
            + " required items accepted (" + str(len(items)) + " items in total)</p>"
            + table(["Item", "Rule", "Document", "Level", "Status", "Attempts", "File"],
                    rows, "No checklist."))


def _tab_documents(conn, case_id, role="analyst", reviewer="analyst.demo", **kw):
    out = ["<h2>Documents and extraction</h2>"]
    for doc in data.documents(conn, case_id):
        held = doc["quality_status"] == "manual_review_required"
        flags = ('<span class="type">[' + e(doc["quality_flags"]) + "]</span>"
                 if doc["quality_flags"] else "")
        summary = ("<summary>" + e(doc["file_name"])
                   + chip(doc["quality_status"],
                          {"accepted_for_checks": "ok",
                           "manual_review_required": "warn",
                           "resubmission_required": "bad"})
                   + flags + "</summary>")

        if doc["sample_path"]:
            href = "/doc/" + e(doc["document_id"])
            if doc["sample_path"].suffix.lower() in (".jpg", ".jpeg", ".png"):
                left = ('<div class="filecard"><img src="' + href + '" alt="'
                        + e(doc["file_name"]) + '">'
                        '<a class="btn" href="' + href + '" download>Open the file</a></div>')
            else:
                left = ('<div class="filecard"><p class="muted">PDF: '
                        + e(doc["sample_path"].name) + "</p>"
                        '<a class="btn" href="' + href + '" download>Open the file</a></div>')
        else:
            left = ('<div class="filecard"><p class="muted">No sample file '
                    "generated for this document.</p></div>")

        right = ["<p><strong>Screen verdict:</strong> "
                 + cell(doc["quality_status_at_screen"]) + "</p>"]
        if doc["resubmission_reasons"]:
            right.append("<p><strong>Reasons:</strong> " + e(doc["resubmission_reasons"]) + "</p>")
        if doc["released_by"]:
            right.append(note("Released by " + e(doc["released_by"]) + ": "
                              + e(doc["release_reason"]), "info"))
        if held:
            right.append(note("Releasing it is a decision on the record.", "warn",
                              "Held for an analyst"))
            right.append(
                '<form method="post" action="/action/release-document">'
                '<input type="hidden" name="document_id" value="' + e(doc["document_id"]) + '">'
                '<input type="hidden" name="back" value="/case/' + e(case_id) + '?tab=Documents">'
                '<div class="field"><label>Reason</label>'
                '<input type="text" name="reason" required></div>'
                '<div class="btn-row">'
                '<button class="btn btn--primary" name="choice" value="accept">Accept</button>'
                '<button class="btn" name="choice" value="request_resubmission">'
                "Request resubmission</button></div></form>")

        fields = ""
        if doc["fields"]:
            rows = []
            for f in doc["fields"]:
                low = f["needs_analyst_correction"] and not f["corrected_by_analyst"]
                if f["corrected_by_analyst"]:
                    state = chip("corrected by an analyst", tone="ok")
                elif low:
                    state = chip("below the confidence floor", tone="bad")
                else:
                    state = ""
                value = e(f["value"]) if f["value"] is not None else em_dash("not read")
                rows.append('<div class="fieldrow"><span class="type">' + e(f["name"])
                            + "</span><span>" + value + '</span><span class="num">'
                            + ("%.2f" % float(f["confidence"])) + "</span><span>"
                            + state + "</span></div>")
                if low:
                    back = "/case/" + e(case_id) + "?tab=Documents"
                    rows.append(
                        '<form method="post" action="/action/field" class="panel panel--tight">'
                        '<input type="hidden" name="field_id" value="' + e(f["field_id"]) + '">'
                        '<input type="hidden" name="back" value="' + back + '">'
                        '<p class="help">' + e(f["field_id"])
                        + ": accept the reading as it stands, or replace it.</p>"
                        '<div class="field"><label>Corrected value</label>'
                        '<input type="text" name="value" value="' + e(f["value"] or "") + '"></div>'
                        '<div class="field"><label>Reason</label>'
                        '<input type="text" name="reason" required></div>'
                        '<div class="btn-row">'
                        '<button class="btn" name="how" value="accept">Accept as read</button>'
                        '<button class="btn btn--primary" name="how" value="correct">Correct</button>'
                        "</div></form>")
            fields = "<h3>Extracted fields</h3>" + "".join(rows)

        cls = "doc doc--held" if held else "doc"
        opened = " open" if held else ""
        out.append('<details class="' + cls + '"' + opened + ">"
                   + summary + '<div class="body"><div class="grid2">' + left
                   + "<div>" + "".join(right) + "</div></div>" + fields + "</div></details>")
    if len(out) == 1:
        out.append('<p class="empty">No documents on this case.</p>')
    return "".join(out)


def _tab_people(conn, case_id, **kw):
    people = data.people(conn, case_id)
    rows = [[idtag(p["individual_id"]), e(p["full_name"]),
             chip(p["role"], tone="neutral"), cell(p["date_of_birth"]),
             cell(p["nationality"]), cell(p["residence_country"])] for p in people]
    out = ["<h2>People</h2>",
           table(["Id", "Name", "Role", "Date of birth", "Nationality", "Resident in"],
                 rows, "No individuals recorded.")]

    owners = data.ubos(conn, case_id)
    out.append("<h2>Beneficial ownership</h2>")
    if not owners:
        out.append('<p class="empty">No beneficial owners declared.</p>')
        return "".join(out)

    threshold = data.kb().ubo_threshold
    urows = []
    for u in owners:
        tone = "ok" if u["verification_status"] == "verified" else "bad"
        urows.append([idtag(u["ubo_id"]) + " " + e(u["full_name"]),
                      '<span class="type">' + e(u["control_type"]) + "</span>",
                      "<strong>" + e(u["working"]) + "</strong>",
                      ("%g%%" % float(u["ownership_percentage"])),
                      chip(u["verification_status"], tone=tone)])
    out.append(table(["Owner", "Control", "Effective ownership", "Declared", "Status"],
                     urows))
    for u in owners:
        if u["effective"] >= threshold and u["verification_status"] != "verified":
            out.append(note(e(u["full_name"]) + " holds " + ("%g" % u["effective"])
                            + "%, at or above the " + ("%g" % threshold)
                            + "% threshold, and is not verified.", "warn"))
    out.append('<p class="foot">Ownership through a company is multiplied along the '
               "chain. The threshold is " + ("%g" % threshold) + "%.</p>")
    return "".join(out)


def _tab_checks(conn, case_id, **kw):
    result = data.checks(conn, case_id)
    out = ["<h2>Registry</h2>"]
    if not result["registry"]:
        out.append('<p class="empty">No registry check was run: the case did not '
                   "reach the paid step.</p>")
    else:
        reg = result["registry"]
        attempts = "attempts" if reg["attempts"] > 1 else "attempt"
        status_tone = "ok" if reg["company_status"] == "active" else "bad"
        out.append('<div class="panel"><strong>' + e(reg["provider_name"])
                   + "</strong> &mdash; company status "
                   + chip(reg["company_status"], tone=status_tone)
                   + " result " + chip(reg["result"], RESULT_TONE)
                   + ' <span class="muted">(' + attempts + ": "
                   + str(reg["attempts"]) + ")</span></div>")
        supported = str(reg["ubo_supported_by_registry"]).lower() == "true"
        out.append(note("The register corroborates the declared beneficial ownership."
                        if supported else
                        "The register does <strong>not</strong> corroborate the declared "
                        "beneficial ownership. The chain rests on the applicant's own "
                        "documents.", "ok" if supported else "warn"))
        rows = [[e(name), cell(held), cell(got), chip(outcome, RESULT_TONE)]
                for name, held, got, outcome in result["comparisons"]]
        out.append(table(["Compared", "The register holds", "Extracted from documents",
                          "Result"], rows))
        out.append('<p class="foot">Match results are computed here by comparing the '
                   "two columns, corrections included &mdash; not taken from the "
                   "provider.</p>")

    out.append("<h2>Identity</h2>")
    rows = [[idtag(r["check_id"]), e(r["full_name"]),
             chip(r["document_result"], RESULT_TONE),
             chip(r["liveness_result"], RESULT_TONE),
             chip(r["biometric_result"], RESULT_TONE),
             cell(r["name_dob_match"]), cell(r["document_expired"]),
             cell(r["duplicate_individual_detected"]),
             chip(r["result"], RESULT_TONE)] for r in result["identity"]]
    out.append(table(["Check", "Subject", "Document", "Liveness", "Biometric",
                      "Name/DOB", "Expired", "Duplicate", "Result"], rows,
                     "No identity checks were run."))

    out.append("<h2>Screening</h2>")
    rows = [[idtag(r["check_id"]), e(r["full_name"] or "the applicant entity"),
             chip(r["sanctions_result"], RESULT_TONE),
             chip(r["pep_result"], RESULT_TONE),
             chip(r["adverse_media_result"], RESULT_TONE),
             chip(r["severity"], RESULT_TONE),
             '<span class="type">' + e(r["evidence_refs"] or "") + "</span>"]
            for r in result["screening"]]
    out.append(table(["Check", "Subject", "Sanctions", "PEP", "Adverse media",
                      "Severity", "Provider refs"], rows, "No screening was run."))
    if result["screening"]:
        out.append(note("Internal only. None of this may be repeated to the "
                        "applicant.", "bad"))
    return "".join(out)


def _tab_risk(conn, case_id, **kw):
    result = data.risk(conn, case_id)
    if not result["assessment"]:
        return ('<p class="empty">No risk assessment yet: the case has not '
                "reached Step 7.</p>")
    a = result["assessment"]
    score = "not scored" if a["risk_score"] is None else str(a["risk_score"])
    out = ['<section class="stats">'
           '<div class="stat"><span class="stat__k">Band</span>'
           '<span class="stat__v stat__v--sm">' + chip(a["risk_band"], BAND_TONE) + "</span></div>"
           '<div class="stat"><span class="stat__k">Score</span>'
           '<span class="stat__v num">' + e(score) + "</span></div>"
           '<div class="stat"><span class="stat__k">Recommended</span>'
           '<span class="stat__v stat__v--sm">' + chip(a["recommended_action"], tone="info")
           + "</span></div></section>"]
    if a["requires_human_signoff"]:
        out.append(note("Human sign-off is required. The system cannot decide "
                        "this case.", "info"))
    if result["floors"]:
        items = "".join("<li><code>" + e(c) + "</code> forces at least <strong>"
                        + e(b) + "</strong></li>" for c, b in result["floors"])
        out.append(note("<ul>" + items + "</ul>", "warn",
                        "Hard floors applied &mdash; these override the score"))

    out.append("<h2>Risk factors</h2>")
    rows = [[e(f["factor"]), '<span class="num">' + str(f["weight"]) + "</span>",
             e(f["explanation"]),
             '<span class="type">' + e((f["evidence_refs"] or "").replace("|", ", ")) + "</span>"]
            for f in result["factors"]]
    out.append(table(["Factor", "Points", "Why", "Evidence"], rows, "No factors fired."))
    out.append('<p class="foot">Every weight in kb/risk_scoring_matrix.csv is a POC '
               "placeholder for Wallester to confirm; the brief does not state them.</p>")

    if result["pack"]:
        pack = result["pack"]
        out.append("<h2>Evidence pack</h2>")
        out.append('<div class="panel">' + e(pack["applicant_summary"]) + "</div>")
        if pack["missing_or_conflicting_evidence"]:
            out.append(note(e(pack["missing_or_conflicting_evidence"]), "warn",
                            "Missing or conflicting"))
        out.append(note("This narrative is internal. It is not shown to the applicant "
                        "and must not be quoted to them.", "bad",
                        "INTERNAL &mdash; NOT FOR CUSTOMER"))
        out.append('<div class="readonly">'
                   + e(pack["draft_compliance_narrative"] or "") + "</div>")
    return "".join(out)


def _tab_decision(conn, case_id, role="analyst", reviewer="analyst.demo", **kw):
    out = ["<h2>Decision</h2>"]
    for d in data.decisions(conn, case_id):
        body = ("<strong>" + e(d["decision"]) + "</strong> by " + e(d["reviewer"])
                + " (" + e(d["reviewer_role"]) + ") &mdash; " + e(d["reason_code"])
                + "<p>" + e(d["rationale"]) + "</p>")
        out.append(note(body, "ok"))
        if d["override_flag"]:
            out.append(note("Override (" + e(d["override_direction"]) + "): "
                            + e(d["override_reason"]), "warn"))

    open_now = data.open_holds(conn, case_id)
    if open_now:
        out.append(note(str(len(open_now)) + " hold(s) open. A decision that would "
                        "close the case is refused while any stands &mdash; try it "
                        "and the backend will say so.", "info"))

    withheld = data.withheld_decisions(conn, case_id)
    band = data.band_for_decisions(conn, case_id)
    if withheld:
        items = "".join("<li><strong>" + e(w["decision"]) + "</strong> (allowed at "
                        + e(w["allowed_bands"]) + ")</li>" for w in withheld)
        out.append(note("The taxonomy does not allow these here, so they are not on "
                        "the list to be attempted:<ul>" + items + "</ul>", "warn",
                        "Not offered at band <code>" + e(band) + "</code>"))

    options = data.available_decisions(conn, case_id)
    out.append('<p class="foot">The dropdown is kb/analyst_decision_taxonomy.csv read '
               "at this band. The role each decision needs is checked by the backend "
               "when you record it.</p>")
    if not options:
        out.append('<p class="empty">No decision is available at this band.</p>')
        return "".join(out)

    opts = "".join("<option>" + e(o) + "</option>" for o in options)
    out.append(
        '<form method="post" action="/action/record-decision" class="panel">'
        '<input type="hidden" name="case_id" value="' + e(case_id) + '">'
        '<input type="hidden" name="back" value="/case/' + e(case_id) + '?tab=Decision">'
        '<div class="field"><label>Decision</label><select name="choice">' + opts
        + "</select></div>"
        '<div class="field"><label>Reason code</label>'
        '<input type="text" name="reason_code" value="demo_decision"></div>'
        '<div class="field"><label>Rationale</label><textarea name="rationale"></textarea></div>'
        '<div class="field"><label>Override reason</label>'
        '<input type="text" name="override_reason">'
        '<span class="help">Required only if the decision differs from the '
        "recommendation; the backend computes that, not you.</span></div>"
        '<div class="field"><label>Escalation target</label>'
        '<input type="text" name="escalation_target">'
        '<span class="help">Required for escalate.</span></div>'
        '<button class="btn btn--primary" type="submit">Record as ' + e(role)
        + "</button></form>")
    return "".join(out)


def _tab_comms(conn, case_id, reviewer="analyst.demo", **kw):
    out = ["<h2>Communications</h2>"]
    for t in data.compliance_tasks(conn, case_id):
        out.append(note(e(t["reason"]), "bad", "Compliance task " + e(t["task_id"])
                        + ": " + e(t["task"])))

    messages = data.communications(conn, case_id)
    if not messages:
        out.append('<p class="empty">Nothing drafted yet.</p>')
    for m in messages:
        head = (idtag(m["template_id"]) + " to " + e(m["audience"]) + " "
                + chip(m["approval_status"], {"approved": "ok"})
                + chip(m["sent_status"], {"sent": "ok", "not_sent": "neutral"}))
        meta = (e(m["communication_id"]) + " &mdash; " + e(m["message_type"])
                + ", situation " + e(m["situation"] or "-")
                + (", approved by " + e(m["approved_by"]) if m["approved_by"] else ""))
        out.append('<details class="doc"><summary>' + head + "</summary>"
                   '<div class="body"><p>' + e(m["rendered_text"]) + "</p>"
                   '<p class="foot">' + meta + "</p></div></details>")

    situations = sorted({r["situation"] for r in data.kb().communication_rules})
    opts = "".join("<option>" + e(s) + "</option>" for s in situations)
    out.append(
        "<h3>Send a message</h3>"
        '<form method="post" action="/action/send-message" class="panel">'
        '<input type="hidden" name="case_id" value="' + e(case_id) + '">'
        '<input type="hidden" name="back" value="/case/' + e(case_id) + '?tab=Communications">'
        '<div class="field"><label>Situation</label><select name="situation">' + opts
        + "</select></div>"
        '<button class="btn btn--primary" type="submit">Draft and approve as me</button>'
        "</form>")
    return "".join(out)


# ---------------------------------------------------------------------------
# The customer view, and the two reference screens
# ---------------------------------------------------------------------------

def customer(conn, case_id, **kw) -> str:
    view = customer_view(conn, case_id)
    found = leaks(view)
    if found:
        return ('<div class="customer">'
                + note("Restricted wording " + e(", ".join(found)) + " reached "
                       "customer-facing content. That is a bug, not a display "
                       "problem.", "bad", "This page refuses to render")
                + "</div>")

    msgs = []
    for m in view["messages"]:
        msgs.append('<div class="msg"><span class="msg__when">' + e(m["sent_at"])
                    + "</span>" + e(m["text"]) + "</div>")
    if not msgs:
        msgs.append('<p class="empty">No messages yet.</p>')

    return ('<div class="customer">'
            '<header class="page"><h1>' + e(view["applicant_name"]) + "</h1>"
            '<p class="sub">Reference ' + e(view["case_id"]) + " &middot; Applied "
            + e(view["applied_on"]) + "</p></header>"
            + note(e(view["status_text"]), "info")
            + "<h2>Messages we have sent you</h2>" + "".join(msgs)
            + '<p class="foot">Exactly what the applicant sees. Nothing internal '
              "appears on this page.</p></div>")


def reuse(conn=None, **kw) -> str:
    t = data.reuse_table()
    rows = [[e(r["component"]), e(r["generic"]), e(r["override"]),
             '<span class="type">'
             + e(", ".join(f["file"] + " (v" + f["version"] + ")" for f in r["files"]))
             + "</span>"] for r in t["rows"]]
    out = ['<header class="page"><h1>Agent reuse (10.9)</h1></header>',
           note(e(data.REUSE_CAPTION), "info"),
           '<p class="foot">The two middle columns are the wording of the brief. The '
           "notes under each component are this POC's commentary on how the override "
           "is realised, and are not from the brief.</p>",
           "<p>Knowledge base in force: <strong>" + e(t["kb_version"]) + "</strong></p>",
           table(["Component", "Generic agent", "Wallester variant override",
                  "Implemented by"], rows),
           "<h2>The files behind each override</h2>"]
    for r in t["rows"]:
        files = []
        for f in r["files"]:
            if not f["exists"]:
                files.append(note(e(f["file"]) + " is named here but missing from "
                                  "the repository.", "bad"))
                continue
            files.append("<p><code>" + e(f["file"]) + "</code> &mdash; version <strong>"
                         + e(f["version"]) + "</strong>, " + str(f["rules"]) + " rules</p>"
                         '<pre class="code">'
                         + e(f["path"].read_text(encoding="utf-8")) + "</pre>")
        out.append('<details class="doc"><summary>' + e(r["component"]) + "</summary>"
                   '<div class="body"><p><strong>Generic agent:</strong> ' + e(r["generic"])
                   + "</p><p><strong>Wallester variant override:</strong> " + e(r["override"])
                   + '</p><p class="foot">How this POC realises it: ' + e(r["notes"])
                   + "</p>" + "".join(files) + "</div></details>")
    out.append('<p class="foot">Read-only. Changing a rule is a CSV edit and a version '
               "bump in kb/kb_manifest.json &mdash; no release.</p>")
    return "".join(out)


def export(conn, case_id, **kw) -> str:
    b = data.export_bundle(conn, case_id)
    rows = [[e(k), '<span class="num">' + str(v) + "</span>"]
            for k, v in b["row_counts"].items()]
    versions = ", ".join("<code>" + e(v) + "</code>"
                         for v in b["model_and_prompt_versions"]) or "none"
    return (
        '<header class="page"><h1>Audit export</h1>'
        '<p class="sub">Every row on the case, the full audit trail, the KB versions '
        "in force and every model or prompt version used.</p></header>"
        '<section class="stats">'
        '<div class="stat"><span class="stat__k">Rows</span>'
        '<span class="stat__v num">' + str(sum(b["row_counts"].values())) + "</span></div>"
        '<div class="stat"><span class="stat__k">Audit events</span>'
        '<span class="stat__v num">' + str(len(b["audit_trail"])) + "</span></div>"
        '<div class="stat"><span class="stat__k">KB version</span>'
        '<span class="stat__v stat__v--sm">' + e(b["kb_version"]) + "</span></div>"
        "</section>"
        "<p><strong>Model and prompt versions used:</strong> " + versions + "</p>"
        + table(["Table", "Rows"], rows)
        + '<p style="margin-top:14px"><a class="btn btn--primary" href="/export/'
        + e(case_id) + '.json" download>Download the bundle (JSON)</a></p>')
