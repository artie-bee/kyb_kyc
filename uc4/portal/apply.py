"""
The demo application form: the questions, the checks on the answers, and the
application they add up to. Demo only - synthetic data.

The form asks for FACTS - the legal form, who runs the business, who owns how
much and how, whether a company or a trust sits in the ownership - and never
for an applicant type. Step 1 of the pipeline classifies the case from those
facts with the AT rules in kb/applicant_type_rules.csv, exactly as it classifies
a dataset application; build_application() cannot even express a type.

Nothing is stored between the form's steps. Each step carries the answers so
far as hidden fields, so there is no draft table and the form works with the
script off. This module holds no SQL: the finished application goes to
app/data.submit_application, which calls the existing intake.
"""

import re
from datetime import date

from orchestrator import clock

# The legal forms offered. The value is what intake's AT rules read.
ENTITY_TYPES = [
    ("private_limited_company", "Private limited company (Ltd, OÜ)"),
    ("public_limited", "Public limited company"),
    ("partnership", "Partnership"),
    ("llp", "Limited liability partnership (LLP)"),
    ("sole_trader", "Sole trader or self-employed"),
]
# Where a business or a person can be. The first nine are the countries
# kb/jurisdiction_routing.csv supports; the last two are not, which is itself a
# demo path (intake sends an unsupported country to an analyst).
COUNTRIES = [
    ("EE", "Estonia"), ("GB", "United Kingdom"), ("LV", "Latvia"), ("LT", "Lithuania"),
    ("FI", "Finland"), ("DE", "Germany"), ("FR", "France"), ("NL", "Netherlands"),
    ("IE", "Ireland"), ("AE", "United Arab Emirates"), ("US", "United States"),
]
CURRENCIES = ("EUR", "GBP")
MAX_PEOPLE = 4

# The fact questions. Plain yes/no questions about the business; none of them
# names a category it would put the business in.
FACTS = [
    ("fact_company_owner", "Does a company own any part of the business?"),
    ("fact_trust", "Is any part of the business held through a trust, or by a nominee on "
                   "someone else's behalf?"),
    ("fact_vat", "Is the business registered for VAT?"),
    ("fact_trade_register", "Is the business registered with a national trade or business "
                            "register?"),
    ("fact_partner", "Will you issue cards to your own customers, under your own brand?"),
]

_PERSON_KEYS = ("name", "dob", "nationality", "residence", "held", "pct",
                "via_company", "via_person_pct", "via_company_pct")
# Every answer the form asks for. Anything else a request carries - a tampered
# "applicant_type", say - is dropped on arrival, so it is neither carried from
# step to step nor ever seen by the mapping below.
ANSWER_KEYS = frozenset(
    ("legal_name", "trading_name", "entity_type", "registration_number", "country",
     "registered_address", "business_activity", "monthly_spend", "currency",
     "contact_name", "contact_email", "confirm_demo")
    + tuple(key for key, _ in FACTS)
    + tuple(f"{p}{n}_{k}" for p in ("d", "o") for n in range(1, MAX_PEOPLE + 1)
            for k in _PERSON_KEYS))


def known_answers(form: dict) -> dict:
    return {k: v for k, v in form.items() if k in ANSWER_KEYS}


STEPS = ["Your business", "Contact", "People", "Owners", "A few facts", "Check and send"]
BUSINESS, CONTACT, PEOPLE, OWNERS, FACTS_STEP, REVIEW = range(1, 7)

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_sole_trader(answers: dict) -> bool:
    return answers.get("entity_type") == "sole_trader"


def next_step(step: int, answers: dict) -> int:
    step += 1
    if step == OWNERS and is_sole_trader(answers):
        step += 1                       # a sole trader owns their own business
    return min(step, REVIEW)


def previous_step(step: int, answers: dict) -> int:
    step -= 1
    if step == OWNERS and is_sole_trader(answers):
        step -= 1
    return max(step, BUSINESS)


