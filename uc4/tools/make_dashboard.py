"""
The operations dashboard (brief 10.1) as plain HTML and CSS.

    python tools/make_dashboard.py
    python tools/make_dashboard.py --open

Reads the database and writes ui/dashboard.html next to ui/dashboard.css. The
read is the only thing it does to the database: this tool has no UPDATE, no
INSERT and no orchestrator call in it, which is what lets a dashboard exist
outside the app at all. Re-run it after Reset demo to pick the new state up.

The rows are rendered into the HTML rather than fetched, so the page is readable
with CSS alone and a browser that never runs the script. The script adds the
filtering and sorting on top; without it you still get the whole table.
"""

import argparse
import html
import json
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from orchestrator import db, holds                                    # noqa: E402

DEFAULT_DB = UC4 / "onboarding.db"
DEFAULT_OUT = UC4 / "ui"

# Which semantic each status and band carries. Kept here rather than in the CSS
# so the mapping is one table a reader can check against the taxonomy.
STATUS_TONE = {
    "submitted": "neutral",
    "document_quality_review": "info",
    "resubmission_required": "warn",
    "verification_in_progress": "info",
    "analyst_review_required": "warn",
    "enhanced_due_diligence": "warn",
    "ready_for_decision": "info",
    "approved": "ok",
    "rejected": "bad",
    "closed_withdrawn": "neutral",
}
BAND_TONE = {
    "low": "ok",
    "medium": "info",
    "high": "warn",
    "critical": "bad",
    "insufficient_evidence": "warn",
}
OWNER_TONE = {"analyst": "info", "compliance": "bad", "customer": "warn", "system": "neutral"}

CLOSED = ("approved", "rejected", "closed_withdrawn")


