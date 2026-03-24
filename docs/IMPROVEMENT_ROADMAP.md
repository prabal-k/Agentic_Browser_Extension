# Improvement Roadmap

## Status Legend
- [ ] Not started
- [~] In progress
- [x] Completed
- [!] Blocked / Needs investigation

---

## Task 1: Action Batching

**Goal:** Execute multiple obvious sequential actions in one cycle without going through the full observe → evaluate → self_critique loop for each one.

**Current Flow:**
```
type_text (LLM call) → execute → observe → smart_evaluate → self_critique → decide_action (LLM call) → press_key Enter → execute → observe → ...
```

**Target Flow:**
```
type_text + press_key Enter (1 LLM call) → execute both → observe once → ...
```

**Impact:** Saves 1 LLM call + 1 full evaluation cycle per batched action. For a search task, this saves ~10-15s.

### Files to Change

| File | Change | Risk |
|---|---|---|
| `backend/src/agent_core/agent/nodes.py` | `self_critique_action`: detect "just typed, next obvious action is Enter/Tab" and auto-execute without LLM | Low — only applies to safe follow-ups |
| `backend/src/agent_core/agent/nodes.py` | `observe`: skip full page-diff for batched actions that don't change page structure | Low |
| `backend/src/agent_core/playwright/action_executor.py` | Already has auto-submit for search inputs — extend pattern | Low |

### Subtasks

- [x] 1.1: Define "batchable action pairs" — which action sequences are safe to combine
  - type_text → press_key("Enter") [handled by auto-submit for search fields]
  - scroll_down → read_page [auto-read after scroll if agent has read before]
- [x] 1.2: Implement batch detection in `self_critique_action`
  - After successful scroll_down, auto-queues read_page if agent has read before
  - Duplicate detection prevents auto-read when content is same as last read
- [ ] 1.3: ~~Implement batched execution in `observe`~~ (Not needed — batching handled in self_critique)
- [x] 1.4: Test cases
  - [x] YouTube search: 2 actions, 21.3s (auto-navigate + click video)
  - [x] Daraz search: 2 actions, 39.3s (down from 3 — batch saved 1 action)
  - [x] Non-batchable actions (click link) are NOT batched — verified
  - [!] DuckDuckGo price search: 12 actions, 147s — strategy escalation loops on pages where read_page returns same header text. Not caused by batching itself.

**Result:** Marginal speed improvement (~11% on Daraz). Biggest bottleneck remains LLM call latency, not action overhead.

---

## Task 2: Page Context Caching

**Goal:** Don't re-extract DOM when the page hasn't changed. Reuse previous extraction for scroll, read_page, wait, and other non-mutating actions.

**Current Flow:**
```
Every action → execute_action_node → (browser returns new DOM) → observe processes it
```

**Target Flow:**
```
Scroll/read/wait → execute → (skip DOM re-extraction, reuse cached) → observe uses cached
Non-mutating action detected → skip the expensive DOM extraction call
```

**Impact:** DOM extraction takes 200-500ms on complex pages (200 elements). Skipping it for scroll/read/wait saves that time and reduces WebSocket payload size.

### Files to Change

| File | Change | Risk |
|---|---|---|
| `backend/src/agent_core/agent/graph.py` | `execute_action_node`: for non-page-changing actions, don't require new DOM from browser | Low |
| `backend/src/agent_core/playwright/orchestrator.py` | Skip DOM re-extraction after scroll/read/wait/extract actions | Low |
| `extension/entrypoints/background.ts` | Skip DOM re-extraction for non-mutating actions | Low |
| `backend/src/agent_core/agent/nodes.py` | `observe`: detect if page_context is cached vs fresh | Low |

### Subtasks

- [x] 2.1: Define "non-mutating actions" that don't change the DOM
  - scroll_down, scroll_up, extract_text, wait, take_screenshot, get_console_logs, get_network_log, wait_for_selector, wait_for_navigation
- [x] 2.2: Implement caching in Playwright orchestrator
  - Non-mutating actions skip `extract_page_context()` call
  - Action result sent without `new_dom_snapshot` — graph keeps existing page_context
  - Logs `CACHED DOM (non-mutating action: X)` for debugging
