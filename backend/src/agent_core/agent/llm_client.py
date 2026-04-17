"""LLM client abstraction — supports Ollama, OpenAI, and Groq.

Provides a unified interface for creating LLM instances that can be
swapped based on configuration. All support tool calling / function calling
which is critical for structured action output.

Design decisions:
- Factory pattern: get_llm() returns the right client based on model name
- Tool binding: All LLMs are bound with browser tools for structured output
- Streaming: All clients support token-by-token streaming
- Temperature: Low (0.1) for action planning, moderate (0.4) for reasoning
- Key resolution: session keys (from KeyVault) > .env keys > error
"""

import os
from enum import Enum

from langchain_core.language_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from agent_core.config import settings
from agent_core.tools.browser_tools import BROWSER_TOOLS, TOOL_GROUPS


class LLMProvider(str, Enum):
    """Supported LLM providers."""
    OLLAMA = "ollama"
    OPENAI = "openai"
    GROQ = "groq"
    OPENROUTER = "openrouter"


# Groq models use OpenAI-compatible API
GROQ_PATTERNS = ("llama-", "mixtral-", "gemma-", "deepseek-", "groq/")


def detect_provider(model_name: str) -> LLMProvider:
    """Detect which provider to use based on model name.

    Convention:
    - Names containing '/' (org/model pattern) → OpenRouter
    - Names containing 'gpt', 'o1', 'o3' → OpenAI
    - Names matching Groq patterns → Groq
    - Everything else → Ollama (local models)
    """
    name_lower = model_name.lower()

    # OpenRouter: models use org/model format (e.g., meta-llama/llama-3.3-70b-instruct:free)
    openrouter_orgs = ("meta-llama/", "mistralai/", "nvidia/", "qwen/", "google/",
                       "anthropic/", "openai/gpt-oss", "z-ai/", "arcee-ai/",
                       "stepfun/", "minimax/")
    if any(name_lower.startswith(org) for org in openrouter_orgs):
        return LLMProvider.OPENROUTER
    if ":free" in name_lower:  # Any model ending in :free is OpenRouter
        return LLMProvider.OPENROUTER

    openai_patterns = ("gpt-", "o1", "o3", "chatgpt")
    if any(pattern in name_lower for pattern in openai_patterns):
        return LLMProvider.OPENAI

    if any(pattern in name_lower for pattern in GROQ_PATTERNS):
        return LLMProvider.GROQ

    return LLMProvider.OLLAMA


