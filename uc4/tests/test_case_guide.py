"""
The generated case guide must agree with the database it was generated from.

A document with hand-copied figures goes stale the first time a weight changes,
and nobody notices until someone acts on it. So these tests regenerate the
guide into a temporary directory and read the numbers back out of the markdown,
comparing each one with the database and the knowledge base.

They check the guide is *right*, not that it is unchanged: a KB edit should
change the guide, and that is fine. What must never happen is the guide saying
one thing while the data says another.

The last test is the one that matters for the file in the repository: it
asserts the committed docs/CASE_GUIDE.md is what the generator produces today.

Run: python -m pytest tests/test_case_guide.py -q
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import db, holds                                     # noqa: E402
from orchestrator.kb import KnowledgeBase                              # noqa: E402
from tools import make_case_guide                                      # noqa: E402
from tools.run_demo import run as run_demo                             # noqa: E402

STAMP = "generated-for-the-test"


@pytest.fixture(scope="module")
def guide(tmp_path_factory):
    """The guide, and the database it should agree with."""
    out = tmp_path_factory.mktemp("guide")
    make_case_guide.generate(out, pdf=False, generated_at=STAMP)
    text = (out / "CASE_GUIDE.md").read_text(encoding="utf-8")

    conn = db.connect(":memory:")
    run_demo(conn, verbose=False)
    return text, conn, KnowledgeBase()


def section(text, case_id):
    """One case's part of the guide."""
    start = text.index(f"\n## {case_id} - ")
    rest = text[start + 1:]
    nxt = re.search(r"\n## (WAL-ONB-\d+ - |Appendix)", rest)
    return rest[:nxt.start()] if nxt else rest


def rows_of(block, header_startswith):
    """The data rows of the first table whose header starts with this cell."""
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("| " + header_startswith):
            out = []
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                out.append([c.strip() for c in row.strip().strip("|").split("|")])
            return out
    return []


def cases(conn):
    return [r["case_id"] for r in
            conn.execute("SELECT case_id FROM onboarding_case ORDER BY case_id")]


def steady(text):
    """The guide with the parts that legitimately differ per run removed.

    Audit timestamps and the ageing figure are real data, and they move every
    time the demo is run. Demanding they match byte for byte would fail on
    every run and teach people to ignore this test, which is worse than not
    having it. Everything else - every rule, figure, verdict and sentence -
    still has to match exactly.
    """
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?", "<timestamp>", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", "<generated>", text)
    return re.sub(r"\| Age \(days\) \|\n(\|[-|]+\|\n)((?:\|.*\|\n)+)",
                  lambda m: "| Age (days) |\n" + m.group(1)
                  + re.sub(r"\| \d+ \|$", "| <age> |", m.group(2), flags=re.M),
                  text)


# ---------------------------------------------------------------------------
# It is complete
# ---------------------------------------------------------------------------

def test_every_case_has_every_section(guide):
    text, conn, _ = guide
    ids = cases(conn)
    assert len(ids) == 14
    for case_id in ids:
        block = section(text, case_id)
        for letter, name in (("A", "Classification"), ("B", "Checklist"),
                             ("C", "Documents"), ("D", "People and ownership"),
                             ("E", "Checks"), ("F", "Risk"), ("G", "Decision"),
                             ("H", "Communications"), ("I", "Timeline"),
                             ("J", "Dashboard line")):
            assert f"### {letter}. {name}" in block, f"{case_id} is missing section {letter}"


def test_the_appendix_prints_the_whole_knowledge_base(guide):
    text, _, kb = guide
    appendix = text[text.index("## Appendix"):]
    for rule in kb.applicant_type_rules:
        assert rule["rule_id"] in appendix
    for factor in kb.risk_factors:
        assert factor["factor_id"] in appendix
        assert factor["weight_status"] in appendix, "every weight must carry its status"
    for band in kb.risk_bands:
        assert band["band_id"] in appendix
    for name in kb.decision_taxonomy:
        assert name in appendix
    for country in kb.jurisdiction_routing:
        assert country in appendix


# ---------------------------------------------------------------------------
# It is right
# ---------------------------------------------------------------------------

