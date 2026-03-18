# Agentic Browser Extension

An AI-powered browser agent that understands web pages, reasons about goals, and performs multi-step tasks autonomously. Combines a Python backend (FastAPI + LangGraph + Ollama) with a Chrome MV3 extension and an optional Next.js dashboard.

## Architecture

```
User (Chrome Extension) <--WebSocket--> Backend (FastAPI + LangGraph) <--HTTP--> Ollama (Local LLM)
                                              |
                                         Vision Model (qwen3-vl)
```

### Core Loop (Reactive)

```
START --> analyze_and_plan (0 LLM calls) --> decide_action (1 LLM call)
              |                                    |
              |                              execute_action
              |                                    |
              |                                 observe (0 LLM calls)
              |                                    |
              |                              smart_evaluate (0 LLM calls, ~70%)
              |                                    |
              |                              self_critique (0 LLM calls)
              |                                    |
              +------------------------------------+
                                                   |
                                         agent calls done(findings)
                                                   |
                                              finalize --> END
```

**No upfront plan. No critique overhead. Just: look at page, do the obvious next thing, check, repeat.**

Typical task: 4-5 LLM calls total (1 per browser action).

### Components

| Component | Path | Description |
|---|---|---|
| **Backend** | `backend/` | FastAPI server, LangGraph agent, Ollama integration |
| **Extension** | `extension/` | Chrome MV3 side panel, content script, background worker |
| **Dashboard** | `dashboard/` | Next.js monitoring UI (optional) |
| **Docs** | `docs/` | Architecture diagrams, checklists |

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- Ollama server with tool-calling model (e.g., `qwen3.5:27b` or `qwen3.5:9b`)
- Chrome browser

### 1. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your Ollama server URL and model names

# Run
python -m agent_core.server
```

### 2. Extension

```bash
cd extension
npm install
npm run build
```

Load in Chrome: `chrome://extensions` > Developer mode > Load unpacked > select `extension/.output/chrome-mv3`

### 3. Dashboard (Optional)

```bash
cd dashboard
npm install
npm run dev
```

## Configuration

All configuration is in `backend/.env`:

```env
# Ollama server
AGENT_OLLAMA_BASE_URL=http://localhost:11434
AGENT_OLLAMA_MODEL=qwen3.5:27b      # Main model (reasoning + tools)
AGENT_FAST_MODEL=qwen3.5:9b          # Fast model (simple actions)
AGENT_VISION_MODEL=qwen3-vl:8b       # Vision model (screenshot analysis)

# Agent behavior
AGENT_MAX_ITERATIONS=25
AGENT_AUTO_CONFIRM=false
```

### Model Routing

The agent automatically selects the right model:

| Situation | Model Used |
|---|---|
| Simple actions (navigate, click, type, scroll) | Fast model (9b) |
| First action, complex decisions, after findings | Main model (27b) |
| Screenshot/photo analysis | Vision model (qwen3-vl) |

### Ollama GPU Optimization (NVIDIA)

```bash
sudo systemctl edit ollama
```

```ini
[Service]
Environment="OLLAMA_NUM_GPU=999"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
```

## How It Works

### 1. Task Understanding (No LLM Call)

The user's task text flows through unchanged. URLs are extracted automatically. No rewriting.

```
User: "check the price of lenovo loq laptop in nepal"
  --> URL: none detected
  --> First action hint: "Navigate to search engine"

User: "https://maps.google.com/place/... check if sells vapes"
  --> URL: https://maps.google.com/place/...
  --> First action hint: "Navigate to URL"
```

### 2. Action Loop (1 LLM Call Per Action)

Each cycle: the LLM sees the original task + current page + action history, and picks ONE tool to call.

### 3. Smart Evaluation (No LLM Call ~70%)

After each action, a deterministic check decides if the LLM needs to evaluate:
- Scroll/wait/type succeeded? Skip LLM.
- Click succeeded, no suspicious redirect? Skip LLM.
- Action failed or suspicious URL? Call LLM evaluate.

### 4. Stuck Loop Detection

- **Duplicate content**: If last 2 `read_page` calls return same text, force `done`.
- **Empty extraction**: If last 2 reads return 0 chars, force `done` or try different site.
- **Same action 3x**: If same action type repeated 3 times with no URL change, force different action.

