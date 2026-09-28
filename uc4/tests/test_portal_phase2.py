"""
Portal phase 2: new demo applications from the portal. Demo only, synthetic data.

What is protected here:
  - a portal case is classified by the FACTS the customer gave, through the
    existing intake, never by a type anyone chose;
  - portal cases (WAL-DEMO-) never appear in a dataset comparison, and every
    comparison score is exactly what it was;
  - in mock mode a value an analyst typed in is never recorded or shown as
    extracted;
  - every simulated provider result says so - in its row, in the audit trail
    and in the evidence pack;
  - the demo scenario control exists in the console only;
  - a PEP scenario reaches the portal as neutral wording only.

Run: python -m pytest tests/test_portal_phase2.py -q
"""

import re
import sys
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import customer_checklist, leaks, visible_text  # noqa: E402
from orchestrator import db                                           # noqa: E402
from orchestrator.demo_scenarios import ScenarioRefused               # noqa: E402
from orchestrator.providers import SIMULATED                          # noqa: E402
from orchestrator.steps import document_quality                       # noqa: E402
from portal import apply, server                                      # noqa: E402
from tools import compare_to_dataset as compare                        # noqa: E402
from web import render as console                                     # noqa: E402

PORTAL = ROOT / "portal"
PDF = b"%PDF-1.4\n% synthetic demo file\n"
INTERNAL = ("sanction", "screening", "pep", "politically", "adverse", "media", "risk",
            "score", "band", "hold", "finding", "compliance", "analyst", "escalat",
            "due diligence", "simulated", "scenario", "enhanced_due_diligence",
            "analyst_review_required")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def demo(tmp_path, monkeypatch):
    """A fresh demo database, with the portal's files kept in tmp_path."""
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    conn = data.connect(path)
    yield {"conn": conn, "db": path, "tmp": tmp_path}
    conn.close()


@pytest.fixture()
def portal(demo):
    httpd = server.serve(0, db_path=demo["db"], demo=True, uploads_dir=demo["tmp"] / "uploads")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {**demo, "base": f"http://127.0.0.1:{httpd.server_address[1]}"}
    httpd.shutdown()
    httpd.server_close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Browser:
    """JavaScript off: plain GETs and plain form posts."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()), NoRedirect())

    def request(self, path, fields=None):
        body = urlencode(fields, doseq=True).encode() if fields is not None else None
        try:
            with self.opener.open(urllib.request.Request(self.base + path, body)) as r:
                return r.status, r.headers.get("Location"), r.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("Location"), err.read().decode("utf-8")

    def get(self, path):
        status, _, page = self.request(path)
        return status, page


def words(page: str) -> str:
    return " ".join(visible_text(page).split())


def internal_words(page: str) -> list[str]:
    page = re.sub(r'<span class="hist__file">.*?</span>', " ", page, flags=re.S)
    text = words(page).lower()
    return [w for w in INTERNAL if re.search(rf"(?<![a-z]){re.escape(w)}", text)]


def answers(**over) -> dict:
    """A complete set of form answers for a simple Estonian company."""
    a = {
        "legal_name": "Northwind Widgets OU", "trading_name": "Northwind",
        "entity_type": "private_limited_company", "registration_number": "EE-55512345",
        "country": "EE", "registered_address": "Tartu mnt 10, 10117 Tallinn",
        "business_activity": "Online retail of household widgets",
        "monthly_spend": "12000", "currency": "EUR",
        "contact_name": "Mari Tamm", "contact_email": "mari@example.test",
        "d1_name": "Mari Tamm", "d1_dob": "1985-03-02", "d1_nationality": "EE",
        "d1_residence": "EE",
        "o1_name": "Jaan Kask", "o1_dob": "1979-11-20", "o1_nationality": "EE",
        "o1_residence": "EE", "o1_held": "direct", "o1_pct": "60",
        "fact_company_owner": "no", "fact_trust": "no", "fact_vat": "yes",
        "fact_trade_register": "yes", "fact_partner": "no", "confirm_demo": "yes",
    }
    a.update(over)
    return {k: v for k, v in a.items() if v is not None}


def submit(conn, **over) -> str:
    """Straight through the form's own mapping and the existing intake."""
    a = answers(**over)
    for step in range(apply.BUSINESS, apply.REVIEW + 1):
        assert not apply.validate(step, a), (step, apply.validate(step, a))
    return data.submit_application(conn, apply.build_application(a, "PORTAL-TEST"))