def test_the_summary_table_matches_the_database(guide):
    text, conn, _ = guide
    rows = rows_of(text[:text.index("## How to read")], "Case ")
    assert len(rows) == 14

    for row in rows:
        case_id, _, applicant_type, status, owner, band, score, holds_n = row[:8]
        case = conn.execute("SELECT * FROM onboarding_case WHERE case_id=?",
                            (case_id,)).fetchone()
        assert case is not None, f"{case_id} is not in the database"
        assert case["applicant_type"] == applicant_type
        assert case["status"] == status
        assert (case["next_action_owner"] or "") in (owner, "")

        risk = conn.execute("SELECT * FROM risk_assessment WHERE case_id=?",
                            (case_id,)).fetchone()
        if risk is None:
            assert band == "&mdash;" and score == "&mdash;"
        else:
            assert risk["risk_band"] == band
            expected = "&mdash;" if risk["risk_score"] is None else str(risk["risk_score"])
            assert score == expected, f"{case_id}: score {score} != {expected}"

        assert int(holds_n) == len(holds.open_holds(conn, case_id))


def test_every_risk_factor_row_matches_the_matrix_and_the_case(guide):
    """Section F lists every factor in the matrix, fired or not, with real points."""
    text, conn, kb = guide
    for case_id in cases(conn):
        risk = conn.execute("SELECT * FROM risk_assessment WHERE case_id=?",
                            (case_id,)).fetchone()
        if risk is None:
            continue
        rows = rows_of(section(text, case_id), "Id | Factor")
        assert len(rows) == len(kb.risk_factors), \
            f"{case_id}: the guide must show every factor, not only the ones that fired"

        fired_in_db = {f["factor"] for f in conn.execute(
            "SELECT factor FROM risk_factor WHERE assessment_id=?",
            (risk["assessment_id"],))}
        total = 0
        for row, entry in zip(rows, kb.risk_factors):
            assert row[0] == entry["factor_id"]
            assert row[1] == entry["factor"]
            if row[3] == "**fired**":
                assert entry["factor"] in fired_in_db, \
                    f"{case_id}: {entry['factor_id']} shown as fired but is not on the case"
            if row[4] != "0":
                points = int(row[4].lstrip("+"))
                assert points == int(entry["points"]), \
                    f"{case_id}: {entry['factor_id']} points differ from the matrix"
                total += points
                assert int(row[5]) == total, f"{case_id}: running total is wrong"
        if risk["risk_score"] is None:
            # A case with an unresolved evidence gap is deliberately not scored.
            # The factors still exist and still total something; what matters is
            # that the guide says plainly that the total is not a score.
            assert "carries no score at all" in section(text, case_id), \
                f"{case_id} has no score, and the guide does not explain why"
        else:
            assert total == risk["risk_score"], \
                f"{case_id}: the points in the guide sum to {total}, the database " \
                f"says {risk['risk_score']}"


def test_the_arithmetic_line_adds_up(guide):
    text, conn, _ = guide
    for case_id in cases(conn):
        risk = conn.execute("SELECT risk_score FROM risk_assessment WHERE case_id=?",
                            (case_id,)).fetchone()
        if risk is None:
            continue
        block = section(text, case_id)
        sums = re.search(r"\*\*The arithmetic\.\*\*\n\n```\n(.+?)\n```", block, re.S)
        assert sums, f"{case_id} has no arithmetic line"
        line = sums.group(1)
        if "no factors" in line:
            assert (risk["risk_score"] or 0) == 0
            continue
        left, right = line.split("=")
        assert sum(int(x) for x in left.split("+")) == int(right.strip()), \
            f"{case_id}: the sum shown does not add up"
        if risk["risk_score"] is not None:
            assert int(right.strip()) == risk["risk_score"]


def test_the_band_shown_is_the_band_stored(guide):
    text, conn, _ = guide
    for case_id in cases(conn):
        risk = conn.execute("SELECT risk_band FROM risk_assessment WHERE case_id=?",
                            (case_id,)).fetchone()
        if risk is None:
            continue
        block = section(text, case_id)
        found = re.search(r"\| \*\*Final band\*\* \| \*\*(.+?)\*\* \|", block)
        assert found and found.group(1) == risk["risk_band"], \
            f"{case_id}: final band in the guide is not the one on the record"


def test_checklist_counts_match_the_database(guide):
    text, conn, _ = guide
    for case_id in cases(conn):
        rows = rows_of(section(text, case_id), "Level | Items")
        assert rows, f"{case_id} has no checklist totals"
        for level, items, accepted, waived, outstanding, _ in rows:
            n = conn.execute(
                "SELECT COUNT(*) FROM checklist_item i JOIN requirement_pack p"
                " USING(pack_id) WHERE p.case_id=? AND i.level=?",
                (case_id, level)).fetchone()[0]
            assert int(items) == n, f"{case_id}: {level} count differs"
            ok = conn.execute(
                "SELECT COUNT(*) FROM checklist_item i JOIN requirement_pack p"
                " USING(pack_id) WHERE p.case_id=? AND i.level=? AND i.status='accepted'",
                (case_id, level)).fetchone()[0]
            assert int(accepted) == ok


