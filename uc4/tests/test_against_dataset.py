"""
End-to-end check against the scripted 10-case dataset.

Replaces the old test_checklist_counts, which asserted item counts for the
sample requirement matrix that kb/requirement_rule.csv no longer carries.

Run: python -m pytest tests -q   (or: python tests/test_against_dataset.py)
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.compare_to_dataset import compare, render, run_orchestrator, score  # noqa: E402
from tools.dataset_to_applications import DEFAULT_DATASET, DEFAULT_OUT        # noqa: E402


def _rows():
    # Rebuild the applications first so the test never runs against stale JSON.
    subprocess.run([sys.executable, str(ROOT / "tools" / "dataset_to_applications.py")],
                   check=True, capture_output=True)
    return compare(run_orchestrator(DEFAULT_OUT), DEFAULT_DATASET)


def test_dataset_comparison_is_a_full_match():
    rows = _rows()
    ok, total = score(rows)
    assert total == 50, f"expected 50 field checks over 10 cases, got {total}"
    assert ok == total, (
        f"{ok}/{total} field checks match; mismatches:\n"
        + render([r for r in rows if r[4] == "NO"]))


def test_every_dataset_case_produced_a_checklist_except_the_blocked_ones():
    conn = run_orchestrator(DEFAULT_OUT)
    packs = dict(conn.execute(
        "SELECT case_id, COUNT(*) FROM requirement_pack GROUP BY case_id"))
    # All ten dataset cases classify cleanly, including the white-label partner,
    # which gets a KYB intake pack and then stops.
    assert len(packs) == 10, f"expected a requirement pack for all 10 cases, got {len(packs)}"


def test_white_label_case_stops_after_the_requirement_pack():
    conn = run_orchestrator(DEFAULT_OUT)
    row = conn.execute(
        "SELECT case_id FROM onboarding_case WHERE white_label_branch_flag = 1").fetchone()
    assert row, "no white-label case found"
    actions = [r["action"] for r in conn.execute(
        "SELECT action FROM audit_event WHERE case_id = ?", (row["case_id"],))]
    assert "routed_to_white_label_branch" in actions
    assert "white_label_kyb_intake_only" in actions
    assert conn.execute(
        "SELECT entity_scope FROM onboarding_case WHERE case_id = ?",
        (row["case_id"],)).fetchone()["entity_scope"] == "undetermined"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