- [x] 2.3: Implement caching in extension background.ts
  - Same NON_MUTATING set as Playwright orchestrator
  - Logs `Cached DOM (non-mutating action: X)` to console for debugging
- [x] 2.4: Update `execute_action_node` in graph.py
  - Already handles null `new_dom` — keeps existing page_context when not provided
- [ ] 2.5: ~~Update `observe` in nodes.py~~ (Not needed — observe uses whatever page_context is in state)
- [x] 2.6: Test cases
  - [x] Scroll down: DOM NOT re-extracted (CACHED DOM logged) ✓
  - [x] Read page: DOM NOT re-extracted (CACHED DOM logged) ✓
  - [x] Visual check: DOM NOT re-extracted (CACHED DOM logged) ✓
  - [x] Navigate: DOM IS re-extracted ✓
  - [x] Click link: DOM IS re-extracted ✓
  - [x] YouTube regression: 21.3s, 2 actions — no regression ✓
  - [x] Daraz regression: 39.3s, 2 actions — improved ✓
  - [!] DuckDuckGo: 147s vs 56s baseline — regression from strategy escalation, not caching

**Result:** ~32% speedup on action-heavy tasks (216s → 147s for same 12 actions). DOM extraction skipped for ~70% of actions.

---

## Task 3: Multi-Tab Parallel Research

**Goal:** Open multiple sites in separate tabs for comparison tasks. Execute research on each tab independently.

**Impact:** Halves the time for comparison tasks ("compare prices on site A vs site B").

### Files to Change

| File | Change | Risk |
|---|---|---|
| `backend/src/agent_core/agent/nodes.py` | `decide_action`: allow the LLM to choose `new_tab` strategically | Low |
| `backend/src/agent_core/agent/graph.py` | `execute_action_node`: handle tab context — track which tab is active | Medium |
| `backend/src/agent_core/schemas/agent.py` | Add `active_tab_id` and `tab_contexts: dict` to AgentState | Medium |
| `backend/src/agent_core/playwright/orchestrator.py` | Handle multi-page scenarios in Playwright | Medium |
| `extension/entrypoints/background.ts` | Already has tab management — may need state tracking | Low |
| `backend/src/agent_core/agent/prompts.py` | Update system prompt to mention tab strategy | Low |

### Subtasks

- [ ] 3.1: Design tab state management in AgentState
  - Track active tab ID, per-tab page_context, per-tab action history
- [ ] 3.2: Update execute_action_node to handle new_tab/switch_tab/close_tab
  - When switching tabs, swap the page_context to the target tab's context
- [ ] 3.3: Update Playwright orchestrator for multi-page support
  - Track multiple Page objects, switch between them
- [ ] 3.4: Update system prompt to guide tab usage
  - "For comparison tasks, open each site in a separate tab"
- [ ] 3.5: Test cases
  - [ ] "Compare iPhone price on Daraz vs Amazon": should open 2 tabs
  - [ ] "Search on YouTube and Google simultaneously": should open 2 tabs
  - [ ] Single-site task: should NOT use tabs (no regression)
  - [ ] Tab switching preserves page context

---

## Task 4: Session Memory Across Tasks

**Goal:** Remember context from previous tasks in the same session. User says "now check another business" and agent knows what type of checking was done before.

**Impact:** Faster repeated tasks, less user re-explanation needed.

### Files to Change

| File | Change | Risk |
|---|---|---|
| `backend/src/agent_core/schemas/agent.py` | Add `session_memory: dict` that persists across tasks | Low |
| `backend/src/agent_core/server/session.py` | Store task history per session | Low |
| `backend/src/agent_core/server/ws_handler.py` | Pass previous task context to new task's initial state | Low |
| `backend/src/agent_core/agent/nodes.py` | `analyze_and_plan`: check session memory for relevant context | Low |

### Subtasks

- [ ] 4.1: Design session memory schema
  - Previous task goals, outcomes, sites visited, credentials used
- [ ] 4.2: Implement session memory storage in Session class
- [ ] 4.3: Pass session context to new tasks
- [ ] 4.4: Update analyze_and_plan to use session context
- [ ] 4.5: Test cases
  - [ ] Two tasks in same session: second task has context from first
  - [ ] Different sessions: no cross-contamination
  - [ ] Credential reuse: if user provided login in task 1, task 2 shouldn't ask again

