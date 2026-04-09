# Agentic Browser Extension

An AI-powered browser agent that understands live pages, decides the next action, and executes multi-step tasks through a Chrome MV3 extension backed by a FastAPI + LangGraph server. Supports local Ollama models and hosted providers, with an optional Next.js dashboard for monitoring and debugging.

## Highlights

- Reactive loop: observe -> decide -> act -> verify, no rigid upfront plan
- WebSocket streaming for actions, status, and interruptions
- Multi-provider LLMs (Ollama, OpenAI, Groq) with runtime key submission
- Extension + Playwright orchestrator share the same action/DOM schemas
- 30+ browser tools (click, type, scroll, extract, visual_check, wait, etc.)

## Architecture (High Level)

```
Chrome Extension (side panel + content script)
          |  WebSocket
          v
FastAPI + LangGraph Agent (Python)
          |  HTTP
          v
LLM Provider (Ollama/OpenAI/Groq)
```

Detailed design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Repository Structure

- `backend/` - FastAPI server, LangGraph agent, Playwright orchestrator
- `extension/` - Chrome MV3 extension (side panel UI + content script)
- `dashboard/` - Next.js monitoring UI (optional)
- `docs/` - architecture, phase reports, roadmap, checklist

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- One LLM provider:
  - Ollama (local) with a tool-calling model, or
  - OpenAI / Groq API key
- Chrome (for extension)

### 1) Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your provider + model settings

python -m agent_core.server
```

Server runs at `http://localhost:8000`.

### 2) Extension

```bash
cd extension
npm install
npm run build
```

Load in Chrome: `chrome://extensions` -> Developer mode -> Load unpacked -> `extension/.output/chrome-mv3`

### 3) Dashboard (Optional)

```bash
cd dashboard
npm install
npm run dev
```

Dashboard runs at `http://localhost:3000`.

### 4) Playwright Orchestrator (Optional, real browser testing)

```bash
cd backend
python -m agent_core.playwright --url "https://example.com" --goal "Find the contact email"
```

## Configuration

All config lives in `backend/.env` (template at `backend/.env.example`). Key fields:

```env
# Provider endpoints
AGENT_OLLAMA_BASE_URL=http://localhost:11434

# Models (names with "gpt" route to OpenAI)
AGENT_OLLAMA_MODEL=qwen3.5:27b
AGENT_FAST_MODEL=qwen3.5:9b
AGENT_VISION_MODEL=qwen3-vl:8b

# Agent behavior
AGENT_MAX_ITERATIONS=25
AGENT_AUTO_CONFIRM=false
```

Multi-provider key flow (Phase 8):
- Submit keys at runtime via `POST /api/keys` and receive a session token
- Tokens live in memory only and are never sent over WebSocket

## How It Works (Short)

1. Task text is preserved; URLs are extracted automatically.
2. The agent chooses one browser action per loop using the current DOM + history.
3. Deterministic checks skip expensive LLM evaluation when safe.
4. A vision model can analyze screenshots via `visual_check` when needed.

## Testing

Backend tests:
```bash
cd backend
pytest
```

CLI test harness:
```bash
cd backend
python -m agent_core.test_harness check
python -m agent_core.test_harness interactive --snapshot wikipedia_article
python -m agent_core.test_harness batch
```

## Status

Phases 1-10 are complete (backend, agent, CLI, server, dashboard, Playwright, extension MVP, security, tool expansion). Phase 11 focuses on production hardening and distribution.

Progress details:
- [docs/CHECKLIST.md](docs/CHECKLIST.md)
- [docs/PHASE_4_REPORT.md](docs/PHASE_4_REPORT.md)
- [docs/IMPROVEMENT_ROADMAP.md](docs/IMPROVEMENT_ROADMAP.md)

## Docs

- [Architecture Deep Dive](docs/ARCHITECTURE.md)
- [Development Plan](docs/DEVELOPMENT_PLAN.md)
- [Phase Reports](docs)
- [Improvement Roadmap](docs/IMPROVEMENT_ROADMAP.md)

## Notes

- The dashboard `README.md` is still the default Next.js template and can be replaced when you want a dedicated dashboard readme.
