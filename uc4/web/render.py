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

# Which screens are about one case, and so take a case id on the end of their
# path. Named rather than inferred from a trailing slash: "/" ends in a slash
# too, and inferring it sent every "Operations dashboard" link to /<case-id>.
CASE_SCREENS = {"Case detail", "Customer view", "Audit export"}

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


def help_mark(text: str, edge: bool = False) -> str:
    """A "?" that explains a label. Pure CSS, and a real button so it is
    reachable by keyboard rather than hover-only."""
    extra = " qmark--left" if edge else ""
    return ('<button type="button" class="qmark' + extra + '" tabindex="0"'
            ' aria-label="What is this?" data-help="' + e(text) + '">?</button>')


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
    return href + case_id if active in CASE_SCREENS and case_id else href


# ---------------------------------------------------------------------------
# Chrome
# ---------------------------------------------------------------------------

def page(title, body, active, conn=None, case_id=None, role="analyst",
         reviewer="analyst.demo", flash=None) -> str:
    # The MOCK badge was removed from the sidebar at the user's request. The
    # amber LIVE state is kept: with nothing on screen saying so, a console
    # pointed at a real provider would look exactly like the demo, and that is
    # the one confusion worth a line of chrome.
    mode = data.mode_badge()
    badge = ""
    if mode != "MOCK":
        badge = ('<div class="badge badge--live"><span class="badge__dot"></span>'
                 "<div><strong>" + e(mode) + "</strong><span>live provider calls"
                 "</span></div></div>")

    cases = [r["case_id"] for r in data.dashboard(conn)] if conn is not None else []
    nav = []
    for name, href in SCREENS:
        if name in CASE_SCREENS:
            target = href + (case_id or (cases[0] if cases else ""))
        else:
            target = href
        on = " navitem--on" if name == active else ""
        nav.append('<a class="navitem' + on + '" href="' + e(target) + '">'
                   + e(name) + "</a>")

    picker = ""
    if cases and active in CASE_SCREENS:
        base = dict(SCREENS)[active]
        options = "".join(
            '<option value="' + e(c) + '"'
            + (" selected" if c == case_id else "") + ">" + e(c) + "</option>"
            for c in cases)
        picker = ('<div><span class="rail__label">Case</span>'
                  '<select data-base="' + e(base) + '" id="casepick">'
                  + options + "</select></div>")

    flash_html = ""
    if flash and flash[0] == "link":
        flash_html = note(
            "<p>Send this link to the customer. It opens their application only, and "
            "stops working after the expiry shown in the audit trail. It is shown this "
            "once.</p>"
            '<div class="btn-row"><input type="text" class="linkbox" id="customer-link" '
            'readonly value="' + e(flash[1]) + '">'
            '<button class="btn btn--primary" type="button" data-copy="customer-link">'
            "Copy</button></div>", "ok", "Customer link")
    elif flash:
        kind, text = flash
        flash_html = note(e(text), "bad" if kind == "err" else "ok",
                          "Refused" if kind == "err" else "Done")

    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>" + e(title) + " &middot; Wallester UC4</title>\n"
        '<link rel="stylesheet" href="/static/app.css">\n</head>\n<body>\n'
        '<div class="shell">\n'
        '  <aside class="rail">\n'
        '    <div class="rail__brand">Wallester UC4</div>\n'
        + ("    " + badge + "\n" if badge else "")
        + '    <nav class="rail__nav"><span class="rail__label">Screen</span>'
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
            '<th scope="col">Hold detail</th>')

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
                          "of these stand"
                          + help_mark("A hold is a lock placed by one step. Only that "
                                      "step, or the named person it belongs to, can "
                                      "release it. The case moves only when every "
                                      "hold is clear - and carrying on can surface a "
                                      "new one."))
    else:
        holds_html = note("No open holds.", "ok")

    # The restricted-finding banner used to sit here. Removed at the user's
    # request: the enforcement is unchanged and lives in the communication step
    # - a narrowed template library, no automatic message at all on a confirmed
    # sanctions match, and a wording scan on every rendered message before it
    # can be sent. What has gone is the warning to the analyst, not the rule.
    restricted = ""

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

    demo = _demo_panel(conn, case_id, reviewer) if data.is_demo_case(case_id) else ""
    demo += _reassessment_panel(conn, case_id)

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
        "</section>"
        '<form method="post" action="/action/customer-link" class="btn-row linkform">'
        '<input type="hidden" name="case_id" value="' + e(case_id) + '">'
        '<input type="hidden" name="back" value="/case/' + e(case_id) + "?tab=" + e(tab) + '">'
        '<button class="btn" type="submit">Copy customer link</button></form>'
        + holds_html + restricted + future + demo
        + '<nav class="tabs">' + tabs + "</nav>" + inner)


