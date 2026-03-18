# Agentic Browser Extension — Development Plan

## Project Overview

An AI-powered browser extension that understands page context, reasons about user goals,
and performs actions (click, type, scroll, navigate) autonomously using a goal-based
LangGraph agent with ReAct + Chain of Thought reasoning.

**Architecture**: Two-part system
- **Backend** (Python): FastAPI + LangGraph + Ollama/OpenAI — the agent brain
- **Test Dashboard** (TypeScript): Next.js + React + ShadCN UI — visual debugging & component prototyping
- **Extension** (TypeScript): Chrome MV3 + React + ShadCN UI — production frontend

**Communication**: WebSocket (industry standard for bidirectional streaming + interrupt/resume)

---

## Technology Stack

### Backend
| Component | Technology | Justification |
|---|---|---|
| Language | Python 3.11+ | Developer expertise, LangGraph ecosystem |
| Agent Framework | LangGraph | Goal-based agent, interrupt(), Send(), checkpointing |
| API Server | FastAPI | Async-native, WebSocket support, industry standard |
| WebSocket | FastAPI WebSocket + starlette | Bidirectional streaming, interrupt/resume |
| LLM (Primary) | Ollama — Qwen2.5:32b-instruct | Free, private, strong tool calling |
| LLM (Fallback) | OpenAI GPT-4o | Better reasoning for complex tasks |
| Browser Testing | Playwright (Python) | Real browser automation for pre-extension testing |
| Testing | pytest + pytest-asyncio | Async test support |
| Validation | Pydantic v2 | Request/response schemas, strict typing |
| Logging | structlog | Structured JSON logging, production-grade |

### Test Dashboard (Pre-Extension Frontend)
| Component | Technology | Justification |
|---|---|---|
| Framework | Next.js 14+ (App Router) | React SSR, API routes, industry standard |
| UI Components | ShadCN UI + Radix | Clean, accessible, reusable in extension later |
| Styling | TailwindCSS | Consistent with ShadCN, utility-first |
| WebSocket Client | native WebSocket API | Direct connection to FastAPI backend |
| State | Zustand | Lightweight, works in both dashboard and extension |
| Animations | Framer Motion | Smooth AI bubble, transitions |

### Browser Extension (Production Frontend — Later)
| Component | Technology | Justification |
|---|---|---|
| Manifest | V3 | Required (V2 deprecated) |
| Bundler | WXT or Vite + CRXJS | MV3-optimized build tooling |
| UI | React + ShadCN (reused from dashboard) | Component reuse |
| Side Panel | Chrome sidePanel API | Native MV3 side panel |
| Content Script | TypeScript | DOM extraction + action execution |

---

## Phase Breakdown

---

### PHASE 1 — Backend Core: Agent State, Schema & Project Setup
**Objective**: Set up project infrastructure, define all data models, and establish
the foundational agent state that everything else builds on.

**Tasks**:
- [ ] Initialize Python project (pyproject.toml, virtual environment, dependency management)
- [ ] Define Pydantic schemas: PageContext, DOMElement, Action, AgentMessage, InterruptRequest
- [ ] Define LangGraph AgentState (TypedDict)
- [ ] Define tool schemas (click, type_text, scroll, navigate, extract_text, wait, ask_user)
- [ ] Set up project directory structure (backend/, docs/, extension/)
- [ ] Set up structlog logging configuration
- [ ] Set up pytest infrastructure with sample tests
- [ ] Create sample DOM snapshots from real websites (Google, Wikipedia, a simple form)

**Risks**:
- Schema design locks in data flow — changes later are expensive
- DOM snapshot format must work for both LLM consumption and action execution

**Validation Criteria**:
- All Pydantic models validate correctly with sample data
- AgentState can be instantiated and serialized
- Tool schemas are complete and well-documented
- Sample DOM snapshots load and validate against PageContext schema
- All tests pass

**Test Strategy**:
- Unit tests: Schema validation (valid + invalid data)
- Unit tests: AgentState serialization/deserialization
- Unit tests: Tool schema validation
- Edge case: Empty DOM, DOM with 500+ elements, nested iframes representation

---

### PHASE 2 — LangGraph Agent: Graph, Nodes & Reasoning
**Objective**: Build the core LangGraph state graph with all nodes, edges,
and the ReAct + CoT reasoning loop.

