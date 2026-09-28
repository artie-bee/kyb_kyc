"""
Hybrid mode: new portal uploads read by the live model, in the background.

EXTRACTION_FOR_NEW_UPLOADS (orchestrator/settings.py) is mock_only by default,
and then nothing here does anything. With live_if_available, a file uploaded to
a WAL-DEMO- case that is NOT a recognised demo sample is queued rather than
held for a person:

  1. at upload, QueuedChecker holds the document with its own per-document hold
     ("reading automatically"), so the portal answers at once and the customer
     sees "Under review";
  2. the worker (process_next, one document per call) screens it with the live
     quality checker, through the same document_quality.rescreen_document()
     every re-screen uses, and the case carries on;
  3. when Step 4 reaches it, its fields are queued too, and the worker reads
     them with the live extractor and hands them to the same extraction.run().

The guardrails are the existing ones, unchanged: strict JSON with one retry
(llm_client), the quality-flag vocabulary and the KB field allow-list, the 0.70
confidence floor (extraction.CONFIDENCE_FLOOR), and a failed call never passes.
Temperature is 0.

When the live call fails for any reason - no key, no network, a rate limit that
did not clear, a reply that would not parse, an invented flag or field - the
document drops back to the mock path it would have taken anyway: the visual
check for an analyst, then the typing form. The reason is on the audit trail
and on the form: "Automatic reading unavailable: <reason>. Type in the fields
instead."

The fourteen scripted cases never come here: only a WAL-DEMO- case queues, and
only a portal upload does.
"""

from pathlib import Path

from . import db, demo_samples, llm_client, settings
from .extractor import ClaudeExtractor, ExtractionResult, Extractor
from .quality_checker import (ClaudeVisionQualityChecker, MockUploadChecker, QualityChecker,
                              QualityVerdict)

LIVE = "live_if_available"
ACTOR = "live_reader"
LABEL = "read by live model"
FIELDS_QUEUED = "fields_queued_for_live_read"
UNAVAILABLE = "live_read_unavailable"
_UNAVAILABLE_TEXT = "automatic reading unavailable: "


def client() -> llm_client.LLMClient:
    """The client the live model is called through: the current provider, at
    temperature 0. Replaced in the tests with a fake one; nothing else changes."""
    return llm_client.get_client(temperature=0.0)


def enabled(case_id: str | None) -> bool:
    return settings.EXTRACTION_FOR_NEW_UPLOADS == LIVE and db.is_demo_case(case_id)


def wants_live(case_id: str, content: bytes) -> bool:
    """Is this upload for the live model? Only on a WAL-DEMO- case in hybrid
    mode, and never for a recognised demo sample, which is replayed."""
    return enabled(case_id) and demo_samples.recognise(content=content) is None


def label(version: str | None) -> str:
    return f"{LABEL} ({version})" if version else LABEL


def _reason(error: Exception) -> str:
    text = " ".join(str(error).split()) or type(error).__name__
    return text if len(text) <= 240 else text[:237] + "..."


# ---------------------------------------------------------------------------
# The checkers
# ---------------------------------------------------------------------------

class QueuedChecker(QualityChecker):
    """At upload: no judgement, no call. The deterministic rules still run in
    Step 3; the document is held for the queue, with its own hold."""

    mode = "live_queue"
    version = None
    uses_judgement = False

    def check(self, document: dict) -> QualityVerdict:
        from .steps.document_quality import LIVE_QUEUED, QUEUED_MARKER
        return QualityVerdict(flags=[], confidence=0.0, notes="queued for the live model",
                              hold_reason=LIVE_QUEUED, hold_marker=QUEUED_MARKER).validate()