def _person_rows(answers: dict, prefix: str) -> list[dict]:
    rows = []
    for n in range(1, MAX_PEOPLE + 1):
        row = {k: (answers.get(f"{prefix}{n}_{k}") or "").strip() for k in _PERSON_KEYS}
        if row["name"]:
            row["n"] = n
            rows.append(row)
    return rows


def people(answers: dict) -> list[dict]:
    return _person_rows(answers, "d")


def owners(answers: dict) -> list[dict]:
    return [] if is_sole_trader(answers) else _person_rows(answers, "o")


def _pct(value: str) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if 0 < v <= 100 else None


def _person_errors(row: dict, label: str) -> list[str]:
    errors = []
    if not _DATE.match(row["dob"]):
        errors.append(f"{label}: date of birth is needed, as YYYY-MM-DD")
    else:
        try:
            if date.fromisoformat(row["dob"]) >= clock.current().today():
                errors.append(f"{label}: date of birth must be in the past")
        except ValueError:
            errors.append(f"{label}: date of birth is not a real date")
    if row["nationality"] not in dict(COUNTRIES):
        errors.append(f"{label}: choose a nationality")
    if row["residence"] not in dict(COUNTRIES):
        errors.append(f"{label}: choose a country of residence")
    return errors


def validate(step: int, answers: dict) -> list[str]:
    """What is wrong with one step's answers, in words for the customer."""
    a = {k: (v or "").strip() for k, v in answers.items()}
    errors = []
    if step == BUSINESS:
        for key, label in (("legal_name", "the business's legal name"),
                           ("registration_number", "the registration number"),
                           ("registered_address", "the registered address"),
                           ("business_activity", "what the business does")):
            if not a.get(key):
                errors.append(f"Please enter {label}.")
        if a.get("entity_type") not in dict(ENTITY_TYPES):
            errors.append("Please choose the legal form of the business.")
        if a.get("country") not in dict(COUNTRIES):
            errors.append("Please choose the country the business is registered in.")
        if not a.get("monthly_spend", "").isdigit():
            errors.append("Please enter the expected monthly card spend as a whole number.")
        if a.get("currency") not in CURRENCIES:
            errors.append("Please choose a currency.")
    elif step == CONTACT:
        if not a.get("contact_name"):
            errors.append("Please enter the contact person's name.")
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", a.get("contact_email", "")):
            errors.append("Please enter a valid email address.")
    elif step == PEOPLE:
        rows = people(a)
        if not rows:
            errors.append("Please enter the business owner." if is_sole_trader(a)
                          else "Please enter at least one director.")
        if is_sole_trader(a) and len(rows) > 1:
            errors.append("A sole trader has one owner. Please enter only that person.")
        for row in rows:
            errors += _person_errors(row, row["name"])
    elif step == OWNERS:
        rows = owners(a)
        directors = {r["name"].lower() for r in people(a)}
        for row in rows:
            label = row["name"]
            if row["name"].lower() not in directors:
                errors += _person_errors(row, label)
            if row["held"] == "direct":
                if _pct(row["pct"]) is None:
                    errors.append(f"{label}: enter the percentage owned, between 1 and 100")
            elif row["held"] == "company":
                if not row["via_company"]:
                    errors.append(f"{label}: enter the name of the company they own it through")
                if _pct(row["via_person_pct"]) is None or _pct(row["via_company_pct"]) is None:
                    errors.append(f"{label}: enter both percentages, between 1 and 100")
            else:
                errors.append(f"{label}: say whether they own it directly or through a company")
        total = sum(_effective(r) or 0 for r in rows)
        if total > 100.01:
            errors.append(f"The ownership adds up to {total:g}%, which is more than the whole "
                          "business.")
    elif step == FACTS_STEP:
        for key, question in FACTS:
            if a.get(key) not in ("yes", "no"):
                errors.append(f"Please answer: {question}")
    elif step == REVIEW:
        for earlier in (BUSINESS, CONTACT, PEOPLE, OWNERS, FACTS_STEP):
            errors += validate(earlier, a)
        if a.get("confirm_demo") != "yes":
            errors.append("Please confirm that every detail is made up for the demo.")
    return errors


