"""
Kept so that `from . import claude_client` still means what it used to.

The provider-neutral module is llm_client.py. This one re-exports the shared
pieces and binds `ask` to whichever provider LLM_PROVIDER selects, so callers
written before there was a choice keep working and get the choice for free.

New code should import orchestrator.llm_client directly. Nothing here adds
behaviour; every name below is the same object as in llm_client.

    LIVE MODE IS STILL OPT-IN. Selecting it while LIVE_MODE_READY is False
    stops with a message rather than attempting a call.
"""

from .llm_client import (  # noqa: F401
    MAX_ATTEMPTS,
    PROMPTS,
    Call,
    CallFailed,
    MissingApiKey,
    Prompt,
    UnknownProvider,
    api_key,
    ask,
    get_client,
    load_prompt,
    model_name,
    parse_strict_json,
    provider_name,
)
from .llm_client import DEFAULT_MODELS, MEDIA_TYPES  # noqa: F401

# The Anthropic default, under the name the original module used for it.
DEFAULT_MODEL = DEFAULT_MODELS["anthropic"]