**Tasks**:
- [ ] Create LangGraph StateGraph with nodes: reasoning, tool_selection, confirm_with_user, execute, evaluate
- [ ] Implement reasoning node (ReAct + Chain of Thought prompting)
- [ ] Implement tool_selection node (agent chooses from available tools)
- [ ] Implement confirm_with_user node (LangGraph interrupt() for human-in-the-loop)
- [ ] Implement execute node (returns action to be executed externally)
- [ ] Implement evaluate node (assess action result, decide next step)
- [ ] Define conditional edges: should_act, needs_clarification, is_done, should_retry
- [ ] Implement Send() for parallel operations (e.g., extract multiple page sections)
- [ ] Configure Ollama LLM client (ChatOllama with Qwen2.5:32b-instruct)
- [ ] Configure OpenAI LLM client as fallback
- [ ] Create system prompts with ReAct template, tool definitions, safety constraints
- [ ] Implement checkpointing (MemorySaver for dev, can swap to persistent later)

**Graph Structure**:
```
START → reasoning → route_decision
                     ├─ "act" → confirm_with_user (interrupt) → execute → evaluate → reasoning
                     ├─ "clarify" → ask_user (interrupt) → reasoning
                     ├─ "parallel" → Send() to multiple workers → collect → reasoning
                     ├─ "retry" → reasoning (with error context)
                     └─ "done" → END
```

**Risks**:
- Prompt engineering is iterative — first prompts won't be optimal
- Ollama Qwen2.5 may not follow tool calling format reliably
- ReAct loop may get stuck in infinite reasoning cycles

**Validation Criteria**:
- Agent can receive a goal + DOM snapshot and produce a valid action plan
- interrupt() pauses execution and resumes correctly after input
- Agent correctly routes between act/clarify/done/retry
- Agent handles "I don't know" / "I can't do this" gracefully
- No infinite loops (max iteration guard)

**Test Strategy**:
- Unit tests: Each node in isolation with mocked state
- Integration test: Full graph execution with mock LLM responses
- Integration test: interrupt() + resume flow
- Integration test: Send() parallel execution
- Edge cases: Empty DOM, ambiguous goal, impossible task, LLM returns invalid JSON
- Golden tests: 5 known scenarios with expected action sequences

---

### PHASE 3 — CLI Test Runner & DOM Snapshot Testing
**Objective**: Build a CLI tool to test the agent end-to-end without any browser or frontend.
Feed DOM snapshots, interact with the agent, validate outputs.

**Tasks**:
- [ ] Build CLI test runner (Python script with rich/click for nice terminal output)
- [ ] Implement interactive mode: user types goal, sees reasoning, approves actions
- [ ] Implement batch mode: run predefined test scenarios, output results
- [ ] Create 10+ DOM snapshots from real websites across categories:
      - Search engine (Google)
      - Information site (Wikipedia)
      - E-commerce product page (simple)
      - Form page (contact form)
      - Login page
      - Navigation-heavy site
- [ ] Implement DOM snapshot capture utility (Python script using requests + BeautifulSoup)
- [ ] Create golden test suite: known goal + DOM → expected action sequence
- [ ] Measure and log: reasoning time, token usage, action accuracy

**Risks**:
- Static DOM snapshots don't capture dynamic page behavior
- Golden tests are brittle if prompt changes

**Validation Criteria**:
- CLI runner connects to Ollama successfully
- Agent produces correct actions for at least 7/10 golden test cases
- Interactive mode properly handles interrupt/resume
- Batch mode produces a clear pass/fail report
- Token usage is logged per task

**Test Strategy**:
- Run all golden test cases, record pass/fail
- Manual testing: 5 freeform goals, evaluate agent reasoning quality
- Performance: Measure avg response time per step with Ollama
- Failure testing: Feed malformed DOM, missing elements, very large pages

---

### PHASE 4 — FastAPI WebSocket Server
**Objective**: Expose the LangGraph agent via a FastAPI WebSocket server
that the test dashboard (and later the extension) connects to.

**Tasks**:
- [ ] Create FastAPI application with WebSocket endpoint
- [ ] Define WebSocket message protocol (JSON):
      - Client → Server: goal, user_response (to interrupts), dom_snapshot, action_result
      - Server → Client: reasoning_stream, action_request, interrupt_request, status, error