---

## Task 5: Smarter Element Selection

**Goal:** Reduce wrong-element clicks by adding heuristics to element scoring.

**Impact:** Fewer failed actions, fewer retries.

### Files to Change

| File | Change | Risk |
|---|---|---|
| `backend/src/agent_core/schemas/dom.py` | `to_llm_representation`: add interaction hints (is this a real link vs a container?) | Low |
| `backend/src/agent_core/playwright/dom_extractor.py` | Extract additional element metadata (is it a leaf element? depth in DOM tree?) | Low |

### Subtasks

- [ ] 5.1: Add `is_leaf` flag to DOMElement (no interactive children)
- [ ] 5.2: Add `depth` or `nesting_level` to help LLM prefer shallow elements
- [ ] 5.3: In to_llm_representation, mark containers differently from leaf interactive elements
- [ ] 5.4: Test cases
  - [ ] Hacker News: clicking "new" should target the `<a>` tag, not a parent container
  - [ ] Daraz: search input should be element [1]
  - [ ] Google Maps: photo buttons should be directly clickable

---

## Task 6: Streaming Response Summary

**Goal:** Stream the `done(summary)` output to the UI token-by-token instead of waiting for the full response.

**Impact:** Better UX — user sees the answer building up in real-time.

### Files to Change

| File | Change | Risk |
|---|---|---|
| `backend/src/agent_core/server/ws_handler.py` | Already has `WebSocketStreamingHandler` — ensure it works for the final done response | Low |
| `extension/entrypoints/sidepanel/main.ts` | Streaming bubble should be used for done summaries too | Low |

### Subtasks

- [ ] 6.1: Verify streaming works for the done summary (may already work)
- [ ] 6.2: If not, ensure the done response goes through the streaming handler
- [ ] 6.3: Test on a finding-heavy task (price search, visual analysis)

---

## Task 7: Preload Next Likely Page

**Goal:** After a search, preload the first result's URL in a hidden tab so clicking it is instant.

**Impact:** Saves page load time (~1-3s per navigation).

### Files to Change

| File | Change | Risk |
|---|---|---|
| `backend/src/agent_core/agent/nodes.py` | After search results load, identify first result URL and preload | Medium — needs tab management |
| Depends on Task 3 (Multi-Tab) | Must be implemented after multi-tab support | — |

### Subtasks

- [ ] 7.1: Depends on Task 3 completion
- [ ] 7.2: After search results page loads, extract first result URL from DOM
- [ ] 7.3: Open in background tab
- [ ] 7.4: When agent clicks the result, switch to preloaded tab instead of navigating

---

## Task 8: Response Templates for Common Tasks

**Goal:** For "find price of X" tasks, provide structured output without LLM needing to figure out the format.

**Impact:** Consistent, structured output for common task types.

### Files to Change

| File | Change | Risk |
|---|---|---|
| `backend/src/agent_core/agent/nodes.py` | `finalize`: detect task type and apply template | Low |
| `backend/src/agent_core/agent/prompts.py` | Add output format hints for common task patterns | Low |

### Subtasks

- [ ] 8.1: Define common task patterns (price check, product search, information lookup)
- [ ] 8.2: Create output templates for each pattern
- [ ] 8.3: In finalize, detect pattern and format findings accordingly
- [ ] 8.4: Test cases
  - [ ] "Find price of X" → structured table output
  - [ ] "Check if business sells X" → boolean + evidence
  - [ ] Generic task → unstructured summary (no template)

---

## Task 9: SPA Click Compatibility & Modal Handling

**Goal:** Handle SPA navigation clicks that timeout due to overlays/animations, and teach the agent to dismiss post-login modals (workspace selectors, cookie consent, onboarding).

**Impact:** Unblocks agent on React/Vue/Next.js SPAs where Playwright actionability checks fail.

### Completed

- [x] 9.1: Click fallback chain — normal click → force click → JS event simulation (mousedown+mouseup+click)
- [x] 9.2: Post-click SPA URL polling — detect async redirects (login → dashboard) within 3s
- [x] 9.3: Orchestrator-level URL change detection — polls after any click for SPA navigation
- [x] 9.4: Modal/overlay awareness in prompts — agent handles modals FIRST before interacting with background
- [x] 9.5: Stuck navigation guidance — after 2+ failed clicks on same element, try navigate() with constructed URL
- [x] 9.6: Re-plan suppression — after v2, stop creating new plans and just adapt next action

