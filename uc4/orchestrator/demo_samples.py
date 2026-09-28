"""
Recognised demo sample files - mock mode only.

tools/make_sample_documents.py writes a demo upload pack to
sample_documents/demo_pack/, with a manifest recording each file's SHA-256, the
quality verdict it should get and the fields printed on it. In mock mode, an
upload whose bytes hash to a manifest entry has that verdict and those fields
replayed - exactly as a scripted dataset document's are - so a presenter can
take a brand-new application all the way to a decision with real files and no
typing. Every replayed result says so: "mock: recognised demo sample file", in
the audit trail and on the console.

Two packs are recognised: the generated Lumen Harbour pack above, and the
committed Vaher Studio pack in demo_packs/vaher_studio/ (fixed files, so the
demo that uses them never depends on the live model, whatever
EXTRACTION_FOR_NEW_UPLOADS says).

Recognition is by content hash, never by file name: a renamed copy of a pack
file is still recognised, and any other file - however it is named - is not,
and keeps the ordinary mock-mode path (the visual-check hold, then an analyst
typing the fields in). Live mode never reads the manifest.
"""

import hashlib
import json
from pathlib import Path

LABEL = "mock: recognised demo sample file"
MANIFEST = Path(__file__).resolve().parents[1] / "sample_documents" / "demo_pack" / "manifest.json"

# Committed packs with fixed bytes, recognised alongside the generated one.
PACKS = Path(__file__).resolve().parents[1] / "demo_packs"

_cache: dict = {}


def manifests() -> list[Path]:
    """Every manifest that is recognised: the generated pack, then the committed ones."""
    return [p for p in [Path(MANIFEST), *sorted(PACKS.glob("*/manifest.json"))] if p.exists()]


def _load(path: Path) -> dict:
    """One manifest, reloaded when the file changes."""
    mtime = path.stat().st_mtime
    if _cache.get(path, (None,))[0] != mtime:
        _cache[path] = (mtime, json.loads(path.read_text(encoding="utf-8")))
    return _cache[path][1]


def _files() -> dict:
    """{sha256: entry} across every recognised manifest."""
    out = {}
    for path in manifests():
        out.update(_load(path).get("files", {}))
    return out


def registers() -> list[dict]:
    """The simulated register entry each pack's company is looked up by."""
    return [m["register"] for m in map(_load, manifests()) if m.get("register")]


def recognise(content: bytes | None = None, path: str | Path | None = None) -> dict | None:
    """The manifest entry for these bytes (or this file), or None."""
    if content is None:
        if not path or not Path(path).exists():
            return None
        content = Path(path).read_bytes()
    return _files().get(hashlib.sha256(content).hexdigest())


def recognised_fields(conn, case_id: str) -> set[str]:
    """Document ids whose FIELDS were replayed from a manifest (Step 4's record).
    Not the same as recognised_documents: a file accepted by an analyst before
    it was recognised has a person's quality verdict and a sample's fields."""
    return {payload.split(" ", 1)[0] for (payload,) in conn.execute(
        "SELECT payload_summary FROM audit_event WHERE case_id = ?"
        " AND action = 'fields_extracted' AND payload_summary LIKE ?",
        (case_id, f"%{LABEL}%"))}


def recognised_documents(conn, case_id: str) -> set[str]:
    """Document ids on this case whose quality verdict was replayed from the
    manifest, read back from the audit trail where Step 3 recorded it."""
    out = set()
    for (payload,) in conn.execute(
            "SELECT payload_summary FROM audit_event WHERE case_id = ?"
            " AND action = 'document_quality_checked' AND payload_summary LIKE ?",
            (case_id, f"%{LABEL}%")):
        out.add(payload.split(" ", 1)[0])
    return out
