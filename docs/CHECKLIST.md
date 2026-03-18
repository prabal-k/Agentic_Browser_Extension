# Project Checklist — Agentic Browser Extension

## Phase 1 — Backend Core: Agent State, Schema & Project Setup ✅ COMPLETE
- [x] Initialize Python project (pyproject.toml, venv, dependencies)
- [x] Define Pydantic schemas (PageContext, DOMElement, Action, AgentMessage, InterruptRequest)
- [x] Define LangGraph AgentState (TypedDict) — Full cognitive architecture with Goal, Plan, ReasoningTrace, SelfCritique, Evaluation, RetryContext, TaskMemory
- [x] Define tool schemas (click, type_text, scroll, navigate, extract_text, wait, ask_user + 7 more)
- [x] Set up project directory structure
- [x] Set up structlog logging configuration
- [x] Set up pytest infrastructure with sample tests — 65 tests, all passing
- [x] Create sample DOM snapshots (Google Search, Wikipedia, Contact Form)

## Phase 2 — LangGraph Agent: Graph, Nodes & Reasoning ✅ COMPLETE
- [x] Create LangGraph StateGraph with 13 nodes, 5 conditional routing functions
- [x] Implement reasoning node (ReAct + CoT with JSON structured output)
- [x] Implement decide_action node (tool-bound LLM selects browser action)
- [x] Implement confirm_action node (LangGraph interrupt() for user confirmation)
- [x] Implement execute_action_node (interrupt-based browser execution)
- [x] Implement evaluate node (post-action goal progress assessment)
- [x] Implement self_critique_action node (success/failure/retry/replan routing)
- [x] Implement handle_retry node (different-strategy retry logic)
- [x] Implement ask_user_node (LangGraph interrupt() for clarification)
- [x] Implement observe node (memory updates, action history)
- [x] Implement analyze_goal + create_plan + critique_plan + finalize nodes
- [x] Define 5 conditional edge routers with all routing paths
- [x] Configure Ollama LLM client (Qwen2.5:32b-instruct) + OpenAI fallback
- [x] Create 6 specialized system prompts (goal, plan, critique, reasoning, action, evaluation, retry)
- [x] Implement MemorySaver checkpointing
- [x] Generate workflow diagram (PNG + mermaid)
- [x] 113 total tests passing (48 Phase 2 + 65 Phase 1)

## Phase 3 — CLI Test Runner & DOM Snapshot Testing ✅ COMPLETE
- [x] Build CLI test runner (Click-based CLI with rich terminal output)
- [x] Implement interactive mode (load snapshot, set goal, run agent with interrupt handling)
- [x] Implement batch mode (run golden test scenarios, produce pass/fail report)
- [x] Create DOM snapshots from real websites (3 fixtures: Google Search, Wikipedia, Contact Form)
- [x] Build DOM snapshot capture utility (httpx + BeautifulSoup, CLI command)
- [x] Create golden test suite (5 scenarios: google search, wikipedia navigate, contact form, wikipedia search, navigate to site)
- [x] Implement `check` command (config display with masked secrets, Ollama connectivity test, snapshot listing)
- [x] Set up secure credential management (.env with git-ignore, .env.example template, SecretStr for API keys)
- [x] Fix Windows Unicode encoding issues (ASCII-safe terminal output)
- [x] 113 tests passing (no regressions from Phase 1 & 2)

## Phase 4 — FastAPI WebSocket Server ✅ COMPLETE
- [x] Create FastAPI app with WebSocket endpoint (`/ws`)
- [x] Define WebSocket message protocol (reuses Phase 1 message schemas)
- [x] Implement session management (SessionManager with max sessions, eviction, lifecycle)
- [x] Implement streaming (graph node outputs streamed as typed WebSocket messages)
- [x] Implement interrupt flow over WebSocket (confirm, execute, ask_user → client responds)
- [x] Implement action execution flow (server sends action_request, client sends action_result)
- [x] Add REST endpoints (`/health`, `/api/config`, `/api/models`, `/api/sessions`)
- [x] Implement connection lifecycle (connect → session → goal → agent loop → done → disconnect)
- [x] Add CORS configuration (configurable origins from settings)
- [x] Add error handling (graceful disconnect, timeout, invalid messages, agent errors)
- [x] Fix structlog `add_logger_name` incompatibility with `PrintLoggerFactory`
- [x] 139 tests passing (26 Phase 4 + 113 existing, zero regressions)

## Phase 5 — Next.js Test Dashboard ✅ COMPLETE
- [x] Initialize Next.js + Tailwind project (Next.js 16, TypeScript, App Router)
- [x] Build WebSocket connection hook (module-level singleton, shared across components)
- [x] Build Zustand store (agent state, message handling, server message dispatch)
- [x] Build Chat Interface component (rich message bubbles with plan/action/eval rendering)
- [x] Build Action Preview component (inline in chat with confidence badge, element details)
- [x] Build Interrupt Input component (dynamic field rendering: confirm, text, select)
- [x] Build DOM Snapshot Viewer (element list, page info, drag-and-drop JSON upload)
- [x] Build Agent Status component (connection indicator, cognitive status, iteration counter)
- [x] Build Floating AI Bubble (animated, pulses when active, color-coded status)
- [x] Build Settings panel (server URL, connect/disconnect, health check, reset)
- [x] Build DOM Snapshot Upload (file picker + drag-and-drop in DOM Viewer)