**Files Changed:** `action_executor.py`, `orchestrator.py`, `prompts.py`, `nodes.py`

**Result:** Login + modal dismiss + SPA navigation working on SellrClub. Time cut from 300s to 158s.

---

## Task 10: Structured Data Extraction (extract_listings)

**Goal:** Generalized tool to extract structured product/listing data from any website — auto-detects repeated card structures in the DOM.

**Impact:** Enables structured JSON output for e-commerce, search results, job boards, etc.

### Completed

- [x] 10.1: New `extract_listings` tool — returns JSON with name, price, url, image_url, rating, specs, discount
- [x] 10.2: Generic card detection JS — finds largest group of same-class siblings (works across all sites)
- [x] 10.3: Price regex for global currencies (Rs, NPR, $, €, £, ₹, ¥, etc.)
- [x] 10.4: Image URL extraction — checks src, data-src, data-lazy-src attributes
- [x] 10.5: Added `src` to DOM element attribute extraction (was missing)
- [x] 10.6: Dynamic tool selection — `extract_listings` auto-included when goal mentions price/product/listing/json
- [x] 10.7: Prompt guidance — agent knows when to use extract_listings vs read_page

**Files Changed:** `browser_tools.py`, `action_executor.py`, `dom_extractor.py`, `llm_client.py`, `prompts.py`

---

## Task 11: Export Output to File (PDF / CSV / Excel / JSON)

**Goal:** Allow users to download the agent's findings as structured files (PDF, CSV, Excel, JSON). Especially useful for data extraction tasks (price checks, product comparisons, listing scrapes).

**Impact:** Makes the agent output actionable — users can share, process, or import data into other tools.

### Files to Change

| File | Change | Risk |
|---|---|---|
| `backend/src/agent_core/server/app.py` | Add REST endpoint `/api/export` that accepts format + data | Low |
| `backend/src/agent_core/server/ws_handler.py` | On `done`, auto-generate export URL and include in done message | Low |
| `backend/src/agent_core/export/` | New module — formatters for JSON, CSV, Excel, PDF | Low |
| `extension/entrypoints/sidepanel/main.ts` | Add download button in done message UI | Low |
| `dashboard/` | Add download button in chat response area | Low |

### Subtasks

- [ ] 11.1: Create `backend/src/agent_core/export/` module with formatters:
  - [ ] `json_formatter.py` — pretty-printed JSON with metadata (task, timestamp, source URL)
  - [ ] `csv_formatter.py` — flatten structured data into CSV rows
  - [ ] `excel_formatter.py` — openpyxl-based Excel with auto-column-width, header styling
  - [ ] `pdf_formatter.py` — reportlab or weasyprint-based PDF with table + branding
- [ ] 11.2: Add `/api/export` REST endpoint
  - Accepts: `{ format: "json"|"csv"|"xlsx"|"pdf", data: {...}, filename: "..." }`
  - Returns: file download response with correct content-type
- [ ] 11.3: Auto-detect exportable data in `done()` output
  - If `extract_listings` was used, offer export automatically
  - If done summary contains structured findings, parse and offer export
- [ ] 11.4: WebSocket: include `export_available: true` + `export_url` in done message
- [ ] 11.5: Extension sidepanel: download button (calls export endpoint, triggers browser download)
- [ ] 11.6: Dashboard: download dropdown with format selection (JSON/CSV/Excel/PDF)
- [ ] 11.7: Test cases
  - [ ] Daraz product search → download CSV with product names, prices, URLs
  - [ ] Price comparison → download PDF with comparison table
  - [ ] Generic text task → no export offered (graceful fallback)

---

## Implementation Order