def _reassessment_panel(conn, case_id) -> str:
    """New evidence arrived after the assessment: the analyst decides whether it
    changes anything. The paid checks run again only from here."""
    hold = data.reassessment_hold(conn, case_id)
    if hold is None:
        return ""
    back = "/case/" + e(case_id)
    return note(
        "<p>A document arrived after the paid checks answered. It has been screened; "
        "once it has been read (Documents tab), choose one. The risk band stays as it "
        "is until you do.</p>"
        '<form method="post" action="/action/reassess">'
        '<input type="hidden" name="case_id" value="' + e(case_id) + '">'
        '<input type="hidden" name="back" value="' + back + '">'
        '<div class="field"><label>Reason</label><input type="text" name="reason" required>'
        "</div>"
        '<div class="btn-row">'
        '<button class="btn btn--primary" name="choice" value="rerun">Re-run verification'
        "</button>"
        '<button class="btn" name="choice" value="keep">Keep the assessment</button></div>'
        '<p class="foot">Re-running replaces the registry, identity and risk results; the '
        "previous ones are archived in the audit trail. Screening results are kept as they "
        "are.</p></form>", "warn", "New evidence after assessment")


def _demo_panel(conn, case_id, reviewer="analyst.demo") -> str:
    """A case made in the portal's demo form: say so, and let the analyst choose
    what the simulated providers will find. Console only - the portal has no
    route, form or word for this."""
    current = data.demo_scenario(conn, case_id)
    back = "/case/" + e(case_id)
    if data.document_stage_open(conn, case_id):
        options = "".join('<option value="' + e(k) + '"' + (" selected" if k == current else "")
                          + ">" + e(v) + "</option>" for k, v in data.DEMO_SCENARIOS.items())
        control = ('<form method="post" action="/action/demo-scenario" class="btn-row">'
                   '<input type="hidden" name="case_id" value="' + e(case_id) + '">'
                   '<input type="hidden" name="back" value="' + back + '">'
                   '<select name="scenario" aria-label="Demo scenario">' + options + "</select>"
                   '<button class="btn btn--primary" type="submit">Set scenario</button></form>'
                   '<p class="foot">Applies when Steps 5 and 6 run. The choice is audited, and '
                   "fixed once the providers have answered.</p>")
    else:
        control = ("<p>Scenario <code>" + e(current) + "</code> &mdash; fixed: the simulated "
                   "providers have already answered on this case.</p>")
    return note("Made in the customer portal's demo application form, with synthetic data. "
                "Every registry, identity and screening result on this case is a <strong>"
                + e(data.SIMULATED) + "</strong>, not a real check. The case is left out of "
                "every dataset comparison, and Reset demo deletes it."
                "<h3>Demo scenario</h3>" + control, "info", "Demo case")


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


# What each checklist status means for the eye: the colour of the left rule and
# the mark in the circle. "waived" is deliberately neutral rather than green -
# the item was not satisfied, it was ruled not to apply, and those are different.
CHECK_STATUS = {
    "accepted":               ("ok",   "✓", "accepted"),
    "pending":                ("warn", "•", "still to come"),
    "manual_review":          ("warn", "!",      "with an analyst"),
    "resubmission_requested": ("bad",  "↻", "sent back to the customer"),
    "rejected":               ("bad",  "✗", "rejected"),
    "waived":                 ("none", "–", "not required here"),
}
LEVEL_ORDER = [("required", "Required"), ("conditional", "Conditional"),
               ("optional", "Optional")]