- [ ] Implement session management (one agent graph instance per WebSocket connection)
- [ ] Implement streaming: send reasoning tokens as they arrive (LLM streaming)
- [ ] Implement interrupt flow over WebSocket:
      - Server sends interrupt_request with field definitions (type, label, options)
      - Client renders appropriate input field
      - Client sends user_response
      - Server resumes graph
- [ ] Implement action execution flow:
      - Server sends action_request (click element X)
      - Client (dashboard/extension) executes on page
      - Client sends action_result (success/failure + new DOM)
      - Server resumes graph with result
- [ ] Add REST endpoints: health check, model info, session list
- [ ] Implement connection lifecycle: connect, disconnect, reconnect handling
- [ ] Add CORS configuration for local development
- [ ] Add rate limiting and basic error handling

**WebSocket Message Protocol**:
```json
// Client → Server
{"type": "goal", "data": {"goal": "Search for pizza", "dom": {...}}}
{"type": "user_response", "data": {"interrupt_id": "abc", "value": "yes"}}
{"type": "action_result", "data": {"action_id": "xyz", "success": true, "new_dom": {...}}}

// Server → Client
{"type": "reasoning", "data": {"content": "I see a search box...", "streaming": true}}
{"type": "action_request", "data": {"action_id": "xyz", "action": "click", "element_id": 5, "description": "Click search button"}}
{"type": "interrupt", "data": {"interrupt_id": "abc", "question": "Proceed with order?", "input_type": "confirm", "options": ["Yes", "No"]}}
{"type": "status", "data": {"state": "done", "summary": "Task completed"}}
{"type": "error", "data": {"message": "Element not found", "recoverable": true}}
```

**Risks**:
- WebSocket connection drops during long-running tasks
- Concurrent sessions may overload Ollama
- Message ordering must be guaranteed

**Validation Criteria**:
- WebSocket connects and maintains connection for 5+ minutes
- Full goal → reasoning → action → result loop works over WebSocket
- Interrupt flow works: server pauses, client responds, server resumes
- Streaming tokens arrive in real-time
- Graceful handling of disconnection mid-task
- Health check endpoint responds

**Test Strategy**:
- Unit tests: Message serialization/deserialization
- Integration test: WebSocket connection lifecycle
- Integration test: Full agent loop over WebSocket (with mock LLM)
- Integration test: Interrupt/resume over WebSocket
- Load test: 3 concurrent WebSocket sessions
- Failure test: Client disconnects mid-task, server handles gracefully
- Use `websocat` or Python websocket client for manual testing

---

### PHASE 5 — Next.js Test Dashboard
**Objective**: Build a Next.js web app that connects to the backend via WebSocket,
displays agent reasoning in real-time, and serves as a visual testing/debugging tool.
Components built here will be reused in the browser extension.

**Tasks**:
- [ ] Initialize Next.js project with TypeScript, TailwindCSS, ShadCN UI
- [ ] Set up ShadCN components: Button, Input, Card, Dialog, ScrollArea, Badge, Textarea
- [ ] Build WebSocket connection hook (useWebSocket) with reconnection logic
- [ ] Build Zustand store for agent state (messages, status, currentAction, interruptData)
- [ ] Build Chat Interface component:
      - Message bubbles (user / agent / system)
      - Streaming text display (token-by-token)
      - Auto-scroll to bottom
- [ ] Build Action Preview component:
      - Shows planned action with element details
      - Confirm / Reject buttons
      - Action execution status indicator
- [ ] Build Interrupt Input component (CRITICAL — not a text box):
      - Dynamically renders input field based on data type from server
      - Types: confirm (Yes/No buttons), text (input field), select (dropdown),
        number (number input), multi-select (checkboxes)
      - Smooth animation on appear/disappear
- [ ] Build DOM Snapshot Viewer component:
      - Tree view of extracted DOM elements
      - Highlight which element the agent is targeting
      - Useful for debugging
- [ ] Build Agent Status component:
      - Current state: reasoning / executing / waiting / done
      - Step counter
      - Token usage display
- [ ] Build Floating AI Bubble component:
      - Circular button, expandable to full chat panel
      - Smooth Framer Motion animations
      - This exact component will be reused in the extension