class FallbackChecker(QualityChecker):
    """The live checker, and if it cannot answer, the mock-mode upload checker.

    The live verdict carries the "read by live model" label. A failure is never
    a pass: the document goes to the visual-check hold for an analyst, as it
    would have with no live model at all, and `failure` says why.
    """

    def __init__(self, make_client=None):
        self.make_client = make_client or client
        self.failure: str | None = None
        self.verdict: QualityVerdict | None = None
        self.mode, self.version, self.uses_judgement = "live_queue", None, False

    def check(self, document: dict) -> QualityVerdict:
        # Asked once, before anything is written (see process_next); Step 3's
        # own call then gets the same answer rather than paying for another.
        if self.verdict is None:
            self.verdict = self._ask(document)
        return self.verdict

    def _ask(self, document: dict) -> QualityVerdict:
        try:
            live = ClaudeVisionQualityChecker(allow_unready=True, client=self.make_client())
            verdict = live.check(document).validate()
        except Exception as e:                      # any reason at all: not a pass
            self.failure = _reason(e)
            fallback = MockUploadChecker()
            self.mode, self.version, self.uses_judgement = fallback.mode, None, False
            return fallback.check(document)
        self.mode, self.version, self.uses_judgement = live.mode, live.version, True
        verdict.source_label = label(live.version)
        return verdict


class _LiveResult(Extractor):
    """Hands a reading the live extractor has already made to extraction.run(),
    so the fields go through the same floor, date rules and routing as any other."""

    mode = "live"

    def __init__(self, result: ExtractionResult, live: ClaudeExtractor):
        self.result, self.version = result, live.version
        self.source_label = label(live.version)

    def extract(self, document: dict, expected_fields: list[dict]) -> ExtractionResult:
        return self.result


# ---------------------------------------------------------------------------
# The queue, read from the records - no table of its own
# ---------------------------------------------------------------------------

def queued_screens(conn, case_id: str | None = None) -> list:
    """Documents held for the live quality check, oldest first."""
    from .steps.document_quality import QUEUED_MARKER
    return conn.execute(
        "SELECT document_id, case_id FROM document WHERE quality_status = 'manual_review_required'"
        " AND ('|' || quality_flags || '|') LIKE ? AND (? IS NULL OR case_id = ?)"
        " ORDER BY document_id", (f"%|{QUEUED_MARKER}|%", case_id, case_id)).fetchall()


def queued_fields(conn, case_id: str | None = None) -> list:
    """Documents whose fields are waiting for the live extractor, oldest first."""
    return conn.execute(
        "SELECT DISTINCT d.document_id, d.case_id FROM document d JOIN extracted_field f"
        " USING (document_id) WHERE f.entry_method = 'awaiting_analyst_entry'"
        " AND (? IS NULL OR d.case_id = ?)"
        " AND EXISTS (SELECT 1 FROM audit_event a WHERE a.case_id = d.case_id"
        "   AND a.action = ? AND a.payload_summary LIKE d.document_id || ' %')"
        " AND NOT EXISTS (SELECT 1 FROM audit_event a WHERE a.case_id = d.case_id"
        "   AND a.action = ? AND a.payload_summary LIKE d.document_id || ' %')"
        " ORDER BY d.document_id", (case_id, case_id, FIELDS_QUEUED, UNAVAILABLE)).fetchall()


def queue_length(conn) -> int:
    return len(queued_screens(conn)) + len(queued_fields(conn))


def unavailable_reason(conn, document_id: str) -> str | None:
    """Why the live model could not read this document, if it tried and could not."""
    row = conn.execute(
        "SELECT payload_summary FROM audit_event WHERE action = ? AND payload_summary LIKE ?"
        " ORDER BY event_id DESC LIMIT 1", (UNAVAILABLE, f"{document_id} %")).fetchone()
    return row[0].split(_UNAVAILABLE_TEXT, 1)[-1] if row else None


def should_queue_fields(conn, case_id: str, document_id: str) -> bool:
    """Step 4 queues an upload's fields for the live model, unless the live model
    has already failed on this document - then the analyst types them in."""
    if not enabled(case_id) or unavailable_reason(conn, document_id):
        return False
    doc = conn.execute("SELECT file_name FROM document WHERE document_id = ?",
                       (document_id,)).fetchone()
    from .steps.document_quality import UPLOADS
    path = Path(UPLOADS) / case_id / doc["file_name"]
    return path.exists() and demo_samples.recognise(path=path) is None