def typed_values(conn, case_id, fields) -> dict:
    """What an analyst would read off each synthetic file: the entered details."""
    application = data._application(case_id)
    entered = application["applicant"]
    directors = [p["full_name"] for p in application["individuals"]
                 if p["role"] in ("director", "sole_trader")]
    recent = (date.today() - timedelta(days=10)).isoformat()
    known = {"company_name": entered["legal_name"], "legal_name": entered["legal_name"],
             "registration_number": entered["registration_number"],
             "registered_address": entered["registered_address"], "address":
             entered["registered_address"], "entity_status": "active",
             "director_name": directors[0] if directors else "",
             "expiry_date": "2031-01-01", "document_date": recent,
             "date_of_birth": "1985-03-02", "incorporation_date": "2015-01-01",
             "appointment_date": "2015-01-01"}
    out = {}
    for f in fields:
        if f["name"] == "director_name_2":
            out[f["name"]] = directors[1] if len(directors) > 1 else ""
        elif f["name"] in known:
            out[f["name"]] = known[f["name"]]
        else:
            out[f["name"]] = "synthetic value" if f["required"] else ""
    return out


def upload_everything(conn, case_id):
    for item in customer_checklist(conn, case_id)["items"]:
        if item["can_upload"] and not item["optional"]:
            data.upload_document(conn, case_id, item["checklist_item_id"], "document.pdf", PDF)


def release_visual_checks(conn, case_id):
    """The analyst looks at each upload the mock visual check could not, and
    releases it with the ordinary release_document()."""
    for doc in conn.execute("SELECT document_id FROM document WHERE case_id = ?"
                            " AND quality_status = 'manual_review_required'"
                            " AND quality_flags LIKE '%visual_check_not_run%'",
                            (case_id,)).fetchall():
        data.release_document(conn, doc[0], "analyst.test", "accept", "looked at it: clear")


def type_everything_in(conn, case_id, override=None):
    for document_id, fields in data.awaiting_fields(conn, case_id).items():
        values = typed_values(conn, case_id, fields)
        values.update((override or {}).get(document_id, {}))
        data.enter_fields(conn, document_id, "analyst.test", values)


def run_to_the_end(conn, case_id, scenario="clean"):
    data.choose_demo_scenario(conn, case_id, scenario, "analyst.test")
    upload_everything(conn, case_id)
    release_visual_checks(conn, case_id)
    type_everything_in(conn, case_id)
    return conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()


# ---------------------------------------------------------------------------
# 1. Classified by facts, through the existing intake
# ---------------------------------------------------------------------------

def test_a_portal_case_is_classified_by_its_facts_not_by_a_chosen_type(portal):
    browser = Browser(portal["base"])
    status, page = browser.get("/apply")
    assert status == 200

    # Walk the whole form, JavaScript off. A field naming a type is sent too, the
    # way a tampered form would send it - and must change nothing.
    a = answers(o1_held="company", o1_pct="", o1_via_company="Kask Holding OU",
                o1_via_person_pct="100", o1_via_company_pct="60")
    step, pages = apply.BUSINESS, [page]
    while True:
        nav = "submit" if step == apply.REVIEW else "next"
        status, where, page = browser.request(
            "/apply", {**a, "step": step, "nav": nav, "applicant_type": "freelancer_sole_trader"})
        if nav == "submit":
            break
        assert status == 200 and "Please check these answers" not in page, (step, words(page))
        pages.append(page)
        step = apply.next_step(step, a)
    assert status == 303 and where.startswith("/?m=")

    for p in pages:     # the form never asks for, or offers, a type
        for word in ("applicant_type", "applicant type", "complex_corporate", "sme_corporate",
                     "freelancer_sole_trader", "white_label_partner"):
            assert word not in p.lower(), word

    conn = portal["conn"]
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id LIKE 'WAL-DEMO-%'").fetchone()
    assert case["case_id"] == "WAL-DEMO-0001" and case["source_channel"] == "portal"
    # an owner holding through a company is a complex structure, whatever was sent
    assert case["applicant_type"] == "complex_corporate_ubo"
    event = conn.execute("SELECT payload_summary FROM audit_event WHERE case_id = ?"
                         " AND action = 'applicant_classified'", (case["case_id"],)).fetchone()
    assert "via AT-03" in event["payload_summary"], "the AT rules decided, from ownership_layers"

    # and the customer is signed in to the case they just made
    assert "Northwind Widgets OU" in browser.get("/")[1]