def get_llm(
    model_name: str | None = None,
    temperature: float = 0.1,
    streaming: bool = True,
    bind_tools: bool = True,
    api_keys: dict | None = None,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Create an LLM instance configured for the cognitive agent.

    Args:
        model_name: Model name. If None, uses settings default.
        temperature: Sampling temperature. Lower = more deterministic.
        streaming: Enable token-by-token streaming.
        bind_tools: Whether to bind browser tools for structured output.
        api_keys: Optional runtime keys from KeyVault. Dict with keys:
                  openai_api_key, groq_api_key, ollama_base_url

    Returns:
        A LangChain chat model ready for use in graph nodes.
    """
    if model_name is None:
        model_name = settings.ollama_model

    provider = detect_provider(model_name)
    keys = api_keys or {}

    if provider == LLMProvider.OPENAI:
        # Resolution: session key > AGENT_OPENAI_API_KEY > OPENAI_API_KEY env var > error
        api_key = (
            keys.get("openai_api_key")
            or settings.openai_api_key.get_secret_value()
            or os.environ.get("OPENAI_API_KEY", "")
        )
        if not api_key:
            raise ValueError(
                "No API key configured for OpenAI. "
                "Enter a key in the extension settings or set AGENT_OPENAI_API_KEY in .env."
            )
        llm = ChatOpenAI(
            model=model_name,
            temperature=temperature,
            streaming=streaming,
            api_key=api_key,
        )

    elif provider == LLMProvider.GROQ:
        api_key = (
            keys.get("groq_api_key")
            or settings.groq_api_key.get_secret_value()
            or os.environ.get("GROQ_API_KEY", "")
        )
        if not api_key:
            raise ValueError(
                "No API key configured for Groq. "
                "Enter a key in the extension settings or set AGENT_GROQ_API_KEY in .env."
            )
        llm = ChatGroq(
            model=model_name,
            temperature=temperature,
            streaming=streaming,
            api_key=api_key,
        )

    elif provider == LLMProvider.OPENROUTER:
        api_key = (
            keys.get("openrouter_api_key")
            or settings.openrouter_api_key.get_secret_value()
            or os.environ.get("OPENROUTER_API_KEY", "")
        )
        if not api_key:
            raise ValueError(
                "No API key configured for OpenRouter. "
                "Enter a key in the extension settings or set AGENT_OPENROUTER_API_KEY in .env."
            )
        llm = ChatOpenAI(
            model=model_name,
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            temperature=temperature,
            streaming=streaming,
            default_headers={
                "X-Title": "Agentic Browser Extension",
            },
        )

    else:
        base_url = keys.get("ollama_base_url") or settings.ollama_base_url
        llm = ChatOllama(
            model=model_name,
            temperature=temperature,
            base_url=base_url,
            num_predict=max_tokens or 4096,
        )

    if bind_tools:
        llm = llm.bind_tools(BROWSER_TOOLS)

    return llm


def get_vision_llm(api_keys: dict | None = None) -> BaseChatModel | None:
    """Get LLM configured for vision tasks (screenshot analysis).

    Returns None if no vision model is configured.
    Vision models accept images in HumanMessage content.
    """
    vision_model = settings.vision_model
    if not vision_model:
        return None

    keys = api_keys or {}
    base_url = keys.get("ollama_base_url") or settings.ollama_base_url

    # Vision models are always Ollama (local) for now
    return ChatOllama(
        model=vision_model,
        temperature=0.1,
        base_url=base_url,
        num_predict=2048,
    )


def get_reasoning_llm(model_name: str | None = None, api_keys: dict | None = None) -> BaseChatModel:
    """Get LLM configured for reasoning tasks (higher temperature, no tools).

    Used for: goal analysis, planning, self-critique, evaluation.
    These nodes need creative reasoning, not structured tool output.
    """
    return get_llm(
        model_name=model_name,
        temperature=0.4,
        bind_tools=False,
        api_keys=api_keys,
        max_tokens=1024,  # Reasoning/eval responses should be concise JSON
    )


def get_action_llm(model_name: str | None = None, api_keys: dict | None = None) -> BaseChatModel:
    """Get LLM configured for action selection (low temperature, with tools).

    Used for: deciding which browser action to take.
    Needs to be deterministic and produce valid tool calls.
    """
    return get_llm(
        model_name=model_name,
        temperature=0.1,
        bind_tools=True,
        api_keys=api_keys,
    )


def select_tools_for_context(
    page_context=None,
    current_step: str = "",
    action_history: list | None = None,
    max_tools: int = 18,
    goal_text: str = "",
) -> list:
    """Select relevant tool groups based on current context.

    Always includes core tools (9). Adds groups based on page state,
    current step description, and recent action history. Caps total at max_tools.
    """
    selected = list(TOOL_GROUPS["core"])
    seen_names = {t.name for t in selected}

    def _add_group(group_name: str):
        for t in TOOL_GROUPS.get(group_name, []):
            if t.name not in seen_names and len(selected) < max_tools:
                selected.append(t)
                seen_names.add(t.name)

    # Always add search tools — they're commonly needed
    _add_group("search")

    # Check page context for form/input elements
    if page_context:
        elements = getattr(page_context, "interactive_elements", [])
        element_tags = {getattr(e, "tag", "") for e in elements}
        element_types = {getattr(e, "type", "") for e in elements}

        has_forms = bool(
            {"input", "select", "textarea", "form"} & element_tags
            or {"checkbox", "radio", "file"} & element_types
        )
        if has_forms:
            _add_group("forms")

    # Check step description AND goal text for keywords
    step_lower = current_step.lower()
    goal_lower = goal_text.lower()
    context_text = step_lower + " " + goal_lower
    if any(kw in context_text for kw in ("extract", "read", "get", "scrape", "data", "text",
                                          "price", "product", "listing", "item", "result",
                                          "compare", "find", "check", "json", "structured")):
        _add_group("data")
    if any(kw in step_lower for kw in ("tab", "new tab", "switch")):
        _add_group("tabs")
    if any(kw in step_lower for kw in ("wait", "load", "appear")):
        _add_group("waiting")

    # Check action history for failures — add monitoring + advanced
    history = action_history or []
    recent_failures = sum(
        1 for entry in history[-5:]
        if entry.get("result", {}).get("status") != "success"
    )
    if recent_failures >= 2:
        _add_group("monitoring")
        _add_group("advanced")
        _add_group("waiting")

    return selected


def get_action_llm_dynamic(
    model_name: str | None = None,
    api_keys: dict | None = None,
    page_context=None,
    current_step: str = "",
    action_history: list | None = None,
    goal_text: str = "",
) -> BaseChatModel:
    """Get LLM with dynamically selected tools based on context.

    Replaces static get_action_llm when context-aware tool selection is desired.
    """
    tools = select_tools_for_context(page_context, current_step, action_history,
                                     goal_text=goal_text)

    return get_llm(
        model_name=model_name,
        temperature=0.1,
        bind_tools=False,  # We'll bind manually
        api_keys=api_keys,
        max_tokens=512,  # Action decisions need only tool call + short reasoning
    ).bind_tools(tools)