def _tab_checklist(conn, case_id, **kw):
    items = data.checklist(conn, case_id)
    if not items:
        return "<h2>Requirement checklist</h2><p class=\"empty\">No checklist.</p>"

    required = [i for i in items if i["level"] == "required"]
    accepted = [i for i in required if i["status"] == "accepted"]
    outstanding = [i for i in required if i["status"] != "accepted"]

    # The bar is the headline: how much of the required pack is in.
    done = len(accepted)
    total = len(required) or 1
    pct = int(round(100 * done / total))
    fill_class = "progress__fill" if done == len(required) else "progress__fill progress__fill--part"
    if outstanding:
        note_text = (str(len(outstanding)) + " required item"
                     + ("s" if len(outstanding) != 1 else "") + " outstanding: "
                     + ", ".join(i["document_type"].replace("_", " ") for i in outstanding))
    else:
        note_text = "Every required item is in."

    out = ["<h2>Requirement checklist</h2>",
           '<div class="progress">'
           '<div class="progress__head">'
           '<span class="progress__count">' + str(done) + " of " + str(len(required))
           + "<small>required items accepted</small></span>"
           '<span class="progress__note">' + e(note_text) + "</span></div>"
           '<div class="progress__bar"><div class="' + fill_class
           + '" style="width:' + str(pct) + '%"></div></div></div>']

    seen = set()
    for level, heading in LEVEL_ORDER:
        group = [i for i in items if i["level"] == level]
        seen.update(id(i) for i in group)
        if not group:
            continue
        in_hand = sum(1 for i in group if i["status"] == "accepted")
        extra = " group--optional" if level == "optional" else ""
        out.append('<section class="group' + extra + '">'
                   '<div class="group__head"><span class="group__name">' + heading
                   + '</span><span class="group__tally">' + str(in_hand) + " of "
                   + str(len(group)) + " accepted</span></div>")
        for i in group:
            out.append(_check_row(i, case_id))
        out.append("</section>")

    # Anything with a level the KB has added since this list was written still
    # has to appear; a checklist that quietly drops an item is worse than an
    # ugly one.
    rest = [i for i in items if id(i) not in seen]
    if rest:
        out.append('<section class="group"><div class="group__head">'
                   '<span class="group__name">Other</span></div>')
        out.extend(_check_row(i, case_id) for i in rest)
        out.append("</section>")
    out.append(_add_item_form(conn, case_id))
    return "".join(out)


def _add_item_form(conn, case_id) -> str:
    """Ask the customer for one more document after the pack was built. It
    appears on their checklist at once, by a generic name on a restricted case."""
    types = "".join('<option value="' + e(t) + '">' + e(t.replace("_", " ")) + "</option>"
                    for t in data.addable_document_types())
    people = '<option value="">The business, not a person</option>' + "".join(
        '<option value="' + e(p["individual_id"]) + '">' + e(p["full_name"]) + "</option>"
        for p in data.people(conn, case_id))
    return ('<form method="post" action="/action/add-item" class="panel">'
            '<input type="hidden" name="case_id" value="' + e(case_id) + '">'
            '<input type="hidden" name="back" value="/case/' + e(case_id) + '?tab=Checklist">'
            '<h3>Request another document</h3>'
            '<div class="formgrid">'
            '<div class="field"><label>Document</label><select name="document_type">'
            + types + "</select></div>"
            '<div class="field"><label>For</label><select name="subject">' + people
            + "</select></div>"
            '<div class="field"><label>Reason (internal)</label>'
            '<input type="text" name="reason" required></div></div>'
            '<button class="btn btn--primary" type="submit">Add to the checklist</button>'
            '<p class="foot">Added as required. The customer sees it on their checklist at '
            "once; send the matching approved message from the Communications tab.</p>"
            "</form>")


def _check_row(item, case_id="") -> str:
    tone, mark, plain = CHECK_STATUS.get(item["status"], ("none", "?", item["status"]))
    confirm = ""
    if item.get("awaiting_confirmation"):
        plain = "awaiting your confirmation"
        confirm = ('<form method="post" action="/action/confirm-condition" class="btn-row '
                   'check__confirm">'
                   '<input type="hidden" name="item_id" value="' + e(item["item_id"]) + '">'
                   '<input type="hidden" name="back" value="/case/' + e(case_id)
                   + '?tab=Checklist">'
                   '<input type="text" name="reason" placeholder="Reason" required>'
                   '<button class="btn" name="applies" value="yes">Applies</button>'
                   '<button class="btn" name="applies" value="no">Does not apply</button>'
                   "</form>")
    if item["file_name"]:
        file_html = '<span class="check__file">' + e(item["file_name"]) + "</span>"
    else:
        file_html = ('<span class="check__file check__file--none">no file '
                     + e(plain) + "</span>")
    attempts = ""
    if item["resubmission_attempts"]:
        attempts = ('<span class="check__attempts">' + str(item["resubmission_attempts"])
                    + " attempt" + ("s" if item["resubmission_attempts"] != 1 else "")
                    + "</span>")
    return ('<div class="check check--' + tone + '">'
            '<span class="check__mark" aria-hidden="true">' + mark + "</span>"
            '<span class="check__body"><span class="check__doc">'
            + e(item["document_type"].replace("_", " ")) + "</span>" + file_html + "</span>"
            '<span class="check__side">' + attempts
            + chip(plain, tone=("neutral" if tone == "none" else tone))
            + '<span class="check__ids">' + e(item["rule_id"]) + " &middot; "
            + e(item["item_id"]) + "</span></span>" + confirm + "</div>")


