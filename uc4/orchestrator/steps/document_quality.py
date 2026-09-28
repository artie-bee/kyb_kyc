"""
STEP 3 - Document quality check  (brief Section 5.3)

Input : case_id + application["documents"] (what the customer uploaded)
Output: QualityResult - every document assessed, every checklist item updated,
        and the case routed to whoever owns the next move

Order of work per document:
  1. link it to its checklist item, by document_type + subject individual
  2. run the deterministic rules in code (file type, expiry, age, page count)
  3. run the AI rules through a QualityChecker (mock by default; nothing calls
     an API unless the mode is changed)
  4. combine: a manual-review flag beats a resubmission flag, and no flag at
     all means accepted_for_checks
  5. write the outcome onto the document and its checklist item, counting a
     failed attempt
  6. an item that has failed three times stops going back to the customer and
     goes to an analyst instead

Nothing downstream may read a document that failed here. accepted_documents()
is the only supported way for a later step to ask what it may work with, and
the case itself only moves to extraction when every required item is accepted.
"""

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .. import clock, db, reassessment
from ..kb import KnowledgeBase
from .. import holds
from .. import llm_client
from .. import settings
from ..quality_checker import (QualityChecker, MockQualityChecker,
                               UnknownQualityFlag, MockUploadChecker, VISUAL_CHECK_NOT_RUN)
from ..virus_scanner import CLEAN, UNAVAILABLE, VirusScanner, get_virus_scanner
from .requirement_pack import shown_to_customer

ACTOR = "step.document_quality"
MAX_ATTEMPTS = 3
ACCEPTED = "accepted_for_checks"
# quality_status of an upload a newer one has replaced on the same item. Kept,
# never deleted, so the history stays on the record; nothing downstream reads it.
SUPERSEDED = "superseded"
# Written into quality_flags on a document held only because nobody has done
# the visual check yet (mock mode). It is a marker, not a fault: the risk step
# does not score it as a quality problem.
VISUAL_MARKER = "visual_check_not_run"

# document.quality_status -> checklist_item.status
ITEM_STATUS = {
    ACCEPTED: "accepted",
    "resubmission_required": "resubmission_requested",
    "manual_review_required": "manual_review",
}


@dataclass
class QualityResult:
    case_id: str
    documents_checked: int
    accepted: int
    resubmission: int
    manual_review: int
    status: str
    next_step: str | None
    problems: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic rules. Each returns the failure_flag if it fires, else None.
# A rule whose input is unknown (no expiry date on file, no page count) does
# not fire - it is not evidence of a problem.
# ---------------------------------------------------------------------------

# Checks that need nothing but the uploaded file itself.
PRE_CHECKER_CHECKS = {"unsupported_file_type", "missing_pages", "missing_pages_structural"}
# Checks that need a date read off the page, so they run after the checker.
DATE_CHECKS = {"expiry_date_passed", "max_age_exceeded"}


def _run_deterministic(rule: dict, doc: dict, dates: dict, max_age_days: str | None, today: date):
    """Run one deterministic rule. Returns its failure_flag, or None.

    `dates` holds what the checker read off the page (see QualityVerdict). A rule
    whose input is unknown - no expiry on the document, no page count, no date
    found - does not fire: absence of evidence is not evidence of a problem.
    """
    check, param = rule["check_name"], rule["parameter"]

    if check == "unsupported_file_type":
        ext = doc["file_name"].rsplit(".", 1)[-1].lower() if "." in doc["file_name"] else ""
        return rule["failure_flag"] if ext not in param.split(";") else None

    if check == "expiry_date_passed":
        expiry = dates.get("expiry_date")
        return rule["failure_flag"] if expiry and date.fromisoformat(expiry) < today else None

    if check == "max_age_exceeded":
        issued, limit = dates.get("document_date"), max_age_days
        if not issued or not limit:
            return None
        return rule["failure_flag"] if (today - date.fromisoformat(issued)).days > int(limit) else None

    if check in ("missing_pages", "missing_pages_structural"):
        pages, expected = doc.get("page_count"), doc.get("expected_page_count")
        if pages is None or expected is None:
            return None
        return rule["failure_flag"] if int(pages) < int(expected) else None

    raise ValueError(f"unknown deterministic check '{check}' in {rule['rule_id']}")