- [ ] Build Settings panel:
      - Backend URL configuration
      - Model selection (Ollama / OpenAI)
      - API key input (for OpenAI)
- [ ] Build DOM Snapshot Upload:
      - Paste or upload a JSON DOM snapshot
      - Or enter a URL (backend fetches via Playwright)

**Risks**:
- WebSocket state management complexity
- ShadCN component customization learning curve
- Streaming text rendering performance

**Validation Criteria**:
- Dashboard connects to backend WebSocket successfully
- User can type a goal and see agent reasoning stream in real-time
- Interrupt input fields render correctly for all data types
- Action preview shows correct element details and accepts confirm/reject
- Floating bubble opens/closes smoothly
- Components are modular and exportable for extension reuse
- Responsive layout works on different screen sizes

**Test Strategy**:
- Manual testing: Full flow with live backend
- Component testing: Each ShadCN component renders correctly
- WebSocket testing: Connection, disconnection, reconnection
- Interrupt testing: All input types render and submit correctly
- Visual testing: UI matches ShadCN design principles
- Cross-browser: Test in Chrome, Firefox, Edge

---

### PHASE 6 — Playwright Real Browser Testing
**Objective**: Use Playwright (Python) to connect the agent to a REAL browser.
The agent extracts live DOM, reasons, and executes actions on actual websites.
This validates the full loop before building the extension.

**Tasks**:
- [ ] Set up Playwright Python with Chromium
- [ ] Build live DOM extractor (Playwright equivalent of the content script):
      - Find all interactive elements
      - Assign numeric IDs
      - Extract text, type, attributes, visibility, bounding box
      - Return PageContext matching the Pydantic schema
- [ ] Build live action executor:
      - Receives Action from agent
      - Maps element_id back to Playwright locator
      - Executes: click, type, scroll, navigate
      - Returns result + new DOM snapshot
- [ ] Build orchestrator script:
      - Opens browser with Playwright
      - Connects to FastAPI WebSocket
      - Loops: extract DOM → send to agent → receive action → execute → repeat
- [ ] Create test scenarios on real websites:
      - Google: Search for a term, click first result
      - Wikipedia: Navigate to a page, extract information
      - Simple form: Fill out and submit a contact form
      - Multi-step: Navigate 2-3 pages to reach a goal
- [ ] Record test sessions (Playwright trace) for debugging
- [ ] Measure end-to-end success rate per scenario
- [ ] Document failure modes and DOM extraction gaps

**Risks**:
- Real websites change their DOM structure
- Anti-bot detection on some sites
- Playwright locator strategy may differ from extension content script
- Network latency adds to already slow LLM response times

**Validation Criteria**:
- Agent successfully completes 3/5 test scenarios end-to-end
- DOM extraction captures all interactive elements on test sites
- Action execution works for click, type, scroll, navigate
- Full loop time (extract → reason → execute) under 15 seconds per step
- Playwright traces capture full session for debugging

**Test Strategy**:
- Run each scenario 3 times, record success rate
- Compare DOM extraction output with manually verified element lists
- Failure analysis: categorize why failures happen (wrong element, missing element, LLM error)
- Performance profiling: time per step breakdown

---

### PHASE 7 — Browser Extension (MVP)
**Objective**: Build the Chrome MV3 extension that replaces Playwright.
The extension extracts DOM and executes actions, while the backend does all reasoning.

**Tasks**:
- [ ] Initialize extension project (WXT or Vite + CRXJS, TypeScript)
- [ ] Create manifest.json with minimum required permissions
- [ ] Build content script: DOM extractor (port Playwright logic to JS)
- [ ] Build content script: Action executor (port Playwright logic to JS)
- [ ] Build content script: State observer (MutationObserver for DOM changes)
- [ ] Build background service worker:
      - WebSocket connection to backend
      - Message routing: side panel ↔ content script ↔ backend
      - Connection lifecycle management
- [ ] Build side panel UI (reuse components from test dashboard):
      - Chat interface
      - Action preview + confirm
      - Interrupt input fields
      - Floating bubble (in content script, not side panel)
      - Settings
- [ ] Implement message passing: side panel → service worker → content script → backend
- [ ] Test on 5 real websites with same scenarios as Playwright phase
- [ ] Handle edge cases: page navigation mid-task, tab switching, extension reload

