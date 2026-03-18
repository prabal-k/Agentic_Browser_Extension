"""Tests for Phase 4 — FastAPI WebSocket Server.

Tests cover:
- REST endpoints (health, config, models, sessions)
- WebSocket connection lifecycle
- Session management
- WebSocket message protocol
- Interrupt flow over WebSocket (with mocked LLM)
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from agent_core.logging import setup_logging
from agent_core.server.app import create_app
from agent_core.server.session import Session, SessionManager
from agent_core.schemas.messages import WSMessageType
from agent_core.schemas.agent import CognitiveStatus

# Ensure structlog is configured before any tests run
setup_logging()


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def app():
    """Create a fresh FastAPI app for each test."""
    return create_app()


@pytest.fixture
def client(app):
    """Synchronous test client for REST endpoints."""
    return TestClient(app)


@pytest.fixture
def session_mgr():
    """Fresh session manager."""
    return SessionManager(max_sessions=3)


# ============================================================
# REST Endpoint Tests
# ============================================================

class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert data["active_sessions"] == 0

    def test_health_uptime_increases(self, client):
        resp1 = client.get("/health")
        time.sleep(0.1)
        resp2 = client.get("/health")
        assert resp2.json()["uptime_seconds"] >= resp1.json()["uptime_seconds"]


class TestConfigEndpoint:
    def test_config_returns_model_info(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "ollama_model" in data
        assert "max_iterations" in data
        assert "confidence_threshold" in data
        assert "auto_confirm" in data
        # API key should NOT be in response
        assert "openai_api_key" not in data
        assert "has_openai_key" in data

    def test_config_no_secrets_leaked(self, client):
        resp = client.get("/api/config")
        data = resp.json()
        raw_str = str(data)
        # Make sure no secret patterns leak
        assert "sk-" not in raw_str


class TestModelsEndpoint:
    def test_models_endpoint_exists(self, client):
        # May fail to connect to Ollama in test, but endpoint should work
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "ollama_url" in data
        assert "models" in data


class TestSessionsEndpoint:
    def test_sessions_empty_initially(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_count"] == 0
        assert data["sessions"] == []


# ============================================================
# Session Manager Tests
# ============================================================

class TestSessionManager:
    def test_create_session(self, session_mgr):
        session = session_mgr.create_session()
        assert session.session_id
        assert session.thread_id
        assert session.graph is not None
        assert session.is_running is False
        assert session_mgr.active_count == 1

    def test_get_session(self, session_mgr):
        session = session_mgr.create_session()
        found = session_mgr.get_session(session.session_id)
        assert found is session

    def test_get_nonexistent_session(self, session_mgr):
        assert session_mgr.get_session("nope") is None

    def test_remove_session(self, session_mgr):
        session = session_mgr.create_session()
        session_mgr.remove_session(session.session_id)
        assert session_mgr.active_count == 0
        assert session_mgr.get_session(session.session_id) is None

    def test_touch_session(self, session_mgr):
        session = session_mgr.create_session()
        old_time = session.last_activity
        time.sleep(0.05)
        session_mgr.touch_session(session.session_id)
        assert session.last_activity > old_time

    def test_max_sessions_eviction(self, session_mgr):
        """When max_sessions reached, oldest inactive session is evicted."""
        s1 = session_mgr.create_session()
        s2 = session_mgr.create_session()
        s3 = session_mgr.create_session()
        assert session_mgr.active_count == 3

        # Creating 4th should evict s1 (oldest)
        s4 = session_mgr.create_session()
        assert session_mgr.active_count == 3
        assert session_mgr.get_session(s1.session_id) is None
        assert session_mgr.get_session(s4.session_id) is not None

    def test_eviction_prefers_inactive(self, session_mgr):
        """Running sessions should be evicted last."""
        s1 = session_mgr.create_session()
        s2 = session_mgr.create_session()
        s3 = session_mgr.create_session()

        # Mark s1 as running
        s1.is_running = True

        # s4 should evict s2 (oldest inactive), not s1 (running)
        s4 = session_mgr.create_session()
        assert session_mgr.get_session(s1.session_id) is not None
        assert session_mgr.get_session(s2.session_id) is None

    def test_list_sessions(self, session_mgr):
        s1 = session_mgr.create_session()
        s1.current_goal = "Test goal"
        s1.is_running = True

        sessions = session_mgr.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == s1.session_id
        assert sessions[0]["current_goal"] == "Test goal"
        assert sessions[0]["is_running"] is True


# ============================================================
# WebSocket Connection Tests
# ============================================================

class TestWebSocketConnection:
    def test_websocket_connects(self, client):
        """WebSocket endpoint accepts connections."""
        with client.websocket_connect("/ws") as ws:
            # Should receive initial status message
            msg = ws.receive_json()
            assert msg["type"] == "server_status"
            assert msg["cognitive_status"] == "connected"
            assert "session_id" in msg

    def test_websocket_receives_session_id(self, client):
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["session_id"]
            assert len(msg["session_id"]) > 0

    def test_websocket_unknown_message_type(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume connect message
            ws.send_json({"type": "unknown_type"})
            msg = ws.receive_json()
            assert msg["type"] == "server_error"
            assert "Unexpected message type" in msg["message"]

    def test_websocket_empty_goal_rejected(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume connect message
            ws.send_json({"type": "client_goal", "goal": ""})
            msg = ws.receive_json()
            assert msg["type"] == "server_error"
            assert "Goal is required" in msg["message"]

    def test_websocket_invalid_dom_rejected(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume connect message
            ws.send_json({
                "type": "client_goal",
                "goal": "Do something",
                "dom_snapshot": {"invalid": "data"},
            })
            msg = ws.receive_json()
            assert msg["type"] == "server_error"
            assert "Invalid DOM snapshot" in msg["message"]

    def test_websocket_cancel_message(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume connect message
            ws.send_json({"type": "client_cancel"})
            msg = ws.receive_json()
            assert msg["type"] == "server_status"
            assert msg["cognitive_status"] == "idle"

    def test_multiple_connections(self, app):
        """Multiple WebSocket connections get different sessions."""
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws1:
            msg1 = ws1.receive_json()
            with client.websocket_connect("/ws") as ws2:
                msg2 = ws2.receive_json()
                assert msg1["session_id"] != msg2["session_id"]


# ============================================================
# WebSocket Message Protocol Tests
# ============================================================

class TestMessageProtocol:
    def test_all_server_messages_have_type(self, client):
        """Every server message must have a type field."""
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert "type" in msg

    def test_all_server_messages_have_timestamp(self, client):
        """Every server message must have a timestamp."""
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert "timestamp" in msg
            assert msg["timestamp"] > 0

    def test_client_goal_structure(self, client):
        """Verify client_goal message is accepted with correct structure."""
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # connect message
            # Valid goal without DOM (should proceed to agent — will eventually error on LLM)
            ws.send_json({
                "type": "client_goal",
                "goal": "Test goal",
            })
            # Should get at least a status message back
            msg = ws.receive_json()
            assert msg["type"] in ("server_status", "server_error")

    def test_ws_message_types_match_schema(self):
        """All WSMessageType values should be valid strings."""
        for mt in WSMessageType:
            assert isinstance(mt.value, str)
            assert mt.value.startswith("client_") or mt.value.startswith("server_")


# ============================================================
# Session Cleanup Tests
# ============================================================

class TestSessionCleanup:
    def test_session_removed_on_disconnect(self, app):
        """Session should be cleaned up when WebSocket disconnects."""
        client = TestClient(app)
        session_id = None
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            session_id = msg["session_id"]

        # After disconnect, session should be removed
        # Check via REST endpoint
        resp = client.get("/api/sessions")
        sessions = resp.json()["sessions"]
        session_ids = [s["session_id"] for s in sessions]
        assert session_id not in session_ids
