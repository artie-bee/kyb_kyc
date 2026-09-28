"""
The customer portal, as HTML. Plain strings, no template engine - the same way
web/render.py builds the console.

Everything here is built from app/customer_view.py, which is the one place that
decides what an applicant may be told. This module lays it out and adds nothing
of its own about the case: no status names, no bands, no scores, no holds, no
findings, because it is never given any.

Every page this returns is scanned again by the server before it is sent
(customer_view.leaks on the whole page), so a leak introduced here is refused
at the door rather than shown.
"""

import html

BANNER = "Demo portal - do not upload real documents."

NAV = [("My application", "/"), ("Documents", "/checklist"), ("Messages", "/messages")]

STATE_WORD = {"done": "Done", "current": "In progress", "todo": "Not started",
              "skipped": "Not completed"}
STATUS_TONE = {"Accepted": "ok", "Under review": "info", "Received": "info",
               "Resubmission needed": "warn", "Not yet uploaded": "neutral",
               "Replaced by a newer upload": "neutral"}


def e(value) -> str:
    return html.escape("" if value is None else str(value))


def note(body, tone="info", title="") -> str:
    head = '<span class="note__title">' + e(title) + "</span>" if title else ""
    return '<div class="note note--' + tone + '">' + head + body + "</div>"


def chip(value) -> str:
    return ('<span class="chip chip--' + STATUS_TONE.get(value, "neutral") + '">'
            '<span class="chip__dot"></span>' + e(value) + "</span>")


# ---------------------------------------------------------------------------
# Chrome
# ---------------------------------------------------------------------------

def page(title, body, view=None, active=None, flash=None, demo=False) -> str:
    """The shell every portal page shares: banner, header, one calm column."""
    nav = ""
    who = ""
    if view is not None:
        links = "".join(
            '<a class="pnav__item' + (" pnav__item--on" if name == active else "")
            + '" href="' + href + '"' + (' aria-current="page"' if name == active else "")
            + ">" + e(name) + "</a>" for name, href in NAV)
        nav = '<nav class="pnav" aria-label="Your application">' + links + "</nav>"
        who = ('<div class="phead__who"><span>' + e(view["applicant_name"]) + "</span>"
               '<form method="post" action="/signout">'
               '<button class="btn btn--quiet" type="submit">Sign out</button></form></div>')
    switch = ('<a class="phead__demo" href="/demo">Switch demo customer</a>'
              if demo else "")

    flash_html = ""
    if flash:
        kind, text = flash
        flash_html = note(e(text), "ok" if kind == "ok" else "warn",
                          "Thank you" if kind == "ok" else "We could not take that file")

    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>" + e(title) + " &middot; Wallester Business onboarding</title>\n"
        '<link rel="stylesheet" href="/static/common.css">\n'
        '<link rel="stylesheet" href="/static/portal.css">\n</head>\n<body>\n'
        '<div class="demo-banner" role="note">' + e(BANNER) + "</div>\n"
        '<header class="phead"><div class="phead__in">'
        '<a class="phead__brand" href="/">Wallester <span>Business onboarding</span></a>'
        + who + "</div>" + ('<div class="phead__in phead__in--nav">' + nav + switch + "</div>"
                            if nav or switch else "")
        + "</header>\n"
        '<main class="pmain">\n' + flash_html + body + "\n</main>\n"
        '<footer class="pfoot"><button class="btn btn--quiet" type="button" id="theme" hidden>'
        "Switch light / dark</button></footer>\n"
        '<script src="/static/portal.js" defer></script>\n</body>\n</html>\n')


def _heading(view, sub=None) -> str:
    return ('<header class="phero"><h1>' + e(view["applicant_name"]) + "</h1>"
            '<p class="sub">Reference ' + e(view["case_id"]) + " &middot; Applied "
            + e(view["applied_on"]) + (" &middot; " + sub if sub else "") + "</p></header>")


# ---------------------------------------------------------------------------
# 1. My application
# ---------------------------------------------------------------------------

def application(view) -> str:
    if view["partner"]:
        return (_heading(view) + note(e(view["status_text"]), "info",
                                      "Partner programme")
                + '<p class="lede">You can read our messages to you under '
                  '<a href="/messages">Messages</a>.</p>')

    steps = []
    for s in view["steps"]:
        steps.append(
            '<li class="step step--' + s["state"] + '"'
            + (' aria-current="step"' if s["state"] == "current" else "") + ">"
            '<span class="step__n" aria-hidden="true">' + str(s["n"]) + "</span>"
            '<div class="step__body"><div class="step__head"><span class="step__title">'
            + e(s["title"]) + '</span><span class="step__state">'
            + e(STATE_WORD[s["state"]]) + "</span></div>"
            '<p class="step__line">' + e(s["line"]) + "</p></div></li>")
    action = next((s for s in view["steps"] if s["n"] == 3), None)
    cta = ""
    if action and action["state"] == "current" and not view["closed"]:
        cta = ('<p class="cta"><a class="btn btn--primary" href="/checklist">'
               "Go to your documents</a></p>")
    return (_heading(view)
            + note(e(view["status_text"]), "info")
            + '<ol class="steps">' + "".join(steps) + "</ol>" + cta)


# ---------------------------------------------------------------------------
# 2. Checklist
# ---------------------------------------------------------------------------

