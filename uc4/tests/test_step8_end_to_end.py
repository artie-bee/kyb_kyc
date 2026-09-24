"""
Step 8 and the pipeline end to end.

Runs all 14 cases through Steps 1-8 with the dataset's scripted human actions
replayed, then checks the things that would matter if this were real: that no
customer was told something they should not have been, that nothing was approved
over an open hold, and that the audit trail can account for every step a case
passed through.

Run: python -m pytest tests -q
"""
import csv
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import db, holds                                        # noqa: E402
from orchestrator.kb import KnowledgeBase                                 # noqa: E402
from orchestrator.steps import communication, decision                    # noqa: E402
from tools.dataset_to_applications import DEFAULT_DATASET, DEFAULT_OUT    # noqa: E402
from tools.export_case import export                                      # noqa: E402
from tools.run_demo import run                                            # noqa: E402


def _csv(name):
    with open(DEFAULT_DATASET / name, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def demo():
    """One full run of all 14 cases, shared by the tests below."""
    conn = db.connect(":memory:")
    run(conn, verbose=False)
    return conn


# ---------------------------------------------------------------------------

def test_a_more_lenient_override_always_carries_a_reason(demo):
    """Going stricter than the system is ordinary judgement. Going more lenient
    is the one a reviewer will be asked about, so it must say why."""
    for r in demo.execute("SELECT * FROM human_decision WHERE override_flag = 1"):
        assert r["override_direction"] in ("stricter", "more_lenient")
        if r["override_direction"] == "more_lenient":
            assert (r["override_reason"] or "").strip(), (
                f"{r['case_id']} was decided more leniently than recommended "
                f"({r['decision']} against {r['override_direction']}) with no reason")

    # the rule itself, exercised directly so it is not vacuous
    kb = KnowledgeBase()
    assert kb.override_direction("request_more_information", "conditional_approve") == "stricter"
    assert kb.override_direction("escalate", "enhanced_due_diligence") == "stricter"
    assert kb.override_direction("approve", "enhanced_due_diligence") == "more_lenient"
    assert kb.override_direction("conditional_approve", "escalate") == "more_lenient"
    assert kb.override_direction("approve", "approve") is None


def test_a_system_closure_is_not_a_decision(demo):
    """Case 14 closed because nobody answered. Nobody decided anything."""
    assert not demo.execute(
        "SELECT 1 FROM human_decision WHERE case_id = 'WAL-ONB-0014'").fetchone()
    assert demo.execute("SELECT status FROM onboarding_case WHERE case_id = 'WAL-ONB-0014'"
                        ).fetchone()[0] == "closed_withdrawn"
    actions = {r["action"] for r in demo.execute(
        "SELECT action FROM audit_event WHERE case_id = 'WAL-ONB-0014'")}
    assert "case_closed_no_response" in actions
    assert "human_decision_recorded" not in actions


def test_customer_communication_approval_is_its_own_audit_action(demo):
    """Approving what the customer reads is separate from deciding the case."""
    approvals = [dict(r) for r in demo.execute(
        "SELECT * FROM audit_event WHERE action = 'customer_communication_approved'")]
    assert approvals, "no customer communication approvals recorded"

    case12 = [r for r in approvals if r["case_id"] == "WAL-ONB-0012"]
    assert case12, "case 12's customer message must carry its own approval record"
    assert all(r["actor_type"] in ("analyst", "compliance") for r in case12)
    # and it is a different event from the decision
    decisions = {r["event_id"] for r in demo.execute(
        "SELECT event_id FROM audit_event WHERE action = 'human_decision_recorded'")}
    assert not decisions & {r["event_id"] for r in approvals}


def test_final_status_of_all_14_cases_matches_the_dataset(demo):
    expected = {r["case_id"]: r["status"] for r in _csv("onboarding_case.csv")}
    actual = {r["case_id"]: r["status"] for r in
              demo.execute("SELECT case_id, status FROM onboarding_case")}
    assert len(expected) == 14
    wrong = {c: (expected[c], actual.get(c)) for c in expected if expected[c] != actual.get(c)}
    assert not wrong, f"statuses differ (dataset, ours): {wrong}"


def test_communications_match_the_dataset(demo):
    """Template, audience, approval and sent status for every applicant message."""
    def key(rows):
        out = {}
        for r in rows:
            if r["audience"] != "applicant":
                continue
            out.setdefault(r["case_id"], []).append(
                (r["template_id"], r["audience"], r["approval_status"], r["sent_status"]))
        return {k: sorted(v) for k, v in out.items()}

    expected = key(_csv("communication.csv"))
    actual = key([dict(r) for r in demo.execute(
        "SELECT case_id, template_id, audience, approval_status, sent_status FROM communication")])
    assert expected, "no dataset communications to compare"
    wrong = {c: (expected[c], actual.get(c)) for c in expected if expected[c] != actual.get(c)}
    assert not wrong, f"communications differ (dataset, ours): {wrong}"


def test_case_6_customer_only_ever_receives_the_generic_holding_message(demo):
    """A possible sanctions match. The customer is told nothing about it, ever."""
    sent = [dict(r) for r in demo.execute(
        "SELECT * FROM communication WHERE case_id = 'WAL-ONB-0006' AND audience = 'applicant'")]
    assert sent, "case 6 should have had a message"
    assert {r["template_id"] for r in sent} == {"TPL-0004"}
    assert {r["message_type"] for r in sent} == {"manual_review_underway"}
    for r in sent:
        assert not communication.scan(r["rendered_text"]), \
            f"case 6 message carries restricted wording: {communication.scan(r['rendered_text'])}"
    # and the case is still held for a human
    assert demo.execute("SELECT restricted_finding FROM onboarding_case "
                        "WHERE case_id = 'WAL-ONB-0006'").fetchone()[0] == 1


def test_case_12_sends_nothing_without_a_compliance_decision():
    """A confirmed sanctions match raises a task; it does not draft a message."""
    conn = db.connect(":memory:")
    run(conn, verbose=False)
    kb = KnowledgeBase()

    # with no approver, the system refuses to write to the customer at all
    result = communication.send_required_message(conn, "WAL-ONB-0012", "status_update", kb)
    assert result.communication_id is None and result.sent_status == "not_sent"
    assert result.compliance_task_id, "a compliance task should have been raised instead"
    task = conn.execute("SELECT * FROM compliance_task WHERE task_id = ?",
                        (result.compliance_task_id,)).fetchone()
    assert task["task"] == "decide customer communication" and task["owner"] == "compliance"

    # the message the case did send came from a compliance decision
    sent = conn.execute(
        "SELECT * FROM communication WHERE case_id = 'WAL-ONB-0012' AND audience = 'applicant'"
    ).fetchall()
    assert [r["template_id"] for r in sent] == ["TPL-0004"]
    dec = conn.execute("SELECT reviewer_role FROM human_decision "
                       "WHERE case_id = 'WAL-ONB-0012'").fetchone()
    assert dec["reviewer_role"] == "compliance"


def test_approval_is_refused_while_a_hold_is_open(demo):
    held = demo.execute(
        "SELECT case_id FROM case_hold WHERE released_at IS NULL LIMIT 1").fetchone()["case_id"]
    with pytest.raises(decision.DecisionRefused) as e:
        decision.record_decision(
            demo, held, "analyst.someone", "analyst", "approve", "looks_fine",
            "everything seems in order", [])
    assert "hold" in str(e.value)


def test_a_non_compliance_reviewer_cannot_decide_a_critical_case(demo):
    critical = demo.execute(
        "SELECT case_id FROM risk_assessment WHERE risk_band = 'critical' LIMIT 1"
    ).fetchone()["case_id"]
    with pytest.raises(decision.DecisionRefused) as e:
        decision.record_decision(
            demo, critical, "analyst.keen", "analyst", "escalate", "sanctions_finding",
            "passing this upstairs", [], escalation_target="mlro.queue")
    assert "compliance" in str(e.value)


def test_an_override_is_computed_and_needs_a_reason(demo):
    """Case 7 escalated against a recommendation of enhanced due diligence."""
    row = demo.execute("SELECT * FROM human_decision WHERE case_id = 'WAL-ONB-0007'").fetchone()
    assert row["override_flag"] == 1
    assert row["override_reason"], "an override must say why"
    # and a decision that agrees carries no override reason
    agree = demo.execute("SELECT * FROM human_decision WHERE case_id = 'WAL-ONB-0011'").fetchone()
    assert agree["override_flag"] == 0 and not agree["override_reason"]


def test_case_14_auto_withdraws_on_the_fake_clock():
    conn = db.connect(":memory:")
    clock = communication.FakeClock(date.today())
    run(conn, clock=clock, verbose=False)

    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = 'WAL-ONB-0014'").fetchone()
    assert case["status"] == "closed_withdrawn"
    sent = [r["template_id"] for r in conn.execute(
        "SELECT template_id FROM communication WHERE case_id = 'WAL-ONB-0014'"
        " ORDER BY communication_id")]
    assert sent == ["TPL-0001", "TPL-0005", "TPL-0012"], \
        "request, one reminder, then the closure notice"
    actions = [r["action"] for r in conn.execute(
        "SELECT action FROM audit_event WHERE case_id = 'WAL-ONB-0014'")]
    assert "applicant_chased" in actions and "case_closed_no_response" in actions
    # no provider was ever paid for this case
    for table in ("registry_check", "identity_check", "screening_check"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE case_id = 'WAL-ONB-0014'"
                            ).fetchone()[0] == 0


def test_the_restricted_wording_scanner_blocks_a_bad_message(demo):
    """A template that would tell a customer about a screening result."""
    kb = KnowledgeBase()
    bad = {"template_id": "BAD-0001", "message_type": "status_update",
           "template_text": "Hello {{contact_name}}, your application is delayed because a "
                            "sanctions screening returned a possible match on a director."}
    text = communication.render(bad, {"contact_name": "Someone"})
    hits = communication.scan(text)
    assert {"sanctions", "screening", "match"} <= set(hits)

    # and the scanner is what the step actually runs before sending
    kb.message_templates["BAD-0001"] = bad
    kb.communication_rules.append(
        {"situation": "bad_test", "template_id": "BAD-0001", "audience": "applicant",
         "allowed_when_restricted": "true", "requires_approval": "false",
         "description": "deliberately bad"})
    with pytest.raises(communication.RestrictedWordingError):
        communication.send_required_message(demo, "WAL-ONB-0009", "bad_test", kb,
                                            approver="ops.queue")
    blocked = demo.execute(
        "SELECT * FROM audit_event WHERE action = 'communication_blocked'").fetchone()
    assert blocked is not None and "restricted wording" in blocked["payload_summary"]


def test_a_chooser_cannot_pick_a_template_outside_the_allowed_list(demo):
    kb = KnowledgeBase()

    class Rogue(communication.MockTemplateChooser):
        def choose(self, situation, allowed, facts):
            return communication.TemplateChoice("TPL-0007", dict(facts))   # approval notice

    with pytest.raises(communication.TemplateNotAllowed):
        communication.send_required_message(demo, "WAL-ONB-0009", "manual_review_underway", kb,
                                            chooser=Rogue(), approver="ops.queue")


def test_every_case_audit_trail_satisfies_the_standard(demo):
    """For each step a case actually passed through, the required actions are there."""
    kb = KnowledgeBase()
    # how we tell a case went through a step: a row only that step writes
    evidence = {
        "intake": "SELECT 1 FROM onboarding_case WHERE case_id = ?",
        "requirement_pack": "SELECT 1 FROM requirement_pack WHERE case_id = ?",
        "document_quality": "SELECT 1 FROM document WHERE case_id = ?",
        "extraction": "SELECT 1 FROM extracted_field f JOIN document d USING (document_id)"
                      " WHERE d.case_id = ?",
        "verification": "SELECT 1 FROM registry_check WHERE case_id = ?",
        "screening": "SELECT 1 FROM screening_check WHERE case_id = ?",
        "risk_assessment": "SELECT 1 FROM risk_assessment WHERE case_id = ?",
        "evidence_pack": "SELECT 1 FROM evidence_pack WHERE case_id = ?",
        "communication": "SELECT 1 FROM communication WHERE case_id = ?",
        "decision": "SELECT 1 FROM human_decision WHERE case_id = ?",
    }
    problems = []
    for (case_id,) in demo.execute("SELECT case_id FROM onboarding_case ORDER BY case_id"):
        actions = {r["action"] for r in demo.execute(
            "SELECT action FROM audit_event WHERE case_id = ?", (case_id,))}
        for step, required in kb.audit_log_standard.items():
            if not demo.execute(evidence[step] + " LIMIT 1", (case_id,)).fetchone():
                continue                       # the case never reached this step
            for action in required:
                if action not in actions:
                    problems.append(f"{case_id}: {step} ran but '{action}' is not in the audit")
    assert not problems, "audit trail gaps:\n  " + "\n  ".join(problems)


def test_the_audit_export_carries_the_versions_a_reviewer_needs(demo):
    bundle = export(demo, "WAL-ONB-0004", KnowledgeBase())
    assert bundle["kb_version"].startswith("kb-")
    assert bundle["kb_items"], "the export must name the KB items in force"
    assert bundle["model_and_prompt_versions"], "no version stamps carried"
    assert bundle["audit_trail"], "no audit trail carried"
    for table in ("applicant", "document", "risk_factor", "evidence_pack", "case_hold"):
        assert table in bundle["tables"]
    assert bundle["tables"]["onboarding_case"][0]["case_id"] == "WAL-ONB-0004"


def test_nothing_was_approved_over_a_hold(demo):
    for r in demo.execute(
            "SELECT case_id, decision FROM human_decision WHERE decision IN "
            "('approve', 'conditional_approve')"):
        assert not holds.open_holds(demo, r["case_id"]), \
            f"{r['case_id']} was {r['decision']}d with holds still open"


def test_no_message_ever_sent_to_an_applicant_carries_restricted_wording(demo):
    for r in demo.execute(
            "SELECT c.case_id, c.template_id, o.body FROM outbox o "
            "JOIN communication c USING (communication_id) WHERE o.audience = 'applicant'"):
        hits = communication.scan(r["body"])
        assert not hits, f"{r['case_id']} sent {r['template_id']} carrying {hits}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