@pytest.mark.parametrize("facts, expected", [
    ({}, "sme_corporate"),
    ({"entity_type": "sole_trader", "o1_name": None, "o1_dob": None, "o1_nationality": None,
      "o1_residence": None, "o1_held": None, "o1_pct": None}, "freelancer_sole_trader"),
    ({"fact_company_owner": "yes"}, "complex_corporate_ubo"),
    ({"fact_trust": "yes"}, "complex_corporate_ubo"),
    ({"fact_partner": "yes"}, "white_label_partner"),
])
def test_the_same_form_lands_on_the_type_its_facts_imply(demo, facts, expected):
    case_id = submit(demo["conn"], **facts)
    assert db.is_demo_case(case_id)
    got = demo["conn"].execute("SELECT applicant_type FROM onboarding_case WHERE case_id = ?",
                               (case_id,)).fetchone()[0]
    assert got == expected


def test_submitting_goes_through_the_existing_intake_and_builds_the_checklist(demo):
    case_id = submit(demo["conn"])
    actions = [r[0] for r in demo["conn"].execute(
        "SELECT action FROM audit_event WHERE case_id = ? ORDER BY event_id", (case_id,))]
    assert actions[0] == "case_created" and "applicant_classified" in actions
    assert customer_checklist(demo["conn"], case_id)["items"], "no checklist was built"
    created = demo["conn"].execute("SELECT payload_summary FROM audit_event WHERE case_id = ?"
                                   " AND action = 'case_created'", (case_id,)).fetchone()[0]
    assert "synthetic data" in created


def test_the_form_refuses_what_it_cannot_use_and_works_step_by_step(portal):
    browser = Browser(portal["base"])
    status, _, page = browser.request("/apply", {"step": "1", "nav": "next"})
    assert status == 200 and "Please check these answers" in page
    assert "legal name" in words(page)
    status, _, page = browser.request("/apply", {**answers(), "step": "3", "nav": "back"})
    assert '<input type="hidden" name="step" value="2">' in page
    # the answers from the other steps ride along as hidden fields
    assert 'name="legal_name" value="Northwind Widgets OU"' in page


# ---------------------------------------------------------------------------
# 2. Never in a dataset comparison
# ---------------------------------------------------------------------------

