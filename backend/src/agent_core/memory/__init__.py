"""Persistent file-based memory for the cognitive agent.

Stores site-specific patterns, task history, and action statistics
in a local SQLite database so the agent learns across sessions.
"""

from agent_core.memory.store import PersistentMemory, get_memory

__all__ = ["PersistentMemory", "get_memory"]
