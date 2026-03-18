"""Cognitive agent module — the brain of the agentic browser extension.

This module contains the LangGraph state graph, all cognitive nodes,
system prompts, and LLM client configuration.
"""

from agent_core.agent.graph import create_agent_graph, AgentGraph
from agent_core.agent.llm_client import get_llm, LLMProvider

__all__ = ["create_agent_graph", "AgentGraph", "get_llm", "LLMProvider"]