def test_portal_cases_never_appear_in_dataset_comparisons(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    functions = (compare.compare, compare.compare_documents, compare.compare_fields,
                 compare.compare_verification, compare.compare_screening, compare.compare_risk)

    conn = compare.run_orchestrator()
    before = [compare.score(f(conn)) for f in functions]
    assert before == [(70, 70), (168, 168), (256, 256), (50, 50), (39, 39), (33, 33)]

    # A demo case sorts before WAL-ONB-0001. Without the filter it would shift
    # every case in the positional comparison by one.
    for scenario in ("clean", "pep_match", "address_mismatch"):
        run_to_the_end(conn, submit(conn), scenario)
    assert conn.execute("SELECT COUNT(*) FROM risk_assessment WHERE case_id LIKE 'WAL-DEMO-%'"
                        ).fetchone()[0] == 3, "the demo cases must really reach the checks"

    after = []
    for f in functions:
        rows = f(conn)
        assert not any("WAL-DEMO" in " ".join(map(str, r)) for r in rows), f.__name__
        after.append(compare.score(rows))
    assert after == before


def test_reset_demo_deletes_the_demo_cases_and_their_files(tmp_path, monkeypatch):
    path = tmp_path / "onboarding.db"
    monkeypatch.setattr(data, "DB_PATH", path)
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    data.reset_demo(path)
    conn = data.connect(path)
    case_id = submit(conn)
    upload_everything(conn, case_id)
    assert list((tmp_path / "applications").glob("*.json"))
    assert list((tmp_path / "uploads").rglob("*.pdf"))
    conn.close()

    data.reset_demo(path)
    conn = data.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM onboarding_case WHERE case_id LIKE 'WAL-DEMO-%'"
                        ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM onboarding_case").fetchone()[0] == 14
    conn.close()
    assert not list((tmp_path / "applications").glob("*.json"))
    assert not list((tmp_path / "uploads").rglob("*.pdf"))


# ---------------------------------------------------------------------------
# 3. Reading uploads in mock mode: keyed, never "extracted"
# ---------------------------------------------------------------------------

def test_mock_mode_never_labels_keyed_values_as_extracted(demo):
    conn = demo["conn"]
    case_id = submit(conn)
    upload_everything(conn, case_id)

    # the deterministic rules ran for real, and the visual check waits for a person
    docs = conn.execute("SELECT * FROM document WHERE case_id = ?", (case_id,)).fetchall()
    assert docs and {d["quality_status"] for d in docs} == {"manual_review_required"}
    reasons = [h.reason for h in data.open_holds(conn, case_id)]
    assert any("visual check not run in mock mode" in r for r in reasons), reasons
    assert all(i["status"] == "Under review" for i in customer_checklist(conn, case_id)["items"]
               if not i["optional"])

    # released by the analyst, the files still wait for their fields to be typed in
    release_visual_checks(conn, case_id)
    reasons = [h.reason for h in data.open_holds(conn, case_id)]
    assert any("fields not read automatically in mock mode" in r for r in reasons), reasons
    # a file with fields still to type in stays under review; one with no fields
    # to read (a selfie, say) is accepted once a person has looked at it
    statuses = [i["status"] for i in customer_checklist(conn, case_id)["items"]
                if not i["optional"]]
    assert set(statuses) <= {"Under review", "Accepted"} and "Under review" in statuses

    type_everything_in(conn, case_id)
    methods = {r[0] for r in conn.execute(
        "SELECT f.entry_method FROM extracted_field f JOIN document d USING (document_id)"
        " WHERE d.case_id = ?", (case_id,))}
    assert methods == {"entered_by_analyst"}, methods
    assert conn.execute(
        "SELECT COUNT(*) FROM extracted_field f JOIN document d USING (document_id)"
        " WHERE d.case_id = ? AND f.corrected_by_analyst = 1", (case_id,)).fetchone()[0] == 0, \
        "a typed-in value is not a correction of a machine reading"

    actions = [r[0] for r in conn.execute(
        "SELECT action FROM audit_event WHERE case_id = ?", (case_id,))]
    assert "fields_entered_by_analyst" in actions
    assert "fields_extracted" not in actions, "nothing was extracted, so nothing may say so"
    for r in conn.execute("SELECT payload_summary FROM audit_event WHERE case_id = ?"
                          " AND action = 'fields_entered_by_analyst'", (case_id,)):
        assert "entered_by_analyst, not as extracted" in r[0]

    page = console.case_detail(conn, case_id, tab="Documents")
    assert "entered by an analyst" in page
    assert "Extracted fields" not in page and "below the confidence floor" not in page

    # nor does the risk step score a typed value as a low-confidence reading
    factors = {r[0] for r in conn.execute(
        "SELECT f.factor FROM risk_factor f JOIN risk_assessment a USING (assessment_id)"
        " WHERE a.case_id = ?", (case_id,))}
    assert "extraction_confidence" not in factors


def test_a_typed_in_date_goes_through_the_expiry_rule(demo):
    conn = demo["conn"]
    case_id = submit(conn)
    upload_everything(conn, case_id)
    release_visual_checks(conn, case_id)
    awaiting = data.awaiting_fields(conn, case_id)
    doc_of = {d: conn.execute("SELECT document_type FROM document WHERE document_id = ?",
                              (d,)).fetchone()[0] for d in awaiting}
    id_doc = next(d for d, t in doc_of.items() if t == "id_document")
    type_everything_in(conn, case_id, {id_doc: {"expiry_date": "2020-01-01"}})

    item = next(i for i in customer_checklist(conn, case_id)["items"]
                if i["document"].startswith("Identity document"))
    assert item["status"] == "Resubmission needed" and "expired" in item["reason"]
    assert item["can_upload"]


def test_the_keyed_values_are_compared_with_what_the_customer_entered(demo):
    conn = demo["conn"]
    case_id = submit(conn)
    run_to_the_end(conn, case_id, "address_mismatch")
    reg = conn.execute("SELECT * FROM registry_check WHERE case_id = ?", (case_id,)).fetchone()
    assert (reg["name_match"], reg["number_match"], reg["address_match"]) == \
        ("match", "match", "mismatch")
    assert conn.execute("SELECT COUNT(*) FROM finding WHERE case_id = ? AND rule_id = 'RG-07'",
                        (case_id,)).fetchone()[0] == 1


# ---------------------------------------------------------------------------
# 4. Every simulated result is labelled
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", ["clean", "address_mismatch", "pep_match",
                                      "possible_sanctions_match"])