```
Phase 1 (Completed):
  [x] Task 1: Action Batching — scroll+read auto-batch, duplicate detection
  [x] Task 2: Page Context Caching — skip DOM re-extraction for non-mutating actions
  [x] Task 9: SPA Click Compatibility & Modal Handling
  [x] Task 10: Structured Data Extraction (extract_listings)

Phase 2 (Next Sprint):
  [ ] Task 11: Export Output to File (PDF / CSV / Excel / JSON)
  [ ] Task 5: Smarter Element Selection
  [ ] Task 8: Response Templates

Phase 3 (Future):
  [ ] Task 6: Streaming Response Summary
  [ ] Task 3: Multi-Tab Parallel Research
  [ ] Task 4: Session Memory
  [ ] Task 7: Preload Next Likely Page (depends on Task 3)
```

## Known Issues

### [!] Retailer Page Content Extraction (affects DuckDuckGo price search and similar tasks)

**Symptom:** Agent takes 15-19 actions and 300+s on tasks that require reading prices from third-party retailer websites (gadgetbytenepal.com, maxell.com.np, etc.).

**Root Cause:** `read_page` uses `innerText` which doesn't capture JavaScript-rendered content. Retailer sites render product cards/prices via React/Vue after page load. The extracted text contains only navigation/header text, not product data. This triggers strategy escalation (scroll → read → same content → scroll → read → visual_check → repeat).

**NOT affected:** Direct URL tasks (YouTube, Daraz, Google Maps), sites with server-rendered content (HN, Wikipedia), login flows.

**IS affected:** Any task that requires `read_page` on a JS-heavy third-party site reached via search.

**Potential fixes (prioritized):**
1. Auto-switch to `visual_check` when `read_page` returns < 200 chars — vision model can read prices from rendered screenshots
2. Wait for JS rendering (3s delay) before `innerText` extraction
3. Check for price/product CSS selectors before attempting text extraction — if DOM has product elements but text is empty, use vision
4. Use `page.evaluate` with `querySelectorAll('[class*=price]')` to extract prices directly from CSS-targeted elements

**Workaround:** Tasks that go directly to known sites (Daraz, Amazon) work fast because the direct URL lands on the correct page with server-rendered content.

### Resolved Issues
- [x] Extension background.ts implements page context caching (Task 2.3)
- [x] Simple button confirmation fixed — only element text triggers risk detection, not description
- [x] Google Maps regression — vision model works, 7 actions, correct answer
- [x] Login credential flow — auto-type from parsed user input works
- [x] SPA hydration wait — content script waits up to 8s for React/Vue to render
- [x] Contenteditable typing — Teams/Slack paste simulation + InputEvent dispatch
- [x] `combined_text` variable crash in nodes.py — fixed to use `element_text`
- [x] SPA click timeouts — force click + JS event fallback chain (Task 9)
- [x] Post-login modal blocking — agent now handles modals first (Task 9)
- [x] SPA login redirect not detected — URL polling catches async redirects (Task 9)
- [x] Re-planning loops — suppressed after v2, agent adapts reactively (Task 9)
- [x] Missing image src in DOM extraction — added `src` to attribute list (Task 10)
- [x] No structured data extraction — new extract_listings tool (Task 10)

## Testing Protocol

After each task implementation:

1. **Syntax check**: All modified Python files pass `ast.parse()`
2. **Graph compilation**: `create_agent_graph()` succeeds
3. **Extension build**: `npm run build` succeeds
4. **Regression tests** (must all pass before moving to next task):
   - [x] YouTube search + play video (known site, direct URL)
   - [!] DuckDuckGo → retailer → read prices (regressed — strategy escalation)
   - [ ] Google Maps photo analysis with visual_check (not retested)
   - [x] Price comparison on Daraz (direct URL + read_page)
   - [ ] Login form with credential detection (not retested with Playwright)
5. **Task-specific tests**: Listed under each task's subtasks
6. **Performance check**: Measure time and action count, compare with baseline

### Current Baselines (after Task 1 + 2)

| Task | Actions | Time | Change vs Previous |
|---|---|---|---|
| YouTube "3 idiots trailer" | 2 | 21.3s | -6% faster |
| YouTube "madrid highlights" | 2 | 21.3s | Same |
| Daraz iPhone search | 2 | 39.3s | -11% faster, 1 less action |
| DuckDuckGo "lenovo loq price" | 12 | 146.7s | +162% slower (escalation bug) |
| Login (test site) | 3 | 57s | Not retested |
| Google Maps vape check | 5 | 156s | Not retested |
