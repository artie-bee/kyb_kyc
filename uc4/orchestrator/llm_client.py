"""
The one place this project talks to a language model, whichever one that is.

Everything the live checkers share lives here: the key, the model setting, the
versioned prompt files, strict JSON parsing with a single retry, and the timing
and token counts that go into the audit row.

    LLM_PROVIDER              anthropic (default) | xai | groq
    ANTHROPIC_API_KEY         read from the environment, never hard-coded
    XAI_API_KEY               read from the environment, never hard-coded
    GROQ_API_KEY              read from the environment, never hard-coded
    WALLESTER_UC4_MODEL       optional model override, for whichever provider
    WALLESTER_UC4_TEMPERATURE optional; defaults to 0

Nothing here decides anything. It fetches an answer and hands it back; the
caller validates it against the KB and decides what to do when it does not
parse. A failed call is never a pass - that rule is enforced by the callers, and
`CallFailed` is what they act on.

The providers differ in one way that matters and the difference is not
cosmetic. Anthropic accepts a PDF as a document and reads it natively. xAI and
Groq take jpg and png only, so a PDF has to be rasterised to page images before
it can be sent at all, and Groq accepts at most three images in one request.
That conversion is recorded on the Call and reaches the audit row, because "the
model read the PDF" and "the model read a picture of the first three pages of
the PDF" are different claims and only one of them can be true of a given call.

Groq is not xAI. The names are one letter apart and both serve an
OpenAI-compatible endpoint, but they are separate companies with separate keys,
and a key for one is rejected by the other.
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
    # The vision-capable model for each provider. A setting, not a constant in
    # the calling code: model names move, and the audit row has to be able to
    # say which one actually ran.
    "xai": "grok-4.7",
    "groq": "qwen/qwen3.8-27b",
}
API_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY",
               "xai": "XAI_API_KEY",
               "groq": "GROQ_API_KEY"}

XAI_BASE_URL = "https://api.x.ai/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# What each provider will accept directly.
MEDIA_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
XAI_IMAGE_TYPES = IMAGE_TYPES          # kept under the old name for callers

# A rasterised PDF is sent as page images. Four pages is more than any document
# in this set needs and keeps a runaway file from turning into a large bill.
# Providers that accept fewer images per request lower this for themselves.
PDF_MAX_PAGES = 4
PDF_RENDER_SCALE = 2.0            # roughly 144 dpi against a 72 dpi page box

# Base64 payloads are rejected above a few megabytes by most providers, and an
# oversized request fails as a 400 that reads like a bad key. Checking here
# turns that into a sentence naming the file.
MAX_IMAGE_BYTES = 4 * 1024 * 1024

# Sampling temperature for every live call. Zero by default: the same document
# asked the same question should give the same answer, or an evaluation run
# measures the sampler as much as the model. It is a setting rather than a
# constant because a future step might want a less literal reading, and the
# value used is written into the audit row either way - a verdict reached at
# temperature 1.0 is a different claim from one reached at 0.
DEFAULT_TEMPERATURE = 0.0

# A 429 is backpressure, not an answer, so it is waited out rather than counted
# against the format-retry budget. Bounded so a stalled provider stops the case
# instead of hanging it.
RATE_LIMIT_ATTEMPTS = 6
MAX_RATE_LIMIT_WAIT = 90.0        # seconds, per wait


def default_temperature() -> float:
    """The temperature a client uses when its caller does not name one."""
    raw = os.environ.get("WALLESTER_UC4_TEMPERATURE")
    if raw is None or not raw.strip():
        return DEFAULT_TEMPERATURE
    try:
        return float(raw)
    except ValueError:
        raise ValueError(
            f"WALLESTER_UC4_TEMPERATURE is {raw!r}, which is not a number")

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
    temperature: float = DEFAULT_TEMPERATURE

    def audit_note(self) -> str:
        note = (f"provider={self.provider}; model={self.model}; "
                f"prompt={self.prompt_version}; temperature={self.temperature:g}; "
                f"attempts={self.attempts}; latency={self.latency_ms}ms; "
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

    def __init__(self, model: str | None = None, temperature: float | None = None):
        self.model = model or model_name(self.provider)
        self.temperature = (default_temperature() if temperature is None
                            else float(temperature))

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
                        provider=self.provider, temperature=self.temperature,
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
            model=self.model, max_tokens=max_tokens, temperature=self.temperature,
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

def pdf_page_count(path: Path) -> int:
    try:
        import pypdfium2
    except ImportError as e:
        raise CallFailed(
            f"{path.name} is a PDF, and this provider can only send images. "
            f"Rendering it needs pypdfium2: pip install pypdfium2") from e
    try:
        pdf = pypdfium2.PdfDocument(str(path))
        total = len(pdf)
        pdf.close()
        return total
    except Exception as e:
        raise CallFailed(f"{path.name}: the PDF could not be opened: "
                         f"{type(e).__name__}: {e}") from e


def render_pdf_pages(path: Path, max_pages: int = PDF_MAX_PAGES) -> list[bytes]:
    """A PDF as PNG page images, for providers whose vision input takes images.

    Refuses a document with more pages than one request can carry, rather than
    rendering the first few and letting the caller believe it saw the whole
    thing. A verdict reached on page 1 of a five-page document is not a verdict
    on that document, and the difference is invisible downstream once the
    answer is written to a row. The caller turns this into a manual-review hold,
    which is the honest outcome: nobody has assessed the document yet.

    Also raises rather than returning an empty list: a document nobody could
    render has not been read either.
    """
    total = pdf_page_count(path)
    if total == 0:
        raise CallFailed(f"{path.name}: the PDF has no pages to send")
    if total > max_pages:
        raise CallFailed(
            f"{path.name} could not be fully assessed: it has {total} pages and "
            f"one request carries at most {max_pages} page image(s). No verdict "
            f"was reached, because a verdict on the first {max_pages} page(s) "
            f"would not be a verdict on this document")

    try:
        import pypdfium2
        pdf = pypdfium2.PdfDocument(str(path))
        pages = []
        for index in range(total):
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
    return pages


class OpenAICompatibleClient(LLMClient):
    """Any provider that serves OpenAI's /v1/chat/completions shape.

    xAI and Groq both do, so the official openai package is pointed at whichever
    base URL is wanted rather than a second SDK being added for each one. A
    subclass supplies the base URL, the default model and its own image limits;
    everything else here is shared, which is the point.

    Neither provider accepts a PDF. A PDF is therefore rasterised to page images
    first, and the conversion is recorded on the Call - the audit row must not
    imply the model read the file when what it saw was a picture of it.
    """

    provider = "openai_compatible"
    base_url_default = ""
    base_url_env = ""
    max_images = PDF_MAX_PAGES
    label = "this provider"

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 temperature: float | None = None):
        super().__init__(model, temperature)
        self.base_url = (base_url
                         or (os.environ.get(self.base_url_env) if self.base_url_env else None)
                         or self.base_url_default)

    # -- transport ---------------------------------------------------------
    def _client(self):
        try:
            import openai
        except ImportError as e:
            raise CallFailed(
                f"the openai package is not installed; the {self.provider} provider "
                f"uses it against an OpenAI-compatible endpoint: "
                f"pip install openai") from e
        return openai.OpenAI(api_key=api_key(self.provider), base_url=self.base_url)

    def _data_url(self, media_type: str, blob: bytes, name: str) -> str:
        if len(blob) > MAX_IMAGE_BYTES:
            raise CallFailed(
                f"{name}: the image is {len(blob) // 1024}KB, over the "
                f"{MAX_IMAGE_BYTES // 1024}KB this client will send as base64. "
                f"Lower PDF_RENDER_SCALE or send a smaller file")
        return f"data:{media_type};base64," + base64.standard_b64encode(blob).decode("ascii")

    def _image_parts(self, path: Path) -> tuple[list[dict], str]:
        suffix = path.suffix.lower()
        if suffix in IMAGE_TYPES:
            url = self._data_url(IMAGE_TYPES[suffix], path.read_bytes(), path.name)
            return [{"type": "image_url",
                     "image_url": {"url": url, "detail": "high"}}], ""
        if suffix == ".pdf":
            # Raises if the document is longer than one request can carry, so
            # the only PDFs that reach the model are ones sent in full.
            pages = render_pdf_pages(path, max_pages=self.max_images)
            parts = [{"type": "image_url",
                      "image_url": {"url": self._data_url("image/png", page, path.name),
                                    "detail": "high"}}
                     for page in pages]
            return parts, (f"whole document: pdf rasterised to {len(pages)} "
                           f"page image(s) in one request")
        raise CallFailed(
            f"{path.name}: {suffix} is not a format {self.label} accepts "
            f"(jpg, jpeg and png only, or a PDF this client rasterises)")

    def _send(self, document_path, text, max_tokens, corrections):
        parts, transport = self._image_parts(document_path)
        content = parts + [{"type": "text", "text": text}]
        for note in corrections:
            content.append({"type": "text", "text": note})

        response = self._with_backpressure(
            lambda: self._client().chat.completions.create(
                model=self.model, max_tokens=max_tokens, temperature=self.temperature,
                messages=[{"role": "user", "content": content}]))
        raw = (response.choices[0].message.content or "") if response.choices else ""
        usage = getattr(response, "usage", None)
        return (raw,
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
                transport)

    def _with_backpressure(self, send):
        """Wait out a rate limit rather than treating it as an answer.

        A 429 is not a bad reply, it is the provider asking for less traffic, so
        it does not spend the format-retry budget: `ask` gets one call, one
        retry on unusable JSON, and that is unchanged. This sits underneath,
        waiting the interval the provider names and trying again.

        Bounded, because a demo that hangs is worse than one that stops: after
        RATE_LIMIT_ATTEMPTS the caller gets a CallFailed and the document goes
        to an analyst, which is the correct outcome for a document nobody has
        managed to look at.
        """
        try:
            import openai
        except ImportError:                             # handled in _client()
            return send()

        for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
            try:
                return send()
            except openai.RateLimitError as e:
                if attempt == RATE_LIMIT_ATTEMPTS:
                    raise CallFailed(
                        f"rate limited by {self.provider} after "
                        f"{RATE_LIMIT_ATTEMPTS} attempts: {e}") from e
                time.sleep(self._retry_after(e, attempt))
        raise CallFailed("unreachable")               # pragma: no cover

    @staticmethod
    def _retry_after(error, attempt: int) -> float:
        """How long the provider asked us to wait, or a backoff if it did not."""
        headers = getattr(getattr(error, "response", None), "headers", None) or {}
        for name in ("retry-after", "x-ratelimit-reset-tokens",
                     "x-ratelimit-reset-requests"):
            raw = headers.get(name)
            if not raw:
                continue
            try:                                       # "20.557s" or "13"
                return min(MAX_RATE_LIMIT_WAIT, float(str(raw).rstrip("s")) + 1.0)
            except ValueError:
                continue
        return min(MAX_RATE_LIMIT_WAIT, 2.0 ** attempt)

    def ping(self, max_tokens: int = 16) -> str:
        """One tiny text-only call, to prove the key and the network work.

        Deliberately not part of the interface: it sends no document and asks
        for no JSON, so it tells you about the connection and nothing else.
        """
        response = self._client().chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}])
        return (response.choices[0].message.content or "").strip()


class XaiClient(OpenAICompatibleClient):
    """Grok, at https://api.x.ai/v1. Images are jpg and png only."""

    provider = "xai"
    label = "xAI"
    base_url_default = XAI_BASE_URL
    base_url_env = "XAI_BASE_URL"