def read_by_live_model(conn, case_id: str) -> dict:
    """{"quality": {document_id: label}, "fields": {document_id: label}} - the
    documents the live model judged, and those whose fields it read, from the
    audit rows where the label was written."""
    out = {"quality": {}, "fields": {}}
    for action, payload in conn.execute(
            "SELECT action, payload_summary FROM audit_event WHERE case_id = ?"
            " AND action IN ('document_quality_checked', 'fields_extracted')"
            " AND payload_summary LIKE ?", (case_id, f"%; {LABEL}%")):
        stage = "quality" if action == "document_quality_checked" else "fields"
        out[stage][payload.split(" ", 1)[0]] = LABEL + payload.split("; " + LABEL, 1)[1]
    return out


# ---------------------------------------------------------------------------
# The worker: one document per call
# ---------------------------------------------------------------------------

def _unavailable(conn, case_id, document_id, doc_type, reason, kb) -> None:
    db.audit(conn, case_id, "system", ACTOR, UNAVAILABLE,
             f"{document_id} ({doc_type}): {_UNAVAILABLE_TEXT}{reason}; left for an analyst "
             f"(the ordinary mock-mode path)", kb.version)


def process_next(conn, kb, make_client=None) -> dict | None:
    """Read the next queued document with the live model. Returns what was done,
    or None when the queue is empty. The caller commits and carries the case on."""
    from .steps import document_quality, extraction

    screens = queued_screens(conn)
    if screens:
        doc_id, case_id = screens[0]["document_id"], screens[0]["case_id"]
        doc = conn.execute("SELECT * FROM document WHERE document_id = ?", (doc_id,)).fetchone()
        subject = conn.execute("SELECT full_name FROM individual WHERE individual_id = ?",
                               (doc["subject_individual_id"],)).fetchone()
        checker = FallbackChecker(make_client)
        # The call is made before anything is written, so the store is never
        # locked while the model thinks.
        checker.check({"document_type": doc["document_type"], "file_name": doc["file_name"],
                       "subject_name": subject["full_name"] if subject else None,
                       "file_path": str(Path(document_quality.UPLOADS) / case_id
                                        / doc["file_name"])})
        if not any(r["document_id"] == doc_id for r in queued_screens(conn, case_id)):
            # An analyst decided on it while the model was reading: theirs stands.
            return {"stage": "quality", "case_id": case_id, "document_id": doc_id,
                    "status": "decided by an analyst meanwhile", "failure": None}
        result = document_quality.rescreen_document(
            conn, doc_id, ACTOR, "queued upload read by the live model", kb, checker=checker)
        if checker.failure:
            doc_type = conn.execute("SELECT document_type FROM document WHERE document_id = ?",
                                    (result.document_id,)).fetchone()[0]
            _unavailable(conn, case_id, result.document_id, doc_type, checker.failure, kb)
        return {"stage": "quality", "case_id": case_id, "document_id": result.document_id,
                "status": result.quality_status, "failure": checker.failure}

    fields = queued_fields(conn)
    if not fields:
        return None
    doc_id, case_id = fields[0]["document_id"], fields[0]["case_id"]
    doc = conn.execute("SELECT * FROM document WHERE document_id = ?", (doc_id,)).fetchone()
    expected = kb.fields_for(doc["document_type"])
    path = Path(document_quality.UPLOADS) / case_id / doc["file_name"]
    try:
        live = ClaudeExtractor(allow_unready=True, client=(make_client or client)())
        result = live.extract({"file_name": doc["file_name"], "document_type": doc["document_type"],
                               "file_path": str(path)}, expected)
    except Exception as e:                          # any reason at all: typed in instead
        reason = _reason(e)
        _unavailable(conn, case_id, doc_id, doc["document_type"], reason, kb)
        extraction.route_case(conn, case_id, kb)
        return {"stage": "fields", "case_id": case_id, "document_id": doc_id, "failure": reason}

    # The empty rows made for typing give way to what the model read, and the
    # values go through the same run() as every other reading.
    conn.execute("DELETE FROM extracted_field WHERE document_id = ?"
                 " AND entry_method = 'awaiting_analyst_entry'", (doc_id,))
    extraction.run(conn, case_id, {"documents": [{"file_name": doc["file_name"],
                                                  "document_type": doc["document_type"]}]},
                   kb, extractor=_LiveResult(result, live), document_ids={doc_id})
    return {"stage": "fields", "case_id": case_id, "document_id": doc_id, "failure": None,
            "fields": len(result.fields)}
