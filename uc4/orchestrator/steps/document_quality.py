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
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .. import db
from ..kb import KnowledgeBase
from .. import holds
from .. import llm_client
from ..quality_checker import (QualityChecker, MockQualityChecker,
                               UnknownQualityFlag, UnscriptedUploadChecker)

ACTOR = "step.document_quality"
MAX_ATTEMPTS = 3
ACCEPTED = "accepted_for_checks"

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
    today = today or date.today()
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
    today = today or date.today()
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

        db.audit(conn, case_id, "ai_agent" if verdict else "system", ACTOR,
                 "document_quality_checked",
                 f"{doc_id} ({doc['document_type']}, {doc['file_name']}) -> {status}"
                 f"; flags={'|'.join(sorted(flags)) or 'none'}"
                 f"; rules={','.join(fired) or 'none fired'}"
                 f"; checker={checker.mode}",
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
        "SELECT d.quality_status FROM document d WHERE d.case_id = ? AND d.document_id IN ("
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
        holds.place(conn, case_id, ACTOR, "manual_review",
                    f"{n} document(s) flagged for an analyst at the quality screen",
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
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
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


def receive_upload(conn, case_id: str, item_id: str, original_name: str, content: bytes,
                   kb: KnowledgeBase, checker: QualityChecker | None = None,
                   uploads_dir: Path | None = None) -> UploadResult:
    """Take one file from the customer portal against one checklist item.

    The only way a portal upload enters the pipeline. The file is refused at the
    door if it could never pass - wrong format, empty, too large, or bytes that
    do not match the extension - so the customer hears at once rather than after
    a failed attempt is counted against them. Anything else is stored, audited
    with its hash, and screened by Step 3 exactly like any other document, and
    the new document supersedes the one before it on that item.

    Pass the checker from quality_checker.get_upload_checker(). With none given
    the file is held for an analyst: there is no scripted verdict to replay, and
    an empty one would accept a file nobody has looked at.
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
                "That item is not on your checklist.", kb)
    if case["white_label_branch_flag"]:
        _refuse(conn, case_id, f"upload against {item_id} on the white-label branch",
                "This application uses a separate partner onboarding process, so documents "
                "are not uploaded here.", kb)
    if case["status"] in CLOSED_STATUSES:
        _refuse(conn, case_id, f"upload against {item_id} on a case that is {case['status']}",
                "This application is closed, so we cannot take new documents for it.", kb)
    if not document_stage_open(conn, case_id):
        _refuse(conn, case_id, f"upload against {item_id} after the paid checks have run",
                "Your documents are already with our onboarding team. If anything else is "
                "needed, we will ask you in a message.", kb)
    if item["status"] not in OPEN_FOR_UPLOAD:
        _refuse(conn, case_id, f"upload against {item_id}, which is {item['status']}",
                "We already have this document, so there is nothing to upload for it.", kb)

    name = Path((original_name or "").replace("\\", "/")).name.strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    allowed = accepted_extensions(kb)
    if ext not in allowed:
        _refuse(conn, case_id, f"{name!r} refused: extension {ext or '(none)'} not in {allowed}",
                "We accept " + ", ".join(a.upper() for a in allowed if a != "jpeg")
                + " files only.", kb)
    if not content:
        _refuse(conn, case_id, f"{name!r} refused: empty file",
                "The file was empty. Please choose it again.", kb)
    if len(content) > MAX_UPLOAD_BYTES:
        _refuse(conn, case_id, f"{name!r} refused: {len(content)} bytes",
                f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
                "Please upload a smaller copy.", kb)
    if not content.startswith(_SIGNATURES.get(ext, b"\0\0\0\0")):
        _refuse(conn, case_id, f"{name!r} refused: content does not match .{ext}",
                "That file does not look like a " + ext.upper() + ". Please upload the "
                "original file rather than a renamed one.", kb)

    digest = hashlib.sha256(content).hexdigest()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name.rsplit(".", 1)[0]).strip("._")[:60] or "upload"
    stored = f"{stem}__{digest[:8]}.{ext}"
    target = uploads_dir / case_id / stored
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    db.audit(conn, case_id, "applicant", "customer", "document_uploaded",
             f"{name!r} received through the customer portal for {item_id} "
             f"({item['document_type']}); {len(content)} bytes; sha256 {digest}; "
             f"stored as {stored}", kb.version)

    doc = {"document_type": item["document_type"], "file_name": stored,
           "file_path": str(target), "item_id": item_id,
           "subject_individual_id": item["subject_individual_id"],
           "upload_time": db.now(), "issue_country": None}
    result = run(conn, case_id, {"documents": [doc]}, kb,
                 checker=checker or UnscriptedUploadChecker())
    document = conn.execute(
        "SELECT MAX(document_id) AS document_id FROM checklist_item_document WHERE item_id = ?",
        (item_id,)).fetchone()["document_id"]
    status = conn.execute("SELECT quality_status FROM document WHERE document_id = ?",
                          (document,)).fetchone()["quality_status"]
    return UploadResult(case_id, item_id, document, stored, status, result.status,
                        result.next_step)