def _tab_documents(conn, case_id, role="analyst", reviewer="analyst.demo", **kw):
    out = ["<h2>Documents and extraction</h2>"]
    names = data.uploaded_names(conn, case_id)
    recognised = data.recognised_documents(conn, case_id)
    for doc in data.documents(conn, case_id):
        replayed = doc["document_id"] in recognised
        held = doc["quality_status"] == "manual_review_required"
        flags = ('<span class="type">[' + e(doc["quality_flags"]) + "]</span>"
                 if doc["quality_flags"] else "")
        own_name = names.get(doc["file_name"])
        by_applicant = (chip("uploaded by applicant", tone="info")
                        if own_name is not None else "")
        summary = ("<summary>" + e(own_name or doc["file_name"])
                   + chip(doc["quality_status"],
                          {"accepted_for_checks": "ok",
                           "manual_review_required": "warn",
                           "resubmission_required": "bad",
                           "superseded": "neutral"})
                   + by_applicant
                   + (chip(data.DEMO_SAMPLE_LABEL, tone="warn") if replayed else "")
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
        if own_name is not None:
            right.append("<p><strong>Uploaded by the applicant</strong> as "
                         + e(own_name) + '; stored as <span class="type">'
                         + e(doc["file_name"]) + "</span></p>")
        if doc["quality_status"] == "superseded":
            right.append(note("Replaced by a newer upload on the same checklist item. Kept "
                              "on the record; nothing downstream reads it.", "info"))
        if doc["resubmission_reasons"]:
            right.append("<p><strong>Reasons:</strong> " + e(doc["resubmission_reasons"]) + "</p>")
        if doc["released_by"]:
            right.append(note("Released by " + e(doc["released_by"]) + ": "
                              + e(doc["release_reason"]), "info"))
        if held:
            right.append(note("Accept keeps the document and lets the case carry "
                              "on. Request resubmission sends it back to the "
                              "customer. Either way your name and reason go on the "
                              "record.", "warn",
                              "Held for an analyst"
                              + help_mark("The quality screen could not settle this "
                                          "one, so it is waiting on a person. "
                                          "Whichever you choose is audited against "
                                          "your name.")))
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
        awaiting = [f for f in doc["fields"] if f["entry_method"] == "awaiting_analyst_entry"]
        if doc["fields"]:
            rows = []
            for f in doc["fields"]:
                if f["entry_method"] == "awaiting_analyst_entry":
                    continue                    # typed in through the form below
                keyed = f["entry_method"] == "entered_by_analyst"
                low = (f["needs_analyst_correction"] and not f["corrected_by_analyst"]
                       and not keyed)
                if keyed:
                    state = chip("entered by an analyst", tone="info")
                elif replayed:
                    state = chip(data.DEMO_SAMPLE_LABEL, tone="warn")
                elif f["corrected_by_analyst"]:
                    state = chip("corrected by an analyst", tone="ok")
                elif low:
                    state = chip("below the confidence floor", tone="bad")
                else:
                    state = ""
                value = e(f["value"]) if f["value"] is not None else em_dash("not given")
                # A typed-in value has no machine confidence to show.
                confidence = em_dash("typed in, not read") if keyed else (
                    "%.2f" % float(f["confidence"]))
                rows.append('<div class="fieldrow"><span class="type">' + e(f["name"])
                            + "</span><span>" + value + '</span><span class="num">'
                            + confidence + "</span><span>" + state + "</span></div>")
                if low:
                    back = "/case/" + e(case_id) + "?tab=Documents"
                    rows.append(
                        '<form method="post" action="/action/field" class="panel panel--tight">'
                        '<input type="hidden" name="field_id" value="' + e(f["field_id"]) + '">'
                        '<input type="hidden" name="back" value="' + back + '">'
                        '<p class="help">' + e(f["field_id"])
                        + ": accept the reading as it stands, or replace it."
                        + help_mark("Accept as read means you looked at the original "
                                    "and the faint reading was right - the value does "
                                    "not change. Correct replaces it. Both are "
                                    "recorded against your name; neither is "
                                    "'dismiss'.") + "</p>"
                        '<div class="field"><label>Corrected value</label>'
                        '<input type="text" name="value" value="' + e(f["value"] or "") + '"></div>'
                        '<div class="field"><label>Reason</label>'
                        '<input type="text" name="reason" required></div>'
                        '<div class="btn-row">'
                        '<button class="btn" name="how" value="accept">Accept as read</button>'
                        '<button class="btn btn--primary" name="how" value="correct">Correct</button>'
                        "</div></form>")
            if rows:
                fields = "<h3>Fields</h3>" + "".join(rows)
        if awaiting:
            # Beside the preview, so the analyst types what the file says.
            right.append(_entry_form(case_id, doc, awaiting))
            held = True

        cls = "doc doc--held" if held else "doc"
        opened = " open" if held else ""
        body_right = "".join(right)
        out.append('<details class="' + cls + '"' + opened + ">"
                   + summary + '<div class="body"><div class="grid2">' + left
                   + "<div>" + body_right + "</div></div>" + fields + "</div></details>")
    if len(out) == 1:
        out.append('<p class="empty">No documents on this case.</p>')
    return "".join(out)


def _entry_form(case_id, doc, awaiting) -> str:
    """Mock mode: nothing reads an uploaded file, so the analyst types in what
    it says. Recorded as entered_by_analyst - never as extracted."""
    required = set(data.kb().required_fields_for(doc["document_type"]))
    # Every input starts EMPTY. Nothing the customer declared is offered here:
    # the value typed is what the document says, or the comparison it feeds
    # would only be the application compared with itself.
    inputs = "".join(
        '<div class="field"><label>' + e(f["name"].replace("_", " "))
        + (" (required)" if f["name"] in required else "") + "</label>"
        '<input type="text" value="" autocomplete="off" name="f_' + e(f["name"]) + '"'
        + (" required" if f["name"] in required else "")
        + (' placeholder="YYYY-MM-DD"' if f["name"].endswith("date") or f["name"] == "date_of_birth"
           else "") + "></div>"
        for f in awaiting)
    return ('<form method="post" action="/action/enter-fields" class="panel">'
            '<input type="hidden" name="document_id" value="' + e(doc["document_id"]) + '">'
            '<input type="hidden" name="back" value="/case/' + e(case_id) + '?tab=Documents">'
            + note("Fields not read automatically in mock mode. Open the file, then type in "
                   "what it says. Each value is recorded as <strong>entered by an "
                   "analyst</strong>, under your name, and never as extracted. Dates typed "
                   "here go through the same expiry and age rules as the quality screen.",
                   "warn", "Type in this document's fields")
            + '<div class="formgrid">' + inputs + "</div>"
            '<button class="btn btn--primary" type="submit">Save the fields</button></form>')


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
    out = []
    if data.is_demo_case(case_id) and (result["registry"] or result["identity"]
                                       or result["screening"]):
        out.append(note("Every result on this tab is a <strong>" + e(data.SIMULATED)
                        + "</strong> (scenario <code>" + e(data.demo_scenario(conn, case_id))
                        + "</code>). None of it is a real check.", "warn", "Simulated"))
    out.append("<h2>Registry</h2>")
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
        out.append(table(["Compared", "The register holds",
                          "From the documents (read, corrected or typed in)", "Result"], rows))
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
             chip(r["result"], RESULT_TONE),
             '<span class="type">' + e(r["provider_name"]) + "</span>"]
            for r in result["identity"]]
    out.append(table(["Check", "Subject", "Document", "Liveness", "Biometric",
                      "Name/DOB", "Expired", "Duplicate", "Result", "Provider"], rows,
                     "No identity checks were run."))

    out.append("<h2>Screening</h2>")
    rows = [[idtag(r["check_id"]), e(r["full_name"] or "the applicant entity"),
             chip(r["sanctions_result"], RESULT_TONE),
             chip(r["pep_result"], RESULT_TONE),
             chip(r["adverse_media_result"], RESULT_TONE),
             chip(r["severity"], RESULT_TONE),
             '<span class="type">' + e(r["evidence_refs"] or "") + "</span>",
             '<span class="type">' + e(r["provider_name"]) + "</span>"]
            for r in result["screening"]]
    out.append(table(["Check", "Subject", "Sanctions", "PEP", "Adverse media",
                      "Severity", "Provider refs", "Provider"], rows, "No screening was run."))
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

    band = data.band_for_decisions(conn, case_id)
    options = data.available_decisions(conn, case_id)
    if not options:
        out.append('<p class="empty">No decision is available at this band.</p>')
        return "".join(out)

    # What the system concluded. Without this the analyst has to leave the tab
    # to find out whether they are agreeing with the recommendation or
    # overriding it - which is the one thing that changes what the form needs.
    a = data.risk(conn, case_id)["assessment"]
    recommended = a["recommended_action"] if a else None
    score = "not scored" if not a or a["risk_score"] is None else str(a["risk_score"])
    signoff = ("Required" if a and a["requires_human_signoff"] else "Not required")
    out.append(
        '<div class="recobar">'
        '<div class="recobar__cell"><span class="recobar__k">Band'
        + help_mark("Which risk bucket the score fell into, or the band a hard "
                    "floor forced. It decides which decisions are offered at all.")
        + '</span><span class="recobar__v">' + chip(band, BAND_TONE) + "</span></div>"
        '<div class="recobar__cell"><span class="recobar__k">Score'
        + help_mark("Points added up from kb/risk_scoring_matrix.csv. Every weight "
                    "is a POC placeholder for Wallester to confirm.")
        + '</span><span class="recobar__v num">' + e(score) + "</span></div>"
        '<div class="recobar__cell"><span class="recobar__k">System recommends'
        + help_mark("What the rules suggest. It is a recommendation, not a "
                    "decision: nothing is recorded until a named person records it. "
                    "Choosing anything else is an override and needs its own reason.")
        + '</span><span class="recobar__v">'
        + (chip(recommended, tone="info") if recommended else em_dash()) + "</span></div>"
        '<div class="recobar__cell"><span class="recobar__k">Human sign-off'
        + help_mark("Whether the rules say this case cannot be closed without a "
                    "person signing it off.")
        + '</span><span class="recobar__v">' + e(signoff) + "</span></div></div>")

    out.append(_ai_preparation(conn, case_id, role))

    opts = []
    for o in options:
        same = " (matches the recommendation)" if o == recommended else ""
        opts.append('<option value="' + e(o) + '">' + e(o) + e(same) + "</option>")

    out.append(
        '<form method="post" action="/action/record-decision" class="panel">'
        '<input type="hidden" name="case_id" value="' + e(case_id) + '">'
        '<input type="hidden" name="back" value="/case/' + e(case_id) + '?tab=Decision">'

        '<div class="formsection"><div class="formsection__head">The decision</div>'
        '<div class="field"><label>Decision'
        + help_mark("Only the decisions kb/analyst_decision_taxonomy.csv allows at "
                    "band " + band + " appear here. Some also need a particular "
                    "role, which the backend checks when you record it.")
        + '</label><select name="choice">' + "".join(opts) + "</select></div>"
        '<div class="field"><label>Rationale'
        + help_mark("Why you decided this, in plain words. Required for every "
                    "decision. This is what a reviewer reads months later, so "
                    "write it for them rather than for yourself today.")
        + '</label><textarea name="rationale" '
          'placeholder="What you concluded, and what you based it on."></textarea></div>'
        '<div class="field"><label>Reason code'
        + help_mark("The category behind the decision, from "
                    "kb/decision_reason_codes.csv. The rationale is for a person "
                    "to read; this is the part that can be counted later - how "
                    "many rejections last quarter were entity_not_active_on_register. "
                    "Free text cannot answer that, because two analysts will word "
                    "the same thing differently.")
        + "</label>" + _reason_code_select(conn, case_id, options) + "</div>"
        "</div>"

        '<div class="formsection"><div class="formsection__head">Only when they apply'
        + help_mark("Both of these are usually left empty. Fill one only in the "
                    "case described beside it.", edge=True)
        + "</div>"
        '<div class="formgrid">'
        '<div class="field"><label>Override reason'
        + help_mark("Fill this only if your decision differs from what the system "
                    "recommended. You do not declare an override - the backend "
                    "compares the two and refuses without a reason if they differ.")
        + '</label><input type="text" name="override_reason" '
          'placeholder="Only if you disagree with the recommendation">'
        '<span class="help">Recommended here: <strong>'
        + (e(recommended) if recommended else "none") + "</strong></span></div>"
        '<div class="field"><label>Escalation target'
        + help_mark("Who the case goes to. Required for 'escalate' and ignored "
                    "otherwise, for example sanctions.desk.")
        + '</label><input type="text" name="escalation_target" '
          'placeholder="Only for escalate"></div>'
        "</div></div>"

        '<div class="formsection"><div class="formsection__head">Acting as'
        + help_mark("Who is recording this. The role is checked by the backend "
                    "against kb/analyst_decision_taxonomy.csv when you submit - "
                    "escalate, for instance, is compliance's to take at high and "
                    "critical, and an analyst who tries it is refused.")
        + "</div>"
        '<div class="formgrid">'
        '<div class="field"><label>Role</label><select name="role">'
        '<option value="analyst"' + (" selected" if role == "analyst" else "")
        + ">analyst</option>"
        '<option value="compliance"' + (" selected" if role == "compliance" else "")
        + ">compliance</option></select></div>"
        '<div class="field"><label>Your name</label>'
        '<input type="text" name="reviewer" value="' + e(reviewer) + '"></div>'
        "</div></div>"
        '<button class="btn btn--primary" type="submit">Record this decision</button>'
        "</form>")
    return "".join(out)


