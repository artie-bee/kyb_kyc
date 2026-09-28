"""
Hybrid mode: new portal uploads read by the live model.

With EXTRACTION_FOR_NEW_UPLOADS = live_if_available, an upload to a WAL-DEMO-
case that is not a recognised demo sample is queued, checked and read by the
live model in the background, and falls back to the analyst's typing form when
the call fails. The scripted cases never go near it, and the three sources of a
value - live model, recognised sample, analyst - are never shown as one another.

No network: every call goes to a FakeClient that subclasses the real LLMClient,
so strict JSON, the retry and the Call record are the production code.

Run: python -m pytest tests/test_hybrid_extraction.py -q
"""

import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import customer_checklist                      # noqa: E402
from orchestrator import clock, demo_samples, live_reading, llm_client, settings  # noqa: E402
from orchestrator.steps import document_quality                       # noqa: E402
from portal import apply                                              # noqa: E402
from tools.make_sample_documents import FORM_VALUES, build_demo_pack  # noqa: E402
from web import render as console                                     # noqa: E402

VERSION = "fake:fake-model-1/"


class FakeClient(llm_client.LLMClient):
    """Answers as a model reading the file would: from the pack manifest entry
    for the file's original bytes (the tests upload each file with one extra
    byte, so it is not recognised). `fail` names document types to fail on."""

    provider = "fake"

    def __init__(self, fail_quality=(), fail_fields=(), log=None):
        super().__init__(model="fake-model-1", temperature=0.0)
        self.fail_quality, self.fail_fields = set(fail_quality), set(fail_fields)
        self.log = log if log is not None else []

    def _send(self, document_path, text, max_tokens, corrections):
        entry = demo_samples.recognise(content=Path(document_path).read_bytes()[:-1])
        doc_type = entry["document_type"]
        extracting = "Read these fields and no others" in text
        self.log.append(("fields" if extracting else "quality", doc_type, self.temperature))
        if extracting:
            if doc_type in self.fail_fields:
                return "I could not read this one, sorry.", 1, 1, "fake"
            wanted = set(re.findall(r"^  (\w+) \((?:required|optional)\)$", text, re.M))
            return json.dumps({"fields": [f for f in entry["fields"] if f["name"] in wanted]}), 1, 1, "fake"
        if doc_type in self.fail_quality:
            raise ConnectionError("the network is unreachable")
        return json.dumps({"flags": entry["quality_flags"], "confidence": 0.93,
                           "notes": "looks like an original",
                           "expiry_date": entry.get("expiry_date"),
                           "document_date": entry.get("document_date")}), 1, 1, "fake"


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    return build_demo_pack(tmp_path_factory.mktemp("pack"), today=clock.current().today())["folder"]


