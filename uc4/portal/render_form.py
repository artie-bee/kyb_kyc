"""
The demo application form, as HTML. Demo only - synthetic data.

Same approach as portal/render.py: plain strings, no template engine. The
questions, the checks and the mapping to an application live in
portal/apply.py; this module only lays them out.

Answers from the other steps travel as hidden fields, so nothing is stored
until the application is sent, and every step is an ordinary form post - no
script is needed to move between them.
"""

from orchestrator.providers import SIMULATED_LOOKUP as LOOKUP_LABEL
from portal import apply
from portal.render import e, note


def _options(choices, selected, blank="Choose...") -> str:
    out = ['<option value="">' + e(blank) + "</option>"]
    for value, label in choices:
        out.append('<option value="' + e(value) + '"'
                   + (" selected" if value == selected else "") + ">" + e(label) + "</option>")
    return "".join(out)


def _field(label, control, hint="") -> str:
    return ('<div class="field"><label>' + e(label) + "</label>" + control
            + ('<span class="help">' + e(hint) + "</span>" if hint else "") + "</div>")


def _text(name, answers, kind="text", extra="") -> str:
    return ('<input type="' + kind + '" name="' + name + '" value="'
            + e(answers.get(name, "")) + '"' + extra + ">")


def _select(name, answers, choices) -> str:
    return '<select name="' + name + '">' + _options(choices, answers.get(name, "")) + "</select>"


def _radio(name, answers, choices) -> str:
    return '<div class="radios">' + "".join(
        '<label class="radio"><input type="radio" name="' + name + '" value="' + e(v) + '"'
        + (" checked" if answers.get(name) == v else "") + "> " + e(label) + "</label>"
        for v, label in choices) + "</div>"


PCT = ' min="1" max="100" step="0.01"'


def _person_fields(prefix, n, answers, owner=False) -> list:
    """(key, html) pairs for one person row."""
    k = f"{prefix}{n}_"
    dob_hint = "YYYY-MM-DD" + (" - not needed if they are listed as a director" if owner else "")
    rows = [(k + "name", _field("Full name", _text(k + "name", answers))),
            (k + "dob", _field("Date of birth", _text(k + "dob", answers, "date"), dob_hint)),
            (k + "nationality", _field("Nationality",
                                       _select(k + "nationality", answers, apply.COUNTRIES))),
            (k + "residence", _field("Country of residence",
                                     _select(k + "residence", answers, apply.COUNTRIES)))]
    if owner:
        rows += [
            (k + "held", _field("How do they own it?", _radio(k + "held", answers, [
                ("direct", "Directly, in their own name"),
                ("company", "Through a company they own")]))),
            (k + "pct", _field("If directly: percentage of the business they own",
                               _text(k + "pct", answers, "number", PCT))),
            (k + "via_company", _field("If through a company: that company's name",
                                       _text(k + "via_company", answers))),
            (k + "via_person_pct", _field(
                "If through a company: percentage of that company they own",
                _text(k + "via_person_pct", answers, "number", PCT))),
            (k + "via_company_pct", _field(
                "If through a company: percentage of this business that company owns",
                _text(k + "via_company_pct", answers, "number", PCT)))]
    return rows


def _step_fields(step, answers) -> list:
    if step == apply.BUSINESS:
        return [
            ("legal_name", _field("Legal name of the business", _text("legal_name", answers))),
            ("trading_name", _field("Trading name, if different",
                                    _text("trading_name", answers))),
            ("entity_type", _field("Legal form",
                                   _select("entity_type", answers, apply.ENTITY_TYPES))),
            ("registration_number", _field(
                "Registration number",
                _text("registration_number", answers)
                + '<button class="btn lookup" type="submit" name="nav" value="lookup"'
                  ' formnovalidate>Look up my company</button>',
                "Fills in the name and address below from " + LOOKUP_LABEL.lower()
                + ". You can still change them.")),
            ("country", _field("Country it is registered in",
                               _select("country", answers, apply.COUNTRIES))),
            ("registered_address", _field("Registered address",
                                          _text("registered_address", answers),
                                          "As it appears on the official register")),
            ("business_activity", _field("What the business does",
                                         _text("business_activity", answers))),
            ("monthly_spend", _field("Expected card spend per month",
                                     _text("monthly_spend", answers, "number",
                                           ' min="0" step="1"'), "A whole number")),
            ("currency", _field("Currency", _select("currency", answers,
                                                    [(c, c) for c in apply.CURRENCIES]))),
        ]
    if step == apply.CONTACT:
        return [("contact_name", _field("Contact person's full name",
                                        _text("contact_name", answers),
                                        "Someone allowed to act for the business")),
                ("contact_email", _field("Email address",
                                         _text("contact_email", answers, "email"),
                                         "Where we would send your link to this portal"))]
    if step == apply.PEOPLE:
        count = 1 if apply.is_sole_trader(answers) else apply.MAX_PEOPLE
        out = []
        for n in range(1, count + 1):
            legend = ("The business owner" if count == 1 else f"Director {n}"
                      + (" (leave blank if there is nobody else)" if n > 1 else ""))
            out.append(("", '<fieldset class="person"><legend>' + e(legend) + "</legend>"))
            out += _person_fields("d", n, answers)
            out.append(("", "</fieldset>"))
        return out
    if step == apply.OWNERS:
        out = [("", '<p class="hint">Everyone who owns 25% or more of the business, directly '
                    "or through a company. Leave the rows blank if nobody does.</p>")]
        for n in range(1, apply.MAX_PEOPLE + 1):
            out.append(("", '<fieldset class="person"><legend>Owner ' + str(n) + "</legend>"))
            out += _person_fields("o", n, answers, owner=True)
            out.append(("", "</fieldset>"))
        return out
    if step == apply.FACTS_STEP:
        return [(key, _field(question, _radio(key, answers, [("yes", "Yes"), ("no", "No")])))
                for key, question in apply.FACTS]
    checked = " checked" if answers.get("confirm_demo") == "yes" else ""
    return [("confirm_demo", _field(
        "Confirm", '<label class="radio"><input type="checkbox" name="confirm_demo" '
                   'value="yes"' + checked + "> Every detail here is made up for the demo."
                   "</label>"))]


