"""
The provider layer: three providers, one set of guarantees.

Nothing here touches a network. Every test drives a FakeClient that records what
it was asked and returns what the test tells it to, which is the only way to
assert the guardrails hold on a reply no real model would reliably produce -
invented flags, unknown field names, prose instead of JSON, a call that fails.

The rule being protected throughout: a failed or malformed call is never a pass.

Run: python -m pytest tests/test_llm_providers.py -q
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import llm_client                                     # noqa: E402
from orchestrator.extractor import (ClaudeExtractor, UnknownExtractedField,  # noqa: E402
                                    get_extractor)
from orchestrator.kb import KnowledgeBase                               # noqa: E402
from orchestrator.quality_checker import (ClaudeVisionQualityChecker,   # noqa: E402
                                          UnknownQualityFlag, get_checker)

SAMPLE = ROOT / "sample_documents" / "WAL-ONB-0001" / "passport_mets_k.jpg"


class FakeClient(llm_client.LLMClient):
    """A client that returns scripted text instead of calling anything.

    It subclasses the real LLMClient, so `ask` - the retry budget, the strict
    JSON rule, the Call record - is the production code path. Only the
    transport is replaced.
    """

    provider = "fake"

    def __init__(self, replies, model="fake-model-1", temperature=None):
        super().__init__(model=model, temperature=temperature)
        self.replies = list(replies)
        self.sent = []

    def _send(self, document_path, text, max_tokens, corrections):
        self.sent.append({"path": Path(document_path), "text": text,
                          "corrections": list(corrections),
                          "temperature": self.temperature})
        if not self.replies:
            raise llm_client.CallFailed("the fake client ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, 11, 22, "fake transport"


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_env():
    """Every setting this module touches, cleared and then restored.

    All of them, not only the ones a given test sets: a leftover temperature or
    key from one test silently changes the next, and a suite that depends on
    its own running order is worse than no suite.
    """
    saved = {k: os.environ.get(k) for k in
             ("LLM_PROVIDER", "WALLESTER_UC4_MODEL", "WALLESTER_UC4_TEMPERATURE",
              "XAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
              "XAI_BASE_URL", "GROQ_BASE_URL")}
    for k in saved:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v


def test_the_default_provider_is_anthropic_so_nothing_changes_by_accident(clean_env):
    assert llm_client.provider_name() == "anthropic"
    assert llm_client.model_name() == "claude-sonnet-5"
    assert llm_client.get_client().provider == "anthropic"


def test_the_provider_is_a_setting(clean_env):
    os.environ["LLM_PROVIDER"] = "xai"
    assert llm_client.provider_name() == "xai"
    client = llm_client.get_client()
    assert isinstance(client, llm_client.XaiClient)
    assert client.model == "grok-4.7", "the vision model name is a setting with a default"
    assert client.base_url == "https://api.x.ai/v1"


def test_the_model_name_is_a_setting_for_either_provider(clean_env):
    os.environ["LLM_PROVIDER"] = "xai"
    os.environ["WALLESTER_UC4_MODEL"] = "grok-some-other-model"
    assert llm_client.get_client().model == "grok-some-other-model"


def test_an_unknown_provider_is_refused_rather_than_guessed(clean_env):
    os.environ["LLM_PROVIDER"] = "openai"
    with pytest.raises(llm_client.UnknownProvider):
        llm_client.provider_name()


def test_each_provider_reads_only_its_own_key_from_the_environment(clean_env):
    with pytest.raises(llm_client.MissingApiKey) as e:
        llm_client.api_key("xai")
    assert "XAI_API_KEY" in str(e.value)

    os.environ["XAI_API_KEY"] = "test-value-not-a-real-key"
    assert llm_client.api_key("xai") == "test-value-not-a-real-key"
    # the xai key must not satisfy anthropic, or a misconfiguration would call
    # the wrong provider with the wrong credential
    with pytest.raises(llm_client.MissingApiKey):
        llm_client.api_key("anthropic")


def test_no_key_value_is_committed_anywhere_in_the_source():
    """No provider's key prefix appears in any file.

    The needles are assembled so that this file does not trip over its own
    text. One per provider, because the point of supporting three is that three
    different credentials can end up on one developer's machine.
    """
    needles = ["xai" + "-", "gsk" + "_", "sk" + "-ant-"]
    for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.txt")) + \
            list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.json")):
        if path.name in (Path(__file__).name, "test_live_mode.py"):
            continue                              # both assemble their own needles
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for needle in needles:
            assert needle not in text, f"{path} looks like it contains an API key"


# ---------------------------------------------------------------------------
# The guarantees, identical for every provider
# ---------------------------------------------------------------------------

def test_strict_json_only_and_one_retry_then_failure():
    client = FakeClient(["not json at all", "still not json"])
    prompt = llm_client.load_prompt("quality_check", "v1")
    with pytest.raises(llm_client.CallFailed) as e:
        client.ask(SAMPLE, prompt, "go")
    assert "after 2 attempts" in str(e.value)
    assert len(client.sent) == 2, "one call, one retry, and no more"
    assert client.sent[1]["corrections"], "the retry says what was wrong with the reply"


def test_the_retry_is_allowed_to_succeed():
    client = FakeClient(["waffle", '{"flags": [], "confidence": 0.9}'])
    call = client.ask(SAMPLE, llm_client.load_prompt("quality_check", "v1"), "go")
    assert call.attempts == 2 and call.data["flags"] == []


def test_a_fenced_object_is_accepted_but_nothing_looser():
    client = FakeClient(['```json\n{"flags": []}\n```'])
    assert client.ask(SAMPLE, llm_client.load_prompt("quality_check", "v1"),
                      "go").data == {"flags": []}
    for bad in ('[1, 2]', '"a string"', 'Here you go: {"a": 1}'):
        with pytest.raises(llm_client.CallFailed):
            llm_client.parse_strict_json(bad)


def test_a_network_failure_surfaces_as_call_failed_and_is_not_retried_into_a_pass():
    client = FakeClient([ConnectionError("proxy said no")])
    with pytest.raises(llm_client.CallFailed) as e:
        client.ask(SAMPLE, llm_client.load_prompt("quality_check", "v1"), "go")
    assert "the API call failed" in str(e.value)
    assert len(client.sent) == 1, "a transport failure is not a format problem to retry"


def test_the_audit_note_names_provider_model_prompt_latency_and_tokens():
    client = FakeClient(['{"flags": []}'])
    call = client.ask(SAMPLE, llm_client.load_prompt("quality_check", "v1"), "go")
    note = call.audit_note()
    for piece in ("provider=fake", "model=fake-model-1", "prompt=quality_check_v1",
                  "temperature=0", "attempts=1", "latency=", "tokens in/out=11/22"):
        assert piece in note
    assert "fake transport" in note, "how the file was sent belongs in the audit row"


def test_temperature_defaults_to_zero_and_is_recorded(clean_env):
    """An evaluation run must measure the model, not the sampler."""
    assert llm_client.default_temperature() == 0.0
    for provider in ("anthropic", "xai", "groq"):
        assert llm_client.get_client(provider).temperature == 0.0

    client = FakeClient(['{"flags": []}'])
    call = client.ask(SAMPLE, llm_client.load_prompt("quality_check", "v1"), "go")
    assert call.temperature == 0.0
    assert client.sent[0]["temperature"] == 0.0, "the transport is given the value"
    assert "temperature=0" in call.audit_note()


def test_a_non_default_temperature_is_carried_and_audited(clean_env):
    os.environ["WALLESTER_UC4_TEMPERATURE"] = "0.7"
    assert llm_client.default_temperature() == 0.7
    call = FakeClient(['{"flags": []}']).ask(
        SAMPLE, llm_client.load_prompt("quality_check", "v1"), "go")
    assert call.temperature == 0.7
    assert "temperature=0.7" in call.audit_note(),         "a verdict reached at 0.7 is a different claim from one reached at 0"


def test_a_temperature_that_is_not_a_number_is_refused(clean_env):
    os.environ["WALLESTER_UC4_TEMPERATURE"] = "warm"
    with pytest.raises(ValueError):
        llm_client.default_temperature()


# ---------------------------------------------------------------------------
# Allowed-value validation, through the real checkers
# ---------------------------------------------------------------------------

def test_an_invented_quality_flag_discards_the_verdict():
    checker = ClaudeVisionQualityChecker(
        allow_unready=True,
        client=FakeClient(['{"flags": ["looks_dodgy"], "confidence": 0.9}']))
    with pytest.raises(UnknownQualityFlag):
        checker.check({"file_path": str(SAMPLE), "file_name": SAMPLE.name,
                       "document_type": "id_document"})


def test_a_flag_from_the_vocabulary_is_accepted():
    checker = ClaudeVisionQualityChecker(
        allow_unready=True,
        client=FakeClient(['{"flags": ["blurred_unreadable"], "confidence": 0.8}']))
    verdict = checker.check({"file_path": str(SAMPLE), "file_name": SAMPLE.name,
                             "document_type": "id_document"})
    assert verdict.flags == ["blurred_unreadable"]
    assert checker.last_call.provider == "fake"


def test_a_date_that_is_not_a_date_discards_the_verdict():
    checker = ClaudeVisionQualityChecker(
        allow_unready=True,
        client=FakeClient(['{"flags": [], "confidence": 1.0, "expiry_date": "soon"}']))
    with pytest.raises(ValueError):
        checker.check({"file_path": str(SAMPLE), "file_name": SAMPLE.name,
                       "document_type": "id_document"})


def test_an_unknown_field_name_discards_the_extraction():
    kb = KnowledgeBase()
    extractor = ClaudeExtractor(
        allow_unready=True,
        client=FakeClient(['{"fields": [{"name": "favourite_colour", "value": "blue",'
                           ' "confidence": 0.99}]}']))
    with pytest.raises(UnknownExtractedField):
        extractor.extract({"file_path": str(SAMPLE), "file_name": SAMPLE.name,
                           "document_type": "id_document"},
                          kb.fields_for("id_document"))


def test_a_known_field_name_is_kept_with_its_confidence():
    kb = KnowledgeBase()
    name = kb.fields_for("id_document")[0]["field_name"]
    extractor = ClaudeExtractor(
        allow_unready=True,
        client=FakeClient(['{"fields": [{"name": "%s", "value": "x", '
                           '"confidence": 0.42}]}' % name]))
    result = extractor.extract({"file_path": str(SAMPLE), "file_name": SAMPLE.name,
                                "document_type": "id_document"},
                               kb.fields_for("id_document"))
    assert [(f.name, f.confidence) for f in result.fields] == [(name, 0.42)]


def test_the_version_string_names_the_provider_and_the_prompt():
    checker = ClaudeVisionQualityChecker(allow_unready=True, client=FakeClient([]))
    assert checker.version == "fake:fake-model-1/quality_check_v1"


# ---------------------------------------------------------------------------
# xAI transport: images only, so a PDF has to be rasterised
# ---------------------------------------------------------------------------

def test_xai_sends_an_image_directly(clean_env):
    parts, transport = llm_client.XaiClient()._image_parts(SAMPLE)
    assert len(parts) == 1 and parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert transport == "", "a jpg needs no conversion and should claim none"


def test_xai_rasterises_a_pdf_and_says_so(clean_env):
    pdf = ROOT / "sample_documents" / "WAL-ONB-0001" / "ee_fie_registry_extract_mets.pdf"
    if not pdf.exists():
        pytest.skip("sample documents not generated")
    parts, transport = llm_client.XaiClient()._image_parts(pdf)
    assert parts and all(p["image_url"]["url"].startswith("data:image/png;base64,")
                         for p in parts)
    assert "rasterised" in transport, \
        "the audit row must not imply the model read the PDF itself"


def test_xai_refuses_a_format_it_cannot_send(clean_env, tmp_path):
    odd = tmp_path / "scan.tiff"
    odd.write_bytes(b"not really a tiff")
    with pytest.raises(llm_client.CallFailed) as e:
        llm_client.XaiClient()._image_parts(odd)
    assert "not a format xAI accepts" in str(e.value)


# ---------------------------------------------------------------------------
# Groq - a different company from xAI, despite the name
# ---------------------------------------------------------------------------

def test_groq_is_its_own_provider_with_its_own_key_and_endpoint(clean_env):
    os.environ["LLM_PROVIDER"] = "groq"
    client = llm_client.get_client()
    assert isinstance(client, llm_client.GroqClient)
    assert client.base_url == "https://api.groq.com/openai/v1"
    assert client.model == "qwen/qwen3.8-27b"

    with pytest.raises(llm_client.MissingApiKey) as e:
        llm_client.api_key("groq")
    assert "GROQ_API_KEY" in str(e.value)

    # an xAI key must not satisfy Groq. The names are one letter apart and the
    # consoles are different companies; silently accepting either would send a
    # credential to the wrong vendor.
    os.environ["XAI_API_KEY"] = "test-value-not-a-real-key"
    with pytest.raises(llm_client.MissingApiKey):
        llm_client.api_key("groq")


def test_groq_sends_a_short_pdf_whole_and_records_that_it_did(clean_env):
    pdf = ROOT / "sample_documents" / "WAL-ONB-0001" / "ee_fie_registry_extract_mets.pdf"
    if not pdf.exists():
        pytest.skip("sample documents not generated")
    parts, transport = llm_client.GroqClient()._image_parts(pdf)
    assert len(parts) <= 3, "Groq accepts at most three images per request"
    assert transport.startswith("whole document"), \
        "the audit row records that the whole document was sent, not just that it was converted"


def _stub_pdf(tmp_path, pages):
    """A real multi-page PDF, built rather than faked, so the page count is read
    by the same library the client uses."""
    from PIL import Image
    path = tmp_path / f"{pages}page.pdf"
    sheets = [Image.new("RGB", (400, 560), "white") for _ in range(pages)]
    sheets[0].save(path, "PDF", save_all=True, append_images=sheets[1:])
    return path


def test_a_pdf_longer_than_one_request_is_refused_not_truncated(clean_env, tmp_path):
    """The rule: never silently drop pages.

    Groq carries three images per request. A five-page document therefore
    cannot be assessed in one call, and the client says so instead of reaching
    a verdict on the first three pages.
    """
    pdf = _stub_pdf(tmp_path, 5)
    assert llm_client.pdf_page_count(pdf) == 5
    with pytest.raises(llm_client.CallFailed) as e:
        llm_client.GroqClient()._image_parts(pdf)
    message = str(e.value)
    assert "could not be fully assessed" in message
    assert "5 pages" in message and "3 page image(s)" in message
    assert pdf.name in message, "the hold has to name the document"


def test_the_page_limit_is_the_provider_s_own(clean_env, tmp_path):
    """Four pages is fine for xAI and Anthropic, too many for Groq."""
    pdf = _stub_pdf(tmp_path, 4)
    parts, transport = llm_client.XaiClient()._image_parts(pdf)
    assert len(parts) == 4 and transport.startswith("whole document")
    with pytest.raises(llm_client.CallFailed):
        llm_client.GroqClient()._image_parts(pdf)


def test_a_refused_pdf_becomes_a_manual_review_hold_not_a_clean_verdict(clean_env, tmp_path):
    """The refusal reaches the caller as CallFailed, which the quality step
    already turns into a hold - the same path a network failure takes."""
    pdf = _stub_pdf(tmp_path, 9)
    checker = ClaudeVisionQualityChecker(
        allow_unready=True, client=llm_client.GroqClient())
    with pytest.raises(llm_client.CallFailed):
        checker.check({"file_path": str(pdf), "file_name": pdf.name,
                       "document_type": "registry_extract"})


def test_an_oversized_image_is_named_rather_than_sent(clean_env, tmp_path):
    big = tmp_path / "huge.png"
    big.write_bytes(b"\x89PNG" + b"\0" * (llm_client.MAX_IMAGE_BYTES + 1))
    with pytest.raises(llm_client.CallFailed) as e:
        llm_client.GroqClient()._image_parts(big)
    assert "huge.png" in str(e.value), \
        "an oversized payload must not surface as an unexplained 400"


def _rate_limited(headers: dict):
    """A real openai.RateLimitError, built the way the SDK builds one."""
    import httpx
    import openai
    response = httpx.Response(429, headers=headers,
                              request=httpx.Request("POST", "https://example.invalid"))
    return openai.RateLimitError("rate limited", response=response, body=None)


def test_a_rate_limit_is_waited_out_not_counted_as_a_bad_answer(clean_env, monkeypatch):
    """429 is the provider asking for less traffic, not a malformed reply.

    It must not spend the format-retry budget, and it must not become a pass.
    """
    calls = {"n": 0}
    slept = []
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: slept.append(s))

    client = llm_client.GroqClient()

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _rate_limited({"x-ratelimit-reset-tokens": "0.01s"})
        return "fine"

    assert client._with_backpressure(flaky) == "fine"
    assert calls["n"] == 3, "it retried rather than giving up or passing"
    assert slept and all(s > 0 for s in slept), "it waited between attempts"


def test_a_rate_limit_that_never_clears_becomes_call_failed(clean_env, monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm_client, "RATE_LIMIT_ATTEMPTS", 3)

    def always():
        raise _rate_limited({})

    with pytest.raises(llm_client.CallFailed) as e:
        llm_client.GroqClient()._with_backpressure(always)
    assert "rate limited" in str(e.value), \
        "a document nobody could look at goes to an analyst, it does not pass"


def test_the_wait_is_capped(clean_env):
    error = _rate_limited({"retry-after": "100000"})
    wait = llm_client.OpenAICompatibleClient._retry_after(error, 1)
    assert wait == llm_client.MAX_RATE_LIMIT_WAIT, "a demo that hangs is worse than one that stops"


def test_both_openai_compatible_providers_share_one_implementation():
    """The guarantees are shared code, not two copies that can drift apart."""
    for cls in (llm_client.XaiClient, llm_client.GroqClient):
        assert issubclass(cls, llm_client.OpenAICompatibleClient)
        assert cls._send is llm_client.OpenAICompatibleClient._send
        assert cls.ask is llm_client.LLMClient.ask


def test_anthropic_sends_a_pdf_as_a_document_unchanged(clean_env):
    pdf = ROOT / "sample_documents" / "WAL-ONB-0001" / "ee_fie_registry_extract_mets.pdf"
    if not pdf.exists():
        pytest.skip("sample documents not generated")
    block = llm_client.AnthropicClient()._block(pdf)
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"


# ---------------------------------------------------------------------------
# Still opt-in, whichever provider is selected
# ---------------------------------------------------------------------------

def test_live_mode_stays_off_for_both_providers(clean_env):
    for provider in ("anthropic", "xai"):
        os.environ["LLM_PROVIDER"] = provider
        with pytest.raises(Exception) as e:
            get_checker("live")
        assert "LIVE MODE NOT CONFIGURED" in str(e.value)
        with pytest.raises(Exception):
            get_extractor("live")
    assert get_checker().mode == "mock" and get_extractor().mode == "mock"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