**Risks**:
- MV3 service worker lifecycle (can be killed)
- Content script CSP restrictions on some sites
- Message passing complexity (3 contexts communicating)
- React bundle size in content script

**Validation Criteria**:
- Extension loads in Chrome developer mode
- Side panel opens and connects to backend
- DOM extraction matches Playwright output on same pages
- Full loop works: user goal → agent reasons → action confirmed → executed on page
- Extension survives page navigation without breaking
- Works on at least 3 different websites

**Test Strategy**:
- Manual testing on 5 real websites
- Compare DOM extraction: extension vs Playwright (should be near-identical)
- Stress test: Run 10 tasks in sequence without reloading
- Edge case: Close side panel mid-task, navigate away, reload page
- Cross-browser: Test on Chrome + Edge (both Chromium)

---

### PHASE 8 — Hardening, Polish & Deployment
**Objective**: Production-quality extension ready for Chrome Web Store submission
and use by friends.

**Tasks**:
- [ ] Security audit: Permissions, data handling, API key storage, CSP compliance
- [ ] Error handling: Graceful failures, user-friendly error messages, retry logic
- [ ] Performance optimization: DOM extraction speed, bundle size, memory usage
- [ ] UI polish: Animations, loading states, empty states, error states
- [ ] Cross-browser testing and fixes (Chrome, Edge, Brave, Firefox)
- [ ] Write privacy policy
- [ ] Create extension store listing (screenshots, description)
- [ ] Chrome Web Store submission ($5 fee, credit card required)
- [ ] Firefox Add-ons submission (free)
- [ ] Create README with setup instructions for self-hosting backend
- [ ] Create user guide: How to install, configure, and use

**Risks**:
- Store review rejection (broad permissions)
- Cross-browser edge cases

**Validation Criteria**:
- Extension passes Chrome Web Store review
- No security vulnerabilities in permissions or data handling
- Works reliably on 10+ websites
- Bundle size under 2MB
- Full documentation complete

**Test Strategy**:
- Security: Manual audit of all data flows
- Performance: Measure DOM extraction time on 20 sites
- Cross-browser: Full test suite on 4 browsers
- User testing: 3 friends install and use with no guidance

---

## Deployment Overview

| Target | Cost | Credit Card | Timeline |
|---|---|---|---|
| Local dev (Load Unpacked) | Free | No | From Phase 7 |
| Chrome Web Store | $5 one-time | Yes | Phase 8 |
| Firefox Add-ons | Free | No | Phase 8 |
| Edge Add-ons | Free | No | Phase 8 |
| Backend (your Ollama server) | Free (your hardware) | No | From Phase 4 |

---

## Estimated Timeline

| Phase | Duration | Cumulative |
|---|---|---|
| Phase 1: Backend Core | 1 week | Week 1 |
| Phase 2: LangGraph Agent | 2 weeks | Week 3 |
| Phase 3: CLI Testing | 1 week | Week 4 |
| Phase 4: WebSocket Server | 1.5 weeks | Week 5-6 |
| Phase 5: Test Dashboard | 2 weeks | Week 7-8 |
| Phase 6: Playwright Testing | 1.5 weeks | Week 9-10 |
| Phase 7: Extension MVP | 2.5 weeks | Week 12 |
| Phase 8: Hardening | 2 weeks | Week 14 |

**Total: ~14 weeks to production-ready extension**

---

## Key Design Decisions

1. **WebSocket over REST** — Bidirectional streaming required for real-time reasoning display and interrupt/resume flow. Industry standard for real-time AI applications.

2. **Separate backend** — LangGraph is Python-only. MV3 service workers are ephemeral. Complex agent logic must live server-side.

3. **ShadCN UI** — Clean, accessible components. Build once in dashboard, reuse in extension.

4. **Dynamic interrupt inputs** — Not a chat textbox. Server sends field definition (type, label, options), client renders appropriate ShadCN component (Input, Select, Button group, etc.).

5. **Qwen2.5:32b-instruct on Ollama** — Best structured output / tool calling in the 32B class. Free inference on user's server.

6. **Test dashboard before extension** — De-risks the TypeScript/React learning curve. Components transfer directly to the extension.

7. **Playwright before extension** — Validates the full agent loop with real browsers using Python (comfort zone) before porting to JS content scripts.