def _reason_code_select(conn, case_id, options) -> str:
    """The reason codes the KB allows, grouped by the decision each one suits.

    Grouped rather than flat because the list is long and most of it is
    irrelevant to whichever decision is being recorded: an optgroup per
    decision lets the eye skip to the right part. Codes that apply everywhere
    sit in their own group at the end.
    """
    kb = data.kb()
    groups, seen = [], set()
    for decision in options:
        rows = [r for r in kb.reason_codes_for(decision) if r["applies_to"] != "*"]
        if not rows:
            continue
        items = []
        for r in rows:
            items.append('<option value="' + e(r["code"]) + '" title="'
                         + e(r["description"]) + '">' + e(r["code"]) + "</option>")
            seen.add(r["code"])
        groups.append('<optgroup label="' + e(decision) + '">'
                      + "".join(items) + "</optgroup>")

    anywhere = [r for r in kb.decision_reason_codes if r["applies_to"] == "*"]
    if anywhere:
        groups.append('<optgroup label="any decision">'
                      + "".join('<option value="' + e(r["code"]) + '" title="'
                                + e(r["description"]) + '">' + e(r["code"]) + "</option>"
                                for r in anywhere) + "</optgroup>")

    if not groups:                       # a KB with no codes is still usable
        return '<input type="text" name="reason_code" value="demo_decision">'
    return '<select name="reason_code">' + "".join(groups) + "</select>"


