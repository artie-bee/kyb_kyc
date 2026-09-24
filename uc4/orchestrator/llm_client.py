"""
The one place this project talks to a language model, whichever one that is.

Everything the live checkers share lives here: the key, the model setting, the
versioned prompt files, strict JSON parsing with a single retry, and the timing
and token counts that go into the audit row.

    LLM_PROVIDER        anthropic (default) | xai
    ANTHROPIC_API_KEY   read from the environment, never hard-coded
    XAI_API_KEY         read from the environment, never hard-coded
    WALLESTER_UC4_MODEL optional model override, applied to whichever provider

Nothing here decides anything. It fetches an answer and hands it back; the
caller validates it against the KB and decides what to do when it does not
parse. A failed call is never a pass - that rule is enforced by the callers, and
`CallFailed` is what they act on.

The two providers differ in one way that matters and the difference is not
cosmetic. Anthropic accepts a PDF as a document and reads it natively. xAI's
image understanding takes jpg and png only, so a PDF has to be rasterised to
page images before it can be sent at all. That conversion is recorded on the
Call and reaches the audit row, because "the model read the PDF" and "the model
read a picture of the PDF" are different claims and only one of them is true.
"""

import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
MAX_ATTEMPTS = 2          # one call, one retry, then the caller places a hold

DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    # xAI's current vision-capable chat model. A setting, not a constant in the
    # calling code: model names move and the audit row has to say which one ran.
    "xai": "grok-4.7",
}
API_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "xai": "XAI_API_KEY"}

XAI_BASE_URL = "https://api.x.ai/v1"

# What each provider will accept directly.
MEDIA_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
XAI_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# A rasterised PDF is sent as page images. Four pages is more than any document
# in this set needs and keeps a runaway file from turning into a large bill.
PDF_MAX_PAGES = 4
PDF_RENDER_SCALE = 2.0            # roughly 144 dpi against a 72 dpi page box

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class CallFailed(RuntimeError):
    """The model could not be reached, or would not return usable JSON."""


class MissingApiKey(RuntimeError):
    """The API key for the selected provider is not set."""


class UnknownProvider(ValueError):
    """LLM_PROVIDER names a provider that does not exist."""


@dataclass
class Call:
    """What came back, and what it cost. All of this goes into the audit row."""

    data: dict
    model: str
    prompt_version: str
    provider: str = DEFAULT_PROVIDER
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 1
    raw: str = ""
    transport: str = ""       # e.g. "pdf rasterised to 2 page image(s)"

    def audit_note(self) -> str:
        note = (f"provider={self.provider}; model={self.model}; "
                f"prompt={self.prompt_version}; attempts={self.attempts}; "
                f"latency={self.latency_ms}ms; "
                f"tokens in/out={self.input_tokens}/{self.output_tokens}")
        return f"{note}; {self.transport}" if self.transport else note


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