## Phase 6 — Playwright Real Browser Testing ✅ COMPLETE
- [x] Set up Playwright Python (playwright 1.58 + Chromium)
- [x] Build live DOM extractor (JS-based extraction → PageContext schema, tested on real sites)
- [x] Build live action executor (all 21 ActionTypes mapped to Playwright methods)
- [x] Build orchestrator script (browser ↔ WebSocket full loop, CLI with --url/--goal/--all)
- [x] Create test scenarios on real websites (5 scenarios: Wikipedia, Google, HN, DuckDuckGo, Example.com)
- [x] Record test sessions (Playwright tracing with screenshots + snapshots)
- [x] Measure success rate (OrchestratorResult with action log, timing, pass/fail)
- [x] Document failure modes (ActionStatus enum: element_not_found, timeout, blocked, etc.)

## Phase 7 — Browser Extension MVP ✅ COMPLETE
- [x] Initialize extension project (WXT 0.20 + TypeScript)
- [x] Create manifest.json (MV3, sidePanel, activeTab, storage, host_permissions)
- [x] Build content script: DOM extractor (same logic as Phase 6 Playwright extractor)
- [x] Build content script: Action executor (all 21 ActionTypes via native DOM APIs)
- [x] Build content script: State observer (MutationObserver, debounced DOM change notifications)
- [x] Build background service worker (WebSocket hub, message routing, side panel ↔ content ↔ backend)
- [x] Build side panel UI (chat interface, plan/action/eval rendering, interrupt handling, settings)
- [x] Implement message passing (sp_* messages from panel, extract_dom/execute_action to content script)
- [x] Test on 5 real websites (Hacker News, Wikipedia, DuckDuckGo — DOM extraction + actions verified via Playwright MCP)
- [x] Handle edge cases (page navigation mid-task, tab switching, content script recovery)
  - Tab locking: task locks to the originating tab, actions always target it even if user switches tabs
  - Navigation recovery: detects page load via chrome.tabs.onUpdated, re-injects content script, re-extracts DOM
  - Tab closure: detects task tab closed, notifies user, cancels server task
  - Content script reconnection: ensureContentScript handles dead scripts after navigation
  - Navigation-aware action execution: waits for page load before DOM re-extraction on navigate/go_back/refresh
  - Service worker keepalive: 20s ping prevents MV3 idle timeout during long LLM calls

## Phase 8 — Security & Multi-Provider LLM ✅ COMPLETE
- [x] Secure API key management (KeyVault in-memory store, session tokens, keys never in WS traffic)
- [x] Multi-provider LLM support (OpenAI + Groq + Ollama with `ChatGroq` native class)
- [x] Runtime key submission via REST (`POST /api/keys` → opaque session token)
- [x] Key status & revocation (`GET /api/keys/status`, `DELETE /api/keys`)
- [x] Provider listing with model catalog (`GET /api/providers`)
- [x] Log redaction (structlog processor masks `sk-`, `gsk_`, `lsv2_pt_` patterns in all log output)
- [x] Extension settings UI (API key inputs, provider/model dropdowns, provider status dots)
- [x] `chrome.storage.session` for token storage (cleared on browser close, never synced)
- [x] CORS opened for `chrome-extension://` origins
- [x] LangSmith tracing support (`LANGCHAIN_TRACING_V2`, `load_dotenv(override=True)`)
- [x] Tested: key submission, status check, revocation, WS flow with Groq — all via Playwright MCP
- [x] Tested: no API keys leak in WebSocket traffic (verified via Playwright browser_evaluate)

## Phase 9 — Tool Expansion & DOM Hardening ✅ COMPLETE
- [x] **Sprint 1 — Tool wrappers**: Expanded from 14 → 25 LangGraph `@tool` definitions
  - [x] `hover` — trigger tooltips, dropdowns, hover effects
  - [x] `check` / `uncheck` — toggle checkboxes and switches
  - [x] `go_forward` — browser forward navigation
  - [x] `refresh` — reload current page
  - [x] `key_combo` — keyboard shortcuts (Ctrl+A, Ctrl+Enter, etc.)
  - [x] `new_tab` — open URL in new tab (via `chrome.tabs.create`)
  - [x] `close_tab` — close current tab (via `chrome.tabs.remove`)
  - [x] `switch_tab` — switch between tabs by index (via `chrome.tabs.update`)
  - [x] `extract_table` — parse HTML `<table>` into TSV format
  - [x] `take_screenshot` — capture visible tab (via `chrome.tabs.captureVisibleTab`)