def _upload_form(item, cl) -> str:
    field_id = "file-" + item["item_id"]
    return (
        '<form class="upload" method="post" action="/upload" enctype="multipart/form-data">'
        '<input type="hidden" name="item_id" value="' + e(item["item_id"]) + '">'
        '<label class="upload__label" for="' + field_id + '">'
        + ("Upload a new copy" if item["status"] == "Resubmission needed" else "Upload")
        + "</label>"
        '<input class="upload__file" type="file" name="file" id="' + field_id + '" required'
        ' accept="' + e(cl["accept_attr"]) + '">'
        '<button class="btn btn--primary upload__go" type="submit">Send file</button>'
        "</form>")


def _item(item, cl) -> str:
    person = ('<span class="item__person">For ' + e(item["person"]) + "</span>"
              if item["person"] else "")
    reason = ('<p class="item__reason">' + e(item["reason"]) + "</p>"
              if item["reason"] else "")
    form = _upload_form(item, cl) if item["can_upload"] else ""
    history = ""
    if item["history"]:
        rows = "".join('<li><span class="hist__file">' + e(h["file_name"]) + "</span>"
                       '<span class="hist__when">' + e(h["uploaded"]) + "</span>"
                       '<span class="hist__label">' + e(h["label"]) + "</span></li>"
                       for h in item["history"])
        history = ('<details class="hist"><summary>Upload history ('
                   + str(len(item["history"])) + ")</summary><ul>" + rows + "</ul></details>")
    tone = {"Accepted": "ok", "Resubmission needed": "warn"}.get(item["status"], "none")
    return ('<li class="item item--' + tone + '" id="item-' + e(item["item_id"]) + '">'
            '<div class="item__top"><div><span class="item__doc">' + e(item["document"])
            + "</span>" + person + "</div>" + chip(item["status"]) + "</div>"
            + reason + form + history + "</li>")


def checklist(view, cl) -> str:
    if view["partner"]:
        return _heading(view) + note(e(view["status_text"]), "info", "Partner programme")

    types = ", ".join(t.upper() for t in cl["accepted_types"])
    if cl["collecting"]:
        intro = ('<p class="lede">Upload each document below. We accept ' + e(types)
                 + " files up to " + str(cl["max_mb"]) + " MB.</p>")
    elif view["closed"]:
        intro = note("This application is closed, so we are not taking new documents for it.",
                     "info")
    else:
        intro = note("Your documents are with our onboarding team. If anything else is "
                     "needed, we will ask you in a message.", "info")

    out = [_heading(view), intro]
    if cl["required"]:
        out.append("<h2>Documents we need</h2><ul class=\"items\">"
                   + "".join(_item(i, cl) for i in cl["required"]) + "</ul>")
    if cl["other"]:
        out.append("<h2>If they apply to you</h2>"
                   '<p class="hint">Only send these if they apply to your business. If one '
                   "is needed, we will ask you.</p><ul class=\"items\">"
                   + "".join(_item(i, cl) for i in cl["other"]) + "</ul>")
    if not cl["required"] and not cl["other"]:
        out.append('<p class="empty">We will list the documents we need from you here.</p>')
    return "".join(out)


# ---------------------------------------------------------------------------
# 3. Messages
# ---------------------------------------------------------------------------

def messages(view) -> str:
    msgs = ['<li class="msg"><span class="msg__when">' + e(m["sent_at"].replace("T", " ")
                                                          .rstrip("Z"))
            + "</span>" + e(m["text"]) + "</li>" for m in view["messages"]]
    body = ('<ol class="msgs">' + "".join(msgs) + "</ol>" if msgs
            else '<p class="empty">We have not sent you any messages yet.</p>')
    return _heading(view) + "<h2>Messages we have sent you</h2>" + body


# ---------------------------------------------------------------------------
# 4. Demo case selector
# ---------------------------------------------------------------------------

def demo_selector(customers) -> str:
    rows = "".join(
        '<li class="pick"><div><span class="pick__name">' + e(c["applicant_name"])
        + '</span><span class="pick__ref">' + e(c["case_id"]) + "</span></div>"
        '<form method="post" action="/demo/open">'
        '<input type="hidden" name="case_id" value="' + e(c["case_id"]) + '">'
        '<button class="btn" type="submit">Open as this customer</button></form></li>'
        for c in customers)
    return ('<header class="phero"><h1>Choose a demo customer</h1>'
            '<p class="sub">Open the portal as any applicant in the demo, without typing '
            "any details. A real customer arrives from the link in our email instead, and "
            "only ever sees their own application.</p></header>"
            '<ul class="picks">' + rows + "</ul>")


# ---------------------------------------------------------------------------
# Pages with no case
# ---------------------------------------------------------------------------

def signed_out(demo=False) -> str:
    extra = ('<p class="cta"><a class="btn btn--primary" href="/demo">'
             "Choose a demo customer</a></p>" if demo else "")
    return ('<header class="phero"><h1>Your application</h1></header>'
            + note("Open your application from the link in the email we sent you. "
                   "If the link has stopped working, reply to that email and we will send "
                   "you a new one.", "info") + extra)


def not_found() -> str:
    return ('<header class="phero"><h1>Page not found</h1></header>'
            + note('There is nothing at this address. <a href="/">Back to your '
                   "application</a>.", "info"))


def refused() -> str:
    return ('<header class="phero"><h1>This page is not available</h1></header>'
            + note("Something went wrong on our side while preparing this page. Please try "
                   "again later.", "warn"))
