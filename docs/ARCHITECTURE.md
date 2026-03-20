# Architecture Deep Dive

## Overview

The Agentic Browser Extension is a reactive AI browser agent. It observes the current page, picks the next action to make progress on the user's task, executes it, checks the result, and repeats — until the task is done or it exhausts its strategies.

**No upfront planning. No rigid step execution. Just: look → act → check → repeat.**

## Core Loop

```
User provides task
      |
      v
analyze_and_plan (0 LLM calls)
  - Extracts URLs from task text
  - Preserves original task — no rewriting
  - Sets initial context
      |
      v
decide_action (1 LLM call) <-----------+
  - Sees: original task + current page  |
  - + action history with DATA entries  |
  - Picks ONE tool to call              |
      |                                 |
      v                                 |
execute_action_node                     |
  - Sends action to browser             |
  - Waits for result                    |
      |                                 |
      v                                 |
observe (0 LLM calls)                   |
  - Deterministic page diff             |
  - Stores extracted_data in memory     |
  - Updates current_reasoning           |
      |                                 |
      v                                 |
smart_evaluate (0 LLM calls, ~70%)      |
  - Trivial actions → skip LLM          |
  - Failed actions → LLM evaluate       |
      |                                 |
      v                                 |
self_critique (0 LLM calls)             |
  - Strategy escalation on failure      |
  - Stuck loop detection                |
  - Duplicate extraction detection      |
      |                                 |
      +--- DECIDING → back to top ------+
      |
      +--- COMPLETED → verify_goal → finalize → END
```

## Strategy Escalation

When text extraction fails repeatedly, the agent doesn't give up — it escalates:

```
Level 1: read_page fails → scroll down, try read_page again
Level 2: scroll + read fails → try visual_check (screenshot → vision AI)
Level 3: visual_check done → report whatever findings exist
Level 4: no findings at all → navigate to different site, try again
Level 5: all strategies exhausted → force-terminate with partial report
```

Each level is a **code-level fallback**, not a prompt suggestion. The code overrides the LLM's action choice when repeated failure is detected.

## Model Routing

Three models are used, selected automatically per-action:

| Model | Config Key | When Used | Latency |
|---|---|---|---|
| `qwen3.5:27b` | `AGENT_OLLAMA_MODEL` | First action, after findings, failed recovery, forced done | ~20-30s |
| `qwen3.5:9b` | `AGENT_FAST_MODEL` | Simple actions: navigate, click, type, scroll | ~5-10s |
| `qwen3-vl:8b` | `AGENT_VISION_MODEL` | visual_check tool (screenshot analysis) | ~15-20s |

**Routing logic** (in `decide_action`):
- First action → big model (needs to understand the task)
- Has findings in history → big model (needs to decide if done)
- Last action failed → big model (needs smarter recovery)
- STUCK/DUPLICATE detected → big model (9b ignores forced instructions)
- Everything else → fast model

## DOM Extraction

**Priority-based** — ensures critical elements are never cut off by the 200-element cap:

1. **P0 (Always included)**: `input`, `textarea`, `select`, `button`, `[role="button"]`
2. **P1 (Viewport first)**: `a[href]`, `[role="link"]`, `[role="tab"]` — viewport elements before off-screen
3. **P2 (If space)**: `h1`-`h6`, `p`, `li`, `img[alt]`, `nav`, `dialog`

**Element representation** (compressed, ~40 chars per element):
```
[1] input ph="Search in Daraz" *focused [form: search]
[2] btn "Add to Cart" [form: checkout]
[3] link "new" href="/newest" [nav bar]
```

**Page context** has two modes:
- `compact=False`: Full element details (for decide_action)
- `compact=True`: Minimal ID + type + short text (for evaluate)

## Token Streaming

LLM tokens stream to the WebSocket in real-time via `WebSocketStreamingHandler`:

```
LLM generates token
    → on_llm_new_token callback fires
    → Tokens buffered (every 3)
    → server_token WS message sent
    → Extension UI appends to streaming bubble
```

Messages:
- `server_stream_start` — LLM call begins
- `server_token` — chunk of tokens
- `server_stream_end` — LLM call complete (includes total_tokens count)

## Vision Pipeline

`visual_check` tool flow:

```
Agent calls visual_check("Are there vape products in this photo?")
    → Action: TAKE_SCREENSHOT with value="__VISUAL_CHECK__|question"
    → Playwright: page.screenshot(type="png")
    → base64 encode → Ollama HTTP API → qwen3-vl:8b
    → Vision model answers the specific question
    → Response stored in extracted_data
    → Flows back to agent via action history DATA entries
```

