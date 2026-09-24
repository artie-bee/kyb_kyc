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

    <div class="badge badge--mock">
      <span class="badge__dot"></span>
      <div>
        <strong>MOCK</strong>
        <span>scripted answers, no API calls</span>
      </div>
    </div>

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


CSS = """/* Wallester UC4 - operations dashboard.
   A system of record: quiet by default, so the few things genuinely wrong on a
   case are the only things carrying colour. Structure is carried by hairlines
   and background steps, never by shadows. */

:root {
  color-scheme: light dark;

  --n-0:#ffffff; --n-1:#fbfcfd; --n-2:#f4f6f8; --n-3:#e9edf1; --n-4:#dde3e9;
  --n-5:#c3ccd6; --n-6:#8f9bab; --n-7:#617082;
  --n-8:#3d4854; --n-9:#232a33; --n-10:#151a20;

  --fg:var(--n-9); --fg-mute:var(--n-7); --fg-faint:var(--n-6);
  --bg:var(--n-2); --surface:var(--n-0); --surface-2:var(--n-1);
  --line:var(--n-4); --line-soft:var(--n-3);

  --accent:#2c5fa8; --accent-soft:#eaf0fa;

  --ok:#2f6f4f;   --ok-bg:#eaf3ee;   --ok-line:#a9ccb9;
  --info:#2c5fa8; --info-bg:#eaf0fa; --info-line:#b0c6e4;
  --warn:#8a6116; --warn-bg:#faf2e2; --warn-line:#e0c894;
  --bad:#963232;  --bad-bg:#faecec;  --bad-line:#e0b0b0;

  --r:5px;
  --mono:"IBM Plex Mono","JetBrains Mono","SFMono-Regular",Consolas,monospace;
  --sans:Inter,"Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,sans-serif;
}

/* A real dark theme with its own ramp, not an inverted light one. Declared once
   and applied from two places: the system preference, unless the reader has
   pinned light, and the explicit toggle. */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --fg:#dbe2ea; --fg-mute:#93a0b0; --fg-faint:#6d7a8a;
    --bg:#11151a; --surface:#171c22; --surface-2:#1c2229;
    --line:#2a323b; --line-soft:#222931;
    --accent:#7aa6e0; --accent-soft:#1b2534;
    --ok:#79c298;   --ok-bg:#16241d;   --ok-line:#2f5442;
    --info:#7aa6e0; --info-bg:#161f2d; --info-line:#2d4363;
    --warn:#d9b167; --warn-bg:#2a2317; --warn-line:#59492a;
    --bad:#e08a8a;  --bad-bg:#2b1a1a;  --bad-line:#5c3434;
  }
}
:root[data-theme="dark"] {
  --fg:#dbe2ea; --fg-mute:#93a0b0; --fg-faint:#6d7a8a;
  --bg:#11151a; --surface:#171c22; --surface-2:#1c2229;
  --line:#2a323b; --line-soft:#222931;
  --accent:#7aa6e0; --accent-soft:#1b2534;
  --ok:#79c298;   --ok-bg:#16241d;   --ok-line:#2f5442;
  --info:#7aa6e0; --info-bg:#161f2d; --info-line:#2d4363;
  --warn:#d9b167; --warn-bg:#2a2317; --warn-line:#59492a;
  --bad:#e08a8a;  --bad-bg:#2b1a1a;  --bad-line:#5c3434;
}

* { box-sizing:border-box; }

html { -webkit-text-size-adjust:100%; }

body {
  margin:0;
  font-family:var(--sans);
  font-size:13.5px;
  line-height:1.5;
  color:var(--fg);
  background:var(--bg);
  font-variant-numeric:tabular-nums;
  -webkit-font-smoothing:antialiased;
}

.num, .age, td.ta-r, th.ta-r { font-variant-numeric:tabular-nums; }
.ta-r { text-align:right; }
.muted { color:var(--fg-faint); }

/* ---------- shell ------------------------------------------------------- */

.shell { display:grid; grid-template-columns:248px minmax(0,1fr); min-height:100vh; }

.rail {
  border-right:1px solid var(--line);
  background:var(--surface);
  padding:20px 16px;
  display:flex; flex-direction:column; gap:20px;
  position:sticky; top:0; height:100vh; overflow:auto;
}
.rail__brand { font-weight:640; letter-spacing:-.01em; font-size:14px; }
.rail__label {
  display:block; font-size:10.5px; text-transform:uppercase; letter-spacing:.07em;
  color:var(--fg-faint); margin-bottom:6px;
}
.rail__nav { display:flex; flex-direction:column; gap:1px; }
.navitem {
  display:block; padding:6px 9px; border-radius:var(--r);
  color:var(--fg-mute); text-decoration:none; font-size:13px;
}
.navitem--on { background:var(--accent-soft); color:var(--accent); font-weight:560; }
.navitem--off { color:var(--fg-faint); cursor:default; }
.navitem--off:hover { background:var(--line-soft); }

.rail__foot { margin-top:auto; display:flex; flex-direction:column; gap:8px; }
.kv { display:flex; justify-content:space-between; gap:8px; font-size:11.5px; color:var(--fg-faint); }
.kv code { font-family:var(--mono); font-size:11px; color:var(--fg-mute); }

.theme {
  font:inherit; font-size:12px; padding:6px 10px; border-radius:var(--r);
  border:1px solid var(--line); background:var(--surface-2); color:var(--fg-mute);
  cursor:pointer;
}
.theme:hover { border-color:var(--n-5); color:var(--fg); }

/* ---------- badge ------------------------------------------------------- */

.badge {
  display:flex; gap:9px; align-items:flex-start;
  padding:9px 10px; border-radius:var(--r);
  border:1px solid var(--ok-line); background:var(--ok-bg);
  border-left-width:3px;
}
.badge__dot { width:7px; height:7px; border-radius:50%; background:var(--ok); margin-top:5px; flex:none; }
.badge strong { display:block; font-size:12px; letter-spacing:.04em; color:var(--ok); }
.badge span:last-child { font-size:11px; color:var(--fg-mute); line-height:1.35; }

/* ---------- page -------------------------------------------------------- */

.main { padding:26px 28px 40px; max-width:1560px; }

.page { margin-bottom:20px; }
.page h1 { font-size:20px; font-weight:620; letter-spacing:-.015em; margin:0; }
.sub { margin:3px 0 0; color:var(--fg-mute); font-size:13px; }

.stats { display:flex; gap:0; border:1px solid var(--line); border-radius:var(--r);
         background:var(--surface); margin-bottom:18px; overflow:hidden; }
.stat { padding:11px 16px; flex:1; border-right:1px solid var(--line-soft); }
.stat:last-child { border-right:0; }
.stat__k { display:block; font-size:11px; color:var(--fg-faint);
           text-transform:uppercase; letter-spacing:.06em; }
.stat__v { display:block; font-size:21px; font-weight:600; letter-spacing:-.02em; margin-top:1px; }

/* ---------- filters ----------------------------------------------------- */

.filters { display:flex; gap:12px; margin-bottom:14px; flex-wrap:wrap; }
.filters fieldset {
  border:1px solid var(--line); border-radius:var(--r); background:var(--surface);
  padding:9px 12px 11px; margin:0; min-width:260px;
}
.filters legend {
  font-size:10.5px; text-transform:uppercase; letter-spacing:.07em;
  color:var(--fg-faint); padding:0 4px;
}
.opts { display:flex; flex-wrap:wrap; gap:4px 12px; }
.opt { display:inline-flex; align-items:center; gap:5px; font-size:12px; color:var(--fg-mute); cursor:pointer; }
.opt input { accent-color:var(--accent); margin:0; }
.opt:hover span { color:var(--fg); }

.count-line { font-size:12px; color:var(--fg-faint); margin:0 0 8px; }

/* ---------- table ------------------------------------------------------- */

.tablewrap { border:1px solid var(--line); border-radius:var(--r); background:var(--surface); overflow:auto; }

table { width:100%; border-collapse:separate; border-spacing:0; }

thead th {
  position:sticky; top:0; z-index:1;
  background:var(--surface-2);
  border-bottom:1px solid var(--line);
  text-align:left; font-size:11px; font-weight:580;
  text-transform:uppercase; letter-spacing:.06em; color:var(--fg-mute);
  padding:9px 12px; white-space:nowrap;
}
thead th[data-sort] { cursor:pointer; user-select:none; }
thead th[data-sort]:hover { color:var(--fg); }
thead th[aria-sort]::after { content:"\\2191"; margin-left:5px; opacity:.85; }
thead th[aria-sort="descending"]::after { content:"\\2193"; }

tbody td { padding:8px 12px; border-bottom:1px solid var(--line-soft); vertical-align:middle; }
tbody tr:last-child td { border-bottom:0; }
tbody tr:hover td { background:var(--surface-2); }

.cell-id { white-space:nowrap; }
.cell-name { font-weight:520; min-width:190px; }
.cell-type { white-space:nowrap; }
.cell-detail { max-width:300px; }

.id { font-family:var(--mono); font-size:11.5px; letter-spacing:-.01em;
      background:var(--line-soft); padding:2px 5px; border-radius:3px; }
.type { font-size:11.5px; color:var(--fg-mute); font-family:var(--mono); }
.detail { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
          font-size:12px; color:var(--fg-mute); }

.count { display:inline-block; min-width:19px; padding:1px 5px; border-radius:3px;
         background:var(--warn-bg); border:1px solid var(--warn-line);
         color:var(--warn); font-size:11.5px; font-weight:600; }

.age { display:inline-block; padding:1px 6px; border-radius:3px; }
.age--due { background:var(--warn-bg); color:var(--warn); }
.age--old { background:var(--bad-bg); color:var(--bad); }

.restricted {
  margin-left:7px; font-size:10px; letter-spacing:.05em; text-transform:uppercase;
  color:var(--bad); background:var(--bad-bg); border:1px solid var(--bad-line);
  padding:1px 5px; border-radius:3px; vertical-align:1px;
}

/* ---------- chips ------------------------------------------------------- */

.chip {
  display:inline-flex; align-items:center; gap:5px; white-space:nowrap;
  font-size:11.5px; padding:2px 8px 2px 6px; border-radius:3px;
  border:1px solid var(--line); background:var(--surface-2); color:var(--fg-mute);
}
.chip__dot { width:5px; height:5px; border-radius:50%; background:currentColor; flex:none; }
.chip--ok   { color:var(--ok);   background:var(--ok-bg);   border-color:var(--ok-line); }
.chip--info { color:var(--info); background:var(--info-bg); border-color:var(--info-line); }
.chip--warn { color:var(--warn); background:var(--warn-bg); border-color:var(--warn-line); }
.chip--bad  { color:var(--bad);  background:var(--bad-bg);  border-color:var(--bad-line); }

/* ---------- notes ------------------------------------------------------- */

.empty { padding:26px 14px; text-align:center; color:var(--fg-faint); font-size:13px; margin:0; }

.note {
  margin-top:14px; padding:10px 13px; font-size:12.5px; color:var(--fg-mute);
  border:1px solid var(--info-line); border-left-width:3px;
  background:var(--info-bg); border-radius:var(--r);
}
.note strong { color:var(--fg); font-weight:580; }

.foot { margin-top:16px; font-size:11.5px; color:var(--fg-faint); max-width:70ch; }

/* ---------- focus ------------------------------------------------------- */

a:focus-visible, button:focus-visible, input:focus-visible, th:focus-visible {
  outline:2px solid var(--accent); outline-offset:2px; border-radius:3px;
}

@media (max-width:900px) {
  .shell { grid-template-columns:1fr; }
  .rail { position:static; height:auto; border-right:0; border-bottom:1px solid var(--line); }
  .main { padding:18px 16px 32px; }
  .stats { flex-wrap:wrap; }
  .stat { min-width:50%; }
}
"""

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
    (args.out / "dashboard.css").write_text(CSS, encoding="utf-8")
    (args.out / "dashboard.js").write_text(JS, encoding="utf-8")

    print(f"{len(cases)} cases -> {args.out / 'dashboard.html'}")
    if args.open:
        webbrowser.open((args.out / "dashboard.html").resolve().as_uri())


if __name__ == "__main__":
    main()