### 5. Vision Analysis

The `visual_check` tool takes a screenshot and sends it to a vision model (qwen3-vl) via direct Ollama API:

```
Agent calls visual_check("Are there vape products in this photo?")
  --> Screenshot captured (PNG)
  --> Sent to qwen3-vl:8b with task-specific question
  --> Vision model returns text analysis
  --> Findings flow back to agent for next decision
```

## Browser Tools (35 total)

### Core (Always Available - 13 tools)
`click`, `type_text`, `clear_and_type`, `navigate`, `go_back`, `scroll_down`, `press_key`, `read_page`, `visual_check`, `extract_text`, `wait`, `ask_user`, `done`

### Search (Always Added - 4 tools)
`select_option`, `scroll_up`, `scroll_to_element`, `key_combo`

### Context-Dependent
- **Forms**: `check`, `uncheck`, `hover`, `upload_file`
- **Tabs**: `new_tab`, `close_tab`, `switch_tab`
- **Data**: `extract_table`, `evaluate_js`
- **Advanced**: `drag`, `handle_dialog`, `go_forward`, `refresh`
- **Waiting**: `wait_for_selector`, `wait_for_navigation`

## DOM Extraction

Priority-based element collection ensures critical elements are never cut off by the 200-element cap:

1. **P0 (Always included)**: inputs, textareas, selects, buttons
2. **P1 (Viewport first)**: links and interactive elements in view
3. **P2 (If space)**: headings, paragraphs, images

Compressed element representation (~40 chars vs ~100 chars before):
```
[1] input ph="Search in Daraz" *focused [form: search]
[2] btn "Add to Cart" [form: checkout]
[3] link "new" href="/newest" [nav bar]
```

## Testing

### Playwright Orchestrator (Automated)

```bash
cd backend
python -m agent_core.playwright --url "https://duckduckgo.com" --goal "search for lenovo loq price in nepal"
```

### Run All Predefined Scenarios

```bash
python -m agent_core.playwright --all
```

### Test Harness (Interactive)

```bash
python -m agent_core.test_harness interactive
```

## Performance

Benchmarks on RTX 5090 (32GB) with qwen3.5:27b + qwen3.5:9b:

| Task | Actions | Time | Result |
|---|---|---|---|
| Hacker News: click "new" link | 1 | 36s | URL changed to /newest |
| DuckDuckGo: search query | 2 | 36s | Results page loaded |
| YouTube: search + play video | 3 | 64s | Video playing |
| Daraz: search product + read price | 3 | 44s | Price found (Rs. 189,999) |
| DuckDuckGo --> retailer: find laptop price | 5 | 56s | Prices found (Rs. 110,000-145,000) |
| Google Maps: analyze business photos | 5 | 156s | Vision confirmed vape products (true) |

### Token Usage

~1,600 tokens per action cycle (80% reduction from initial architecture).

## Project Structure

```
backend/
  src/agent_core/
    agent/
      nodes.py          # Graph nodes (analyze_and_plan, decide_action, observe, evaluate, etc.)
      graph.py           # LangGraph wiring
      prompts.py         # System prompts and templates
      llm_client.py      # Model routing (main/fast/vision)
    tools/
      browser_tools.py   # 35 tool definitions + TOOL_GROUPS
    schemas/
      agent.py           # AgentState, Goal, Plan, Evaluation
      actions.py         # ActionType, Action, ActionResult
      dom.py             # PageContext, DOMElement (compressed representation)
    playwright/
      orchestrator.py    # Playwright test runner
      dom_extractor.py   # Priority-based DOM extraction
      action_executor.py # Browser action execution + vision analysis
    server/
      app.py             # FastAPI application
      ws_handler.py      # WebSocket message handler
      session.py         # Session management
      key_vault.py       # API key storage

extension/
  entrypoints/
    background.ts        # Service worker (WebSocket hub, action routing)
    content.ts           # Content script (DOM extraction, action execution)
    sidepanel/
      index.html         # Side panel UI
      main.ts            # UI logic
      style.css          # Styling

dashboard/               # Next.js monitoring dashboard (optional)
docs/
  agent_workflow.mmd     # Mermaid diagram source
  agent_workflow.png     # Compiled workflow diagram
```

## License

Private — All rights reserved.
