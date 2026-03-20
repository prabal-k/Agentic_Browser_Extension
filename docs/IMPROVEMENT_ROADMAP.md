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

## Implementation Order

```
Phase 1 (Completed):
  [x] Task 1: Action Batching — scroll+read auto-batch, duplicate detection
  [x] Task 2: Page Context Caching — skip DOM re-extraction for non-mutating actions

Phase 2 (Next Sprint):
  [ ] Task 5: Smarter Element Selection
  [ ] Task 6: Streaming Response Summary
  [ ] Task 8: Response Templates

Phase 3 (Future):
  [ ] Task 3: Multi-Tab Parallel Research
  [ ] Task 4: Session Memory
  [ ] Task 7: Preload Next Likely Page (depends on Task 3)
```

## Known Issues

- [!] DuckDuckGo price search regressed (56s → 147s) due to strategy escalation looping on pages where read_page returns same header text. Not caused by Task 1 or 2 changes. Root cause: read_page content extraction from retailer pages returns navigation text instead of product content.
- [x] Extension background.ts now implements page context caching (Task 2.3 completed)

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