def ageing_days(created_at: str) -> int:
    try:
        created = datetime.fromisoformat((created_at or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0
    return (datetime.now(timezone.utc) - created).days


def read_cases(conn) -> list[dict]:
    """Every case, with what an operations team needs to triage it. Read only."""
    out = []
    for case in conn.execute("SELECT * FROM onboarding_case ORDER BY case_id"):
        risk = conn.execute("SELECT risk_band, risk_score FROM risk_assessment"
                            " WHERE case_id = ?", (case["case_id"],)).fetchone()
        applicant = conn.execute("SELECT legal_name FROM applicant WHERE applicant_id = ?",
                                 (case["applicant_id"],)).fetchone()
        open_now = holds.open_holds(conn, case["case_id"])
        out.append({
            "case_id": case["case_id"],
            "applicant": applicant["legal_name"] if applicant else "",
            "applicant_type": case["applicant_type"] or "",
            "status": case["status"],
            "owner": case["next_action_owner"] or "",
            "band": risk["risk_band"] if risk else "",
            # None is not 0. A case that has not been scored has no score, and the
            # page has to say that differently from a case that scored nothing.
            "score": (None if not risk or risk["risk_score"] is None
                      else int(risk["risk_score"])),
            "holds": len(open_now),
            "hold_detail": "; ".join(f"{h.code} ({h.owner})" for h in open_now),
            "restricted": bool(case["restricted_finding"]),
            "age": ageing_days(case["created_at"]),
        })
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def chip(value: str, tone: str) -> str:
    if not value:
        return '<span class="muted">&mdash;</span>'
    return (f'<span class="chip chip--{tone}"><span class="chip__dot"></span>'
            f'{html.escape(value)}</span>')


def age_cell(days: int) -> str:
    # The ageing signal an operations lead scans for. Three quiet steps, not a
    # traffic light: the number stays legible and the tint does the sorting.
    step = "age--old" if days >= 21 else "age--due" if days >= 14 else ""
    return f'<span class="age {step}">{days}</span>'


def row_html(c: dict) -> str:
    score = ('<span class="muted" title="not scored yet">&mdash;</span>'
             if c["score"] is None else f'<span class="num">{c["score"]}</span>')
    band = chip(c["band"], BAND_TONE.get(c["band"], "neutral")) if c["band"] else \
        '<span class="muted" title="not scored yet">&mdash;</span>'
    holds_cell = ('<span class="muted">0</span>' if not c["holds"]
                  else f'<span class="count">{c["holds"]}</span>')
    detail = (f'<span class="detail" title="{html.escape(c["hold_detail"])}">'
              f'{html.escape(c["hold_detail"])}</span>' if c["hold_detail"]
             else '<span class="muted">&mdash;</span>')
    flag = ('<span class="restricted" title="Restricted finding: customer messages '
            'are limited to generic templates">restricted</span>' if c["restricted"] else "")
    return f"""        <tr data-status="{html.escape(c['status'])}" data-owner="{html.escape(c['owner'])}"
            data-open="{'0' if c['status'] in CLOSED else '1'}">
          <td class="cell-id"><span class="id">{html.escape(c['case_id'])}</span>{flag}</td>
          <td class="cell-name">{html.escape(c['applicant'])}</td>
          <td class="cell-type"><span class="type">{html.escape(c['applicant_type'])}</span></td>
          <td>{chip(c['status'], STATUS_TONE.get(c['status'], 'neutral'))}</td>
          <td>{chip(c['owner'], OWNER_TONE.get(c['owner'], 'neutral'))}</td>
          <td>{band}</td>
          <td class="ta-r">{score}</td>
          <td class="ta-r">{holds_cell}</td>
          <td class="cell-detail">{detail}</td>
          <td class="ta-r">{age_cell(c['age'])}</td>
        </tr>"""


def build(cases: list[dict], kb_version: str, generated: str) -> str:
    open_cases = [c for c in cases if c["status"] not in CLOSED]
    with_holds = [c for c in cases if c["holds"]]
    restricted = [c for c in cases if c["restricted"]]
    statuses = sorted({c["status"] for c in cases})
    owners = sorted({c["owner"] for c in cases if c["owner"]})

    rows = "\n".join(row_html(c) for c in cases)
    status_opts = "\n".join(
        f'            <label class="opt"><input type="checkbox" name="status" '
        f'value="{html.escape(s)}" checked><span>{html.escape(s)}</span></label>'
        for s in statuses)
    owner_opts = "\n".join(
        f'            <label class="opt"><input type="checkbox" name="owner" '
        f'value="{html.escape(o)}" checked><span>{html.escape(o)}</span></label>'
        for o in owners)

    return f"""<!doctype html>
<html lang="en" data-theme="auto">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Operations dashboard &middot; Wallester UC4</title>
<link rel="stylesheet" href="dashboard.css">
</head>
<body>
<div class="shell">

  <aside class="rail">
    <div class="rail__brand">Wallester UC4</div>

    <nav class="rail__nav" aria-label="Screens">
      <span class="rail__label">Screen</span>
      <a class="navitem navitem--on" href="#" aria-current="page">Operations dashboard</a>
      <a class="navitem navitem--off" href="#" aria-disabled="true">Case detail</a>
      <a class="navitem navitem--off" href="#" aria-disabled="true">Customer view</a>
      <a class="navitem navitem--off" href="#" aria-disabled="true">Agent reuse</a>
      <a class="navitem navitem--off" href="#" aria-disabled="true">Audit export</a>
    </nav>

    <div class="rail__foot">
      <div class="kv"><span>Knowledge base</span><code>{html.escape(kb_version)}</code></div>
      <div class="kv"><span>Generated</span><code>{html.escape(generated)}</code></div>
      <button class="theme" type="button" id="theme">Toggle theme</button>
    </div>
  </aside>

  <main class="main">
    <header class="page">
      <div>
        <h1>Operations dashboard</h1>
        <p class="sub">Every case, its owner and what it is waiting for.</p>
      </div>
    </header>

    <section class="stats" aria-label="Summary">
      <div class="stat"><span class="stat__k">Cases</span><span class="stat__v num">{len(cases)}</span></div>
      <div class="stat"><span class="stat__k">Open</span><span class="stat__v num">{len(open_cases)}</span></div>
      <div class="stat"><span class="stat__k">With holds</span><span class="stat__v num">{len(with_holds)}</span></div>
      <div class="stat"><span class="stat__k">Restricted findings</span><span class="stat__v num">{len(restricted)}</span></div>
    </section>

    <section class="filters" aria-label="Filters">
      <fieldset>
        <legend>Status</legend>
        <div class="opts">
{status_opts}
        </div>
      </fieldset>
      <fieldset>
        <legend>Next action owner</legend>
        <div class="opts">
{owner_opts}
        </div>
      </fieldset>
    </section>

    <p class="count-line"><span id="shown">{len(cases)}</span> of {len(cases)} cases</p>

    <div class="tablewrap">
      <table id="cases">
        <thead>
          <tr>
            <th scope="col" data-sort="text">Case</th>
            <th scope="col" data-sort="text">Applicant</th>
            <th scope="col" data-sort="text">Type</th>
            <th scope="col" data-sort="text">Status</th>
            <th scope="col" data-sort="text">Owner</th>
            <th scope="col" data-sort="text">Band</th>
            <th scope="col" data-sort="num" class="ta-r">Score</th>
            <th scope="col" data-sort="num" class="ta-r">Holds</th>
            <th scope="col">Hold detail</th>
            <th scope="col" data-sort="num" class="ta-r">Age&nbsp;(days)</th>
          </tr>
        </thead>
        <tbody>
{rows}
        </tbody>
      </table>
      <p class="empty" id="empty" hidden>No case matches these filters.</p>
    </div>

    <aside class="note">
      <strong>Restricted findings.</strong> A case carrying one gets generic customer
      wording only. The finding itself is never shown to the applicant.
    </aside>

    <p class="foot">
      A dash in Band or Score means the case has not reached risk scoring &mdash; it is
      not a score of zero. Read-only: this page is generated from the database and
      changes nothing in it.
    </p>
  </main>

</div>
<script src="dashboard.js"></script>
</body>
</html>
"""


# The one stylesheet lives in web/static/app.css and is shared with the served
# console, so the static export and the app cannot drift apart. This file used
# to carry its own copy, and a change to one left the other stale - which is
# exactly the failure the comment at the top of app.css claims does not happen.
STYLESHEET = UC4 / "web" / "static" / "app.css"


def stylesheet() -> str:
    return STYLESHEET.read_text(encoding="utf-8")


JS = """/* Filtering and sorting. The table is complete in the HTML; this only narrows
   and reorders what is already there, so the page still reads with the script
   blocked. */
(function () {
  var table = document.getElementById("cases");
  if (!table) return;
  var tbody = table.tBodies[0];
  var rows  = Array.prototype.slice.call(tbody.rows);
  var shown = document.getElementById("shown");
  var empty = document.getElementById("empty");

  function checked(name) {
    var out = {};
    document.querySelectorAll('input[name="' + name + '"]:checked')
      .forEach(function (b) { out[b.value] = true; });
    return out;
  }

  function apply() {
    var st = checked("status"), ow = checked("owner"), n = 0;
    rows.forEach(function (tr) {
      var ok = st[tr.dataset.status] && (ow[tr.dataset.owner] || !tr.dataset.owner);
      tr.hidden = !ok;
      if (ok) n++;
    });
    shown.textContent = n;
    empty.hidden = n !== 0;
  }

  document.querySelectorAll('.filters input').forEach(function (b) {
    b.addEventListener("change", apply);
  });

  var dir = {};
  Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th, i) {
    if (!th.dataset.sort) return;
    th.tabIndex = 0;
    function sort() {
      var desc = dir[i] === "ascending";
      Array.prototype.forEach.call(table.tHead.rows[0].cells, function (o) {
        o.removeAttribute("aria-sort");
      });
      th.setAttribute("aria-sort", desc ? "descending" : "ascending");
      dir[i] = desc ? "descending" : "ascending";
      var num = th.dataset.sort === "num";
      rows.sort(function (a, b) {
        var x = a.cells[i].textContent.trim(), y = b.cells[i].textContent.trim();
        if (num) {
          // an em dash is "not scored", which sorts below every real number
          var nx = parseFloat(x), ny = parseFloat(y);
          if (isNaN(nx)) nx = -1;
          if (isNaN(ny)) ny = -1;
          return desc ? ny - nx : nx - ny;
        }
        return desc ? y.localeCompare(x) : x.localeCompare(y);
      });
      rows.forEach(function (tr) { tbody.appendChild(tr); });
    }
    th.addEventListener("click", sort);
    th.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); }
    });
  });

  var btn = document.getElementById("theme");
  if (btn) btn.addEventListener("click", function () {
    var root = document.documentElement;
    var now = root.getAttribute("data-theme");
    var dark = now === "dark" ||
      (now !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    root.setAttribute("data-theme", dark ? "light" : "dark");
  });

  apply();
})();
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--open", action="store_true", help="open the page afterwards")
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit(f"no database at {args.db}; run tools/run_demo.py first")

    conn = db.connect(args.db)
    cases = read_cases(conn)
    manifest = json.loads((UC4 / "kb" / "kb_manifest.json").read_text(encoding="utf-8"))
    conn.close()

    args.out.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    (args.out / "dashboard.html").write_text(
        build(cases, manifest["kb_version"], generated), encoding="utf-8")
    (args.out / "dashboard.css").write_text(stylesheet(), encoding="utf-8")
    (args.out / "dashboard.js").write_text(JS, encoding="utf-8")

    print(f"{len(cases)} cases -> {args.out / 'dashboard.html'}")
    if args.open:
        webbrowser.open((args.out / "dashboard.html").resolve().as_uri())


if __name__ == "__main__":
    main()