**Key design**: The vision prompt is dynamic — whatever the agent passes as `description` becomes the question asked to the vision model. No hardcoded "describe the page".

## Safety Nets

| Safety Net | Trigger | Action |
|---|---|---|
| **Max iterations** | 25+ iterations | Force COMPLETED |
| **Max actions** | 12+ total actions | Force COMPLETED |
| **No findings** | 8+ actions, zero extracted data | Force COMPLETED |
| **Duplicate extraction** | Last 2 reads return same text | Strategy escalation |
| **Empty extraction** | Last 2 reads return < 5 chars | Strategy escalation |
| **Stuck loop** | 3x same action type, no URL change | Force different action or done |

## Tool Architecture

**35 tools total**, organized into groups for dynamic selection:

### Core (Always Available — 13 tools)
```
click, type_text, clear_and_type, navigate, go_back, scroll_down,
press_key, read_page, visual_check, extract_text, wait, ask_user, done
```

### Search (Always Added — 4 tools)
```
select_option, scroll_up, scroll_to_element, key_combo
```

### Context-Dependent (Added when relevant)
- **Forms**: `check`, `uncheck`, `hover`, `upload_file`
- **Data**: `extract_table`, `evaluate_js`
- **Tabs**: `new_tab`, `close_tab`, `switch_tab`
- **Advanced**: `drag`, `handle_dialog`, `go_forward`, `refresh`
- **Waiting**: `wait_for_selector`, `wait_for_navigation`

### Special Tools
- `read_page` — reads DOM text from main content area (tries `<main>`, `<article>`, `[role="main"]` before `body`)
- `visual_check` — sends screenshot to vision model with task-specific question
- `done(summary)` — terminates with findings. Summary becomes the user-facing output.

### Tool Aliases
When LLM hallucinates a removed tool, code-level aliases fix it:
- `take_screenshot` → `visual_check`
- `fill` → `type_text`

## Findings Flow

How information gathered by the agent reaches the user:

```
read_page/visual_check returns extracted_data
    → observe stores in task_memory.important_data
    → observe updates current_reasoning with findings preview
    → format_action_history includes DATA entries in prompt
    → self_critique reminds LLM: "you have findings, check if they answer the task"
    → LLM calls done(summary) with formatted answer
    → finalize builds task_summary from done() arg + memory findings
    → _send_done sends summary to client
```

## Auto-Confirm

Most actions are auto-confirmed (no user interaction needed):

| Auto-Confirmed | Needs User Confirmation |
|---|---|
| click, type_text, navigate, scroll, press_key, read_page, visual_check, extract_text, go_back, hover, wait, done | evaluate_js, upload_file |

## URL Extraction

URLs in the user's task text are extracted at the code level (no LLM needed):

```python
_URL_PATTERN = re.compile(r'https?://[^\s<>"]+')
```

If a URL is found and the page is `about:blank`, the initial reasoning says "Target URL detected" and the first `decide_action` navigates to it.

## File Structure

```
backend/src/agent_core/
├── agent/
│   ├── nodes.py          # All graph nodes (analyze_and_plan, decide_action,
│   │                     #   observe, smart_evaluate, evaluate, self_critique,
│   │                     #   verify_goal, finalize, reason, handle_retry)
│   ├── graph.py          # LangGraph wiring + routing functions
│   ├── prompts.py        # System prompts + human message templates
│   └── llm_client.py     # Model routing (main/fast/vision) + dynamic tool selection
├── tools/
│   └── browser_tools.py  # 35 tool definitions + TOOL_GROUPS
├── schemas/
│   ├── agent.py          # AgentState, Goal, Plan, PlanStep, Evaluation, etc.
│   ├── actions.py        # ActionType (31 types), Action, ActionResult
│   └── dom.py            # PageContext, DOMElement (compressed repr)
├── playwright/
│   ├── orchestrator.py   # Playwright test runner (automated scenarios)
│   ├── dom_extractor.py  # Priority-based DOM extraction (JS eval)
│   └── action_executor.py # Browser action execution + vision analysis
├── server/
│   ├── app.py            # FastAPI app + REST endpoints
│   ├── ws_handler.py     # WebSocket handler + streaming callback
│   ├── session.py        # Session management
│   └── key_vault.py      # API key storage
└── config.py             # Settings from .env

extension/
├── entrypoints/
│   ├── background.ts     # Service worker (WS hub, action routing, tab management)
│   ├── content.ts        # Content script (DOM extraction, action execution)
│   └── sidepanel/
│       ├── index.html    # Side panel structure
│       ├── main.ts       # UI logic (messages, streaming, status)
│       └── style.css     # Dark theme with glassmorphism
└── .output/chrome-mv3/   # Built extension (load in Chrome)
```
