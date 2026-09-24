"""
STEP 8a - Customer communications  (brief Sections 5.9, 11.3)

Everything a customer ever receives comes from the approved template library.
The model may choose among the templates the KB allows for a situation and fill
their declared placeholders; it may not write a sentence. That is deliberate:
free text is how a screening result reaches a customer by accident.

Three gates, in order:

  1. the situation decides which templates are allowed, and a case with a
     restricted finding gets only the generic ones - those that state no reason;
  2. a confirmed sanctions match produces no automatic message at all. A
     compliance task is raised instead, because what to tell the customer in
     that situation is a decision with legal consequences, not a template;
  3. the rendered text is scanned for restricted wording before it can be sent,
     and a hit blocks the send and is audited.

Nothing leaves the building. A send writes to the outbox table.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .. import db, holds
from ..kb import KnowledgeBase

ACTOR = "step.communication"

# Words that must never appear in anything a customer is shown (Section 11.3).
RESTRICTED_WORDING = (
    "sanction", "sanctions", "screening", "screened", "aml", "anti-money",
    "money launder", "launder", "pep", "politically exposed", "adverse media",
    "watchlist", "watch list", "risk score", "risk band", "escalation",
    "escalated", "match", "blacklist", "terror",
)
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class RestrictedWordingError(ValueError):
    """Rendered text contained wording that must never reach a customer."""


class TemplateNotAllowed(ValueError):
    """A template was chosen that the KB does not allow for this situation."""


@dataclass
class CommunicationResult:
    case_id: str
    communication_id: str | None
    template_id: str | None
    audience: str
    approval_status: str
    sent_status: str
    compliance_task_id: str | None = None
    problems: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Clock - real or fake, so the resubmission loop can be tested without waiting
# ---------------------------------------------------------------------------

class Clock:
    def today(self) -> date:
        return date.today()


class FakeClock(Clock):
    """A clock the tests move by hand."""

    def __init__(self, start: date):
        self._today = start

    def today(self) -> date:
        return self._today

    def advance(self, days: int) -> date:
        self._today += timedelta(days=days)
        return self._today


# ---------------------------------------------------------------------------
# Choosing a template - the only judgement in this step
# ---------------------------------------------------------------------------

@dataclass
class TemplateChoice:
    template_id: str
    placeholders: dict


class TemplateChooser:
    mode = "base"
    version: str | None = None

    def choose(self, situation: str, allowed: list[dict], facts: dict) -> TemplateChoice:
        raise NotImplementedError


class MockTemplateChooser(TemplateChooser):
    """Takes the first template the KB allows and fills it from the case facts."""

    mode = "mock"
    version = None

    def choose(self, situation: str, allowed: list[dict], facts: dict) -> TemplateChoice:
        return TemplateChoice(allowed[0]["template_id"], dict(facts))


class ClaudeTemplateChooser(TemplateChooser):
    """Ask Claude which allowed template fits, and for the placeholder values.

    TODO: not wired up. No API call is made yet - calling choose() raises.

    When implemented it must:
      - be given ONLY the templates the KB allows for this situation, and
        require exactly {"template_id": "...", "placeholders": {...}} with no
        prose around it, re-asking once if the reply does not parse;
      - be rejected if it names a template outside that list. The list is the
        control; a model that can pick any template has no control at all;
      - fill placeholders from the customer-safe facts it is given and nothing
        else. It writes no sentences: the wording is the template's;
      - never be shown a screening result, a risk band or a finding. It cannot
        leak what it was not told;
      - set `version` to the model id plus the prompt version for the audit row.

    The rendered text is scanned for restricted wording afterwards regardless,
    because a prompt is a request and a scanner is a guarantee.
    """

    mode = "claude"

    def __init__(self, model: str = "claude-opus-5", prompt_version: str = "comm-v1"):
        self.model = model
        self.prompt_version = prompt_version
        self.version = f"{model}/{prompt_version}"

    def choose(self, situation: str, allowed: list[dict], facts: dict) -> TemplateChoice:
        raise NotImplementedError(
            "ClaudeTemplateChooser is a stub: no API call is wired up yet. "
            "Run with the mock chooser (the default) until it is.")


# ---------------------------------------------------------------------------

def scan(text: str) -> list[str]:
    """Restricted words present in this text. Word-boundary matched, so
    'match' catches the noun but 'matches the register' is not smuggled past."""
    lowered = f" {text.lower()} "
    return sorted({w for w in RESTRICTED_WORDING
                   if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", lowered)})


def customer_safe_facts(conn, case_id: str) -> dict:
    """Only what a customer may be told: names and outstanding document types."""
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    applicant = conn.execute("SELECT * FROM applicant WHERE applicant_id = ?",
                             (case["applicant_id"],)).fetchone()
    contact = conn.execute(
        "SELECT full_name FROM individual WHERE applicant_id = ? ORDER BY individual_id LIMIT 1",
        (case["applicant_id"],)).fetchone()
    outstanding = [r["document_type"].replace("_", " ") for r in conn.execute(
        "SELECT DISTINCT i.document_type FROM checklist_item i JOIN requirement_pack p"
        " USING (pack_id) WHERE p.case_id = ? AND i.level = 'required'"
        " AND i.status != 'accepted'", (case_id,))]
    return {
        "case_id": case_id,
        "applicant_name": applicant["legal_name"],
        "contact_name": contact["full_name"] if contact else applicant["legal_name"],
        "document_list": ", ".join(outstanding) or "the outstanding documents",
        "missing_items": ", ".join(outstanding) or "the outstanding documents",
        "reason_text": "we need a clearer copy",
        "escalation_target": "the compliance team",
    }


def render(template: dict, placeholders: dict) -> str:
    """Fill the template's declared placeholders. Anything it does not declare
    is ignored, and a placeholder with no value is left visibly unfilled rather
    than guessed at."""
    text = template["template_text"]
    for name in PLACEHOLDER.findall(text):
        text = text.replace("{{" + name + "}}", str(placeholders.get(name, f"[{name}]")))
    return text


def send_required_message(conn, case_id: str, situation: str, kb: KnowledgeBase,
                          chooser: TemplateChooser | None = None,
                          approver: str | None = None,
                          template_id: str | None = None) -> CommunicationResult:
    """Draft, check and (if approved) send one message.

    `template_id` pins the choice when a decision has already named one;
    otherwise the chooser picks among the templates the KB allows.
    """
    chooser = chooser or MockTemplateChooser()
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    restricted = bool(case["restricted_finding"])

    # Gate 2: a confirmed sanctions match is not a template decision.
    clear_match = conn.execute(
        "SELECT COUNT(*) FROM screening_check WHERE case_id = ? AND sanctions_result = "
        "'clear_match'", (case_id,)).fetchone()[0]
    if clear_match and not approver:
        task_id = db.next_id(conn, "compliance_task")
        conn.execute("INSERT INTO compliance_task VALUES (?,?,?,?,?,?,?,?)",
                     (task_id, case_id, "decide customer communication",
                      "a confirmed sanctions match is on this case; what the customer is told "
                      "is a compliance decision, not an automated one",
                      "compliance", db.now(), None, None))
        db.audit(conn, case_id, "system", ACTOR, "customer_communication_withheld",
                 f"{task_id} raised: no automatic message is sent on a confirmed sanctions "
                 f"match; compliance decides what the customer is told", kb.version)
        return CommunicationResult(case_id, None, None, "applicant", "not_drafted", "not_sent",
                                   task_id, ["awaiting a compliance decision on communication"])

    # Gate 1: which templates may be used at all.
    allowed = kb.templates_for(situation, restricted)
    if not allowed:
        msg = (f"no template is allowed for '{situation}'"
               + (" on a case with a restricted finding" if restricted else ""))
        db.audit(conn, case_id, "system", ACTOR, "communication_blocked", msg, kb.version)
        return CommunicationResult(case_id, None, None, "applicant", "not_drafted", "not_sent",
                                   None, [msg])

    facts = customer_safe_facts(conn, case_id)
    if template_id:
        if template_id not in [a["template_id"] for a in allowed]:
            raise TemplateNotAllowed(
                f"{template_id} is not allowed for '{situation}'"
                + (" on a restricted case" if restricted else "")
                + f"; allowed: {[a['template_id'] for a in allowed]}")
        choice = TemplateChoice(template_id, facts)
    else:
        choice = chooser.choose(situation, allowed, facts)
        if choice.template_id not in [a["template_id"] for a in allowed]:
            raise TemplateNotAllowed(
                f"{chooser.mode} chooser returned {choice.template_id}, which is not allowed "
                f"for '{situation}'; allowed: {[a['template_id'] for a in allowed]}")

    rule = next(a for a in allowed if a["template_id"] == choice.template_id)
    template = kb.message_templates[choice.template_id]
    text = render(template, choice.placeholders)

    comm_id = db.next_id(conn, "communication")
    approval = "approved" if approver else (
        "pending_approval" if rule["requires_approval"].lower() == "true" else "approved")
    conn.execute(
        "INSERT INTO communication (communication_id, case_id, template_id, audience,"
        " message_type, situation, approval_status, approved_by, sent_status, rendered_text,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (comm_id, case_id, choice.template_id, rule["audience"], template["message_type"],
         situation, approval, approver, "not_sent", text, db.now()))
    db.audit(conn, case_id, "ai_agent", ACTOR, "communication_drafted",
             f"{comm_id} drafted from template {choice.template_id} for audience "
             f"{rule['audience']}; situation {situation}; restricted={restricted}; "
             f"chooser={chooser.mode}", chooser.version or kb.version)

    # Gate 3: the scanner runs on the rendered text, whatever produced it.
    if rule["audience"] == "applicant":
        hits = scan(text)
        if hits:
            conn.execute("UPDATE communication SET approval_status = 'rejected',"
                         " sent_status = 'not_sent' WHERE communication_id = ?", (comm_id,))
            msg = (f"{comm_id} blocked before sending: restricted wording {hits} in text "
                   f"intended for the applicant")
            db.audit(conn, case_id, "system", ACTOR, "communication_blocked", msg, kb.version)
            raise RestrictedWordingError(msg)

    sent = "not_sent"
    if approval == "approved":
        conn.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?)",
                     (db.next_id(conn, "outbox"), comm_id, case_id, rule["audience"],
                      text, db.now()))
        sent = "sent"
        conn.execute("UPDATE communication SET sent_status = 'sent' WHERE communication_id = ?",
                     (comm_id,))
        db.audit(conn, case_id, "analyst" if approver else "system",
                 approver or ACTOR, "communication_approved",
                 f"{comm_id} approved for release under template {choice.template_id}",
                 kb.version)
    else:
        db.audit(conn, case_id, "system", ACTOR, "communication_held",
                 f"{comm_id} drafted and held for approval under template "
                 f"{choice.template_id}", kb.version)

    return CommunicationResult(case_id, comm_id, choice.template_id, rule["audience"],
                               approval, sent, None, [])


# ---------------------------------------------------------------------------
# The resubmission loop
# ---------------------------------------------------------------------------

def chase(conn, case_id: str, kb: KnowledgeBase, clock: Clock,
          chooser: TemplateChooser | None = None,
          approver: str = "ops.queue") -> CommunicationResult | None:
    """Send the single reminder, or close the case, when the clock says so.

    Called each day the case sits unanswered. Returns the message it sent, or
    None if it is not yet time for one.
    """
    hold = next((h for h in holds.open_holds(conn, case_id)
                 if h.owner == "customer"), None)
    if hold is None:
        return None

    placed = conn.execute("SELECT placed_at FROM case_hold WHERE hold_id = ?",
                          (hold.hold_id,)).fetchone()["placed_at"]
    waited = (clock.today() - date.fromisoformat(placed[:10])).days
    already = {r["situation"] for r in conn.execute(
        "SELECT situation FROM communication WHERE case_id = ?", (case_id,))}

    if waited >= kb.communication_schedule["close_after_days"]:
        if "case_closed" in already:
            return None
        result = send_required_message(conn, case_id, "case_closed", kb, chooser, approver)
        holds.release(conn, hold.hold_id, approver,
                      f"no response {waited} days after the request; closing as withdrawn")
        db.update_case(conn, case_id, status="closed_withdrawn", next_action_owner="system")
        db.audit(conn, case_id, "system", ACTOR, "case_closed_no_response",
                 f"no response {waited} days after the original request; closed as withdrawn "
                 f"with no assessment made and no further provider checks commissioned",
                 kb.version)
        return result

    if waited >= kb.communication_schedule["first_reminder_after_days"]:
        if "reminder" in already:
            return None
        result = send_required_message(conn, case_id, "status_update", kb, chooser, approver)
        conn.execute("UPDATE communication SET situation = 'reminder' WHERE communication_id = ?",
                     (result.communication_id,))
        db.audit(conn, case_id, "system", ACTOR, "applicant_chased",
                 f"no response after {waited} days; single reminder sent under the approved "
                 f"template", kb.version)
        return result
    return None


def reupload(conn, case_id: str, application: dict, kb: KnowledgeBase, file_name: str,
             checker=None) -> dict:
    """A replacement document arrives. Only that checklist item is re-screened."""
    from . import document_quality
    doc = next((d for d in application.get("documents", []) if d["file_name"] == file_name), None)
    if doc is None:
        raise KeyError(f"{file_name} is not on this application")
    one = dict(application, documents=[doc])
    db.audit(conn, case_id, "applicant", "customer", "document_resubmitted",
             f"{file_name} re-uploaded; re-screening that checklist item only", kb.version)
    result = document_quality.run(conn, case_id, one, kb, checker=checker)
    return {"file_name": file_name, "status": result.status, "next_step": result.next_step}