def _review(answers) -> str:
    a = answers
    rows = [("Business", a.get("legal_name", "")),
            ("Legal form", dict(apply.ENTITY_TYPES).get(a.get("entity_type"), "")),
            ("Registered in", dict(apply.COUNTRIES).get(a.get("country"), "")),
            ("Registration number", a.get("registration_number", "")),
            ("Registered address", a.get("registered_address", "")),
            ("Card spend per month", a.get("monthly_spend", "") + " " + a.get("currency", "")),
            ("Contact", a.get("contact_name", "") + " (" + a.get("contact_email", "") + ")"),
            ("People", ", ".join(r["name"] for r in apply.people(a)) or "none")]
    if not apply.is_sole_trader(a):
        rows.append(("Owners", ", ".join(
            r["name"] + (f" ({r['pct']}% directly)" if r["held"] == "direct"
                         else f" (through {r['via_company']})")
            for r in apply.owners(a)) or "none"))
    rows += [(question, "Yes" if a.get(key) == "yes" else "No")
             for key, question in apply.FACTS]
    return ('<dl class="review">' + "".join("<dt>" + e(k) + "</dt><dd>" + e(v) + "</dd>"
                                            for k, v in rows) + "</dl>")


def lookup_note(found) -> str:
    """What the lookup found, labelled for what it is."""
    if found:
        return note("Filled in the business name and registered address. Check them and "
                    "change anything that is not right.", "info", LOOKUP_LABEL)
    return note("Nothing found for that registration number. Type the name and address "
                "yourself.", "warn", LOOKUP_LABEL)


def application_form(step, answers, errors=None, notice="") -> str:
    fields = _step_fields(step, answers)
    shown = {key for key, _ in fields if key}
    hidden = "".join('<input type="hidden" name="' + e(k) + '" value="' + e(v) + '">'
                     for k, v in sorted(answers.items())
                     if k not in shown and k not in ("step", "nav") and v)
    steps = []
    for n, title in enumerate(apply.STEPS, 1):
        if n == apply.OWNERS and apply.is_sole_trader(answers):
            continue
        state = "current" if n == step else ("done" if n < step else "todo")
        steps.append('<li class="fsteps__item fsteps__item--' + state + '"'
                     + (' aria-current="step"' if n == step else "") + ">" + e(title) + "</li>")
    problems = ""
    if errors:
        problems = note("<ul>" + "".join("<li>" + e(x) + "</li>" for x in errors) + "</ul>",
                        "warn", "Please check these answers")
    back = ('<button class="btn" type="submit" name="nav" value="back" formnovalidate>'
            "Back</button>" if step > apply.BUSINESS else "")
    go = ('<button class="btn btn--primary" type="submit" name="nav" value="submit">'
          "Send application</button>" if step == apply.REVIEW else
          '<button class="btn btn--primary" type="submit" name="nav" value="next">'
          "Continue</button>")
    return ('<header class="phero"><h1>Apply for a business account</h1>'
            '<p class="sub">Demo application - use made-up details only.</p></header>'
            '<ol class="fsteps">' + "".join(steps) + "</ol>" + problems + notice
            + '<form class="apply" method="post" action="/apply">'
            '<input type="hidden" name="step" value="' + str(step) + '">' + hidden
            + "<h2>" + e(apply.STEPS[step - 1]) + "</h2>"
            + (_review(answers) if step == apply.REVIEW else "")
            + "".join(html for _, html in fields)
            + '<div class="btn-row apply__nav">' + back + go + "</div></form>")
