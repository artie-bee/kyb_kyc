"""
Case holds - one mechanism, owned by the orchestrator.

Every step used to decide the case status for itself, which meant a later step
could quietly undo an earlier one's decision. Screening finding nothing is not a
reason to release a case that verification held. So the rule is now explicit:

    any step may PLACE a hold
    only the step that placed it, or a named human, may RELEASE it

and the case status is derived from whatever holds are open, never set directly.
A step that finishes cleanly says so; it does not get to decide that the case is
clear, because it does not know what else is outstanding.

Precedence when several holds are open: compliance, then analyst, then
insufficient evidence, then customer. The worst open hold owns the case.

Hold reasons carry a code so the precedence can be read off them:

    reason = "<code>: <plain text for the person who picks the case up>"

The codes are in HOLD_CODES. insufficient_evidence is its own code rather than
its own owner column, because an unanswered provider still lands on an analyst's
desk - what differs is what the analyst is being asked to do.
"""

from dataclasses import dataclass

from . import db

# Reason codes, worst first within an owner.
HOLD_CODES = (
    "sanctions_escalation",     # compliance must resolve a sanctions match
    "eligibility",              # the entity itself cannot be onboarded
    "manual_review",            # an analyst must look at a document or finding
    "insufficient_evidence",    # a provider did not answer, or a value is unusable
    "resubmission",             # the customer owes a better document
)

# Which owner outranks which. Lower sorts first.
OWNER_RANK = {"compliance": 0, "analyst": 1, "customer": 2}

# (owner, code) -> the case status that hold implies.
_STATUS = {
    ("compliance", "sanctions_escalation"): "analyst_review_required",
    ("compliance", "manual_review"): "analyst_review_required",
    ("analyst", "manual_review"): "analyst_review_required",
    ("analyst", "eligibility"): "analyst_review_required",
    ("analyst", "insufficient_evidence"): "analyst_review_required",
    ("customer", "resubmission"): "resubmission_required",
    ("customer", "insufficient_evidence"): "document_quality_review",
}


class HoldReleaseError(PermissionError):
    """A step tried to release a hold that a different step placed."""


@dataclass
class Hold:
    hold_id: str
    case_id: str
    placed_by_step: str
    code: str
    reason: str
    owner: str


def _code_of(reason: str) -> str:
    code = (reason or "").split(":", 1)[0].strip()
    return code if code in HOLD_CODES else "manual_review"


def place(conn, case_id: str, placed_by_step: str, code: str, reason: str, owner: str,
          kb=None) -> str:
    """Place a hold. The step names itself, so only it can lift this later."""
    if code not in HOLD_CODES:
        raise ValueError(f"unknown hold code '{code}'; choose from {HOLD_CODES}")
    if owner not in OWNER_RANK:
        raise ValueError(f"hold owner must be one of {sorted(OWNER_RANK)}, not {owner!r}")
    if not (reason or "").strip():
        raise ValueError("a hold needs a reason; someone has to pick this case up")

    hold_id = db.next_id(conn, "case_hold")
    conn.execute(
        "INSERT INTO case_hold (hold_id, case_id, placed_by_step, reason, owner, placed_at)"
        " VALUES (?,?,?,?,?,?)",
        (hold_id, case_id, placed_by_step, f"{code}: {reason}", owner, db.now()))
    db.audit(conn, case_id, "system", placed_by_step, "case_hold_placed",
             f"{hold_id} placed by {placed_by_step}; owner {owner}; {code}: {reason}",
             getattr(kb, "version", None))
    return hold_id


def release(conn, hold_id: str, released_by: str, release_reason: str,
            by_step: str | None = None, kb=None) -> None:
    """Release a hold.

    `by_step` is set when a step is lifting its own hold, and it must match the
    step that placed it. Leave it None for a human release, in which case
    `released_by` must be a person rather than a step.
    """
    hold = conn.execute("SELECT * FROM case_hold WHERE hold_id = ?", (hold_id,)).fetchone()
    if hold is None:
        raise KeyError(f"no such hold {hold_id}")
    if hold["released_at"]:
        raise ValueError(f"{hold_id} was already released by {hold['released_by']}")
    if not (release_reason or "").strip():
        raise ValueError("releasing a hold needs a reason")

    if by_step is not None:
        if by_step != hold["placed_by_step"]:
            raise HoldReleaseError(
                f"{by_step} cannot release {hold_id}: it was placed by "
                f"{hold['placed_by_step']}. A step may only lift its own hold; anything "
                f"else needs a human decision")
    elif released_by.startswith("step."):
        raise HoldReleaseError(
            f"{released_by} looks like a step but did not identify itself as one; "
            f"a human release must name a person")

    conn.execute("UPDATE case_hold SET released_by = ?, release_reason = ?, released_at = ?"
                 " WHERE hold_id = ?", (released_by, release_reason, db.now(), hold_id))
    db.audit(conn, hold["case_id"], "system" if by_step else "analyst", released_by,
             "case_hold_released",
             f"{hold_id} (placed by {hold['placed_by_step']}) released by {released_by}; "
             f"{release_reason}", getattr(kb, "version", None))


def release_own(conn, case_id: str, step: str, release_reason: str, kb=None) -> int:
    """Lift every open hold this step placed on this case. Returns how many."""
    rows = conn.execute(
        "SELECT hold_id FROM case_hold WHERE case_id = ? AND placed_by_step = ?"
        " AND released_at IS NULL", (case_id, step)).fetchall()
    for row in rows:
        release(conn, row["hold_id"], step, release_reason, by_step=step, kb=kb)
    return len(rows)


def open_holds(conn, case_id: str) -> list[Hold]:
    rows = conn.execute(
        "SELECT * FROM case_hold WHERE case_id = ? AND released_at IS NULL"
        " ORDER BY hold_id", (case_id,)).fetchall()
    return [Hold(r["hold_id"], r["case_id"], r["placed_by_step"], _code_of(r["reason"]),
                 r["reason"], r["owner"]) for r in rows]


def worst(conn, case_id: str) -> Hold | None:
    """The open hold that owns the case, by precedence."""
    holds = open_holds(conn, case_id)
    if not holds:
        return None
    return min(holds, key=lambda h: (OWNER_RANK[h.owner], HOLD_CODES.index(h.code)))


def apply_status(conn, case_id: str, kb=None, clear_status: str = "verification_in_progress",
                 clear_owner: str = "system") -> tuple[str, str]:
    """Set the case status from its open holds.

    With nothing open the case takes the status the caller says it has reached.
    With anything open, the worst hold decides, and the step's own view is
    overridden - which is the whole point.
    """
    hold = worst(conn, case_id)
    if hold is None:
        status, owner = clear_status, clear_owner
    else:
        status = _STATUS.get((hold.owner, hold.code), "analyst_review_required")
        owner = hold.owner
    db.update_case(conn, case_id, status=status, next_action_owner=owner)
    return status, owner