def _effective(row: dict) -> float | None:
    if row["held"] == "direct":
        return _pct(row["pct"])
    if row["held"] == "company":
        a, b = _pct(row["via_person_pct"]), _pct(row["via_company_pct"])
        return round(a * b / 100, 4) if a and b else None
    return None


def build_application(answers: dict, application_id: str) -> dict:
    """The application the answers add up to, in the shape intake reads.

    There is no applicant type in it and no way to put one there: the facts go
    in, and Step 1 decides the type from them.
    """
    a = {k: (v or "").strip() for k, v in answers.items()}
    yes = {key: a.get(key) == "yes" for key, _ in FACTS}
    sole = is_sole_trader(a)
    legal_name = a["legal_name"]

    individuals, ref_by_name = [], {}

    def person(row, role, relationship):
        key = row["name"].lower()
        if key in ref_by_name:
            return ref_by_name[key]
        ref = f"P{len(individuals) + 1}"
        individuals.append({
            "ref": ref, "role": role, "full_name": row["name"],
            "date_of_birth": row.get("dob") or None,
            "nationality": row.get("nationality") or None,
            "residence_country": row.get("residence") or None,
            "relationship_to_entity": relationship})
        ref_by_name[key] = ref
        return ref

    for row in people(a):
        person(row, "sole_trader" if sole else "director",
               "Owner of the business" if sole else "Director")

    ubos, indirect_25 = [], False
    for row in owners(a):
        ref = person(row, "ubo", "Beneficial owner")
        effective = _effective(row)
        if row["held"] == "company":
            chain = [float(row["via_person_pct"]), float(row["via_company_pct"])]
            ubos.append({"individual_ref": ref, "ownership_percentage": effective,
                         "ownership_chain_percentages": chain,
                         "control_type": "indirect_shareholding",
                         "ownership_path": f"{row['via_company']} > {legal_name}"})
            indirect_25 = indirect_25 or effective >= 25
        else:
            ubos.append({"individual_ref": ref, "ownership_percentage": effective,
                         "ownership_chain_percentages": [effective],
                         "control_type": "direct_shareholding", "ownership_path": legal_name})

    contact_is_director = a["contact_name"].lower() in ref_by_name
    if not contact_is_director:
        person({"name": a["contact_name"]}, "authorised_signatory",
               "Contact for this application and authorised to act for the business")

    through_company = any(u["control_type"] == "indirect_shareholding" for u in ubos)
    spend = int(a["monthly_spend"])
    return {
        "application_id": application_id,
        "source_channel": "portal",
        "programme_type": "white_label" if yes["fact_partner"] else "direct",
        "applicant": {
            "legal_name": legal_name, "trading_name": a.get("trading_name") or None,
            "registration_number": a["registration_number"],
            "entity_type": a["entity_type"], "country": a["country"],
            "registered_address": a["registered_address"],
            "business_activity": a["business_activity"],
            "expected_usage": f"Around {spend} {a['currency']} per month on cards",
            "vat_registered": yes["fact_vat"],
        },
        "contact": {"name": a["contact_name"], "email": a["contact_email"]},
        "ownership": {
            # the applicant, plus one layer for an intermediate company
            "ownership_layers": 2 if through_company else 1,
            "has_corporate_shareholder": yes["fact_company_owner"] or through_company,
            "has_trust_or_nominee": yes["fact_trust"],
        },
        "individuals": individuals,
        "ubos": ubos,
        "flags": {
            "spend_above_50k": spend > 50000,
            "nominee_or_trust_in_chain": yes["fact_trust"],
            "ubo_indirect_25pct_or_more": indirect_25,
            "signatory_not_director": not contact_is_director,
            "vat_registered": yes["fact_vat"],
            "remote_onboarding": True,          # every portal application is remote
            "registered_with_trade_register": yes["fact_trade_register"],
        },
        "documents": [],
    }