def run_date_rules(doc_type: str, dates: dict, max_age_days: str | None,
                   kb: KnowledgeBase, today: date | None = None) -> set[str]:
    """QR-02 and QR-03 against a given pair of dates.

    Step 3 calls this with the dates the quality checker read off the page;
    Step 4 calls it again with the dates OCR extracted, and compares.
    """
    today = today or clock.current().today()
    flags = set()
    for rule in kb.quality_rules_for(doc_type):
        if rule["check_name"] not in DATE_CHECKS:
            continue
        flag = _run_deterministic(rule, {}, dates, max_age_days, today)
        if flag:
            flags.add(flag)
    return flags


def _resolve(flags: set[str], doc_type: str, kb: KnowledgeBase):
    """Map the flags raised on a document to (status, reasons, rule_ids fired).

    quality_rules_for() returns document-type rules ahead of the catch-all, so
    the first rule matching a flag is the most specific one for that document.
    """
    rules = kb.quality_rules_for(doc_type)
    status, reasons, fired = ACCEPTED, [], []
    for flag in sorted(flags):
        rule = next((r for r in rules if r["failure_flag"] == flag), None)
        if rule is None:                       # no rule maps this flag on this type
            continue
        fired.append(rule["rule_id"])
        if rule["resubmission_reason"] and rule["resubmission_reason"] not in reasons:
            reasons.append(rule["resubmission_reason"])
        # Manual review outranks resubmission, which outranks accepted.
        if rule["outcome"] == "manual_review_required":
            status = "manual_review_required"
        elif status != "manual_review_required":
            status = "resubmission_required"
    return status, reasons, fired


def accepted_documents(conn, case_id: str) -> list:
    """The only documents a later step may read. A document that failed quality
    never appears here, so it cannot reach extraction or a paid provider check."""
    return conn.execute(
        "SELECT * FROM document WHERE case_id = ? AND quality_status = ?",
        (case_id, ACCEPTED)).fetchall()


