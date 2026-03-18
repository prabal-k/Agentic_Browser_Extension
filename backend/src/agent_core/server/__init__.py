"""FastAPI WebSocket server — exposes the LangGraph agent to frontends.

Provides:
- WebSocket endpoint for real-time bidirectional agent communication
- REST endpoints for health checks and model info
- Session management (one agent graph per WebSocket connection)
"""

from agent_core.server.app import create_app

__all__ = ["create_app"]
