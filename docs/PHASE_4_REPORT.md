# Phase 4 Report — FastAPI WebSocket Server

**Status**: COMPLETE
**Date**: 2026-03-14

## What Was Implemented

### Files Created
```
backend/src/agent_core/server/
├── __init__.py         # Module exports
├── __main__.py         # Entry point: python -m agent_core.server
├── app.py              # FastAPI app, REST endpoints, WebSocket endpoint
├── session.py          # Session manager (one agent graph per connection)
└── ws_handler.py       # WebSocket message handler + agent orchestration
```

### Architecture

```
Client (Dashboard/Extension)
    │
    ├── REST: GET /health, /api/config, /api/models, /api/sessions
    │
    └── WebSocket: ws://host:port/ws
         │
         ├── → client_goal (goal + DOM) → runs LangGraph agent
         │     ← server_status (analyzing_goal, reasoning, deciding...)
         │     ← server_reasoning (chain of thought text)
         │     ← server_plan (plan steps)
         │     ← server_action_request (action to execute)
         │     ← server_evaluation (action result assessment)
         │
         ├── → client_user_response (interrupt reply) → resumes graph
         │     ← server_interrupt (confirmation/clarification request)
         │
         ├── → client_action_result (browser execution result + new DOM) → resumes graph
         │
         ├── → client_cancel → stops agent loop
         │
         └──   ← server_done (task summary)
```

### REST Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Server status, uptime, active session count |
| `/api/config` | GET | Non-secret config (model names, thresholds) — NO API keys |
| `/api/models` | GET | Ollama connectivity check, available models |
| `/api/sessions` | GET | List active agent sessions (debug) |

### Session Management

- One LangGraph graph instance per WebSocket connection
- Max 10 concurrent sessions (configurable)
- LRU eviction: oldest inactive session removed when limit reached
- Running sessions preferred over inactive during eviction
- Auto-cleanup on WebSocket disconnect
- Activity tracking for each session

### WebSocket Message Flow

**Full agent loop over WebSocket:**
1. Client connects → server sends `server_status` with `session_id`
2. Client sends `client_goal` with goal text + optional DOM snapshot
3. Server runs LangGraph graph, streaming node outputs as typed messages:
   - `server_status` — cognitive status changes (analyzing, reasoning, deciding...)
   - `server_reasoning` — chain of thought text
   - `server_plan` — plan steps with version
   - `server_action_request` — action details for the browser
   - `server_evaluation` — post-action assessment
4. On interrupt (confirm/execute/ask_user):
   - Server sends `server_interrupt` with input field definitions
   - Client renders appropriate UI and responds with `client_user_response`
   - Server resumes the graph
5. On action execution:
   - Server sends `server_action_request` with `execute=True`
   - Client executes in browser, responds with `client_action_result` + new DOM
   - Server resumes the graph with ActionResult
6. On completion: server sends `server_done` with summary
7. Client can send `client_cancel` at any time to stop

### Error Handling

- Invalid messages → `server_error` with `recoverable=True`
- Empty goal → rejected with clear message
- Invalid DOM snapshot → rejected with validation error
- Agent execution error → `server_error` with traceback logged
- Client disconnect mid-task → session cleaned up, graph stopped
- Interrupt timeout (5 minutes) → task cancelled with message
- Unknown message types → `server_error` with type info

## Test Report

```
139 tests passed in 6.36s

Phase 4 Tests (26):
- REST endpoints: 6 tests (health, config, models, sessions)
- Session manager: 8 tests (create, get, remove, touch, eviction, list)
- WebSocket connection: 7 tests (connect, session ID, unknown msg, empty goal,
  invalid DOM, cancel, multiple connections)
- Message protocol: 4 tests (type field, timestamp, goal structure, enum values)
- Session cleanup: 1 test (cleanup on disconnect)

Previous Tests (113): All passing (zero regressions)
```

## Bugs Fixed

1. **structlog `add_logger_name` crash** — `PrintLoggerFactory` creates loggers without a `name` attribute, causing `AttributeError`. Removed `add_logger_name` processor since we use `get_logger(name)` pattern instead.

## How to Run

```bash
# Start the server
python -m agent_core.server

# Or with uvicorn directly
uvicorn agent_core.server.app:app --reload --host 0.0.0.0 --port 8000

# Test endpoints
curl http://localhost:8000/health
curl http://localhost:8000/api/config
curl http://localhost:8000/api/models
```

## Design Decisions

1. **One graph per connection**: Each WebSocket gets its own LangGraph instance with its own checkpointer. This ensures session isolation — one user's agent can't interfere with another's.

2. **Interrupt as client-server round-trip**: When the graph hits `interrupt()`, the WebSocket handler sends the interrupt data to the client, then `await`s the client's response. The graph stays paused (via LangGraph checkpointing) until the response arrives.

3. **Typed messages, not free-form JSON**: Every message has a `type` discriminator matching `WSMessageType` enum. This makes the protocol self-documenting and enables type-safe handling on both sides.

4. **No rate limiting yet**: Deferred to Phase 8 (hardening). For now, the max sessions limit provides basic protection.

5. **CORS from settings**: Origins are configurable via `.env` — defaults to localhost:3000 (Next.js) and localhost:5173 (Vite). Extension origin will be added in Phase 7.

## Next Phase Objectives

**Phase 5 — Next.js Test Dashboard**
- Initialize Next.js + ShadCN UI project
- Build WebSocket connection hook
- Build Zustand store for agent state
- Build chat interface, action preview, interrupt input components
- Build floating AI bubble
- Build DOM snapshot upload/viewer