def run(conn, case_id: str, application: dict, kb: KnowledgeBase,
        checker: QualityChecker | None = None, today: date | None = None) -> QualityResult:
    checker = checker or MockQualityChecker()
    today = today or clock.current().today()
    documents = application.get("documents", [])

    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    items = conn.execute(
        "SELECT i.* FROM checklist_item i JOIN requirement_pack p USING (pack_id) "
        "WHERE p.case_id = ?", (case_id,)).fetchall()
    # The application refs individuals as the customer wrote them; map to our ids.
    ref_to_id = {p["ref"]: r["individual_id"] for p, r in zip(
        application.get("individuals", []),
        conn.execute("SELECT individual_id FROM individual WHERE applicant_id = ? "
                     "ORDER BY individual_id", (case["applicant_id"],)).fetchall())}
    max_age = {(r["document_type"], r["rule_id"]): r["max_age_days"] for r in kb.requirement_rules}

    counts = {ACCEPTED: 0, "resubmission_required": 0, "manual_review_required": 0}
    problems = []

    for doc in documents:
        # A portal upload already knows its person and its checklist item; an
        # application names people by the ref the customer wrote.
        subject = doc.get("subject_individual_id") or (
            ref_to_id.get(doc.get("subject_ref")) if doc.get("subject_ref") else None)

        # 1. Which checklist item does this document answer?
        item = next((i for i in items
                     if i["item_id"] == doc.get("item_id")
                     or (not doc.get("item_id")
                         and i["document_type"] == doc["document_type"]
                         and (i["subject_individual_id"] or None) == subject)), None)
        if item is None:
            msg = (f"Uploaded {doc['document_type']} ({doc['file_name']}) matches no checklist "
                   f"item on this case")
            problems.append(msg)

        doc_id = db.next_id(conn, "document")
        rule_max_age = max_age.get((doc["document_type"], item["rule_id"])) if item else None

        # 2. Deterministic rules that need only the file: format and page count.
        flags, fired = set(), []
        for rule in kb.quality_rules_for(doc["document_type"]):
            if rule["check_type"] != "deterministic" or rule["check_name"] not in PRE_CHECKER_CHECKS:
                continue
            flag = _run_deterministic(rule, doc, {}, rule_max_age, today)
            if flag:
                flags.add(flag)

        # 3. The checker. It returns the judgement flags AND the dates it read off
        #    the page - nothing has been extracted yet, so this is the only place
        #    the date rules can get them. Skipped when the file is already known to
        #    be unusable: no point paying for a model call on it.
        verdict = None
        dates = {}
        ai_failed = None
        if not flags:
            try:
                verdict = checker.check(doc).validate()
            except (llm_client.CallFailed, UnknownQualityFlag, ValueError) as e:
                # A checker that could not answer has told us nothing about the
                # document. That is not a pass: the document goes to an analyst.
                ai_failed = str(e)
                flags = set()
                problems.append(f"{doc['file_name']}: quality check failed ({ai_failed})")
                db.audit(conn, case_id, "system", ACTOR, "quality_check_failed",
                         f"{doc['file_name']}: the {checker.mode} checker returned nothing "
                         f"usable ({ai_failed}); sent for manual review rather than passed",
                         checker.version or kb.version)
            else:
                flags.update(verdict.flags)
                dates = {"expiry_date": verdict.expiry_date,
                         "document_date": verdict.document_date}

            # 3b. Date rules, against what the checker read. Step 4 re-reads both
            #     dates and re-runs these same rules to confirm.
            flags.update(run_date_rules(doc["document_type"], dates, rule_max_age, kb, today))

        # 4. Combine into one outcome for the document.
        status, reasons, fired = _resolve(flags, doc["document_type"], kb)
        if ai_failed:
            status, note = "manual_review_required", f"quality check failed: {ai_failed}"
        elif verdict is not None and verdict.hold_reason and status == ACCEPTED:
            # The deterministic rules passed it; the visual half was not done by
            # anyone. A person does it before the document counts as accepted.
            status = "manual_review_required"
            flags.add(VISUAL_MARKER)
            problems.append(f"{doc['file_name']}: {verdict.hold_reason}")
        counts[status] += 1

        # 5. Record it.
        conn.execute(
            "INSERT INTO document (document_id, case_id, subject_individual_id, document_type,"
            " file_name, upload_time, quality_status, quality_status_at_screen, quality_flags,"
            " expiry_date, document_date, issue_country, resubmission_required,"
            " resubmission_reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, case_id, subject, doc["document_type"], doc["file_name"],
             doc.get("upload_time"), status, status, "|".join(sorted(flags)),
             dates.get("expiry_date"), dates.get("document_date"), doc.get("issue_country"),
             int(status == "resubmission_required"), "|".join(reasons)))

        if item is not None:
            conn.execute("INSERT OR IGNORE INTO checklist_item_document VALUES (?,?)",
                         (item["item_id"], doc_id))
            item_status = ITEM_STATUS[status]
            attempts = item["resubmission_attempts"]
            if status != ACCEPTED:
                attempts += 1
                # 6. Three failed attempts is the end of the back-and-forth.
                if attempts >= MAX_ATTEMPTS:
                    item_status = "manual_review"
                    status_note = (f"{item['item_id']} reached {attempts} failed attempts; "
                                   f"sent to an analyst instead of asking the customer again")
                    problems.append(status_note)
                    db.audit(conn, case_id, "system", ACTOR, "resubmission_limit_reached",
                             status_note, kb.version)
                    counts[status] -= 1
                    counts["manual_review_required"] += 1
            conn.execute("UPDATE checklist_item SET status = ?, resubmission_attempts = ? "
                         "WHERE item_id = ?", (item_status, attempts, item["item_id"]))

        # A checker that made no judgement (deterministic rules only) is not an
        # AI verdict and is not recorded as one.
        judged = verdict is not None and getattr(checker, "uses_judgement", True)
        db.audit(conn, case_id, "ai_agent" if judged else "system", ACTOR,
                 "document_quality_checked",
                 f"{doc_id} ({doc['document_type']}, {doc['file_name']}) -> {status}"
                 f"; flags={'|'.join(sorted(flags)) or 'none'}"
                 f"; rules={','.join(fired) or 'none fired'}"
                 f"; checker={checker.mode}"
                 + (f"; {verdict.source_label}" if verdict and verdict.source_label else ""),
                 checker.version or kb.version)

    return route_case(conn, case_id, kb, len(documents), problems)


