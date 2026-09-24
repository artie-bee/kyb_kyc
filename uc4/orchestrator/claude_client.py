"""
TODO - LIVE MODE IS A PLACEHOLDER.

This code is written and unit-tested, but it has never been run against the real
Claude API: there is no API access on this network. Treat it as a first draft to
be exercised, not as working integration. Nothing calls it unless live mode is
selected explicitly, and selecting live mode currently stops with a message
rather than attempting a call.

To enable it later: set ANTHROPIC_API_KEY, confirm the network or proxy allows
api.anthropic.com, set LIVE_MODE_READY = True in orchestrator/live_mode.py, then
run tools/evaluate_live.py and read eval_report.md before trusting any of it.

The one place this project talks to the Claude API.

Everything the live checkers share lives here: the key, the model setting, the
versioned prompt files, strict JSON parsing with a single retry, and the timing
and token counts that go into the audit row.

Nothing here decides anything. It fetches an answer and hands it back; the
caller validates it against the KB and decides what to do when it does not
parse. A failed call is never a pass - that rule is enforced by the callers, and
`CallFailed` is what they act on.

    ANTHROPIC_API_KEY   read from the environment, never hard-coded
    WALLESTER_UC4_MODEL optional model override
"""

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_ATTEMPTS = 2          # one call, one retry, then the caller places a hold

MEDIA_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class CallFailed(RuntimeError):
    """The model could not be reached, or would not return usable JSON."""


class MissingApiKey(RuntimeError):
    """ANTHROPIC_API_KEY is not set."""


@dataclass
class Call:
    """What came back, and what it cost. Both go into the audit row."""

    data: dict
    model: str
    prompt_version: str
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 1
    raw: str = ""

    def audit_note(self) -> str:
        return (f"model={self.model}; prompt={self.prompt_version}; "
                f"attempts={self.attempts}; latency={self.latency_ms}ms; "
                f"tokens in/out={self.input_tokens}/{self.output_tokens}")


@dataclass
class Prompt:
    name: str
    version: str
    text: str = field(repr=False, default="")

    @property
    def stamp(self) -> str:
        return f"{self.name}_{self.version}"


def load_prompt(name: str, version: str = "v1") -> Prompt:
    """Read a versioned prompt file. The version is what gets audited, so it has
    to come from the filename rather than from a string in the code."""
    path = PROMPTS / f"{name}_{version}.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"no prompt at {path}. Prompts are versioned files: add it rather than "
            f"inlining the text, or the audit trail cannot say what was asked")
    return Prompt(name, version, path.read_text(encoding="utf-8"))


def model_name() -> str:
    return os.environ.get("WALLESTER_UC4_MODEL", DEFAULT_MODEL)


def api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise MissingApiKey(
            "ANTHROPIC_API_KEY is not set. Live mode reads the key from the "
            "environment; it is never stored in this repository")
    return key


def _document_block(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise CallFailed(f"{path.name}: {suffix} is not a format the API accepts")
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    kind = "document" if suffix == ".pdf" else "image"
    return {"type": kind,
            "source": {"type": "base64", "media_type": MEDIA_TYPES[suffix], "data": data}}


def parse_strict_json(text: str) -> dict:
    """The prompts ask for a bare JSON object. Accept a code fence around one,
    because models add them, but nothing looser than that."""
    cleaned = _FENCE.sub("", (text or "").strip()).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise CallFailed(f"the reply is not JSON: {e}; reply began {cleaned[:120]!r}")
    if not isinstance(value, dict):
        raise CallFailed(f"expected a JSON object, got {type(value).__name__}")
    return value


def ask(document_path: Path, prompt: Prompt, instruction: str,
        model: str | None = None, max_tokens: int = 1500) -> Call:
    """Send one document and one prompt; return parsed JSON, retried once.

    The retry repeats the request with the reply appended and a reminder that
    the format was wrong. Two attempts and no more: a model that will not
    produce the format twice is not going to on the third try, and the caller
    needs to place a hold rather than keep paying.
    """
    try:
        import anthropic
    except ImportError as e:
        raise CallFailed(
            "the anthropic package is not installed; live mode needs it: "
            "pip install anthropic") from e

    client = anthropic.Anthropic(api_key=api_key())
    chosen = model or model_name()
    content = [_document_block(Path(document_path)),
               {"type": "text", "text": f"{prompt.text}\n\n{instruction}"}]

    last_error, started = None, time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.messages.create(
                model=chosen, max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}])
            raw = "".join(block.text for block in response.content
                          if getattr(block, "type", "") == "text")
            data = parse_strict_json(raw)
            return Call(data=data, model=chosen, prompt_version=prompt.stamp,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        input_tokens=getattr(response.usage, "input_tokens", 0),
                        output_tokens=getattr(response.usage, "output_tokens", 0),
                        attempts=attempt, raw=raw)
        except CallFailed as e:
            last_error = e
            if attempt < MAX_ATTEMPTS:
                content = content + [
                    {"type": "text",
                     "text": "That reply was not the JSON object the instructions asked for. "
                             "Return only the object, with no text around it."}]
        except Exception as e:                      # network, auth, rate limit
            raise CallFailed(f"the API call failed: {type(e).__name__}: {e}") from e

    raise CallFailed(f"no usable JSON after {MAX_ATTEMPTS} attempts: {last_error}")
