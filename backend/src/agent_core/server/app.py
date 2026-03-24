"""FastAPI application — WebSocket server for the cognitive agent.

Usage:
    # Development
    uvicorn agent_core.server.app:app --reload --host 0.0.0.0 --port 8000

    # Or via the module entry point
    python -m agent_core.server
"""

import time

import structlog
from fastapi import FastAPI, WebSocket, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr

from agent_core.config import settings
from agent_core.logging import setup_logging
from agent_core.server.session import SessionManager
from agent_core.server.key_vault import key_vault, ProviderKeys
from agent_core.server.ws_handler import handle_websocket

logger = structlog.get_logger("server.app")


# ============================================================
# Request/Response models
# ============================================================

class KeySubmission(BaseModel):
    """Keys submitted from extension UI. SecretStr masks in logs."""
    openai_api_key: str = ""
    groq_api_key: str = ""
    ollama_base_url: str = ""
    preferred_provider: str = ""
    preferred_model: str = ""


class KeySubmissionResponse(BaseModel):
    session_token: str
    providers: dict


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    setup_logging()

    app = FastAPI(
        title="Agentic Browser Extension — Backend",
        description="AI-powered browser agent with goal-based reasoning",
        version="0.1.0",
    )

    # CORS — allow dashboard, extension, and chrome-extension:// origins
    # Chrome extensions use chrome-extension://<id> as origin for fetch() calls
    cors_origins = list(settings.cors_origins)
    cors_origins.append("chrome-extension://*")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allow all origins (localhost-only server)
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Shared session manager
    session_mgr = SessionManager(max_sessions=10)
    _start_time = time.time()

    # ============================================================
    # REST Endpoints
    # ============================================================

    @app.get("/health")
    async def health():
        """Health check — verifies the server is running."""
        return {
            "status": "ok",
            "uptime_seconds": round(time.time() - _start_time, 1),
            "active_sessions": session_mgr.active_count,
        }

    @app.get("/api/config")
    async def get_config():
        """Return non-secret configuration for the frontend."""
        return {
            "ollama_model": settings.ollama_model,
            "openai_model": settings.openai_model,
            "groq_model": settings.groq_model,
            "has_openai_key": bool(settings.openai_api_key.get_secret_value()),
            "has_groq_key": bool(settings.groq_api_key.get_secret_value()),
            "max_iterations": settings.max_iterations,
            "confidence_threshold": settings.confidence_threshold,
            "auto_confirm": settings.auto_confirm,
        }

    @app.get("/api/models")
    async def get_models():
        """Check available Ollama models."""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{settings.ollama_base_url}/api/tags",
                    timeout=5,
                )
                if resp.status_code == 200:
                    models = [m["name"] for m in resp.json().get("models", [])]
                    return {
                        "ollama_url": settings.ollama_base_url,
                        "ollama_reachable": True,
                        "models": models,
                        "active_model": settings.ollama_model,
                        "model_available": any(settings.ollama_model in m for m in models),
                    }
        except Exception as e:
            return {
                "ollama_url": settings.ollama_base_url,
                "ollama_reachable": False,
                "error": str(e),
                "models": [],
            }

    @app.get("/api/sessions")
    async def list_sessions():
        """List active agent sessions (for debugging)."""
        return {
            "active_count": session_mgr.active_count,
            "sessions": session_mgr.list_sessions(),
        }

    # ============================================================
    # Key Management Endpoints
    # ============================================================

    @app.post("/api/keys", response_model=KeySubmissionResponse)
    async def submit_keys(submission: KeySubmission):
        """Submit API keys, receive an opaque session token.

        Keys are stored in-memory only (KeyVault). The token is used
        to associate keys with WebSocket sessions. No keys are ever
        returned or logged.
        """
        provider_keys = ProviderKeys(
            openai_api_key=SecretStr(submission.openai_api_key),
            groq_api_key=SecretStr(submission.groq_api_key),
            ollama_base_url=submission.ollama_base_url or settings.ollama_base_url,
            preferred_provider=submission.preferred_provider,
            preferred_model=submission.preferred_model,
        )
        token = key_vault.store_keys(provider_keys)
        status = key_vault.get_status(token)
        return KeySubmissionResponse(
            session_token=token,
            providers=status.get("providers", {}),
        )

    @app.get("/api/keys/status")
    async def keys_status(token: str = Query(..., description="Session token")):
        """Check which providers are configured. No keys exposed."""
        return key_vault.get_status(token)

    @app.delete("/api/keys")
    async def revoke_keys(token: str = Query(..., description="Session token")):
        """Revoke a session token and clear stored keys."""
        revoked = key_vault.revoke(token)
        return {"revoked": revoked}

    @app.get("/api/providers")
    async def list_providers(token: str = Query(default="", description="Optional session token")):
        """List available providers and their models.

        Checks which providers have keys configured (via .env or session token),
        and lists available models for each.
        """
        import httpx

        providers = {}

        # OpenAI
        has_openai = bool(settings.openai_api_key.get_secret_value())
        if token:
            keys = key_vault.get_keys(token)
            if keys and keys.openai_api_key.get_secret_value():
                has_openai = True
        providers["openai"] = {
            "available": has_openai,
            "models": [
                "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
                "o1", "o1-mini", "o3-mini",
            ],
        }

        # Groq
        has_groq = bool(settings.groq_api_key.get_secret_value())
        if token:
            keys = key_vault.get_keys(token)
            if keys and keys.groq_api_key.get_secret_value():
                has_groq = True
        providers["groq"] = {
            "available": has_groq,
            "models": [
                "llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                "mixtral-8x7b-32768", "gemma2-9b-it",
                "deepseek-r1-distill-llama-70b",
            ],
        }

        # Ollama — check actual server
        ollama_models = []
        ollama_reachable = False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{settings.ollama_base_url}/api/tags",
                    timeout=5,
                )
                if resp.status_code == 200:
                    ollama_models = [m["name"] for m in resp.json().get("models", [])]
                    ollama_reachable = True
        except Exception:
            pass

        providers["ollama"] = {
            "available": ollama_reachable,
            "url": settings.ollama_base_url,
            "models": ollama_models,
        }

        return {"providers": providers}

    # ============================================================
    # Export Endpoints
    # ============================================================

    from agent_core.export import export_store, format_export

    @app.get("/api/export/{export_id}")
    async def export_data(
        export_id: str,
        format: str = Query(default="json", description="Export format: json, csv, xlsx, pdf"),
    ):
        """Download exported data in the requested format."""
        stored = export_store.get(export_id)
        if not stored:
            return JSONResponse(
                status_code=404,
                content={"error": "Export not found or expired (10 min TTL)"},
            )

        content, content_type, filename = format_export(
            stored["data"], format, stored.get("metadata")
        )
        return Response(
            content=content,
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ============================================================
    # WebSocket Endpoint
    # ============================================================

    @app.websocket("/ws")
    async def websocket_endpoint(
        ws: WebSocket,
        token: str = Query(default="", description="KeyVault session token"),
    ):
        """Main WebSocket endpoint for agent communication.

        Protocol:
        1. Client connects to /ws?token=<session_token>
        2. Server sends server_status with session_id
        3. Client sends client_goal with goal text and optional DOM snapshot
        4. Server streams reasoning, plan, actions, evaluations
        5. On interrupt: server sends server_interrupt, waits for client_user_response
        6. On action execution: server sends server_action_request (execute=True),
           waits for client_action_result
        7. When done: server sends server_done
        """
        await handle_websocket(ws, session_mgr, vault_token=token or None)

    return app


# Module-level app instance for uvicorn
app = create_app()
