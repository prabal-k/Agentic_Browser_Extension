"""Tests for LangSmith trace redaction — secrets must never reach a trace.

Regression guard for the leak where LeadState/WorkerState.api_keys (carrying the
raw openai_api_key) was uploaded verbatim to LangSmith because LangGraph traces
the full node state.
"""

import json

from agent_core.observability import (
    _is_secret_key,
    install_trace_redaction,
    redact_secrets,
)


def test_masks_api_keys_dict():
    state = {"api_keys": {"openai_api_key": "sk-proj-REAL", "ollama_base_url": "http://x"}}
    assert redact_secrets(state)["api_keys"] == "[REDACTED]"


def test_masks_all_secret_key_variants():
    payload = {
        "openai_api_key": "sk-1",
        "groq_api_key": "gsk_2",
        "openrouter_api_key": "or_3",
        "stored_credentials": {"password": "p"},
        "vault_token": "t",
        "authorization": "Bearer z",
        "some_secret": "s",
    }
    red = redact_secrets(payload)
    assert all(v == "[REDACTED]" for v in red.values())


def test_preserves_non_secret_values():
    state = {
        "model_name": "gpt-4o-mini",
        "original_goal": "Order boots",
        "page_context": {"url": "https://daraz.com", "title": "Daraz"},
    }
    red = redact_secrets(state)
    assert red["model_name"] == "gpt-4o-mini"
    assert red["original_goal"] == "Order boots"
    assert red["page_context"]["url"] == "https://daraz.com"


def test_recurses_into_nested_dicts_and_lists():
    payload = {"messages": [{"content": "hi", "meta": {"auth_header": "secret"}}]}
    red = redact_secrets(payload)
    assert red["messages"][0]["content"] == "hi"
    assert red["messages"][0]["meta"]["auth_header"] == "[REDACTED]"


def test_does_not_mutate_original():
    state = {"api_keys": {"openai_api_key": "sk-proj-REAL"}}
    redact_secrets(state)
    assert state["api_keys"]["openai_api_key"] == "sk-proj-REAL"


def test_no_secret_survives_serialization():
    state = {
        "api_keys": {"openai_api_key": "sk-proj-LEAKME", "groq_api_key": "gsk_LEAK"},
        "stored_credentials": {"password": "hunter2"},
        "vault_token": "abc123",
    }
    blob = json.dumps(redact_secrets(state))
    for leak in ("sk-proj-LEAKME", "gsk_LEAK", "hunter2", "abc123"):
        assert leak not in blob


def test_is_secret_key_matching():
    for k in ("openai_api_key", "api_keys", "GROQ_API_KEY", "vault_token",
              "stored_credentials", "authorization", "my_password"):
        assert _is_secret_key(k)
    for k in ("model_name", "url", "goal", "title", "elements"):
        assert not _is_secret_key(k)
    # non-string keys never trip it
    assert not _is_secret_key(3)


def test_install_wires_redactors_on_client():
    # force=True installs regardless of the tracing env (conftest turns it off).
    install_trace_redaction(force=True)
    from langsmith.run_trees import get_cached_client

    client = get_cached_client()
    assert client._hide_run_inputs({"api_keys": {"openai_api_key": "sk-x"}}) == {
        "api_keys": "[REDACTED]"
    }
    assert client._hide_run_outputs({"vault_token": "t"}) == {"vault_token": "[REDACTED]"}