@pytest.fixture()
def conn(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(demo_samples, "MANIFEST", pack / "manifest.json")
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(settings, "EXTRACTION_FOR_NEW_UPLOADS", "live_if_available")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    c = data.connect(path)
    yield c
    c.close()


def items(c, case_id):
    return {i["document"] + (" - " + i["person"] if i["person"] else ""): i
            for i in customer_checklist(c, case_id)["items"]}


def pack_files(pack):
    """{checklist item name: file} for the pack, less the blurred ID."""
    out = {}
    for line in (pack / "FORM_VALUES.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `([^`]+)` \| (.+?)( \*\(upload first.*\)\*)? \|$", line)
        if m and "BLURRED" not in m.group(1):
            out[m.group(2)] = pack / m.group(1)
    return out


def apply_for(c):
    answers = {key: value for _, _, key, value in FORM_VALUES}
    return data.submit_application(c, apply.build_application(answers, "PORTAL-TEST"))


def upload(c, case_id, pack, name, recognised=False):
    path = pack_files(pack)[name]
    content = path.read_bytes() + (b"" if recognised else b"\n")
    data.upload_document(c, case_id, items(c, case_id)[name]["checklist_item_id"],
                         path.name, content)
    return c.execute("SELECT MAX(document_id) FROM checklist_item_document WHERE item_id = ?",
                     (items(c, case_id)[name]["checklist_item_id"],)).fetchone()[0]


def drain(c, fake):
    done = []
    while (step := data.read_next_live(c, make_client=lambda: fake)) is not None:
        done.append(step)
        assert len(done) < 60, "the queue never empties"
    return done


def current(c, case_id, name):
    return c.execute("SELECT d.* FROM document d WHERE d.document_id = (SELECT MAX(document_id)"
                     " FROM checklist_item_document WHERE item_id = ?)",
                     (items(c, case_id)[name]["checklist_item_id"],)).fetchone()


def fields_of(c, document_id):
    return c.execute("SELECT * FROM extracted_field WHERE document_id = ? ORDER BY field_id",
                     (document_id,)).fetchall()


def blocks(page):
    return re.findall(r"<details.*?</details>", page, re.S)


# ---------------------------------------------------------------------------

def test_an_unrecognised_upload_is_queued_then_read_by_the_live_model(conn, pack):
    c = conn
    case_id = apply_for(c)
    fake = FakeClient()
    doc = upload(c, case_id, pack, "Certificate of incorporation")

    # The portal answered at once: queued, Under review, nothing called yet.
    assert fake.log == []
    row = c.execute("SELECT * FROM document WHERE document_id = ?", (doc,)).fetchone()
    assert row["quality_status"] == "manual_review_required"
    assert "live_read_queued" in row["quality_flags"]
    assert items(c, case_id)["Certificate of incorporation"]["status"] == "Under review"
    assert any(document_quality.visual_hold_document(h.reason) == doc
               and "reading automatically" in h.reason for h in data.open_holds(c, case_id))
    page = console.case_detail(c, case_id, tab="Documents")
    assert "Reading automatically... (1 in queue)" in page

    # Every other item, so that Step 4 runs; then the worker empties the queue.
    for name in pack_files(pack):
        if name != "Certificate of incorporation":
            upload(c, case_id, pack, name)
    assert data.live_queue_length(c) == 11
    done = drain(c, fake)
    assert {d["stage"] for d in done} == {"quality", "fields"}
    assert all(t == 0.0 for _, _, t in fake.log), "temperature 0 on every call"
    assert data.live_queue_length(c) == 0

    for name in pack_files(pack):
        d = current(c, case_id, name)
        assert d["quality_status"] == "accepted_for_checks", name
        rows = fields_of(c, d["document_id"])
        # A selfie has no fields to read (the KB lists none): nothing to label.
        assert bool(rows) == bool(data.kb().fields_for(d["document_type"])), name
        assert all(r["entry_method"] == "extracted" for r in rows), name
    labels = data.live_read_documents(c, case_id)
    cert = current(c, case_id, "Certificate of incorporation")["document_id"]
    assert labels["quality"][cert].startswith("read by live model (" + VERSION)
    assert labels["fields"][cert].startswith("read by live model (" + VERSION)
    assert customer_checklist(c, case_id)["accepted"] == 11
    assert not any("reading automatically" in h.reason for h in data.open_holds(c, case_id))


def test_the_confidence_floor_and_allow_list_still_apply_to_a_live_reading(conn, pack):
    c = conn
    case_id = apply_for(c)
    for name in pack_files(pack):
        upload(c, case_id, pack, name)

    class Faint(FakeClient):
        def _send(self, document_path, text, max_tokens, corrections):
            reply, *rest = super()._send(document_path, text, max_tokens, corrections)
            if "Read these fields" in text and "certificate of incorporation" in text:
                body = json.loads(reply)
                body["fields"][0]["confidence"] = 0.40
                reply = json.dumps(body)
            if "Read these fields" in text and "registry extract" in text:
                reply = json.dumps({"fields": [{"name": "favourite_colour", "value": "blue",
                                                "confidence": 0.99, "source_page": 1}]})
            return (reply, *rest)

    drain(c, Faint())
    cert = fields_of(c, current(c, case_id, "Certificate of incorporation")["document_id"])
    assert sum(r["needs_analyst_correction"] for r in cert) == 1, "0.40 is below the 0.70 floor"
    extract = current(c, case_id, "Company registration extract")
    assert all(r["entry_method"] == "awaiting_analyst_entry"
               for r in fields_of(c, extract["document_id"])), \
        "a field outside the KB list discards the reading: it is typed in instead"
    assert "favourite_colour" in (data.live_unavailable_reason(c, extract["document_id"]) or "")


def test_a_failed_call_falls_back_to_the_typing_form_with_the_reason(conn, pack):
    c = conn
    case_id = apply_for(c)

    # No key at all: the client cannot even be made. The document goes to the
    # ordinary visual-check hold, for a person - never a pass.
    doc = upload(c, case_id, pack, "Certificate of incorporation")

    def no_key():
        raise llm_client.CallFailed("ANTHROPIC_API_KEY is not set")
    step = data.read_next_live(c, make_client=no_key)
    assert step["failure"] == "ANTHROPIC_API_KEY is not set"
    now = current(c, case_id, "Certificate of incorporation")
    assert now["document_id"] != doc
    assert now["quality_status"] == "manual_review_required"
    assert "visual_check_not_run" in now["quality_flags"]
    assert any(document_quality.visual_hold_document(h.reason) == now["document_id"]
               and "visual check not run" in h.reason for h in data.open_holds(c, case_id))
    data.release_document(c, now["document_id"], "analyst.test", "accept", "looked at it")

    # The rest: the network is down for one quality check, and the reply for
    # one document's fields will not parse, twice.
    for name in pack_files(pack):
        if name != "Certificate of incorporation":
            upload(c, case_id, pack, name)
    fake = FakeClient(fail_quality={"registry_extract"}, fail_fields={"ubo_declaration"})
    drain(c, fake)
    for name in ("Company registration extract",):
        d = current(c, case_id, name)
        assert "visual_check_not_run" in d["quality_flags"]
        data.release_document(c, d["document_id"], "analyst.test", "accept", "looked at it")
    drain(c, fake)

    page = console.case_detail(c, case_id, tab="Documents")
    for name, why in (("Certificate of incorporation", "ANTHROPIC_API_KEY is not set"),
                      ("Company registration extract", "ConnectionError: the network is unreachable"),
                      ("Declaration of beneficial owners", "no usable JSON after 2 attempts")):
        d = current(c, case_id, name)
        rows = fields_of(c, d["document_id"])
        assert rows and all(r["entry_method"] == "awaiting_analyst_entry" for r in rows), name
        block = next(b for b in blocks(page) if 'value="' + d["document_id"] + '"' in b)
        m = re.search(r"Automatic reading unavailable: (.*?)\. Type in the fields instead\.", block)
        assert m and why in m.group(1), (name, m and m.group(1))
        assert "/action/enter-fields" in block, "the typing form is there to use"

    # And the form works as it always did.
    d = current(c, case_id, "Declaration of beneficial owners")
    entry = demo_samples.recognise(content=(pack_files(pack)["Declaration of beneficial owners"]
                                            ).read_bytes())
    data.enter_fields(c, d["document_id"], "analyst.test",
                      {f["name"]: f["value"] for f in entry["fields"]})
    assert all(r["entry_method"] == "entered_by_analyst" for r in fields_of(c, d["document_id"]))


def test_scripted_cases_never_use_live_mode(conn, pack):
    c = conn
    fake = FakeClient()
    # Nothing the scripted build did touched the queue or the live model.
    assert data.live_queue_length(c) == 0
    assert data.read_next_live(c, make_client=lambda: fake) is None
    assert c.execute("SELECT COUNT(*) FROM audit_event WHERE action IN (?, ?)"
                     " OR payload_summary LIKE ?",
                     (live_reading.FIELDS_QUEUED, live_reading.UNAVAILABLE,
                      "%read by live model%")).fetchone()[0] == 0

    # An extra document on a scripted case takes the mock path, setting or no.
    assert not live_reading.wants_live("WAL-ONB-0005", b"%PDF-anything")
    item_id = data.add_checklist_item(c, "WAL-ONB-0005", "bank_statement", "compliance.test",
                                      "EDD: recent statement")
    data.upload_document(c, "WAL-ONB-0005", item_id, "statement.pdf",
                         (pack / "01_certificate_of_incorporation.pdf").read_bytes() + b"\n")
    doc = c.execute("SELECT d.* FROM document d JOIN checklist_item_document USING (document_id)"
                    " WHERE item_id = ?", (item_id,)).fetchone()
    assert "visual_check_not_run" in doc["quality_flags"]
    assert "live_read_queued" not in doc["quality_flags"]
    assert data.read_next_live(c, make_client=lambda: fake) is None and fake.log == []


def test_mock_only_is_the_default_and_queues_nothing(conn, pack, monkeypatch):
    c = conn
    monkeypatch.setattr(settings, "EXTRACTION_FOR_NEW_UPLOADS", "mock_only")
    case_id = apply_for(c)
    doc = upload(c, case_id, pack, "Certificate of incorporation")
    flags = c.execute("SELECT quality_flags FROM document WHERE document_id = ?", (doc,)).fetchone()[0]
    assert "visual_check_not_run" in flags and "live_read_queued" not in flags
    assert data.live_queue_length(c) == 0


def test_labels_are_right_for_all_three_sources(conn, pack):
    c = conn
    case_id = apply_for(c)
    recognised = {"Certificate of incorporation", "Tax registration certificate"}
    typed = "List of authorised signatories"
    for name in pack_files(pack):
        upload(c, case_id, pack, name, recognised=name in recognised)
    fake = FakeClient(fail_fields={"authorised_signatory_list"})
    drain(c, fake)
    d = current(c, case_id, typed)
    data.enter_fields(c, d["document_id"], "analyst.test", {"signatory_name": "Mari Tamm"})

    page = console.case_detail(c, case_id, tab="Documents")
    live_label = "read by live model (" + VERSION
    seen = set()
    for name in pack_files(pack):
        d = current(c, case_id, name)
        if not data.kb().fields_for(d["document_type"]):
            continue                            # a selfie: no fields, nothing to label
        # The re-screen kept the stored file, so the superseded row names it too;
        # the current one is the row with the fields.
        block = next(b for b in blocks(page) if "<h3>Fields</h3>" in b
                     and "stored as <span class=\"type\">" + d["file_name"] in b)
        fields = block.split("<h3>Fields</h3>", 1)[1]
        has = {"live": live_label in fields, "sample": data.DEMO_SAMPLE_LABEL in fields,
               "typed": "entered by an analyst" in fields}
        want = ("sample" if name in recognised else "typed" if name == typed else "live")
        assert has == {k: k == want for k in has}, (name, has)
        seen.add(want)
        # The rows underneath say the same.
        methods = {r["entry_method"] for r in fields_of(c, d["document_id"])}
        assert methods == {"entered_by_analyst" if want == "typed" else "extracted"}, name
    assert seen == {"live", "sample", "typed"}
    # A recognised sample never went near the model.
    assert not {t for _, t, _ in fake.log} & {"certificate_of_incorporation",
                                              "tax_registration_certificate"}


def test_the_vaher_studio_pack_is_recognised_file_by_file():
    folder = ROOT / "demo_packs" / "vaher_studio"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = sorted(p for p in folder.iterdir() if p.name != "manifest.json")
    assert len(files) == 12
    for path in files:
        entry = demo_samples.recognise(path=path)
        assert entry is not None and entry["file"] == path.name, path.name
        assert not live_reading.wants_live("WAL-DEMO-0001", path.read_bytes())
    blurred = demo_samples.recognise(path=folder / "07b_identity_document_kristiina_vaher_BLURRED.jpg")
    assert blurred["quality_flags"] == ["blurred_unreadable"]
    assert manifest["register"]["number"] == "16880342"
    from orchestrator import providers
    found = providers.lookup_register(manifest["register"]["number"])
    assert found and found["legal_name"] == "Vaher Studio OÜ"


def test_files_set_up_for_typing_before_they_were_recognised_can_be_read(conn, pack, tmp_path,
                                                                           monkeypatch):
    """What happened to WAL-DEMO-0001: the files were uploaded, accepted by an
    analyst and set up for typing before the pack was recognised. Once it is,
    the recorded fields replace the empty rows - and nothing typed is touched."""
    c = conn
    monkeypatch.setattr(settings, "EXTRACTION_FOR_NEW_UPLOADS", "mock_only")
    monkeypatch.setattr(demo_samples, "MANIFEST", tmp_path / "no_manifest.json")
    monkeypatch.setattr(demo_samples, "PACKS", tmp_path / "no_packs")
    case_id = apply_for(c)
    for name in pack_files(pack):
        upload(c, case_id, pack, name, recognised=True)      # exact bytes, not yet known
    for name in pack_files(pack):
        data.release_document(c, current(c, case_id, name)["document_id"], "analyst.test",
                              "accept", "looked at it")
        data.carry_on(c, case_id)
    typed = current(c, case_id, "Tax registration certificate")["document_id"]
    data.enter_fields(c, typed, "analyst.test", {"vat_number": "EE-TYPED"})
    assert any("fields not read automatically" in h.reason for h in data.open_holds(c, case_id))
    assert data.recognised_awaiting(c, case_id) == []

    monkeypatch.setattr(demo_samples, "MANIFEST", pack / "manifest.json")     # the pack arrives
    stale = data.recognised_awaiting(c, case_id)
    assert typed not in {d["document_id"] for d in stale} and len(stale) >= 8
    page = console.case_detail(c, case_id, tab="Documents")
    assert "/action/read-recognised" in page

    assert data.read_recognised_samples(c, case_id) == len(stale)
    assert not any("fields not read automatically" in h.reason for h in data.open_holds(c, case_id))
    assert data.recognised_awaiting(c, case_id) == []
    for d in stale:
        rows = fields_of(c, d["document_id"])
        assert rows and all(r["entry_method"] == "extracted" for r in rows)
    kept = fields_of(c, typed)
    assert {r["entry_method"] for r in kept} == {"entered_by_analyst"}, "typed rows stand"
    assert next(r["value"] for r in kept if r["name"] == "vat_number") == "EE-TYPED"
    page = console.case_detail(c, case_id, tab="Documents")
    assert "/action/read-recognised" not in page
    block = next(b for b in blocks(page) if "<h3>Fields</h3>" in b and 'stored as <span class="type">'
                 + current(c, case_id, "Certificate of incorporation")["file_name"] in b)
    assert data.DEMO_SAMPLE_LABEL in block.split("<h3>Fields</h3>", 1)[1]