def route_case(conn, case_id: str, kb: KnowledgeBase,
               documents_checked: int | None = None,
               problems: list[str] | None = None) -> QualityResult:
    """Decide where the case goes, from the current state of its documents.

    Kept separate from run() so an analyst releasing a document can re-run the
    same routing without re-screening anything (see steps/analyst_review.py).
    """
    problems = problems or []
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()

    # Count the CURRENT document for each checklist item, not every upload ever
    # made against it: a document that has since been resubmitted is history, and
    # counting it again would hold the case open forever. Documents matching no
    # checklist item are counted too, so an unsolicited failure is not lost.
    docs = conn.execute(
        "SELECT d.quality_status, d.quality_flags FROM document d WHERE d.case_id = ?"
        " AND d.document_id IN ("
        "  SELECT MAX(cid.document_id) FROM checklist_item_document cid"
        "  JOIN document d2 ON d2.document_id = cid.document_id"
        "  WHERE d2.case_id = ? GROUP BY cid.item_id"
        "  UNION ALL"
        "  SELECT d3.document_id FROM document d3 WHERE d3.case_id = ? AND d3.document_id NOT IN"
        "    (SELECT document_id FROM checklist_item_document))",
        (case_id, case_id, case_id)).fetchall()
    counts = {s: sum(1 for d in docs if d["quality_status"] == s)
              for s in (ACCEPTED, "resubmission_required", "manual_review_required")}
    if documents_checked is None:
        documents_checked = len(docs)

    # The checklist item is what the case actually owes, so it drives the routing.
    open_items = conn.execute(
        "SELECT i.status, i.level FROM checklist_item i JOIN requirement_pack p USING (pack_id) "
        "WHERE p.case_id = ?", (case_id,)).fetchall()
    required_open = [r for r in open_items if r["level"] == "required" and r["status"] != "accepted"]
    held = [r for r in open_items if r["status"] == "manual_review"]
    to_resend = [r for r in open_items if r["status"] == "resubmission_requested"]

    # This step re-runs after an analyst release, so it lifts its own holds first
    # and places whatever the current picture warrants. It never touches another
    # step's hold.
    holds.release_own(conn, case_id, ACTOR, "document quality re-evaluated", kb)

    if held or counts["manual_review_required"]:
        n = len(held) or counts["manual_review_required"]
        summary = f"{n} item(s) need an analyst"
        visual = sum(1 for d in docs if d["quality_status"] == "manual_review_required"
                     and VISUAL_MARKER in (d["quality_flags"] or "").split("|"))
        if visual:
            holds.place(conn, case_id, ACTOR, "manual_review",
                        f"{VISUAL_CHECK_NOT_RUN}: {visual} uploaded document(s) waiting for "
                        f"an analyst to look at them", "analyst", kb)
        if n > visual:
            holds.place(conn, case_id, ACTOR, "manual_review",
                        f"{n - visual} document(s) flagged for an analyst at the quality screen",
                        "analyst", kb)
    elif to_resend or counts["resubmission_required"]:
        n = len(to_resend) or counts["resubmission_required"]
        summary = f"{n} item(s) must be resubmitted"
        holds.place(conn, case_id, ACTOR, "resubmission",
                    f"{n} document(s) must be resubmitted before the case can proceed",
                    "customer", kb)
    elif required_open:
        summary = f"{len(required_open)} required item(s) still outstanding"
        holds.place(conn, case_id, ACTOR, "insufficient_evidence",
                    f"{len(required_open)} required checklist item(s) not yet supplied",
                    "customer", kb)
    else:
        summary = "all required checklist items accepted"

    # Extraction is free and runs whatever is outstanding; the paid step gates
    # itself on the checklist, so a hold here does not stop Step 4.
    next_step = "extraction"

    # The white-label branch is KYB intake only: its documents are screened so the
    # partner knows where it stands, but nothing after Step 3 runs in this phase.
    if case["white_label_branch_flag"] and not holds.open_holds(conn, case_id):
        next_step = None
        db.update_case(conn, case_id, status="submitted", next_action_owner="system")
        db.audit(conn, case_id, "system", ACTOR, "white_label_kyb_intake_only",
                 "KYB documents screened; no further steps run in this phase", kb.version)
        status, owner = "submitted", "system"
    else:
        status, owner = holds.apply_status(conn, case_id, kb, "document_quality_review", "system")

    db.audit(conn, case_id, "system", ACTOR, "document_quality_completed",
             f"{documents_checked} document(s) checked: {counts[ACCEPTED]} accepted, "
             f"{counts['resubmission_required']} resubmission, "
             f"{counts['manual_review_required']} manual review; {summary}; "
             f"case -> {status}", kb.version)

    return QualityResult(case_id, documents_checked, counts[ACCEPTED],
                         counts["resubmission_required"], counts["manual_review_required"],
                         status, next_step, problems)