def _next_steps(conn, case_id, role) -> str:
    """What this analyst can actually do next, and what is in the way.

    Derived, not drafted: every line comes from the open holds and from
    kb/analyst_decision_taxonomy.csv read at this band. It is marked apart from
    the AI summary above it for that reason - a suggestion computed from the
    rules and a suggestion written by a model are different things, and a screen
    that blurred them would be making the claim this POC exists to avoid.
    """
    steps = []
    taxonomy = data.kb().decision_taxonomy
    assessment = data.risk(conn, case_id)["assessment"]
    recommended = assessment["recommended_action"] if assessment else None
    offered = data.available_decisions(conn, case_id)
    band = data.band_for_decisions(conn, case_id)

    # 1. Anything that has to be cleared before a decision can close the case.
    for hold in data.open_holds(conn, case_id):
        what = hold.reason.split(": ", 1)[-1]
        if hold.owner == "customer":
            steps.append('<span class="nextsteps__blocked">Waiting on the applicant'
                         "</span>: " + e(what) + ". Nothing for you to clear - send "
                         "the reminder from the Communications tab if it is overdue.")
        elif hold.owner != role:
            steps.append('<span class="nextsteps__blocked">Only ' + e(hold.owner)
                         + " can release " + idtag(hold.hold_id) + "</span>: "
                         + e(what) + ". Switch role in the sidebar, or hand it on.")
        else:
            steps.append("Clear " + idtag(hold.hold_id) + " (" + e(hold.code)
                         + "): " + e(what) + ". Use the Documents tab.")

    # 2. Whether the recommended action is something this person may record.
    if recommended:
        rule = taxonomy.get(recommended)
        allowed_here = recommended in offered
        needs = rule["required_role"] if rule else None
        if not allowed_here:
            steps.append("The recommendation is <strong>" + e(recommended)
                         + "</strong>, which the taxonomy does not offer at band <code>"
                         + e(band) + "</code>. Choose from what is offered, and give "
                         "an override reason.")
        elif needs and needs != role:
            steps.append("Recording the recommendation <strong>" + e(recommended)
                         + "</strong> needs the <strong>" + e(needs)
                         + "</strong> role. As " + e(role) + " it will be refused - "
                         "switch role in the sidebar first.")
        else:
            extra = (" It also needs an escalation target."
                     if recommended == "escalate" else "")
            steps.append("Recording <strong>" + e(recommended)
                         + "</strong> matches the recommendation, so it needs a "
                         "rationale and no override reason." + extra)
            steps.append("Recording anything else is an override: give a rationale "
                         "<em>and</em> an override reason, or the backend refuses it.")

    if not steps:
        steps.append("No holds and no recommendation on this case yet. There is "
                     "nothing to record until it has been assessed.")

    return ('<div class="nextsteps"><div class="nextsteps__head">'
            '<span class="nextsteps__tag">Derived from the rules</span>'
            '<span class="nextsteps__title">What you can do next</span>'
            + help_mark("Worked out from the holds open on this case and from "
                        "kb/analyst_decision_taxonomy.csv read at band " + band
                        + ". Not written by the model, and not a decision - it only "
                          "says what the rules will and will not accept from you.")
            + "</div><ol>"
            + "".join("<li>" + s + "</li>" for s in steps) + "</ol></div>")


