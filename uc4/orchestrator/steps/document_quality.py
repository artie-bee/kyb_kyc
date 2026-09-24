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

from dataclasses import dataclass, field
from datetime import date

from .. import db
from ..kb import KnowledgeBase
from ..quality_checker import QualityChecker, MockQualityChecker

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

def _run_deterministic(rule: dict, doc: dict, max_age_days: str | None, today: date):
    check, param = rule["check_name"], rule["parameter"]

    if check == "unsupported_file_type":
        ext = doc["file_name"].rsplit(".", 1)[-1].lower() if "." in doc["file_name"] else ""
        return rule["failure_flag"] if ext not in param.split(";") else None

    if check == "expiry_date_passed":
        expiry = doc.get("expiry_date")
        return rule["failure_flag"] if expiry and date.fromisoformat(expiry) < today else None

    if check == "max_age_exceeded":
        issued, limit = doc.get("document_date"), max_age_days
        if not issued or not limit:
            return None
        return rule["failure_flag"] if (today - date.fromisoformat(issued)).days > int(limit) else None

    if check in ("missing_pages", "missing_pages_structural"):
        pages, expected = doc.get("page_count"), doc.get("expected_page_count")
        if pages is None or expected is None:
            return None
        return rule["failure_flag"] if int(pages) < int(expected) else None

    raise ValueError(f"unknown deterministic check '{check}' in {rule['rule_id']}")


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
        subject = ref_to_id.get(doc.get("subject_ref")) if doc.get("subject_ref") else None

        # 1. Which checklist item does this document answer?
        item = next((i for i in items
                     if i["document_type"] == doc["document_type"]
                     and (i["subject_individual_id"] or None) == subject), None)
        if item is None:
            msg = (f"Uploaded {doc['document_type']} ({doc['file_name']}) matches no checklist "
                   f"item on this case")
            problems.append(msg)

        doc_id = db.next_id(conn, "document")
        rule_max_age = max_age.get((doc["document_type"], item["rule_id"])) if item else None

        # 2. Deterministic rules.
        flags, fired = set(), []
        for rule in kb.quality_rules_for(doc["document_type"]):
            if rule["check_type"] != "deterministic":
                continue
            flag = _run_deterministic(rule, doc, rule_max_age, today)
            if flag:
                flags.add(flag)

        # 3. AI rules. A deterministic failure already means the file is unusable,
        #    so there is nothing to gain from paying for a model call on it.
        verdict = None
        if not flags:
            verdict = checker.check(doc).validate()
            flags.update(verdict.flags)

        # 4. Combine into one outcome for the document.
        status, reasons, fired = _resolve(flags, doc["document_type"], kb)
        counts[status] += 1

        # 5. Record it.
        conn.execute(
            "INSERT INTO document (document_id, case_id, subject_individual_id, document_type,"
            " file_name, upload_time, quality_status, quality_flags, expiry_date, document_date,"
            " issue_country, resubmission_required, resubmission_reasons)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, case_id, subject, doc["document_type"], doc["file_name"],
             doc.get("upload_time"), status, "|".join(sorted(flags)), doc.get("expiry_date"),
             doc.get("document_date"), doc.get("issue_country"),
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

    # ---- where does the case go now? --------------------------------------
    remaining = conn.execute(
        "SELECT i.status, i.level FROM checklist_item i JOIN requirement_pack p USING (pack_id) "
        "WHERE p.case_id = ?", (case_id,)).fetchall()
    required_open = [r for r in remaining if r["level"] == "required" and r["status"] != "accepted"]

    if counts["manual_review_required"]:
        status, owner, next_step = "analyst_review_required", "analyst", None
        summary = f"{counts['manual_review_required']} document(s) need an analyst"
    elif counts["resubmission_required"]:
        status, owner, next_step = "resubmission_required", "customer", None
        summary = f"{counts['resubmission_required']} document(s) must be resubmitted"
    elif not required_open:
        status, owner, next_step = "verification_in_progress", "system", "extraction"
        summary = "all required checklist items accepted"
    else:
        status, owner, next_step = "document_quality_review", "customer", None
        summary = f"{len(required_open)} required item(s) still outstanding"

    db.update_case(conn, case_id, status=status, next_action_owner=owner)
    db.audit(conn, case_id, "system", ACTOR, "document_quality_completed",
             f"{len(documents)} document(s) checked: {counts[ACCEPTED]} accepted, "
             f"{counts['resubmission_required']} resubmission, "
             f"{counts['manual_review_required']} manual review; {summary}; "
             f"case -> {status}", kb.version)

    return QualityResult(case_id, len(documents), counts[ACCEPTED],
                         counts["resubmission_required"], counts["manual_review_required"],
                         status, next_step, problems)
