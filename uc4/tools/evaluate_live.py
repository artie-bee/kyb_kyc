"""
Live vs mock: how well does the real model agree with the scripted answers?

Runs the live quality checker and extractor over sample_documents/, compares
what comes back with the verdicts and fields the dataset scripts, and writes
eval_report.md.

Which model answers is set by LLM_PROVIDER (anthropic | xai); the key comes from
that provider's environment variable, and live mode has to be enabled first by
setting LIVE_MODE_READY = True in orchestrator/live_mode.py.

The report is the point. Nothing here tunes a prompt or loosens a KB rule to
make the numbers look better - a disagreement is information about the prompt,
the document or the rule, and hiding it would waste the exercise. Every
disagreement is listed in full for a person to read.

One thing to keep in mind when reading a run made through xAI: that provider
takes images only, so every PDF is rasterised to page images before it is sent.
The conversion is recorded on each call and printed at the top of the report,
because a disagreement on a PDF may be about the rendering rather than about
the model.

Costs money: one call per document for quality, one more for extraction. Use
--limit while you are still checking the setup works.

    python tools/evaluate_live.py --limit 2             # two documents, four calls
    python tools/evaluate_live.py --cases WAL-ONB-0001
    python tools/evaluate_live.py                       # every demo case
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from orchestrator import live_mode, llm_client                      # noqa: E402
from orchestrator.extractor import ClaudeExtractor                  # noqa: E402
from orchestrator.kb import KnowledgeBase                           # noqa: E402
from orchestrator.quality_checker import ClaudeVisionQualityChecker  # noqa: E402
from tools.dataset_to_applications import DEFAULT_DATASET           # noqa: E402
from tools.make_sample_documents import DEMO_CASES, DEFAULT_OUT as DOCS  # noqa: E402

REPORT = UC4 / "eval_report.md"


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def evaluate(dataset: Path, docs_root: Path, cases: tuple, limit: int | None,
             quality_only: bool, extraction_only: bool) -> dict:
    kb = KnowledgeBase()
    documents = [d for d in read_csv(dataset / "document.csv") if d["case_id"] in cases]
    if limit:
        documents = documents[:limit]
    fields_by_doc = {}
    for r in read_csv(dataset / "extracted_field.csv"):
        fields_by_doc.setdefault(r["document_id"], []).append(r)
    subject_names = {r["individual_id"]: r["full_name"]
                     for r in read_csv(dataset / "individual.csv")}

    quality_rows, field_rows, errors = [], [], []
    checker = None if extraction_only else ClaudeVisionQualityChecker()
    extractor = None if quality_only else ClaudeExtractor()

    for d in documents:
        path = docs_root / d["case_id"] / d["file_name"]
        if not path.exists():
            errors.append((d["document_id"], d["file_name"], "no sample file generated"))
            continue
        payload = {"file_path": str(path), "file_name": d["file_name"],
                   "document_type": d["document_type"],
                   "subject_name": subject_names.get(d["subject_individual_id"])}

        if checker:
            scripted = sorted(f for f in d["quality_flags"].split("|") if f)
            try:
                verdict = checker.check(payload)
                live = sorted(verdict.flags)
                call = checker.last_call
                quality_rows.append({
                    "document_id": d["document_id"], "case_id": d["case_id"],
                    "file_name": d["file_name"], "scripted": scripted, "live": live,
                    "agree": scripted == live, "confidence": verdict.confidence,
                    "notes": verdict.notes,
                    "transport": call.transport if call else "",
                    "cost": call.audit_note() if call else ""})
            except Exception as e:
                errors.append((d["document_id"], d["file_name"], f"quality: {e}"))

        if extractor:
            expected = kb.fields_for(d["document_type"])
            scripted = {f["name"]: f["value"] for f in fields_by_doc.get(d["document_id"], [])}
            try:
                result = extractor.extract(payload, expected)
                live = {f.name: f.value for f in result.fields if f.value is not None}
                for name in sorted(set(scripted) | set(live)):
                    want, got = scripted.get(name), live.get(name)
                    field_rows.append({
                        "document_id": d["document_id"], "case_id": d["case_id"],
                        "file_name": d["file_name"], "field": name,
                        "scripted": want, "live": got,
                        "agree": _same(want, got)})
            except Exception as e:
                errors.append((d["document_id"], d["file_name"], f"extraction: {e}"))

    return {"quality": quality_rows, "fields": field_rows, "errors": errors,
            "provider": llm_client.provider_name(),
            "model": llm_client.model_name(),
            "transport": sorted({r["transport"] for r in quality_rows if r.get("transport")}),
            "prompts": [p.stamp for p in
                        (checker.prompt if checker else None,
                         extractor.prompt if extractor else None) if p]}


def _same(want, got) -> bool:
    """Compare on meaning, not whitespace or case."""
    if want is None or got is None:
        return want == got
    return " ".join(str(want).split()).lower() == " ".join(str(got).split()).lower()


def report(result: dict) -> str:
    q, f, errors = result["quality"], result["fields"], result["errors"]
    lines = [
        "# Live evaluation - the live model against the scripted answers", "",
        f"Provider: `{result.get('provider', '?')}`  ",
        f"Model: `{result['model']}`  ",
        f"Prompts: {', '.join(f'`{p}`' for p in result['prompts']) or 'none'}", "",
        "Nothing was tuned to improve these numbers. Where the model and the script "
        "disagree, both are listed so a person can decide which is right - sometimes "
        "it will be the script.", "",
    ]
    if result.get("transport"):
        lines += ["> **How the files were sent.** " + "; ".join(result["transport"])
                  + ". A disagreement on one of those may be about the rendering "
                    "rather than about the model.", ""]

    if q:
        agreed = sum(1 for r in q if r["agree"])
        lines += ["## Document quality", "",
                  f"**{agreed}/{len(q)} documents agree** on the exact flag set.", "",
                  "| case | document | scripted | live | agree |",
                  "|---|---|---|---|---|"]
        for r in q:
            lines.append(f"| {r['case_id']} | {r['file_name']} | "
                         f"{', '.join(r['scripted']) or 'clean'} | "
                         f"{', '.join(r['live']) or 'clean'} | "
                         f"{'yes' if r['agree'] else '**no**'} |")
        lines.append("")
        disagreements = [r for r in q if not r["agree"]]
        if disagreements:
            lines += ["### Quality disagreements, in full", ""]
            for r in disagreements:
                lines += [f"**{r['file_name']}** ({r['case_id']}, {r['document_id']})",
                          f"- scripted: `{r['scripted'] or 'clean'}`",
                          f"- live: `{r['live'] or 'clean'}` at confidence {r['confidence']:.2f}",
                          f"- model notes: {r['notes'] or '(none)'}",
                          f"- call: {r['cost']}", ""]

    if f:
        agreed = sum(1 for r in f if r["agree"])
        by_doc = Counter(r["document_id"] for r in f)
        lines += ["## Extracted fields", "",
                  f"**{agreed}/{len(f)} field values agree** across "
                  f"{len(by_doc)} document(s).", ""]
        disagreements = [r for r in f if not r["agree"]]
        if disagreements:
            lines += ["| case | document | field | scripted | live |",
                      "|---|---|---|---|---|"]
            for r in disagreements:
                lines.append(f"| {r['case_id']} | {r['file_name']} | {r['field']} | "
                             f"{r['scripted'] if r['scripted'] is not None else '(absent)'} | "
                             f"{r['live'] if r['live'] is not None else '(absent)'} |")
            lines.append("")
        else:
            lines += ["Every field agreed.", ""]

    if errors:
        lines += ["## Calls that failed", "",
                  "A failed call is never a pass: in the pipeline each of these would "
                  "put the document in front of an analyst.", "",
                  "| document | file | error |", "|---|---|---|"]
        for doc_id, name, err in errors:
            lines.append(f"| {doc_id} | {name} | {str(err)[:160]} |")
        lines.append("")

    if not (q or f or errors):
        lines += ["Nothing was evaluated.", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--documents", type=Path, default=DOCS)
    ap.add_argument("--cases", nargs="*", default=list(DEMO_CASES))
    ap.add_argument("--limit", type=int, default=None, help="stop after N documents")
    ap.add_argument("--out", type=Path, default=REPORT)
    ap.add_argument("--quality-only", action="store_true")
    ap.add_argument("--extraction-only", action="store_true")
    args = ap.parse_args()

    # Live mode first: there is no point asking for a key for an integration
    # that has not been enabled yet.
    try:
        live_mode.require_ready()
    except live_mode.LiveModeNotConfigured as e:
        raise SystemExit(str(e))

    try:
        provider = llm_client.provider_name()
        llm_client.api_key(provider)
    except (llm_client.MissingApiKey, llm_client.UnknownProvider) as e:
        raise SystemExit(f"{e}\n\nMock mode needs no key and is the default everywhere else.")
    print(f"provider={provider}  model={llm_client.model_name(provider)}")

    if not args.documents.exists():
        raise SystemExit(f"no sample documents at {args.documents}; "
                         f"run tools/make_sample_documents.py first")

    result = evaluate(args.dataset, args.documents, tuple(args.cases), args.limit,
                      args.quality_only, args.extraction_only)
    args.out.write_text(report(result), encoding="utf-8")

    q, f = result["quality"], result["fields"]
    if q:
        print(f"quality:    {sum(1 for r in q if r['agree'])}/{len(q)} documents agree")
    if f:
        print(f"extraction: {sum(1 for r in f if r['agree'])}/{len(f)} field values agree")
    if result["errors"]:
        print(f"failed:     {len(result['errors'])} call(s) - listed in the report")
    print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    main()
