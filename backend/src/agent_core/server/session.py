"""Session management — one LangGraph agent instance per WebSocket connection.

Each WebSocket connection gets its own:
- Thread ID (for LangGraph checkpointing)
- Agent graph instance
- Connection state tracking
"""

import time
import uuid
from dataclasses import dataclass, field

import structlog

from agent_core.agent.graph import create_agent_graph

logger = structlog.get_logger("server.session")


@dataclass
class Session:
    """A single agent session tied to a WebSocket connection."""

    session_id: str
    thread_id: str
    graph: object  # CompiledStateGraph
    created_at: float
    last_activity: float
    is_running: bool = False
    current_goal: str = ""
    iteration_count: int = 0
    action_count: int = 0
    vault_token: str | None = None  # KeyVault token for this session's API keys
    session_memory_k: int = 6  # Keep last k messages for context across goals (default: 6 = past 3 conversations)


class SessionManager:
    """Manages active WebSocket sessions.

    Thread-safe for concurrent WebSocket connections.
    Each session gets its own LangGraph graph with its own checkpointer.
    """

    def __init__(self, max_sessions: int = 10):
        self._sessions: dict[str, Session] = {}
        self._max_sessions = max_sessions

    def create_session(self) -> Session:
        """Create a new agent session."""
        if len(self._sessions) >= self._max_sessions:
            # Evict oldest inactive session
            self._evict_oldest()

        session_id = str(uuid.uuid4())[:8]
        thread_id = f"ws_{session_id}_{int(time.time())}"
        graph = create_agent_graph()
        now = time.time()

        session = Session(
            session_id=session_id,
            thread_id=thread_id,
            graph=graph,
            created_at=now,
            last_activity=now,
        )
        self._sessions[session_id] = session
        logger.info("session_created", session_id=session_id, thread_id=thread_id)
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> None:
        """Remove a session (on WebSocket disconnect)."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info("session_removed", session_id=session_id)

    def touch_session(self, session_id: str) -> None:
        """Update last activity timestamp."""
        session = self._sessions.get(session_id)
        if session:
            session.last_activity = time.time()

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def list_sessions(self) -> list[dict]:
        """List all active sessions (for admin/debug)."""
        return [
            {
                "session_id": s.session_id,
                "created_at": s.created_at,
                "last_activity": s.last_activity,
                "is_running": s.is_running,
                "current_goal": s.current_goal,
                "iteration_count": s.iteration_count,
                "action_count": s.action_count,
            }
            for s in self._sessions.values()
        ]

    def _evict_oldest(self) -> None:
        """Remove the oldest inactive session to make room."""
        inactive = [
            s for s in self._sessions.values() if not s.is_running
        ]
        if inactive:
            oldest = min(inactive, key=lambda s: s.last_activity)
            self.remove_session(oldest.session_id)
            logger.info("session_evicted", session_id=oldest.session_id)
        elif self._sessions:
            # All sessions running — evict oldest anyway
            oldest = min(self._sessions.values(), key=lambda s: s.last_activity)
            self.remove_session(oldest.session_id)
            logger.warning("running_session_evicted", session_id=oldest.session_id)