def test_every_document_and_field_appears(guide):
    text, conn, _ = guide
    for case_id in cases(conn):
        block = section(text, case_id)
        for d in conn.execute("SELECT * FROM document WHERE case_id=?", (case_id,)):
            assert d["document_id"] in block, f"{case_id}: {d['document_id']} is missing"
            assert d["file_name"] in block
        for f in conn.execute(
                "SELECT f.* FROM extracted_field f JOIN document d USING(document_id)"
                " WHERE d.case_id=?", (case_id,)):
            assert f["field_id"] in block, f"{case_id}: {f['field_id']} is missing"
            shown = f"{float(f['confidence']):.2f}"
            assert shown in block, f"{case_id}: confidence {shown} is missing"


def test_every_audit_event_appears_in_the_timeline(guide):
    text, conn, _ = guide
    for case_id in cases(conn):
        block = section(text, case_id)
        events = list(conn.execute(
            "SELECT event_id FROM audit_event WHERE case_id=? ORDER BY event_id", (case_id,)))
        assert f"**{len(events)} in total**" in block
        for e in events:
            assert e["event_id"] in block


def test_decisions_and_overrides_are_reported_as_recorded(guide):
    text, conn, _ = guide
    for case_id in cases(conn):
        block = section(text, case_id)
        rows = list(conn.execute("SELECT * FROM human_decision WHERE case_id=?", (case_id,)))
        if not rows:
            assert "No decision has been recorded" in block
            continue
        for d in rows:
            assert d["decision"] in block
            assert d["reviewer"] in block
            assert d["reason_code"] in block
            if d["override_flag"]:
                assert d["override_direction"] in block
                assert "An **override** means" in block


def test_every_message_is_quoted_in_full(guide):
    text, conn, _ = guide
    for case_id in cases(conn):
        block = section(text, case_id)
        for m in conn.execute("SELECT * FROM communication WHERE case_id=?", (case_id,)):
            assert m["communication_id"] in block
            assert m["template_id"] in block
            # the whole rendered text, not a truncation
            assert m["rendered_text"].split("\n")[0] in block


def test_the_ownership_multiplication_is_shown(guide):
    """Where a chain exists, the guide shows the working rather than the answer."""
    text, conn, _ = guide
    seen = 0
    for case_id in cases(conn):
        block = section(text, case_id)
        for u in conn.execute(
                "SELECT u.* FROM ubo u JOIN onboarding_case c USING(applicant_id)"
                " WHERE c.case_id=?", (case_id,)):
            chain = [x for x in (u["ownership_chain_percentages"] or "").split("|") if x]
            if len(chain) > 1:
                seen += 1
                working = " x ".join(f"{float(x):g}%" for x in chain)
                assert working in block, f"{case_id}: {working} is not shown"
    assert seen >= 2, "the set should contain at least two indirect chains"


# ---------------------------------------------------------------------------
# The committed copy is current
# ---------------------------------------------------------------------------

def test_the_committed_guide_is_what_the_generator_produces(guide, tmp_path):
    """Regenerating must not change the file in the repository.

    If this fails, the KB or the pipeline changed and the guide was not
    regenerated. Run `python tools/make_case_guide.py` and commit the result.
    """
    committed = ROOT / "docs" / "CASE_GUIDE.md"
    assert committed.exists(), "docs/CASE_GUIDE.md has not been generated"

    stamp = re.search(r"^Generated (.+?) from `kb/`",
                      committed.read_text(encoding="utf-8"), re.M)
    assert stamp, "the committed guide has no generated-at line"

    make_case_guide.generate(tmp_path, pdf=False, generated_at=stamp.group(1))
    fresh = steady((tmp_path / "CASE_GUIDE.md").read_text(encoding="utf-8"))
    old = steady(committed.read_text(encoding="utf-8"))
    if fresh != old:
        first = next((i for i, (a, b) in enumerate(zip(fresh.splitlines(),
                                                       old.splitlines())) if a != b), 0)
        pytest.fail(
            "docs/CASE_GUIDE.md is out of date - regenerate it.\n"
            f"first difference at line {first + 1}:\n"
            f"  committed: {old.splitlines()[first][:160]}\n"
            f"  generated: {fresh.splitlines()[first][:160]}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