# ---------------------------------------------------------------------------
# A file from the customer portal
# ---------------------------------------------------------------------------

# Where uploaded files are kept. Git-ignored: the portal is a demo, and nothing a
# visitor uploads belongs in the repository.
UPLOADS = Path(__file__).resolve().parents[2] / "uploads"
MAX_UPLOAD_BYTES = settings.MAX_UPLOAD_MB * 1024 * 1024
# What each accepted extension must actually start with. The extension is the
# customer's claim; the first bytes are the file's.
_SIGNATURES = {"pdf": b"%PDF-", "jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff",
               "png": b"\x89PNG\r\n\x1a\n"}
OPEN_FOR_UPLOAD = ("pending", "resubmission_requested")
CLOSED_STATUSES = ("approved", "rejected", "closed_withdrawn")


class UploadRefused(ValueError):
    """The upload was not taken. The message is written for the customer: it
    says what to do and nothing about how the case is being assessed."""


@dataclass
class UploadResult:
    case_id: str
    item_id: str
    document_id: str
    file_name: str
    quality_status: str
    case_status: str
    next_step: str | None


def accepted_extensions(kb: KnowledgeBase) -> list[str]:
    """The upload formats, from QR-01 - the same list the quality screen applies."""
    rule = next(r for r in kb.document_quality_rules if r["check_name"] == "unsupported_file_type")
    return [x for x in rule["parameter"].split(";") if x]


# The tables the paid checks write. Once any of them holds a row for a case, the
# document stage is over: Steps 5 to 7 have run on the documents as they were,
# and a new file now would need those steps re-run, which they are not built to
# do. The onboarding team asks for anything further by message.
_PAID_CHECK_TABLES = ("registry_check", "identity_check", "screening_check", "risk_assessment")


def document_stage_open(conn, case_id: str) -> bool:
    """True while the case is still collecting documents: nothing paid has run."""
    return not any(conn.execute(f"SELECT 1 FROM {t} WHERE case_id = ? LIMIT 1",
                                (case_id,)).fetchone() for t in _PAID_CHECK_TABLES)


def _refuse(conn, case_id, why_internal, customer_text, kb):
    db.audit(conn, case_id, "applicant", "customer", "portal_upload_refused",
             why_internal, kb.version)
    raise UploadRefused(customer_text)


