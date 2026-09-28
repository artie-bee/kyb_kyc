"""
STEP 4 - OCR and structured extraction  (brief Section 5.4)

Input : case_id. Documents are read ONLY through
        document_quality.accepted_documents(), so a file that failed the quality
        screen - or that is still held for an analyst - cannot be extracted.
Output: ExtractionResultSummary, plus one extracted_field row per value read

What happens to each accepted document:
  1. the KB says which fields this document type should yield
  2. the extractor reads them (mock replays the script; Claude is stubbed)
  3. a required field that is missing, or any value below CONFIDENCE_FLOOR, is
     marked needs_analyst_correction - the value is never guessed
  4. the date rules QR-02 and QR-03 run again on the dates OCR read. Step 3 ran
     them on the dates its checker read off the page; if the two readings
     disagree the document goes to manual review, because one of the two
     readings is wrong and neither is automatically the right one to believe

Routing: every required field present and confident -> "verification".
Anything outstanding -> analyst_review_required, owner analyst.
"""

from dataclasses import dataclass, field
from datetime import date

from .. import clock, db
from .. import holds
from .. import llm_client
from ..extractor import (Extractor, MockExtractor,
                         UnknownExtractedField)
from ..kb import KnowledgeBase
from .. import demo_samples, live_reading
from . import document_quality

ACTOR = "step.extraction"
# Below this, a value is not acted on without a human confirming it (Section 5.4).
CONFIDENCE_FLOOR = 0.70


@dataclass
class ExtractionSummary:
    case_id: str
    documents_read: int
    fields_extracted: int
    low_confidence: int
    missing_required: int
    date_conflicts: int
    status: str
    next_step: str | None
    problems: list[str] = field(default_factory=list)


def _date_fields(doc_type: str, values: dict) -> dict:
    """The dates OCR read, in the shape the date rules expect."""
    dates = {}
    if "expiry_date" in values:
        dates["expiry_date"] = values["expiry_date"]
    if "document_date" in values:
        dates["document_date"] = values["document_date"]
    return dates


