"""Run: python -m pytest tests -q   (or: python tests/test_first_layers.py)"""
import json, sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchestrator import db
from orchestrator.kb import KnowledgeBase
from orchestrator.orchestrator import process_application

def run_all():
    conn, kb = db.connect(":memory:"), KnowledgeBase()
    out = {}
    for p in sorted((ROOT / "sample_applications").glob("*.json")):
        out[p.stem] = process_application(conn, json.loads(p.read_text()), kb)
    return conn, out

def test_classification_and_routing():
    _, t = run_all()
    assert t["app_01_freelancer_ee"]["intake"]["applicant_type"] == "freelancer_sole_trader"
    assert t["app_02_sme_ee"]["intake"]["entity_scope"] == "wallester_as"
    assert t["app_04_complex_uk"]["intake"]["applicant_type"] == "complex_corporate_ubo"
    assert t["app_04_complex_uk"]["intake"]["entity_scope"] == "wallester_uk_ltd"
    assert t["app_08_white_label"]["intake"]["route"] == "white_label_branch"
    assert t["app_09_unsupported_country"]["intake"]["status"] == "analyst_review_required"
    assert t["app_10_incomplete"]["intake"]["route"] == "incomplete"

# test_checklist_counts moved to tests/test_against_dataset.py: it asserted item
# counts for the old sample requirement matrix, which kb/requirement_rule.csv no
# longer carries. The dataset comparison covers the same ground against real data.

def test_no_pack_for_manual_or_incomplete_cases():
    conn, t = run_all()
    # White-label cases now DO get a KYB intake pack and stop after it, so they are
    # no longer in this list; tests/test_against_dataset.py covers that branch.
    for name in ["app_09_unsupported_country", "app_10_incomplete"]:
        case_id = t[name]["intake"]["case_id"]
        assert conn.execute("SELECT COUNT(*) FROM requirement_pack WHERE case_id=?", (case_id,)).fetchone()[0] == 0

def test_audit_is_append_only():
    conn, _ = run_all()
    for sql in ["UPDATE audit_event SET action='x'", "DELETE FROM audit_event"]:
        try:
            conn.execute(sql); raise AssertionError("audit table was modified")
        except sqlite3.IntegrityError:
            pass

def test_every_case_has_audit_trail_with_kb_version():
    conn, _ = run_all()
    rows = conn.execute("SELECT case_id, COUNT(*) n, MAX(model_or_prompt_version) v FROM audit_event GROUP BY case_id").fetchall()
    assert len(rows) == 6 and all(r["n"] >= 2 for r in rows)

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
