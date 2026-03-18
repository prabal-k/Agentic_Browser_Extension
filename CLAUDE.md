# Agentic Browser Extension — Project Instructions

## Project Overview
AI-powered browser extension: Python backend (FastAPI + LangGraph + Ollama/OpenAI) + Next.js dashboard + Chrome MV3 extension. Communication via WebSocket.

## Testing with Playwright MCP
A **Playwright MCP server** is connected and available for browser testing. Use it to:
- **Test the full application flow** after each phase or significant change
- Navigate to `http://localhost:3000` (dashboard) or `http://localhost:8000/health` (backend)
- Upload DOM snapshots, send goals, verify agent responses render correctly
- Test the real Playwright orchestrator on live websites

### How to test
1. Verify backend: `mcp__playwright__browser_navigate` to `http://localhost:8000/health`
2. Verify dashboard: `mcp__playwright__browser_navigate` to `http://localhost:3000`
3. Interact with dashboard: upload DOM snapshots, type goals, check agent responses
4. Test live websites: navigate to real sites and verify DOM extraction works

### When to test
- After completing each phase
- After fixing bugs that affect cross-phase integration
- When the user asks to verify the application works

## Running the Application
- **Backend**: `cd backend && python -m agent_core.server`
- **Dashboard**: `cd dashboard && npm run dev`
- **Playwright orchestrator**: `cd backend && python -m agent_core.playwright --url "URL" --goal "GOAL"`

## Development Workflow
Follow strict phase-based engineering: planning → implementation → testing → documentation → next phase.
Never skip phases. Update `docs/CHECKLIST.md` after each phase completion.

## Key Configuration
- Backend `.env`: `AGENT_OLLAMA_MODEL` sets the default LLM (currently `gpt-4o-mini`, auto-routes to OpenAI)
- Models with "gpt" in name → OpenAI provider; everything else → Ollama provider
- Server restarts needed after `.env` changes (Settings singleton cached at import)