def run(conn, case_id: str, application: dict, kb: KnowledgeBase,
        extractor: Extractor | None = None, today: date | None = None,
        document_ids: set[str] | None = None) -> ExtractionSummary:
    """document_ids, when given, limits the run to those documents - the live
    reader hands over one document it has read, and nothing else on the case."""
    extractor = extractor or MockExtractor()
    today = today or clock.current().today()

    # The ONLY way documents enter this step.
    documents = document_quality.accepted_documents(conn, case_id)
    # A document already read is not read again. Re-running the step after an
    # analyst releases one more document should extract that one, not duplicate
    # every value on the case.
    already = {r["document_id"] for r in conn.execute(
        "SELECT DISTINCT document_id FROM extracted_field f JOIN document d"
        " USING (document_id) WHERE d.case_id = ?", (case_id,))}
    if extractor.mode == "mock":
        # A portal upload set up for typing before its file was a recognised
        # demo sample (the pack was added later) is read now, like any other
        # recognised file. Only rows nobody has typed into are replaced.
        for d in documents:
            if d["document_id"] in already and recognised_awaiting(conn, case_id, d):
                conn.execute("DELETE FROM extracted_field WHERE document_id = ?"
                             " AND entry_method = 'awaiting_analyst_entry'", (d["document_id"],))
                db.audit(conn, case_id, "system", ACTOR, "awaiting_fields_replaced_by_sample",
                         f"{d['document_id']} ({d['document_type']}, {d['file_name']}): now a "
                         f"{demo_samples.LABEL}; the empty rows set up for typing are replaced "
                         f"by its recorded fields", kb.version)
                already.discard(d["document_id"])
    documents = [d for d in documents if d["document_id"] not in already
                 and (document_ids is None or d["document_id"] in document_ids)]
    payload_by_name = {d["file_name"]: d for d in application.get("documents", [])}
    max_age = {r["document_type"]: r["max_age_days"] for r in kb.requirement_rules}

    problems = []
    n_fields = low_conf = missing_req = conflicts = 0

    for doc in documents:
        doc_type = doc["document_type"]
        expected = kb.fields_for(doc_type)
        payload = payload_by_name.get(doc["file_name"])

        replayed = None
        if payload is None:
            # A file uploaded through the portal: nothing about it is scripted.
            upload = document_quality.UPLOADS / case_id / doc["file_name"]
            sample = (demo_samples.recognise(path=upload)
                      if extractor.mode == "mock" else None)
            if sample is not None and sample["document_type"] == doc_type:
                # A file from the demo upload pack: its fields are on record.
                replayed = f"{demo_samples.LABEL} ({sample['file']})"
                payload = {"file_name": doc["file_name"], "document_type": doc_type,
                           "scripted_fields": sample.get("fields", [])}
        if payload is None:
            if extractor.mode == "mock":
                # Nothing in mock mode can read it, and a value nobody read must
                # not look like one a machine did. Each field it should yield is
                # set up empty, for an analyst to type in from the file.
                for f in expected:
                    conn.execute(
                        "INSERT INTO extracted_field (field_id, document_id, name, value,"
                        " confidence, source_page, corrected_by_analyst,"
                        " needs_analyst_correction, entry_method)"
                        " VALUES (?,?,?,NULL,0,NULL,0,1,'awaiting_analyst_entry')",
                        (db.next_id(conn, "extracted_field"), doc["document_id"], f["field_name"]))
                if expected and live_reading.should_queue_fields(conn, case_id, doc["document_id"]):
                    # Hybrid mode: the live model reads it from the queue. The empty
                    # rows stay as they are, so if the call fails the analyst types
                    # the fields in exactly as they would have.
                    db.audit(conn, case_id, "system", ACTOR, live_reading.FIELDS_QUEUED,
                             f"{doc['document_id']} ({doc_type}, {doc['file_name']}): queued for "
                             f"the live model to read {len(expected)} field(s)", kb.version)
                    continue
                db.audit(conn, case_id, "system", ACTOR, "fields_awaiting_analyst_entry",
                         f"{doc['document_id']} ({doc_type}, {doc['file_name']}): fields not "
                         f"read automatically in mock mode; {len(expected)} field(s) set up for "
                         f"an analyst to type in from the file", kb.version)
                continue
            payload = {"file_name": doc["file_name"], "document_type": doc_type,
                       "file_path": str(upload) if upload.exists() else None}

        try:
            result = extractor.extract(payload, expected)
        except (llm_client.CallFailed, UnknownExtractedField, ValueError) as e:
            # Nothing was read, so nothing is known. The document is held rather
            # than recorded as having yielded no fields.
            msg = f"{doc['file_name']}: extraction failed ({e})"
            problems.append(msg)
            conflicts += 1
            conn.execute("UPDATE document SET quality_status = 'manual_review_required' "
                         "WHERE document_id = ?", (doc["document_id"],))
            _set_item_status(conn, doc["document_id"], "manual_review")
            db.audit(conn, case_id, "system", ACTOR, "extraction_failed",
                     f"{msg}; the {extractor.mode} extractor returned nothing usable, so the "
                     f"document is held rather than treated as empty",
                     extractor.version or kb.version)
            continue
        values, flagged = {}, []

        for value in result.fields:
            needs_correction = (value.value is None or str(value.value).strip() == ""
                                or float(value.confidence) < CONFIDENCE_FLOOR)
            if needs_correction:
                low_conf += 1
                flagged.append(f"{value.name} ({value.confidence:.2f})")
            conn.execute(
                "INSERT INTO extracted_field (field_id, document_id, name, value, confidence,"
                " source_page, corrected_by_analyst, needs_analyst_correction)"
                " VALUES (?,?,?,?,?,?,0,?)",
                (db.next_id(conn, "extracted_field"), doc["document_id"], value.name,
                 value.value, value.confidence, value.source_page, int(needs_correction)))
            n_fields += 1
            values[value.name] = value.value

        # 3. Required fields the document did not yield at all.
        for name in kb.required_fields_for(doc_type):
            if name not in values:
                missing_req += 1
                msg = f"{doc['file_name']}: required field '{name}' was not extracted"
                problems.append(msg)
                conn.execute(
                    "INSERT INTO extracted_field (field_id, document_id, name, value, confidence,"
                    " source_page, corrected_by_analyst, needs_analyst_correction)"
                    " VALUES (?,?,?,NULL,0,NULL,0,1)",
                    (db.next_id(conn, "extracted_field"), doc["document_id"], name))
                n_fields += 1

        # 4. Re-confirm the dates. Step 3 read them off the page before anything
        #    was extracted; OCR has now read them properly.
        ocr_dates = _date_fields(doc_type, values)
        if ocr_dates:
            screen_flags = document_quality.run_date_rules(
                doc_type, {"expiry_date": doc["expiry_date"],
                           "document_date": doc["document_date"]},
                max_age.get(doc_type), kb, today)
            ocr_flags = document_quality.run_date_rules(
                doc_type, ocr_dates, max_age.get(doc_type), kb, today)
            if screen_flags != ocr_flags:
                conflicts += 1
                msg = (f"{doc['file_name']}: the dates read at the quality screen and by OCR "
                       f"disagree (screen {sorted(screen_flags) or 'clear'}, "
                       f"OCR {sorted(ocr_flags) or 'clear'}); sent for manual review")
                problems.append(msg)
                conn.execute("UPDATE document SET quality_status = 'manual_review_required' "
                             "WHERE document_id = ?", (doc["document_id"],))
                _set_item_status(conn, doc["document_id"], "manual_review")
                db.audit(conn, case_id, "system", ACTOR, "extraction_date_conflict", msg,
                         kb.version)

        if flagged:
            msg = (f"{doc['file_name']}: {len(flagged)} field(s) below the "
                   f"{CONFIDENCE_FLOOR:.2f} confidence floor: {', '.join(flagged)}")
            problems.append(msg)
            db.audit(conn, case_id, "ai_agent", ACTOR, "extraction_low_confidence", msg,
                     extractor.version or kb.version)

        # Where the values came from, in words, on the audit row: a replayed demo
        # sample, or the live model with its provider, model and prompt version.
        label = replayed or getattr(extractor, "source_label", None)
        db.audit(conn, case_id, "system" if replayed else "ai_agent", ACTOR, "fields_extracted",
                 f"{doc['document_id']} ({doc_type}, {doc['file_name']}): "
                 f"{len(result.fields)} field(s) read, {len(flagged)} below the floor; "
                 f"extractor={extractor.mode}" + (f"; {label}" if label else ""),
                 extractor.version or kb.version)

    return route_case(conn, case_id, kb, len(documents), n_fields, low_conf, missing_req,
                      conflicts, problems)