class GroqClient(OpenAICompatibleClient):
    """Groq, at https://api.groq.com/openai/v1.

    Not the same thing as xAI's Grok, despite the name: Groq runs open models
    on its own hardware. Only its multimodal model takes images, it accepts at
    most three per request, and each image costs a flat 2,048 input tokens - so
    a rasterised PDF is capped at three pages here rather than four.
    """

    provider = "groq"
    label = "Groq"
    base_url_default = GROQ_BASE_URL
    base_url_env = "GROQ_BASE_URL"
    max_images = 3


# ---------------------------------------------------------------------------

CLIENTS = {"anthropic": AnthropicClient, "xai": XaiClient, "groq": GroqClient}


def get_client(provider: str | None = None, model: str | None = None,
               temperature: float | None = None) -> LLMClient:
    """The client for the selected provider. Default is anthropic."""
    provider = (provider or provider_name()).strip().lower()
    if provider not in CLIENTS:
        raise UnknownProvider(
            f"unknown provider {provider!r}; choose from {sorted(CLIENTS)}")
    return CLIENTS[provider](model=model, temperature=temperature)


def ask(document_path: Path, prompt: Prompt, instruction: str,
        model: str | None = None, max_tokens: int = 1500,
        provider: str | None = None) -> Call:
    """Module-level convenience: pick the configured client and call it."""
    return get_client(provider, model).ask(document_path, prompt, instruction,
                                           max_tokens=max_tokens)