def test_every_simulated_result_is_labelled(demo, scenario):
    conn = demo["conn"]
    case_id = submit(conn)
    run_to_the_end(conn, case_id, scenario)

    rows = 0
    for table in ("registry_check", "identity_check", "screening_check"):
        for (name,) in conn.execute(f"SELECT provider_name FROM {table} WHERE case_id = ?",
                                    (case_id,)):
            assert name == f"{SIMULATED} ({scenario})", (table, name)
            rows += 1
    assert rows >= 3, "the checks must have run"

    provider_events = conn.execute(
        "SELECT actor_id, payload_summary FROM audit_event WHERE case_id = ?"
        " AND actor_type = 'external_provider'", (case_id,)).fetchall()
    assert provider_events
    for actor, payload in provider_events:
        assert actor.startswith(SIMULATED), (actor, payload)
    assert "provider=" + SIMULATED in " ".join(p for _, p in provider_events)

    pack = conn.execute("SELECT provider_results FROM evidence_pack WHERE case_id = ?",
                        (case_id,)).fetchone()[0]
    assert SIMULATED in pack

    checks = console.case_detail(conn, case_id, tab="Checks")
    assert checks.count(SIMULATED) >= rows


def test_dataset_cases_keep_their_own_providers(demo):
    names = {r[0] for r in demo["conn"].execute(
        "SELECT provider_name FROM registry_check UNION SELECT provider_name FROM screening_check")}
    assert names and not any(SIMULATED in n for n in names)


# ---------------------------------------------------------------------------
# 5. The scenario control is the console's alone
# ---------------------------------------------------------------------------

def test_the_scenario_control_does_not_exist_in_portal_code(portal):
    for path in sorted(PORTAL.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".css", ".js"):
            text = path.read_text(encoding="utf-8").lower()
            for word in ("scenario", "demo_scenario", "simulated", "pep_match"):
                assert word not in text, f"{path.name} mentions {word!r}"

    case_id = submit(portal["conn"])
    browser = Browser(portal["base"])
    for path in ("/action/demo-scenario", "/demo-scenario", "/scenario"):
        status, _, _ = browser.request(path, {"case_id": case_id, "scenario": "pep_match"})
        assert status == 404, path
    assert data.demo_scenario(portal["conn"], case_id) == "clean"


def test_the_console_offers_the_scenario_on_demo_cases_only_and_audits_it(demo):
    conn = demo["conn"]
    case_id = submit(conn)
    assert 'action="/action/demo-scenario"' in console.case_detail(conn, case_id)
    assert 'action="/action/demo-scenario"' not in console.case_detail(conn, "WAL-ONB-0001")

    data.choose_demo_scenario(conn, case_id, "possible_sanctions_match", "analyst.one")
    event = conn.execute("SELECT actor_type, actor_id, payload_summary FROM audit_event"
                         " WHERE case_id = ? AND action = 'demo_scenario_selected'",
                         (case_id,)).fetchone()
    assert (event["actor_type"], event["actor_id"]) == ("analyst", "analyst.one")
    assert event["payload_summary"].startswith("scenario=possible_sanctions_match")

    with pytest.raises(ScenarioRefused):
        data.choose_demo_scenario(conn, "WAL-ONB-0006", "clean", "analyst.one")
    with pytest.raises(ScenarioRefused):
        data.choose_demo_scenario(conn, case_id, "made_up", "analyst.one")

    upload_everything(conn, case_id)
    release_visual_checks(conn, case_id)
    type_everything_in(conn, case_id)
    with pytest.raises(ScenarioRefused):   # the providers have answered; it stands
        data.choose_demo_scenario(conn, case_id, "clean", "analyst.one")
    assert "fixed" in console.case_detail(conn, case_id)


# ---------------------------------------------------------------------------
# 6. A PEP scenario reaches the portal as neutral wording
# ---------------------------------------------------------------------------

def test_a_pep_scenario_shows_only_neutral_wording_in_the_portal(portal):
    conn = portal["conn"]
    case_id = submit(conn)
    case = run_to_the_end(conn, case_id, "pep_match")
    assert conn.execute("SELECT COUNT(*) FROM screening_check WHERE case_id = ?"
                        " AND pep_result = 'pep_match'", (case_id,)).fetchone()[0] == 1
    assert case["status"] == "enhanced_due_diligence"

    token = data.issue_portal_access(conn, case_id, "test.email_link")
    browser = Browser(portal["base"])
    assert browser.get("/access/" + token)[0] == 303
    for path in ("/", "/checklist", "/messages"):
        status, page = browser.get(path)
        assert status == 200, path
        assert not internal_words(page), f"{path} shows {internal_words(page)}"
        assert not leaks(page), f"{path} carries {leaks(page)}"
    assert "additional review" in words(browser.get("/")[1])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