def recognised_awaiting(conn, case_id: str, doc) -> bool:
    """Is this a portal upload whose fields are all still waiting to be typed in,
    and whose file is a recognised demo sample of the same document type?"""
    methods = {r[0] for r in conn.execute(
        "SELECT entry_method FROM extracted_field WHERE document_id = ?", (doc["document_id"],))}
    if methods != {"awaiting_analyst_entry"}:
        return False
    sample = demo_samples.recognise(path=document_quality.UPLOADS / case_id / doc["file_name"])
    return sample is not None and sample["document_type"] == doc["document_type"]


def route_case(conn, case_id: str, kb: KnowledgeBase, documents_read: int = 0,
               n_fields: int = 0, low_conf: int = 0, missing_req: int = 0,
               conflicts: int = 0, problems: list[str] | None = None) -> ExtractionSummary:
    """Route on what is still outstanding. Called again after an analyst corrects
    a field, so a correction can release the case without re-extracting anything."""
    problems = problems or []
    outstanding = conn.execute(
        "SELECT COUNT(*) FROM extracted_field f JOIN document d USING (document_id) "
        "WHERE d.case_id = ? AND f.needs_analyst_correction = 1 AND f.corrected_by_analyst = 0"
        " AND f.entry_method = 'extracted'", (case_id,)).fetchone()[0]
    # Documents whose fields nobody has read yet (mock mode, portal uploads).
    awaiting = conn.execute(
        "SELECT COUNT(DISTINCT f.document_id) FROM extracted_field f JOIN document d"
        " USING (document_id) WHERE d.case_id = ? AND f.entry_method = 'awaiting_analyst_entry'",
        (case_id,)).fetchone()[0]
    held = conn.execute(
        "SELECT COUNT(*) FROM document WHERE case_id = ? AND quality_status = "
        "'manual_review_required'", (case_id,)).fetchone()[0]

    required_open = conn.execute(
        "SELECT COUNT(*) FROM checklist_item i JOIN requirement_pack p USING (pack_id) "
        "WHERE p.case_id = ? AND i.level = 'required' AND i.status != 'accepted'",
        (case_id,)).fetchone()[0]

    queued = len(live_reading.queued_fields(conn, case_id))
    awaiting -= queued

    holds.release_own(conn, case_id, ACTOR, "extraction re-evaluated", kb)

    if queued:
        holds.place(conn, case_id, ACTOR, "manual_review",
                    f"{document_quality.LIVE_QUEUED}: {queued} document(s) queued for the live "
                    f"model to read their fields", "analyst", kb)
    if awaiting:
        holds.place(conn, case_id, ACTOR, "manual_review",
                    f"fields not read automatically in mock mode: {awaiting} document(s) "
                    f"need their fields typed in by an analyst from the file", "analyst", kb)
    if outstanding or held:
        # `held` counts every document waiting on an analyst, whatever put it
        # there - a date conflict found here, or a fault found at the quality
        # screen. Naming one cause for both would put a false reason on the
        # hold banner, so the wording states the fact and not the cause.
        summary = (f"{outstanding} field(s) need an analyst"
                   + (f"; {held} document(s) still held for an analyst" if held else ""))
        holds.place(conn, case_id, ACTOR, "insufficient_evidence",
                    f"{outstanding} extracted field(s) cannot be relied on as read"
                    + (f" and {held} document(s) are still held for an analyst" if held else ""),
                    "analyst", kb)
    elif queued or awaiting:
        summary = (f"{queued} document(s) queued for the live model, " if queued else "") + (
            f"{awaiting} document(s) waiting for an analyst to type their fields in")
    elif required_open:
        summary = (f"{required_open} required checklist item(s) still outstanding; "
                   f"verification stays closed")
    else:
        summary = "every required field extracted above the confidence floor"

    status, owner = holds.apply_status(conn, case_id, kb, "verification_in_progress", "system")
    # Verification gates itself on the checklist, so it is only offered when the
    # case is actually ready for a paid call.
    next_step = "verification" if not required_open and not holds.open_holds(conn, case_id) else None

    db.audit(conn, case_id, "system", ACTOR, "extraction_completed",
             f"{documents_read} accepted document(s) read, {n_fields} field(s) extracted, "
             f"{low_conf} below the floor, {missing_req} required field(s) missing; "
             f"{summary}; case -> {status}", kb.version)

    return ExtractionSummary(case_id, documents_read, n_fields, low_conf, missing_req,
                             conflicts, status, next_step, problems)