def _refusal_text(kind: str, kb: KnowledgeBase, ext: str = "") -> str:
    """What the customer is told. Every line here reaches the portal, which scans
    it for restricted wording - so none of it may say "match", for one."""
    allowed = [a.upper() for a in accepted_extensions(kb) if a != "jpeg"]
    return {
        "not_requested": "We have not asked you for this document, so it cannot be uploaded.",
        "closed": "This application is closed, so we cannot take new documents for it.",
        "have_it": "We already have this document, so there is nothing to upload for it.",
        "file_type": "We accept " + ", ".join(allowed[:-1]) + " and " + allowed[-1]
                     + " files only. Please choose a file of one of those types.",
        "empty": "The file was empty. Please choose it again.",
        "too_large": f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
                     "Please upload a smaller copy.",
        "mismatch": f"This file is not really a {ext.upper()}, although its name ends in "
                    f".{ext}. Please upload the original file rather than a renamed one.",
        "scan_unavailable": "We could not check this file just now. Please try again later.",
        "scan_failed": "We cannot accept this file. Please upload a different copy.",
    }[kind]


def receive_upload(conn, case_id: str, item_id: str, original_name: str, content: bytes,
                   kb: KnowledgeBase, checker: QualityChecker | None = None,
                   uploads_dir: Path | None = None,
                   scanner: VirusScanner | None = None) -> UploadResult:
    """Take one file from the customer portal against one checklist item.

    The only way a portal upload enters the pipeline, and it screens the file
    with the same Step 3 run() that screens every scripted document - there is
    no second quality-check path.

      1. refused at the door, before anything is stored or counted against the
         customer: the case is closed, the item is not one we asked for (never
         requested, waived, or a condition an analyst has not confirmed), the
         item is already accepted or being looked at, or the file is the wrong
         type, empty, over the size limit, or its bytes do not match its
         extension;
      2. virus-scanned; a scan that did not answer is a refusal, not a pass;
      3. stored outside any web-served folder under a random name, with its
         SHA-256 in the audit trail and the customer's own file name beside it;
      4. any earlier upload on the item is marked superseded and kept;
      5. screened by Step 3 for this item only. In mock mode the deterministic
         rules run for real and the visual half is held for an analyst
         ("visual check not run in mock mode"); in live mode the real checker
         runs. The three-attempt limit is Step 3's own.

    The caller carries the case on (app/data.upload_document calls resume):
    this step cannot import the orchestrator that imports it.
    """
    uploads_dir = uploads_dir or UPLOADS
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    if case is None:
        raise KeyError(f"no such case {case_id}")
    item = conn.execute(
        "SELECT i.* FROM checklist_item i JOIN requirement_pack p USING (pack_id)"
        " WHERE p.case_id = ? AND i.item_id = ?", (case_id, item_id)).fetchone()
    if item is None:
        _refuse(conn, case_id, f"upload against {item_id}, which is not on this case",
                _refusal_text("not_requested", kb), kb)
    if case["status"] in CLOSED_STATUSES:
        _refuse(conn, case_id, f"upload against {item_id} on a case that is {case['status']}",
                _refusal_text("closed", kb), kb)
    if not shown_to_customer(item):
        _refuse(conn, case_id, f"upload against {item_id}, which the customer is not asked for "
                               f"({item['status']}{', condition unconfirmed' if item['status'] != 'waived' else ''})",
                _refusal_text("not_requested", kb), kb)
    if item["status"] not in OPEN_FOR_UPLOAD:
        _refuse(conn, case_id, f"upload against {item_id}, which is {item['status']}",
                _refusal_text("have_it", kb), kb)

    name = Path((original_name or "").replace("\\", "/")).name.strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    allowed = accepted_extensions(kb)
    if ext not in allowed:
        _refuse(conn, case_id, f"{name!r} refused: extension {ext or '(none)'} not in {allowed}",
                _refusal_text("file_type", kb), kb)
    if not content:
        _refuse(conn, case_id, f"{name!r} refused: empty file", _refusal_text("empty", kb), kb)
    if len(content) > MAX_UPLOAD_BYTES:
        _refuse(conn, case_id, f"{name!r} refused: {len(content)} bytes, over the "
                               f"{MAX_UPLOAD_BYTES} limit", _refusal_text("too_large", kb), kb)
    if not content.startswith(_SIGNATURES.get(ext, b"\0\0\0\0")):
        _refuse(conn, case_id, f"{name!r} refused: content does not match .{ext}",
                _refusal_text("mismatch", kb, ext), kb)

    scanner = scanner or get_virus_scanner()
    scan = scanner.scan(content, name)
    db.audit(conn, case_id, "system", scanner.name, "upload_virus_scanned",
             f"{name!r} for {item_id}: {scan.verdict}; mode={scanner.mode}; {scan.detail}",
             kb.version)
    if scan.verdict != CLEAN:
        _refuse(conn, case_id, f"{name!r} refused: virus scan {scan.verdict}",
                _refusal_text("scan_unavailable" if scan.verdict == UNAVAILABLE
                              else "scan_failed", kb), kb)

    digest = hashlib.sha256(content).hexdigest()
    stored = f"{secrets.token_hex(16)}.{ext}"
    target = uploads_dir / case_id / stored
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    # The customer's own name for the file goes in the audit trail, beside the
    # random one it is stored under: that is the only place it is kept.
    db.audit(conn, case_id, "applicant", "customer", "document_uploaded",
             f"{json.dumps(name)} received through the customer portal for {item_id} "
             f"({item['document_type']}); {len(content)} bytes; sha256 {digest}; "
             f"stored as {stored}", kb.version)

    earlier = [r["document_id"] for r in conn.execute(
        "SELECT d.document_id FROM checklist_item_document cid JOIN document d"
        " USING (document_id) WHERE cid.item_id = ? AND d.quality_status != ?",
        (item_id, SUPERSEDED))]
    for old in earlier:
        conn.execute("UPDATE document SET quality_status = ? WHERE document_id = ?",
                     (SUPERSEDED, old))
    if earlier:
        db.audit(conn, case_id, "system", ACTOR, "document_superseded",
                 f"{', '.join(earlier)} on {item_id} superseded by the customer's new upload; "
                 f"kept on the record", kb.version)

    paid_checks_open = document_stage_open(conn, case_id)
    doc = {"document_type": item["document_type"], "file_name": stored,
           "file_path": str(target), "item_id": item_id,
           "subject_individual_id": item["subject_individual_id"],
           "upload_time": db.now(), "issue_country": None}
    # The one quality-check path: the same run() every scripted document goes through.
    result = run(conn, case_id, {"documents": [doc]}, kb,
                 checker=checker or MockUploadChecker())
    document = conn.execute(
        "SELECT MAX(document_id) AS document_id FROM checklist_item_document WHERE item_id = ?",
        (item_id,)).fetchone()["document_id"]
    status = conn.execute("SELECT quality_status FROM document WHERE document_id = ?",
                          (document,)).fetchone()["quality_status"]
    # After the assessment, a new file is evidence for an analyst to weigh; it
    # does not move the case, or its risk band, by itself.
    if not paid_checks_open:
        reassessment.place(conn, case_id, document, kb)
        holds.apply_status(conn, case_id, kb, case["status"], case["next_action_owner"])
    # The customer has answered for this item: it drops out of any reminder.
    db.audit(conn, case_id, "system", ACTOR, "reminders_stopped",
             f"{item_id} ({item['document_type']}): the customer has sent it; no further "
             f"reminders about this item", kb.version)
    return UploadResult(case_id, item_id, document, stored, status, result.status,
                        result.next_step)


_UPLOAD_EVENT = re.compile(r'^(".*?(?<!\\)") received through the customer portal .* '
                           r"stored as (\S+)$", re.S)


def uploaded_names(conn, case_id: str) -> dict:
    """{stored file name: the customer's own file name} for every portal upload
    on the case, read from the audit trail, where the upload recorded both."""
    out = {}
    for (payload,) in conn.execute(
            "SELECT payload_summary FROM audit_event WHERE case_id = ?"
            " AND action = 'document_uploaded'", (case_id,)):
        m = _UPLOAD_EVENT.match(payload)
        if m:
            out[m.group(2)] = json.loads(m.group(1))
    return out
