"""
End-to-end demo: all 14 cases through Steps 1-8.

Steps 1-7 are automatic. Step 8 is not: a decision is a person's, so this
replays the human actions the dataset scripted - document releases, low-
confidence fields accepted as read, the analyst and compliance decisions, and
the customer who never replied - in the order they happened.

The clock is fake, so case 14's thirty days of silence pass in a loop rather
than a month.

    python tools/run_demo.py
    python tools/run_demo.py --db onboarding.db --keep
"""

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from orchestrator import db, holds                                  # noqa: E402
from orchestrator.kb import KnowledgeBase                           # noqa: E402
from orchestrator.orchestrator import process_application           # noqa: E402
from orchestrator.steps import communication, decision              # noqa: E402
from tools.dataset_to_applications import DEFAULT_DATASET, DEFAULT_OUT   # noqa: E402

DECISION_RE = re.compile(r"^(?P<id>DEC-\d+) -> (?P<decision>\w+)")


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def scripted_decisions(dataset: Path) -> dict:
    """The human decisions the dataset recorded, by case."""
    out = {}
    for r in read_csv(dataset / "human_decision.csv"):
        out[r["case_id"]] = r
    return out


def scripted_silence(dataset: Path) -> set:
    """Cases where the customer never replied.

    Read from the audit trail rather than a decision row: a case closing for
    want of an answer is something that happened to it, not a judgement anyone
    made about it.
    """
    return {r["case_id"] for r in read_csv(dataset / "audit_event.csv")
            if r["action"] == "case_closed_no_response"}


# Human actions the dataset scripts, which -stop-before-human-actions withholds.
SCRIPTED_HUMAN_ACTIONS = ("analyst_releases", "field_corrections", "field_acceptances")


def run(conn, dataset: Path = DEFAULT_DATASET, app_dir: Path = DEFAULT_OUT,
        clock: communication.Clock | None = None, verbose: bool = True,
        stop_before_human_actions: bool = False) -> dict:
    """Run every case. With stop_before_human_actions, each one runs as far as
    the pipeline can take it on its own and then stops, leaving the releases,
    corrections and decisions for a person to make - which is what the demo app
    is for."""
    kb = KnowledgeBase()
    # Holds are stamped with the real clock, so the demo clock starts there and
    # then runs forward; otherwise no time appears to pass at all.
    clock = clock or communication.FakeClock(date.today())
    decisions = scripted_decisions(dataset)
    silent = scripted_silence(dataset)
    results = {}

    for path in sorted(app_dir.glob("*.json")):
        application = json.loads(path.read_text(encoding="utf-8"))
        if stop_before_human_actions:
            application = dict(application,
                               **{k: [] for k in SCRIPTED_HUMAN_ACTIONS})
        trace = process_application(conn, application, kb)
        case_id = trace["intake"]["case_id"]
        results[case_id] = {"trace": trace, "decision": None, "closed": False}

        # ---- the customer who never replied ------------------------------
        # Case 14 is held for the customer and no re-upload ever arrives. Wind
        # the clock forward and let the chase run to its end.
        # ---- the message the situation calls for -------------------------
        # A case with no decision still owes the customer a word: what they are
        # waiting for, or that nothing is needed from them.
        customer_hold = [h for h in holds.open_holds(conn, case_id) if h.owner == "customer"]
        status_now = conn.execute("SELECT status, white_label_branch_flag FROM onboarding_case"
                                  " WHERE case_id = ?", (case_id,)).fetchone()
        situation = None
        if stop_before_human_actions:
            situation = None        # the presenter sends the messages
        elif status_now["white_label_branch_flag"]:
            situation = "white_label_intake"
        elif customer_hold:
            situation = "resubmission"
        elif case_id not in decisions and status_now["status"] == "ready_for_decision":
            situation = "status_update"
        if situation:
            communication.send_required_message(conn, case_id, situation, kb,
                                                approver="ops.queue")

        if customer_hold and case_id in silent and not stop_before_human_actions:
            for _ in range(7):
                communication.chase(conn, case_id, kb, clock)
                clock.advance(5)
            results[case_id]["closed"] = True

        # ---- the scripted human decision ---------------------------------
        scripted = decisions.get(case_id)
        if scripted and not results[case_id]["closed"] and not stop_before_human_actions:
            assessment = conn.execute(
                "SELECT recommended_action FROM risk_assessment WHERE case_id = ?",
                (case_id,)).fetchone()
            recommended = assessment["recommended_action"] if assessment else None
            override_reason = (scripted["rationale"]
                               if scripted["decision"] != recommended else None)
            # the scripted evidence references are dataset ids; cite this run's
            # equivalents instead, since ids are minted per run
            refs = _refs_for(conn, case_id)
            try:
                out = decision.record_decision(
                    conn, case_id, scripted["reviewer"], scripted["reviewer_role"],
                    scripted["decision"], scripted["reason_code"], scripted["rationale"],
                    refs, override_reason=override_reason,
                    escalation_target=scripted["escalation_target"] or None, kb=kb)
                results[case_id]["decision"] = out
            except decision.DecisionRefused as e:
                results[case_id]["decision"] = str(e)

        if verbose:
            row = conn.execute("SELECT status, next_action_owner FROM onboarding_case "
                               "WHERE case_id = ?", (case_id,)).fetchone()
            d = results[case_id]["decision"]
            print(f"{case_id}  {path.name:<30} status={row['status']:<24} "
                  f"owner={row['next_action_owner']:<11} "
                  f"decision={getattr(d, 'decision', d) or '-'}")
    conn.commit()
    return results


def _refs_for(conn, case_id: str) -> list[str]:
    """A handful of real ids from this run for the decision to rest on."""
    refs = []
    for sql in ("SELECT assessment_id FROM risk_assessment WHERE case_id = ?",
                "SELECT check_id FROM registry_check WHERE case_id = ?",
                "SELECT evidence_pack_id FROM evidence_pack WHERE case_id = ?"):
        refs += [r[0] for r in conn.execute(sql, (case_id,))]
    return refs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=UC4 / "onboarding.db")
    ap.add_argument("--keep", action="store_true", help="keep an existing database")
    ap.add_argument("--stop-before-human-actions", action="store_true",
                    help="run each case up to its first human action and stop, so the "
                         "releases and decisions can be made live in the demo app")
    args = ap.parse_args()

    if args.db.exists() and not args.keep:
        args.db.unlink()
    conn = db.connect(args.db)
    run(conn, stop_before_human_actions=args.stop_before_human_actions)

    print()
    for label, sql in (("communications", "SELECT COUNT(*) FROM communication"),
                       ("sent", "SELECT COUNT(*) FROM outbox"),
                       ("decisions", "SELECT COUNT(*) FROM human_decision"),
                       ("overrides", "SELECT COUNT(*) FROM human_decision WHERE override_flag=1"),
                       ("compliance tasks", "SELECT COUNT(*) FROM compliance_task"),
                       ("open holds", "SELECT COUNT(*) FROM case_hold WHERE released_at IS NULL")):
        print(f"{label:>18}: {conn.execute(sql).fetchone()[0]}")


if __name__ == "__main__":
    main()
