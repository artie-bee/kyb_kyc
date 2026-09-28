"""
Demo-case document checks and portal status, after the WAL-DEMO-0001 / 0002
investigation.

  A. the demo scenario control touches simulated providers only - never a
     document-quality or extraction hold;
  B. the analyst's typing form starts empty, beside the document preview;
  C. comparisons use what the DOCUMENTS say, so another company's documents
     produce mismatches;
  D. an unrecognised mock upload stays held until an analyst acts; a
     recognised blurred ID is refused automatically;
  E. the portal and the console read the same records, and only items with no
     acceptable upload count as "still needed".

Run: python -m pytest tests/test_demo_case_checks.py -q
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import customer_checklist                      # noqa: E402
from orchestrator import clock, demo_samples                          # noqa: E402
from orchestrator.steps import document_quality                       # noqa: E402
from orchestrator.steps.requirement_pack import awaiting_confirmation  # noqa: E402
from portal import apply                                              # noqa: E402
from tools.make_sample_documents import FORM_VALUES, build_demo_pack  # noqa: E402
from web import render as console                                     # noqa: E402

ID_ITEM = "Identity document (passport or ID card) - Kristiina Vaher"
# What the other company's documents say - the Vaher Studio files that were
# uploaded against the Lumen Harbour application.
OTHER_COMPANY = {"company_name": "Vaher Studio OU", "legal_name": "Vaher Studio OU",
                 "registration_number": "16880342",
                 "registered_address": "Ruutli 18, 51007 Tartu, Estonia",
                 "entity_status": "active", "director_name": "Kristiina Vaher",
                 "full_name": "Kristiina Vaher", "date_of_birth": "1988-04-14",
                 "expiry_date": "2031-05-31", "signatory_name": "Kristiina Vaher",
                 "ubo_name": "Kristiina Vaher", "ownership_percentage": "100",
                 "declared_source": "Design fees", "declared_industry": "Design studio"}


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    return build_demo_pack(tmp_path_factory.mktemp("pack"), today=clock.current().today())


@pytest.fixture()
def conn(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(demo_samples, "MANIFEST", pack["folder"] / "manifest.json")
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    c = data.connect(path)
    yield c
    c.close()


def lumen_harbour(conn) -> str:
    """The Lumen Harbour application, exactly as FORM_VALUES.md has it typed."""
    answers = {key: value for _, _, key, value in FORM_VALUES}
    return data.submit_application(conn, apply.build_application(answers, "PORTAL-TEST"))


def items(conn, case_id) -> dict:
    return {i["document"] + (" - " + i["person"] if i["person"] else ""): i
            for i in customer_checklist(conn, case_id)["items"]}


def pack_files(pack) -> dict:
    """{portal item name: the clear pack file for it} from FORM_VALUES.md."""
    out = {}
    for line in (pack["folder"] / "FORM_VALUES.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `([^`]+)` \| (.+?)( \*\(upload first.*\)\*)? \|$", line)
        if m and "BLURRED" not in m.group(1):
            out[m.group(2)] = pack["folder"] / m.group(1)
    return out


def upload(conn, case_id, name, content, file_name="upload.pdf"):
    data.upload_document(conn, case_id, items(conn, case_id)[name]["checklist_item_id"],
                         file_name, content)


def unrecognised(content: bytes) -> bytes:
    """The same file with one byte added after its end: a valid file of the same
    type, but not the one the manifest knows."""
    return content + b"\n"


def status(conn, case_id):
    return conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone()[0]


def visual_holds(conn, case_id):
    return [h for h in data.open_holds(conn, case_id)
            if "visual check not run in mock mode" in h.reason]


# ---------------------------------------------------------------------------

def test_an_unrecognised_id_stays_held_and_the_case_never_gets_ready(conn, pack):
    """A sharp ID the manifest does not know: the blur rule passes it, nothing
    in mock mode can judge the rest, so it waits for a person. (A blurred one is
    refused outright by QR-13 - see tests/test_blur_check.py.)"""
    case_id = lumen_harbour(conn)
    data.choose_demo_scenario(conn, case_id, "clean", "analyst.test")
    files = pack_files(pack)
    for name, path in files.items():
        if name == ID_ITEM:
            upload(conn, case_id, name, unrecognised(path.read_bytes()), "my_id.jpg")
        else:
            upload(conn, case_id, name, path.read_bytes(), path.name)

    assert len(visual_holds(conn, case_id)) == 1, "the unrecognised ID waits for a person"
    assert status(conn, case_id) != "ready_for_decision"
    assert conn.execute("SELECT COUNT(*) FROM risk_assessment WHERE case_id = ?",
                        (case_id,)).fetchone()[0] == 0, "no paid check before a person looks"
    assert items(conn, case_id)[ID_ITEM]["status"] == "Under review"

    # Rule A: choosing a scenario again changes nothing about that hold.
    data.choose_demo_scenario(conn, case_id, "clean", "analyst.test")
    data.choose_demo_scenario(conn, case_id, "address_mismatch", "analyst.test")
    assert len(visual_holds(conn, case_id)) == 1
    assert status(conn, case_id) != "ready_for_decision"

    # Only an analyst's decision on the document moves it: here, send it back.
    held = conn.execute("SELECT document_id FROM document WHERE case_id = ?"
                        " AND quality_status = 'manual_review_required'", (case_id,)).fetchone()[0]
    data.release_document(conn, held, "analyst.test", "request_resubmission", "not legible",
                          reason_code="document_unreadable")
    assert not visual_holds(conn, case_id)
    assert items(conn, case_id)[ID_ITEM]["status"] == "Resubmission needed"


def test_the_scenario_control_never_touches_a_document_hold():
    source = (ROOT / "orchestrator" / "demo_scenarios.py").read_text(encoding="utf-8")
    for call in ("holds.", "release", "case_hold", "UPDATE", "INSERT", "DELETE"):
        assert call not in source.split('"""', 2)[2], f"demo_scenarios.py uses {call}"