- [x] **Sprint 2 — DOM extraction hardening** (content.ts rewrite)
  - [x] Shadow DOM traversal — recursive `element.shadowRoot` walking for Web Components (YouTube/Polymer)
  - [x] Priority-based element cap — 250 interactive + 50 informational (was flat 200)
  - [x] Viewport-first ordering — in-viewport elements extracted before offscreen
  - [x] Nested element deduplication — skip `<span>` inside `<button>` inside `<a>`
  - [x] `data-testid` / `data-qa` / `data-cy` / `data-test` attribute collection
  - [x] Stable CSS selector generation — prefer `#id` > `[data-testid]` > `tag.stable-class`, skip hashed class names
  - [x] Additional ARIA attributes (`aria-expanded`, `aria-selected`, `aria-checked`, `contenteditable`)
  - [x] `key_combo` action handler — parses "Ctrl+Shift+Enter" into KeyboardEvent
  - [x] `extract_table` action handler — outputs TSV from `<table>` elements
  - [x] Tab management routed to background script (content script cannot access `chrome.tabs`)
- [x] **Tab management in background.ts**
  - [x] `new_tab` — creates tab, waits for load, re-injects content script, extracts DOM
  - [x] `close_tab` — closes tab, switches to remaining tab, extracts DOM
  - [x] `switch_tab` — switches by index, waits for load, extracts DOM
  - [x] `take_screenshot` — captures via `chrome.tabs.captureVisibleTab`
- [x] Tested via Playwright MCP: Google Home → Google Search → YouTube (cross-site navigation with content script injection)

## Phase 10 — Advanced Capabilities ✅ COMPLETE
- [x] **Sprint 3 — Dialog handling & JS evaluation**
  - [x] `HANDLE_DIALOG` ActionType + `@tool handle_dialog` — accept/dismiss/prompt dialogs
  - [x] `EVALUATE_JS` ActionType + `@tool evaluate_js` — run JS in page context, return result
  - [x] Content script: override `window.alert/confirm/prompt` to auto-intercept dialogs
  - [x] Content script: `evaluate_js` handler via `new Function()` with Promise support
  - [x] Dialog history tracked in consoleLogs with `level: 'dialog'`
- [x] **Sprint 4 — Network & console monitoring**
  - [x] `GET_CONSOLE_LOGS` ActionType + `@tool get_console_logs` — last 30 console entries
  - [x] `GET_NETWORK_LOG` ActionType + `@tool get_network_log` — last 30 XHR/fetch entries
  - [x] Content script: monkey-patch `console.log/warn/error/info` → ring buffer (50 entries)
  - [x] Content script: monkey-patch `window.fetch` → captures method, URL, status
  - [x] Content script: monkey-patch `XMLHttpRequest.open/send` → captures method, URL, status
  - [x] All monitoring set up inside `main()` to avoid build-time Node.js errors
  - [x] Tested via Playwright MCP: console.log/warn/error captured on Hacker News
- [x] **Sprint 5 — Smart waiting**
  - [x] `WAIT_FOR_SELECTOR` ActionType + `@tool wait_for_selector` — MutationObserver until CSS selector appears (configurable timeout)
  - [x] `WAIT_FOR_NAVIGATION` ActionType + `@tool wait_for_navigation` — poll `location.href` until URL changes
  - [x] Content script: MutationObserver-based wait with auto-cleanup on timeout
- [x] **Sprint 6 — File upload & drag-and-drop**
  - [x] `UPLOAD_FILE` ActionType + `@tool upload_file` — clicks file input to open dialog
  - [x] `DRAG` ActionType + `@tool drag` — dispatches full drag event sequence (dragstart → dragenter → dragover → drop → dragend) with DataTransfer
  - [x] Content script: drag handler with coordinate calculation from bounding boxes
- [x] **Tool count: 25 → 33 tools** (8 new: get_console_logs, get_network_log, evaluate_js, handle_dialog, upload_file, drag, wait_for_selector, wait_for_navigation)
- [x] All 33 tools verified bound to Groq `llama-3.3-70b-versatile` LLM
- [x] Extension builds clean (58.88 KB)
- [x] Tested on Hacker News and Example.com via Playwright MCP

## Phase 11 — Production & Distribution (Planned)
- [ ] Security audit (OWASP, CSP, extension permissions review)
- [ ] Error handling improvements (graceful degradation, retry with backoff)
- [ ] Performance optimization (DOM extraction timing, WebSocket message batching)
- [ ] UI polish (loading states, animations, responsive layout, dark/light theme)
- [ ] Cross-browser testing (Firefox MV2/MV3 via WXT, Edge)
- [ ] Write privacy policy
- [ ] Create store listing assets (screenshots, description, promo tiles)
- [ ] Chrome Web Store submission
- [ ] Firefox Add-ons submission
- [ ] Create README and user guide
