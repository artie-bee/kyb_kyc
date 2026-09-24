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

from .. import db
from ..extractor import Extractor, MockExtractor
from ..kb import KnowledgeBase
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
        extractor: Extractor | None = None, today: date | None = None) -> ExtractionSummary:
    extractor = extractor or MockExtractor()
    today = today or date.today()

    # The ONLY way documents enter this step.
    documents = document_quality.accepted_documents(conn, case_id)
    payload_by_name = {d["file_name"]: d for d in application.get("documents", [])}
    max_age = {r["document_type"]: r["max_age_days"] for r in kb.requirement_rules}

    problems = []
    n_fields = low_conf = missing_req = conflicts = 0

    for doc in documents:
        doc_type = doc["document_type"]
        expected = kb.fields_for(doc_type)
        payload = payload_by_name.get(doc["file_name"], {})

        result = extractor.extract(payload, expected)
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

        db.audit(conn, case_id, "ai_agent", ACTOR, "fields_extracted",
                 f"{doc['document_id']} ({doc_type}, {doc['file_name']}): "
                 f"{len(result.fields)} field(s) read, {len(flagged)} below the floor; "
                 f"extractor={extractor.mode}",
                 extractor.version or kb.version)

    return route_case(conn, case_id, kb, len(documents), n_fields, low_conf, missing_req,
                      conflicts, problems)


def route_case(conn, case_id: str, kb: KnowledgeBase, documents_read: int = 0,
               n_fields: int = 0, low_conf: int = 0, missing_req: int = 0,
               conflicts: int = 0, problems: list[str] | None = None) -> ExtractionSummary:
    """Route on what is still outstanding. Called again after an analyst corrects
    a field, so a correction can release the case without re-extracting anything."""
    problems = problems or []
    outstanding = conn.execute(
        "SELECT COUNT(*) FROM extracted_field f JOIN document d USING (document_id) "
        "WHERE d.case_id = ? AND f.needs_analyst_correction = 1 AND f.corrected_by_analyst = 0",
        (case_id,)).fetchone()[0]
    held = conn.execute(
        "SELECT COUNT(*) FROM document WHERE case_id = ? AND quality_status = "
        "'manual_review_required'", (case_id,)).fetchone()[0]

    if outstanding or held:
        status, owner, next_step = "analyst_review_required", "analyst", None
        summary = (f"{outstanding} field(s) need an analyst"
                   + (f"; {held} document(s) held after a date conflict" if held else ""))
    else:
        status, owner, next_step = "verification_in_progress", "system", "verification"
        summary = "every required field extracted above the confidence floor"

    db.update_case(conn, case_id, status=status, next_action_owner=owner)
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
