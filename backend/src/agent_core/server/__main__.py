"""Entry point for running the server directly.

Usage:
    python -m agent_core.server
    python -m agent_core.server --host 0.0.0.0 --port 8000
"""

import os
import sys
from pathlib import Path

# CRITICAL: Load .env BEFORE any langchain/langgraph imports so that
# LANGCHAIN_TRACING_V2 is set when LangChain modules initialize their
# tracing callbacks. This runs in both parent (reloader) and child (worker).
from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"
load_dotenv(_ENV_FILE, override=True)  # override=True ensures .env wins

import uvicorn

from agent_core.config import settings


def main():
    host = settings.server_host
    port = settings.server_port

    # Allow CLI overrides
    args = sys.argv[1:]
    if "--host" in args:
        idx = args.index("--host")
        host = args[idx + 1]
    if "--port" in args:
        idx = args.index("--port")
        port = int(args[idx + 1])

    tracing = os.environ.get("LANGCHAIN_TRACING_V2", "false")
    project = os.environ.get("LANGCHAIN_PROJECT", "default")

    print(f"Starting server at http://{host}:{port}")
    print(f"WebSocket endpoint: ws://{host}:{port}/ws")
    print(f"Health check: http://{host}:{port}/health")
    print(f"Ollama: {settings.ollama_base_url} ({settings.ollama_model})")
    print(f"LangSmith tracing: {tracing} (project: {project})")

    uvicorn.run(
        "agent_core.server.app:app",
        host=host,
        port=port,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