def provider_name() -> str:
    """Which provider is selected. Default stays anthropic, so an environment
    that sets nothing behaves exactly as it did before this module existed."""
    chosen = (os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if chosen not in DEFAULT_MODELS:
        raise UnknownProvider(
            f"LLM_PROVIDER is {chosen!r}; choose from {sorted(DEFAULT_MODELS)}")
    return chosen


def model_name(provider: str | None = None) -> str:
    provider = provider or provider_name()
    return os.environ.get("WALLESTER_UC4_MODEL") or DEFAULT_MODELS[provider]


def api_key(provider: str | None = None) -> str:
    provider = provider or provider_name()
    env = API_KEY_ENV[provider]
    key = os.environ.get(env, "").strip()
    if not key:
        raise MissingApiKey(
            f"{env} is not set. Live mode reads the key from the environment; "
            f"it is never stored in this repository")
    return key


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


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------

class LLMClient:
    """One document, one prompt, one JSON object back.

    Implementations own the transport and nothing else. The retry budget, the
    strict-JSON rule and the meaning of a failure are the same for every
    provider, and live in `run`, so a new provider cannot quietly relax them.
    """

    provider = "base"

    def __init__(self, model: str | None = None):
        self.model = model or model_name(self.provider)

    # -- what a provider implements ---------------------------------------
    def _send(self, document_path: Path, text: str, max_tokens: int,
              corrections: list[str]) -> tuple[str, int, int, str]:
        """Return (raw_text, input_tokens, output_tokens, transport_note)."""
        raise NotImplementedError

    # -- what every provider gets -----------------------------------------
    def ask(self, document_path: Path, prompt: Prompt, instruction: str,
            max_tokens: int = 1500) -> Call:
        """Send one document and one prompt; return parsed JSON, retried once.

        The retry repeats the request with a reminder that the format was wrong.
        Two attempts and no more: a model that will not produce the format twice
        is not going to on the third try, and the caller needs to place a hold
        rather than keep paying.
        """
        text = f"{prompt.text}\n\n{instruction}"
        corrections: list[str] = []
        last_error, started = None, time.monotonic()

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw, tok_in, tok_out, transport = self._send(
                    Path(document_path), text, max_tokens, corrections)
            except CallFailed:
                raise
            except Exception as e:                  # network, auth, rate limit
                raise CallFailed(f"the API call failed: {type(e).__name__}: {e}") from e

            try:
                data = parse_strict_json(raw)
            except CallFailed as e:
                last_error = e
                if attempt < MAX_ATTEMPTS:
                    corrections.append(
                        "That reply was not the JSON object the instructions asked "
                        "for. Return only the object, with no text around it.")
                continue

            return Call(data=data, model=self.model, prompt_version=prompt.stamp,
                        provider=self.provider,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        input_tokens=tok_in, output_tokens=tok_out,
                        attempts=attempt, raw=raw, transport=transport)

        raise CallFailed(f"no usable JSON after {MAX_ATTEMPTS} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Anthropic - the original integration, behind the interface unchanged
# ---------------------------------------------------------------------------

class AnthropicClient(LLMClient):
    """Claude. Sends a PDF as a document and reads it natively."""

    provider = "anthropic"

    def _block(self, path: Path) -> dict:
        suffix = path.suffix.lower()
        if suffix not in MEDIA_TYPES:
            raise CallFailed(f"{path.name}: {suffix} is not a format the API accepts")
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        kind = "document" if suffix == ".pdf" else "image"
        return {"type": kind,
                "source": {"type": "base64", "media_type": MEDIA_TYPES[suffix],
                           "data": data}}

    def _send(self, document_path, text, max_tokens, corrections):
        try:
            import anthropic
        except ImportError as e:
            raise CallFailed(
                "the anthropic package is not installed; live mode needs it: "
                "pip install anthropic") from e

        client = anthropic.Anthropic(api_key=api_key(self.provider))
        content = [self._block(document_path), {"type": "text", "text": text}]
        for note in corrections:
            content.append({"type": "text", "text": note})

        response = client.messages.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}])
        raw = "".join(block.text for block in response.content
                      if getattr(block, "type", "") == "text")
        return (raw,
                getattr(response.usage, "input_tokens", 0),
                getattr(response.usage, "output_tokens", 0),
                "")


# ---------------------------------------------------------------------------
# xAI - the OpenAI-compatible chat completions endpoint
# ---------------------------------------------------------------------------

def render_pdf_pages(path: Path, max_pages: int = PDF_MAX_PAGES) -> list[bytes]:
    """A PDF as PNG page images, because xAI's vision input takes images only.

    Raises rather than returning an empty list: a document nobody could render
    has not been read, and the caller must place a hold instead of recording a
    clean verdict against a blank page.
    """
    try:
        import pypdfium2
    except ImportError as e:
        raise CallFailed(
            f"{path.name} is a PDF, and the xAI provider can only send images. "
            f"Rendering it needs pypdfium2: pip install pypdfium2") from e

    try:
        pdf = pypdfium2.PdfDocument(str(path))
        pages = []
        for index in range(min(len(pdf), max_pages)):
            bitmap = pdf[index].render(scale=PDF_RENDER_SCALE)
            buffer = io.BytesIO()
            bitmap.to_pil().convert("RGB").save(buffer, "PNG")
            pages.append(buffer.getvalue())
        pdf.close()
    except CallFailed:
        raise
    except Exception as e:
        raise CallFailed(f"{path.name}: the PDF could not be rendered: "
                         f"{type(e).__name__}: {e}") from e

    if not pages:
        raise CallFailed(f"{path.name}: the PDF has no pages to send")
    return pages