def test_a_recognised_blurred_id_is_refused_with_the_resubmission_reason(conn, pack):
    case_id = lumen_harbour(conn)
    upload(conn, case_id, ID_ITEM,
           (pack["folder"] / "07_identity_document_BLURRED.jpg").read_bytes(), "id.jpg")
    item = items(conn, case_id)[ID_ITEM]
    assert item["status"] == "Resubmission needed"
    assert item["reason"].startswith("We could not read this document clearly")
    assert not visual_holds(conn, case_id), "recognised: no analyst needed to refuse it"


def test_documents_for_a_different_company_produce_mismatches(conn, pack):
    """The Vaher Studio files against the Lumen Harbour application: the
    analyst types what they actually say, and the register disagrees."""
    case_id = lumen_harbour(conn)
    for name, path in pack_files(pack).items():
        upload(conn, case_id, name, unrecognised(path.read_bytes()), path.name)
    for (doc,) in conn.execute("SELECT document_id FROM document WHERE case_id = ?"
                               " AND quality_status = 'manual_review_required'",
                               (case_id,)).fetchall():
        data.release_document(conn, doc, "analyst.test", "accept", "legible")
    for doc, fields in data.awaiting_fields(conn, case_id).items():
        typed = {f["name"]: OTHER_COMPANY.get(f["name"], "synthetic" if f["required"] else "")
                 for f in fields}
        if "document_date" in typed:
            typed["document_date"] = clock.current().today().isoformat()
        data.enter_fields(conn, doc, "analyst.test", typed)

    reg = conn.execute("SELECT registry_legal_name, name_match, number_match, address_match"
                       " FROM registry_check WHERE case_id = ?", (case_id,)).fetchone()
    assert reg["registry_legal_name"] == "Lumen Harbour OU"
    assert (reg["name_match"], reg["number_match"], reg["address_match"]) == \
        ("mismatch", "mismatch", "mismatch")
    assert status(conn, case_id) != "ready_for_decision"


def test_the_director_check_never_falls_back_to_the_declared_names(conn, pack):
    """No document names a director: nothing to compare, so no match claimed."""
    from orchestrator.steps import verification
    case_id = lumen_harbour(conn)
    for name, path in pack_files(pack).items():
        upload(conn, case_id, name, path.read_bytes(), path.name)
    conn.execute("UPDATE extracted_field SET value = NULL WHERE name = 'director_name'"
                 " AND document_id IN (SELECT document_id FROM document WHERE case_id = ?)",
                 (case_id,))
    conn.execute("DELETE FROM registry_check WHERE case_id = ?", (case_id,))
    from orchestrator.providers import get_providers
    registry, identity = get_providers("mock", data._application(case_id, conn))
    verification.run(conn, case_id, data._application(case_id, conn), data.kb(),
                     registry_provider=registry, identity_provider=identity)
    row = conn.execute("SELECT director_match FROM registry_check WHERE case_id = ?"
                       " ORDER BY check_id DESC LIMIT 1", (case_id,)).fetchone()
    assert row[0] == "unavailable"