def _ai_preparation(conn, case_id, role="analyst") -> str:
    """What the model wrote about this case, on the tab where it is used.

    Deliberately not called a verdict or a recommendation. The band, the score
    and the recommended action are computed in code from the KB before a word is
    written; the narrative describes that outcome and cites the rows behind it.
    Labelling drafting as judgement is the one claim this POC must not make, and
    a screen that made it would contradict the rest of the system.
    """
    pack = data.risk(conn, case_id)["pack"]
    if not pack or not (pack["draft_compliance_narrative"] or "").strip():
        # No drafted summary yet, but the next steps are derived rather than
        # drafted, so they still apply and still help.
        return ('<section class="aiprep"><div class="aiprep__body">'
                + _next_steps(conn, case_id, role) + "</div></section>")

    # Which model and prompt version wrote it, from the audit row rather than
    # from a constant, so the panel cannot claim a version that did not run.
    version = ""
    for event in reversed(data.audit_trail(conn, case_id)):
        if event["action"] == "evidence_pack_generated":
            version = event["model_or_prompt_version"] or ""
            break

    missing = ""
    if pack["missing_or_conflicting_evidence"]:
        missing = note(e(pack["missing_or_conflicting_evidence"]), "warn",
                       "Missing or conflicting evidence")

    meta = ("written by " + e(version) if version else "written in mock mode")

    return (
        '<section class="aiprep"><div class="aiprep__head">'
        '<span class="aiprep__tag">AI-drafted</span>'
        '<span class="aiprep__title">Case summary prepared for you</span>'
        '<span class="muted">internal &mdash; not shown to the applicant</span>'
        + help_mark("The model wrote this summary from the rows already on the case, "
                    "and every reference in it must point at a row that exists. It is "
                    "preparation, not a decision: the band, the score and the "
                    "recommended action were all computed from the rule files before "
                    "any of it was written. Read it, check it against the evidence, "
                    "and decide for yourself.")
        + "</div>"
        '<div class="aiprep__body">'
        + missing
        + '<div class="aiprep__narrative">'
        + e(pack["draft_compliance_narrative"]) + "</div>"
        + '<p class="aiprep__caveat">This is drafting to save you starting from a '
          "blank page. It carries no authority: nothing is recorded against this "
          "case until you record it below, under your own name.</p>"
        '<p class="aiprep__meta">' + meta + " &middot; every evidence reference is "
        "checked against the database before the pack is stored</p>"
        + _next_steps(conn, case_id, role)
        + "</div></section>")


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