class XaiClient(LLMClient):
    """Grok, over the OpenAI-compatible /v1/chat/completions endpoint.

    Base URL is https://api.x.ai/v1 and the request is the standard OpenAI
    shape, so the official openai package is pointed at xAI rather than a second
    SDK being added.

    Images go as base64 data URLs. xAI accepts jpg and png only, so a PDF is
    rasterised first and the conversion is recorded on the Call - the audit row
    has to be able to say the model saw page images, not the file itself.
    """

    provider = "xai"

    def __init__(self, model: str | None = None, base_url: str | None = None):
        super().__init__(model)
        self.base_url = base_url or os.environ.get("XAI_BASE_URL") or XAI_BASE_URL

    def _image_parts(self, path: Path) -> tuple[list[dict], str]:
        suffix = path.suffix.lower()
        if suffix in XAI_IMAGE_TYPES:
            data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
            url = f"data:{XAI_IMAGE_TYPES[suffix]};base64,{data}"
            return [{"type": "image_url",
                     "image_url": {"url": url, "detail": "high"}}], ""
        if suffix == ".pdf":
            pages = render_pdf_pages(path)
            parts = [{"type": "image_url",
                      "image_url": {
                          "url": "data:image/png;base64,"
                                 + base64.standard_b64encode(p).decode("ascii"),
                          "detail": "high"}}
                     for p in pages]
            return parts, f"pdf rasterised to {len(pages)} page image(s)"
        raise CallFailed(
            f"{path.name}: {suffix} is not a format xAI accepts "
            f"(jpg, jpeg and png only, or a PDF this client rasterises)")

    def _send(self, document_path, text, max_tokens, corrections):
        try:
            import openai
        except ImportError as e:
            raise CallFailed(
                "the openai package is not installed; the xAI provider uses it "
                "against xAI's OpenAI-compatible endpoint: pip install openai") from e

        client = openai.OpenAI(api_key=api_key(self.provider), base_url=self.base_url)
        parts, transport = self._image_parts(document_path)
        content = parts + [{"type": "text", "text": text}]
        for note in corrections:
            content.append({"type": "text", "text": note})

        response = client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}])
        raw = (response.choices[0].message.content or "") if response.choices else ""
        usage = getattr(response, "usage", None)
        return (raw,
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
                transport)

    def ping(self, max_tokens: int = 16) -> str:
        """One tiny text-only call, to prove the key and the network work.

        Deliberately not part of the interface: it sends no document and asks
        for no JSON, so it tells you about the connection and nothing else.
        """
        try:
            import openai
        except ImportError as e:
            raise CallFailed("the openai package is not installed: "
                             "pip install openai") from e
        client = openai.OpenAI(api_key=api_key(self.provider), base_url=self.base_url)
        response = client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}])
        return (response.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------

CLIENTS = {"anthropic": AnthropicClient, "xai": XaiClient}


def get_client(provider: str | None = None, model: str | None = None) -> LLMClient:
    """The client for the selected provider. Default is anthropic."""
    provider = (provider or provider_name()).strip().lower()
    if provider not in CLIENTS:
        raise UnknownProvider(
            f"unknown provider {provider!r}; choose from {sorted(CLIENTS)}")
    return CLIENTS[provider](model=model)


def ask(document_path: Path, prompt: Prompt, instruction: str,
        model: str | None = None, max_tokens: int = 1500,
        provider: str | None = None) -> Call:
    """Module-level convenience: pick the configured client and call it."""
    return get_client(provider, model).ask(document_path, prompt, instruction,
                                           max_tokens=max_tokens)