def test_the_typing_form_has_no_prefilled_values_and_sits_beside_the_preview(conn, pack):
    case_id = lumen_harbour(conn)
    for name, path in pack_files(pack).items():
        upload(conn, case_id, name, unrecognised(path.read_bytes()), path.name)
    for (doc,) in conn.execute("SELECT document_id FROM document WHERE case_id = ?"
                               " AND quality_status = 'manual_review_required'",
                               (case_id,)).fetchall():
        data.release_document(conn, doc, "analyst.test", "accept", "legible")
    page = console.case_detail(conn, case_id, tab="Documents")
    forms = re.findall(r'<form method="post" action="/action/enter-fields".*?</form>', page, re.S)
    assert forms, "there is something to type in"
    declared = {value for _, _, key, value in FORM_VALUES
                if key in ("legal_name", "registration_number", "registered_address",
                           "d1_name", "d1_dob", "contact_email")}
    assert len(declared) == 6
    for form in forms:
        for tag in re.findall(r'<input type="text"[^>]*>', form):
            assert 'value=""' in tag, tag
        for value in declared:
            assert value not in form, f"the form offers the declared value {value!r}"
    for block in re.findall(r"<details.*?</details>", page, re.S):
        if "/action/enter-fields" in block:
            grid = block[block.index('<div class="grid2">'):]
            assert grid.index('class="filecard"') < grid.index("/action/enter-fields")
            assert "<h3>Fields</h3>" not in grid[:grid.index("/action/enter-fields")]


def _expected_from_console(conn, case_id):
    """What the portal must show, worked out from the console's own rows."""
    kb = data.kb()
    white_label = data.is_white_label(conn, case_id)
    out, owed = {}, 0
    for item in data.checklist(conn, case_id):
        if item["status"] == "waived" or awaiting_confirmation(item):
            continue
        current = conn.execute("SELECT MAX(document_id) FROM checklist_item_document"
                               " WHERE item_id = ?", (item["item_id"],)).fetchone()[0]
        rows = conn.execute("SELECT entry_method FROM extracted_field WHERE document_id = ?",
                            (current,)).fetchall() if current else []
        reading = current and not white_label and (
            any(r[0] == "awaiting_analyst_entry" for r in rows)
            or (not rows and kb.fields_for(item["document_type"])))
        if item["status"] == "resubmission_requested":
            s = "Resubmission needed"
        elif item["status"] == "accepted":
            s = "Accepted"          # accepted is accepted, whether or not read yet
        elif item["status"] == "manual_review" or current:
            s = "Under review"
        else:
            s = "Not uploaded yet"
        out[item["item_id"]] = s
        owed += item["level"] != "optional" and s in ("Not uploaded yet", "Resubmission needed")
    return out, owed


def test_portal_and_console_agree_on_every_item_and_on_the_count(conn, pack):
    case_id = lumen_harbour(conn)
    files = pack_files(pack)
    states = []
    for n, (name, path) in enumerate(files.items()):
        # half recognised, half not: both paths, at every point along the way
        content = path.read_bytes() if n % 2 else unrecognised(path.read_bytes())
        upload(conn, case_id, name, content, path.name)
        states.append(case_id)
    cases = [r[0] for r in conn.execute("SELECT case_id FROM onboarding_case").fetchall()]
    for cid in cases:
        cl = customer_checklist(conn, cid)
        expected, owed = _expected_from_console(conn, cid)
        assert {i["checklist_item_id"]: i["status"] for i in cl["items"]} == expected, cid
        assert cl["still_needed"] == owed, cid
    lumen = customer_checklist(conn, case_id)
    assert lumen["still_needed"] == 0, "everything is uploaded, so nothing is still needed"
    assert any(i["status"] == "Under review" for i in lumen["items"])


def test_the_matching_pack_reaches_ready_for_decision_and_only_a_person_approves(conn, pack):
    case_id = lumen_harbour(conn)
    for name, path in pack_files(pack).items():
        upload(conn, case_id, name, path.read_bytes(), path.name)
    assert status(conn, case_id) == "ready_for_decision"
    assert conn.execute("SELECT COUNT(*) FROM human_decision WHERE case_id = ?",
                        (case_id,)).fetchone()[0] == 0
    data.carry_on(conn, case_id)
    assert status(conn, case_id) == "ready_for_decision", "nothing approves it by itself"

    result = data.record_decision(conn, case_id, "analyst.test", "analyst", "approve",
                                  "demo_decision", "documents and checks complete")
    assert result.status == "approved" and status(conn, case_id) == "approved"
    decision = conn.execute("SELECT reviewer, reviewer_role FROM human_decision"
                            " WHERE case_id = ?", (case_id,)).fetchone()
    assert tuple(decision) == ("analyst.test", "analyst")
