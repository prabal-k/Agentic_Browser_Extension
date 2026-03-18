"""Playwright integration — Real browser testing for the agent.

This module provides:
- dom_extractor: Extracts live DOM into PageContext schema
- action_executor: Executes agent actions on real pages
- orchestrator: Connects browser ↔ WebSocket server for full agent loop
"""

from agent_core.playwright.dom_extractor import extract_page_context
from agent_core.playwright.action_executor import execute_action

__all__ = ["extract_page_context", "execute_action"]