def _set_item_status(conn, document_id: str, status: str) -> None:
    item = conn.execute("SELECT item_id FROM checklist_item_document WHERE document_id = ?",
                        (document_id,)).fetchone()
    if item:
        conn.execute("UPDATE checklist_item SET status = ? WHERE item_id = ?",
                     (status, item["item_id"]))


def accept_field_as_read(conn, field_id: str, analyst_id: str, reason: str,
                        kb: KnowledgeBase | None = None) -> dict:
    """An analyst has read the original and is content with what OCR produced.

    The alternative to correcting a faint value is not ignoring it: someone looks
    at the document and says the reading is right. The confidence stays on the
    record and corrected_by_analyst stays false - nothing was changed - but the
    field no longer holds the case up.
    """
    if not (reason or "").strip():
        raise ValueError("a reason is required to accept a low-confidence field as read")
    if not (analyst_id or "").strip():
        raise ValueError("the accepting analyst must be identified")

    kb = kb or KnowledgeBase()
    row = conn.execute(
        "SELECT f.*, d.case_id, d.file_name FROM extracted_field f JOIN document d "
        "USING (document_id) WHERE f.field_id = ?", (field_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such extracted field {field_id}")
    if not row["needs_analyst_correction"]:
        raise ValueError(f"{field_id} is not waiting on an analyst")

    conn.execute("UPDATE extracted_field SET needs_analyst_correction = 0 WHERE field_id = ?",
                 (field_id,))
    db.audit(conn, row["case_id"], "analyst", analyst_id, "extracted_field_accepted_as_read",
             f"{field_id} ({row['name']} on {row['file_name']}) accepted as read by {analyst_id}; "
             f"value {row['value']!r} kept at confidence {row['confidence']}; reason: {reason}",
             kb.version)
    return {"field_id": field_id, "value": row["value"], "accepted_as_read": True}


def replay_scripted_acceptances(conn, case_id: str, acceptances: list[dict],
                                kb: KnowledgeBase | None = None) -> list[dict]:
    """Mock-mode replay of the analyst decisions the dataset scripted."""
    out = []
    for a in acceptances:
        row = conn.execute(
            "SELECT f.field_id FROM extracted_field f JOIN document d USING (document_id) "
            "WHERE d.case_id = ? AND d.file_name = ? AND f.name = ?",
            (case_id, a["file_name"], a["name"])).fetchone()
        if row is None:
            raise KeyError(f"scripted acceptance names {a['name']} on {a['file_name']}, "
                           f"which was not extracted on {case_id}")
        out.append(accept_field_as_read(conn, row["field_id"], a["analyst_id"], a["reason"], kb))
    return out


def replay_scripted_corrections(conn, case_id: str, corrections: list[dict],
                                kb: KnowledgeBase | None = None) -> list[dict]:
    """Mock-mode equivalent of an analyst working through the correction queue.

    Matched on (file name, field name) because ids are minted per run. A scripted
    correction naming a field this run did not flag is an error worth surfacing,
    not something to skip quietly.
    """
    out = []
    for c in corrections:
        row = conn.execute(
            "SELECT f.field_id FROM extracted_field f JOIN document d USING (document_id) "
            "WHERE d.case_id = ? AND d.file_name = ? AND f.name = ?",
            (case_id, c["file_name"], c["name"])).fetchone()
        if row is None:
            raise KeyError(f"scripted correction names {c['name']} on {c['file_name']}, "
                           f"which was not extracted on {case_id}")
        out.append(correct_field(conn, row["field_id"], c["analyst_id"], c["value"],
                                 c["reason"], kb))
    return out


def correct_field(conn, field_id: str, analyst_id: str, value: str, reason: str,
                  kb: KnowledgeBase | None = None) -> dict:
    """An analyst supplies the value OCR could not read confidently.

    The corrected value is what every later step reads: nothing re-extracts the
    document afterwards, so a correction made here is the value the registry
    check, the risk assessment and the evidence pack all see.
    """
    if not (value or "").strip():
        raise ValueError("a corrected value is required")
    if not (reason or "").strip():
        raise ValueError("a reason is required to correct an extracted field")
    if not (analyst_id or "").strip():
        raise ValueError("the correcting analyst must be identified")

    kb = kb or KnowledgeBase()
    row = conn.execute(
        "SELECT f.*, d.case_id, d.file_name FROM extracted_field f JOIN document d "
        "USING (document_id) WHERE f.field_id = ?", (field_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such extracted field {field_id}")

    conn.execute(
        "UPDATE extracted_field SET value = ?, corrected_by_analyst = 1,"
        " needs_analyst_correction = 0 WHERE field_id = ?", (value, field_id))
    db.audit(conn, row["case_id"], "analyst", analyst_id, "extracted_field_corrected",
             f"{field_id} ({row['name']} on {row['file_name']}) corrected by {analyst_id}; "
             f"read as {row['value']!r} at confidence {row['confidence']}, "
             f"set to {value!r}; reason: {reason}", kb.version)
    return {"field_id": field_id, "value": value, "corrected_by_analyst": True}


def fields_awaiting_entry(conn, document_id: str) -> list:
    """The fields of one document still waiting for an analyst to type them in."""
    return conn.execute(
        "SELECT * FROM extracted_field WHERE document_id = ?"
        " AND entry_method = 'awaiting_analyst_entry' ORDER BY field_id",
        (document_id,)).fetchall()


def enter_fields(conn, document_id: str, analyst_id: str, values: dict,
                 kb: KnowledgeBase | None = None, today: date | None = None) -> dict:
    """An analyst reads an uploaded file and types in what it says.

    Mock mode only: nothing else can read a portal upload there. Each value is
    recorded as entered_by_analyst - never as extracted, and never as a
    correction, because nothing was read for it to correct - and it is what
    every later step reads, exactly as an extracted value would be.

    Every required field must be given. An optional one left blank stays blank.
    The dates typed in go through the same expiry and age rules the quality
    screen applies; one that fails sends the document back to the customer.
    """
    kb = kb or KnowledgeBase()
    today = today or clock.current().today()
    if not (analyst_id or "").strip():
        raise ValueError("the analyst typing in the fields must be identified")
    doc = conn.execute("SELECT * FROM document WHERE document_id = ?",
                       (document_id,)).fetchone()
    if doc is None:
        raise KeyError(f"no such document {document_id}")
    pending = fields_awaiting_entry(conn, document_id)
    if not pending:
        raise ValueError(f"{document_id} has no fields waiting to be typed in")

    required = set(kb.required_fields_for(doc["document_type"]))
    given = {k: (v or "").strip() for k, v in (values or {}).items()}
    missing = sorted(f["name"] for f in pending if f["name"] in required and not given.get(f["name"]))
    if missing:
        raise ValueError(f"required field(s) not given: {', '.join(missing)}")
    for name in ("expiry_date", "document_date", "date_of_birth", "incorporation_date"):
        if given.get(name):
            try:
                date.fromisoformat(given[name])
            except ValueError:
                raise ValueError(f"{name} must be a date written as YYYY-MM-DD") from None

    for f in pending:
        value = given.get(f["name"]) or None
        conn.execute(
            "UPDATE extracted_field SET value = ?, needs_analyst_correction = 0,"
            " entry_method = 'entered_by_analyst' WHERE field_id = ?", (value, f["field_id"]))
    entered = [f["name"] for f in pending if given.get(f["name"])]
    db.audit(conn, doc["case_id"], "analyst", analyst_id, "fields_entered_by_analyst",
             f"{document_id} ({doc['document_type']}, {doc['file_name']}): {len(entered)} "
             f"field(s) typed in by {analyst_id} from the file ({', '.join(entered) or 'none'}); "
             f"recorded as entered_by_analyst, not as extracted", kb.version)

    # The dates typed in meet the rules the quality screen could not run.
    max_age = {r["document_type"]: r["max_age_days"] for r in kb.requirement_rules}
    flags = document_quality.run_date_rules(
        doc["document_type"], _date_fields(doc["document_type"], given),
        max_age.get(doc["document_type"]), kb, today)
    sent_back = None
    if flags:
        status, reasons, fired = document_quality._resolve(flags, doc["document_type"], kb)
        conn.execute("UPDATE document SET quality_status = ?, quality_flags = ?,"
                     " resubmission_required = ?, resubmission_reasons = ?"
                     " WHERE document_id = ?",
                     (status, "|".join(sorted(flags)), int(status == "resubmission_required"),
                      "|".join(reasons), document_id))
        _set_item_status(conn, document_id, document_quality.ITEM_STATUS[status])
        if status == "resubmission_required":
            conn.execute("UPDATE checklist_item SET resubmission_attempts ="
                         " resubmission_attempts + 1 WHERE item_id = (SELECT item_id FROM"
                         " checklist_item_document WHERE document_id = ?)", (document_id,))
        sent_back = status
        db.audit(conn, doc["case_id"], "system", ACTOR, "entered_dates_failed_rules",
                 f"{document_id}: the dates typed in fire {sorted(flags)} "
                 f"({','.join(fired)}); document -> {status}", kb.version)
        document_quality.route_case(conn, doc["case_id"], kb)
    route_case(conn, doc["case_id"], kb)
    return {"document_id": document_id, "entered": entered, "date_rules": sorted(flags),
            "document_status": sent_back}
