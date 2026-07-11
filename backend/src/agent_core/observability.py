"""LangSmith trace redaction — keep secrets out of uploaded traces.

The agent threads the user's API keys through the graph STATE (LeadState /
WorkerState `api_keys` field) so every node can build an LLM client. LangGraph
traces each node's input and output — which IS the full state — so without
redaction the raw `openai_api_key` (sk-proj-...) is uploaded to LangSmith on
every run. That is a real secret leak: it shows up verbatim in a run's
Output.Fields.api_keys.openai_api_key.

Fix: install process-wide input/output redactors on the cached LangSmith
client. That client is a lazy module singleton (langsmith.run_trees._CLIENT)
shared by every LangChain/LangGraph tracer, so redacting there covers all runs
— lead, worker, and per-LLM — regardless of where a secret sits in the payload.
The redactors only transform what is UPLOADED to LangSmith; the live graph
state is untouched, so the LLM still receives the real key.
"""

import os

import structlog

logger = structlog.get_logger("observability")

# A key whose name contains any of these (case-insensitive) has its VALUE
# replaced wholesale. Substring match, so "openai_api_key", "api_keys",
# "groq_api_key", "openrouter_api_key", "vault_token" and "stored_credentials"
# all trip it without needing an exact allowlist.
_SECRET_HINTS = (
    "api_key", "apikey", "secret", "password", "passwd",
    "token", "credential", "authorization", "auth_header",
)

_REDACTED = "[REDACTED]"


def _is_secret_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    k = key.lower()
    return any(hint in k for hint in _SECRET_HINTS)


def redact_secrets(obj):
    """Return a copy of `obj` with values under secret-looking keys masked.

    Recurses through dicts and lists. Never mutates the input — LangSmith hands
    us the live run payload. Non-container leaves pass through untouched.
    """
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if _is_secret_key(k) else redact_secrets(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact_secrets(v) for v in obj]
    return obj


def _tracing_enabled() -> bool:
    truthy = ("1", "true", "yes", "on")
    return (
        os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in truthy
        or os.environ.get("LANGSMITH_TRACING", "").lower() in truthy
    )


_installed = False


def install_trace_redaction(force: bool = False) -> None:
    """Attach secret redactors to the shared LangSmith tracing client.

    Idempotent. No-op when tracing is disabled (nothing is uploaded, so there is
    nothing to redact and we avoid constructing a client needlessly). Pass
    force=True to wire the redactors regardless of the tracing env — useful in
    tests.

    Sets the redactors both via the cached-client constructor kwargs (when the
    client has not been built yet) and directly on the instance (when it already
    exists), so ordering relative to the first trace never matters.
    """
    global _installed
    if _installed:
        return
    if not force and not _tracing_enabled():
        return
    try:
        from langsmith.run_trees import get_cached_client

        client = get_cached_client(
            hide_inputs=redact_secrets, hide_outputs=redact_secrets
        )
        # If the client was already constructed before this call, the kwargs
        # above were ignored (get_cached_client only applies them on first
        # build). Set the private hooks directly so both paths end with the
        # redactors attached.
        client._hide_inputs = redact_secrets
        client._hide_outputs = redact_secrets
        _installed = True
        logger.info("langsmith_trace_redaction_installed")
    except Exception as exc:  # never let observability wiring break startup
        logger.warning("langsmith_trace_redaction_failed", error=str(exc)[:120])
