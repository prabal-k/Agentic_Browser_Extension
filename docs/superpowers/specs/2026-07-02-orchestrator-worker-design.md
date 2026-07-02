# Orchestrator–Worker Agent Harness Redesign

**Status:** Design approved (brainstorming complete) — ready for planning
**Date:** 2026-07-02
**Author:** Prabal + Claude
**Scope:** Backend agent harness (`backend/src/agent_core`)

---

## 1. Problem & Goals

### Current state
The agent is a single flat ReAct loop driven by a god-node `decide_action` (`nodes.py:1091`, ~350 lines) over a flat god-state `AgentState` (`agent.py:439`). It has **no orchestration tier**: one loop tries to do everything with all 32+ tools bound at once, and a thick layer of brittle regex heuristics stands in for reasoning it doesn't trust the model to do.

### Observed failure modes (all four confirmed by the user)
1. **Loses the plot on long tasks** — flat context accumulates noise (full DOM snapshots, every action) until the goal is buried.
2. **Wrong action on complex pages** — 32+ overlapping tools bound at once; the model picks the wrong one.
3. **Can't handle conditional flows** — an upfront regex-decomposed step list can't adapt when the page branches (login appears, item out of stock, captcha).
4. **Tool confusion** — four different "read the page" tools plus 30 others create a large hallucination surface.

### Task profile (drives every trade-off)
- **One-site-branchy**: mostly one site at a time, with conditional branches (login walls, multi-step flows). Not a wide parallel-scrape workload.
- **Model: cloud small (`gpt-4o-mini`)** — reliable native tool-calling. This is the key enabler: we can **delete** most regex band-aids because the model can do the reasoning they were faking.

### Goals
- Introduce an **orchestration tier** (a "lead" agent) that delegates chunks of work to role-scoped "worker" agents — Claude-Code-style main-loop-with-delegation, not an always-decompose DAG orchestrator.
- **Never expose all tool names/descriptions to the coordinator.** The lead has zero browser tools; workers see only their role's 4–6 tools.
- Keep **human-in-the-loop** for critical/destructive decisions, routed through a single choke point.
- Replace brittle regex with model reasoning + checkable criteria.

### Non-goals (v1)
- Parallel execution (deferred behind a flag — see §5).
- Fixing plaintext credential storage inline (tracked as a separate security item — see §6).

---

## 2. Topology & Living Plan Ledger

### Topology (approved)
- **Lead = pure coordinator.** No browser tools. Owns the plan, delegates subgoal *chunks* (3–8 actions each, with checkable done-criteria), integrates results, talks to the human. Cannot be tool-confused because its menu is four coordination tools.
- **Workers = ephemeral ReAct loops** with a role-scoped tool menu, their own context window, returning a compact digest — not their transcript.
- Delegation granularity is a **subgoal chunk**, not an atomic click (keeps lead context clean) and not the whole task (keeps workers focused). On trivial tasks the lead delegates a single chunk and effectively collapses to one worker.

### Living plan ledger
The plan is **editable data**, not an upfront frozen DAG.

```python
class PlanItem(TypedDict):
    id: str
    subgoal: str
    role: str                 # navigator | extractor | form_filler | verifier | auth
    done_criteria: str        # checkable condition that means this item is complete
    status: str               # pending | active | done | failed | blocked | skipped
    depends_on: list[str]     # item ids (usually linear for one-site-branchy)
    result_digest: str        # filled after the worker returns
```

### Lead loop
```
seed_plan (first turn only)
  → plan_step  ── emits ONE decision: delegate | update_plan | ask_user | finish
      → dispatch (conditional edge)
      → integrate (fold digest into ledger)
  → plan_step → … → finish
```

- **`seed_plan`** runs once at the first turn: one LLM call, structured output, 2–5 coarse `PlanItem`s. **Approved: the plan is model-seeded** — replaces the `_decompose_goal_into_steps` regex (`nodes.py:440`).
- **`plan_step`** is a thin node: one LLM call producing exactly one decision. It does not act on the browser.
- **`done_criteria`** per item replaces the scattered done-detection guards (`_looks_like_contradicted_done`, `_action_history_has_evidence`).
- **`finish` gating (approved):** `finish` is allowed only when every non-skipped item is `done` or `failed`. Escape hatch: the lead may `skip` an item with a reason, so it is never a hard lock.
- **Loop control:** per-item worker budgets + a lead-level delegation cap replace the global 25-iteration / 12-action counters (see §6).

---

## 3. Worker Roles & Tool Exposure

This section directly answers the core requirement: *don't list all tools to the coordinator.*

### Lead tool menu (four tools, no browser access)
`delegate`, `update_plan`, `ask_user`, `finish`. The coordinator's menu is four items, never 32.

### Five worker roles (approved, incl. Auth)
Each role binds only its own small menu.

| Role | Tools (~4–6) | Parallel-safe |
|------|--------------|:---:|
| **Navigator** | navigate, click, scroll, wait, go_back | no (mutates) |
| **Extractor** | read, see, extract_table | yes (read-only) |
| **Form-filler** | fill_form, select_option, check, upload_file, press_key | no |
| **Verifier** | read, see, finish_subgoal | yes |
| **Auth** | navigate, fill_form, click, type_credential, submit, see | no |

**Auth role** isolates the messy login/credential branch (recurring in this app). The lead delegates "log in"; the Auth worker runs the whole dance and returns "logged in" or "need creds" (which bubbles to HITL). It absorbs the existing credential plumbing (`_stored_credentials`, `_detect_auth_intent`, the credential fast-paths in `decide_action`).

### Consolidate the four read tools → `read` + `see` (approved)
Today there are four overlapping ways to read a page — `read_page`, `extract_text`, `extract_listings`, `visual_check` — which is a top source of tool confusion. Collapse along one clear axis (text vs pixels):

- **`read(what)`** — all text/DOM extraction; internally auto-selects table vs listing vs plain. The model no longer chooses between three read variants.
- **`see(question)`** — vision; only when the answer needs pixels (layout, image content, "is the button red").

The only decision left for the model is "eyes or text?" — a clean, non-overlapping line.

### Role → tools is a static dict (deletes keyword heuristics)
Role is chosen by the **model** at delegate-time (it knows intent); tools follow deterministically from a static dict. This deletes `select_tools_for_context`'s substring guessing (`llm_client.py:231`). Wrong-role is self-correcting: a worker returns "can't do this with my tools" and the lead re-delegates.

### Progressive disclosure ladder
- **Now:** lead has no browser tools + role-scoped worker menus. That's the 90% win.
- **Later (only if a single role grows past ~8 tools):** semantic tool-RAG *inside* a worker (embed descriptions, retrieve top-k for the current subgoal). At 32 tools split across five roles this is unnecessary — noted, not built (YAGNI).

---

## 4. State & LangGraph Wiring

### Two graphs
**Lead graph** (outer loop, no browser tools):
```
seed_plan → plan_step → [dispatch] → integrate → plan_step → … → finish
                            ↓
                    worker subgraph runs here
```
- `integrate` is 0-LLM: a pure state merge that folds the worker's `result_digest` back into the ledger and marks the item done/failed.

**Worker subgraph** (inner ReAct loop, role-scoped tools) — the current graph, trimmed:
```
decide_action → execute_action_node → observe → smart_evaluate → (loop | finish_subgoal)
```
Existing nodes are reused almost as-is. The worker returns a compact `result_digest`, not a transcript.

### State shapes
```python
class LeadState(TypedDict):
    original_goal: str
    plan: list[PlanItem]          # the living ledger
    active_item_id: str | None
    lead_decision: dict           # last plan_step output
    delegations_used: int         # lead-level cap
    tabs: dict[str, str]          # tab_id -> what's there ("gmail inbox")
    stored_credentials: dict      # SECURITY: see §6
    messages: list                # lead's own short history

class WorkerState(TypedDict):
    role: str
    subgoal: str
    done_criteria: str
    tab_id: str | None
    page_context: PageContext     # DOM snapshot (big/noisy)
    action_history: list          # (big/noisy)
    actions_used: int             # per-worker budget
    result_digest: ResultDigest   # what bubbles back
```

**The isolation invariant:** the worker's `page_context` and `action_history` (the big, noisy state) **never enter lead state**. Only `result_digest` crosses back. This keeps the lead's context clean over long tasks — the fix for *loses-the-plot*.

### Worker-as-node (approved: subgraph-as-node)
The worker graph is compiled and added as a single node in the lead graph. LangGraph provides checkpointing, streaming, and interrupt bubbling across the subgraph boundary for free. (The rejected alternative — a Task-tool-style handler spinning a fresh graph run — reinvents that plumbing.)

### Mapping to existing code
| Now | Becomes |
|-----|---------|
| `AgentState` god-TypedDict (`agent.py:439`) | split → `LeadState` + `WorkerState` |
| `analyze_and_plan` (`nodes.py:579`) | → `seed_plan` (keep LLM decompose, drop regex) |
| `decide_action` god-node (`nodes.py:1091`) | → worker `decide_action`, role-scoped tools; fast-paths move to Auth/Navigator |
| `execute_action_node` interrupt (`graph.py:234`) | unchanged — worker still uses `interrupt()` |
| global 25-iter / 12-action counters | → per-worker `actions_used` + lead `delegations_used` |
| `select_tools_for_context` (`llm_client.py:231`) | deleted — role→tools static dict |

---

## 5. Browser Execution, Tabs, Parallelism

### Core constraint
The browser is a single stateful resource. Two workers mutating the same page race and corrupt state. Therefore:
- **Mutations serialize** — click/type/navigate/submit run one at a time, ever.
- **Reads can parallelize** — but only across *different tabs*.
- The task profile (one-site-branchy) is mostly sequential anyway; parallelism is a rare bonus, not the core path.

### Action queue (exists — keep)
`execute_action_node` + `interrupt()` (`graph.py:234`) already serializes: an action goes to the extension, waits for `ActionResult`, resumes. That's a natural queue of one. Because the sequential lead loop runs only one worker at a time, there is no cross-worker race by construction.

### Tab ownership
- The lead assigns a tab at delegate time: `delegate(role, subgoal, done_criteria, tab=<id|new>)`.
- A worker operates only on its own tab; it never touches sibling tabs.
- `new` → the worker opens a fresh tab and returns its id in the digest; the lead records it in `LeadState.tabs` (0-LLM), so it can route the next delegate to the right tab.

### Parallelism rule (deferred to a flag)
When it eventually ships, concurrency is gated by a hard rule, not LLM judgment:
```
can_parallel(a, b) = a.readonly and b.readonly and a.tab != b.tab
```
This replaces the dead `depends_on` / `can_parallelize` fields on the old `PlanStep` (`agent.py:89,93`); fan-out would use `langgraph.types.Send` only when the rule passes.

### **Decision (approved): ship v1 sequential-only.**
The lead loop runs one worker at a time. Parallel read fan-out is a later flag, added once the sequential path is solid. The multi-tab compare-prices flow still works fully sequentially (two tabs, never concurrent).

---

## 6. HITL, Budgets, Safety, Observability

### Human-in-the-loop — single choke point (approved)
Two interrupt kinds:
1. **Browser action interrupt** (exists) — `execute_action_node`'s `interrupt()` hands an action to the extension and gets the result. Internal to the worker, not user-facing. Unchanged.
2. **Decision interrupt (HITL)** — a worker that hits something needing a human (missing creds, destructive action, genuine ambiguity) **does not decide alone**. It sets `needs_user` in its digest, which bubbles to the lead; the lead emits `ask_user` → `interrupt()` to the human.

**The worker never talks to the user directly.** All HITL routes worker → digest → lead → human. The lead owns every human interaction — one choke point.

### Safety gate — static classification (approved)
```python
DESTRUCTIVE = {submit_order, delete, pay, send_email, post, confirm_purchase}
```
A worker about to run a destructive action auto-bubbles `needs_user` unless pre-authorized. Deterministic, not an LLM risk judgment. The existing `confirm_action` node's shape is reused, with the gate moved into the worker.

### Budgets — three tiers (approved: 8 / 2 / 15)
Replaces the blunt global 25-iteration / 12-action counters.

| Level | Cap | Purpose |
|-------|-----|---------|
| Per-worker | `actions_used` ≤ 8 | one subgoal can't run away |
| Per-item | worker retries ≤ 2 | limit re-delegation of a failing subgoal |
| Lead | `delegations_used` ≤ 15 | whole-task ceiling |

A worker that hits 8 actions without meeting `done_criteria` returns a `failed` digest; the lead then decides to re-delegate, replan, or ask the user. Failure is a lead signal, not a crash.

### Observability — LangSmith nesting
Extend `run_name=f"agent_{session_id}"` (`ws_handler.py:375`) into a nested trace tree. Subgraph-as-node (§4) makes LangGraph emit child runs that LangSmith nests automatically:
```
agent_{session}                 (lead run)
 ├─ seed_plan
 ├─ plan_step #1
 ├─ worker:navigator[item_1]     (subgraph = nested run)
 │   ├─ decide_action
 │   └─ execute_action
 ├─ plan_step #2
 └─ worker:extractor[item_2]
```
Tag each worker run with `metadata={"role":…, "item_id":…}` for per-subgoal cost/latency and precise derailment location.

### Digest contract (worker → lead)
The one contract that makes isolation work:
```python
class ResultDigest(TypedDict):
    status: str               # done | failed | needs_user
    summary: str              # 1–2 lines
    data: dict | None         # extracted values (prices, text)
    needs_user: bool
    question: str | None      # if needs_user
    tab_id: str | None        # if a new tab was opened
    actions_used: int         # for budget accounting
```
Compact — no transcript, no DOM. The lead reads it, updates the ledger, and moves on.

### Security note (tracked separately)
`LeadState.stored_credentials` currently flows through the MemorySaver checkpoint as plaintext (pre-existing finding). **Decision: keep as-is for this redesign, tracked as a separate security item** — the redesign stays focused on the harness. Fix candidates: encrypt-at-rest in the checkpoint, or keep creds out of checkpointed state entirely.

---

## 7. What to Delete / Migrate

### Delete (regex band-aids made obsolete by `gpt-4o-mini`)
| Target | File:line | Replaced by |
|--------|-----------|-------------|
| `_KNOWN_SITES` + `_build_direct_url` | `nodes.py:193,233` | Navigator role + LLM URL building |
| `_decompose_goal_into_steps` | `nodes.py:440` | `seed_plan` structured output |
| `_detect_auth_intent` | `nodes.py:334` | Auth role delegation |
| `_page_has_login_fields` | `nodes.py:344` | Auth worker sees fields itself |
| `_CONTRADICTION_MARKERS` + `_looks_like_contradicted_done` | `nodes.py:513,529` | per-item `done_criteria` |
| `_action_history_has_evidence` | `nodes.py:545` | `done_criteria` check |
| `_build_success_criteria` | `nodes.py:490` | `done_criteria` at seed |
| `_collapse_to_milestones` | `nodes.py:466` | `seed_plan` emits milestones |
| `select_tools_for_context` | `llm_client.py:231` | role→tools static dict |
| `PlanStep.depends_on`, `.can_parallelize` | `agent.py:89,93` | `can_parallel()` rule |

### Migrate (keep logic, move home)
| Now | Goes to |
|-----|---------|
| `decide_action` fast-paths (credential, auto-nav) | Auth + Navigator roles |
| `confirm_action` node | worker safety gate (DESTRUCTIVE) |
| `execute_action_node` interrupt | worker subgraph, unchanged |
| `smart_evaluate` / `evaluate` | worker done-check vs `done_criteria` |
| vision inline (`ws_handler.py:740`) | `see` tool path |
| `TaskMemory.important_data` | digest `data` field |

### Legacy nodes — delete
`analyze_goal`, `create_plan`, `critique_plan`, `reason` (the rare re-plan path). The lead's `plan_step` / `update_plan` replaces all of them. The dead re-plan path was a band-aid for having no orchestrator; the lead is now the orchestrator.

### Phased migration (each phase shippable & testable)
- **P1** — new state shapes (`LeadState` / `WorkerState`) + `ResultDigest` contract. No behavior change.
- **P2** — worker subgraph from existing nodes; role→tools dict; consolidate read tools (`read` / `see`).
- **P3** — lead graph (`seed_plan` / `plan_step` / `integrate`); wire worker as subgraph-node.
- **P4** — delete regex heuristics + legacy nodes; cut over.
- **P5** — LangSmith nesting, budgets, safety gate polish.

---

## 8. Testing Strategy

### Per-phase gates (required by the project phase-workflow)
- **P1** — unit: state constructs, digest serialize/round-trip. No LLM.
- **P2** — worker subgraph alone: given subgoal + tab, assert correct tool calls and digest shape; mock the browser via an `interrupt` stub.
- **P3** — lead loop: mock workers, assert `plan_step` decisions, ledger updates, `finish` gating.
- **P4** — regression: old regex cases now handled by the LLM path (auth intent, decompose, done-detect).
- **P5** — trace/budget: assert LangSmith nesting and that budget caps trigger.

### Integration (Playwright MCP, per project instructions)
1. **Simple** — "search X on Google, read top result" → Navigator + Extractor, no branch.
2. **Branchy** — "log into site, go to settings, change Y" → Auth role + branch + HITL confirm.
3. **Multi-tab** — "compare price of X on two sites" → two tabs sequential, verify digest data merge.
4. **Failure** — worker exhausts budget → lead re-delegates / asks user, no crash.
5. **HITL** — destructive action → interrupt bubbles to human, resumes on approve.

### Eval harness (regression protection)
A golden set of ~10 recorded goals + expected final states, run pre/post each phase. This proves the LLM path is ≥ the old heuristic path as regex is deleted.

---

## Appendix — Approved decisions log
- Approach A: Lead + delegation (not always-decompose orchestrator).
- Topology: lead = pure coordinator, no browser tools; subgoal-chunk delegation.
- Plan is model-seeded at the first turn.
- `finish` gated on all non-skipped items done/failed, with `skip` escape hatch.
- Read tools consolidated to `read` + `see`.
- Five roles including a dedicated Auth role.
- Worker-as-node via subgraph-as-node (LangGraph native).
- v1 is sequential-only; parallel read fan-out deferred to a flag.
- HITL via a single choke point (worker → digest → lead → human).
- Static `DESTRUCTIVE` set for the safety gate.
- Budgets: 8 (per-worker) / 2 (per-item retries) / 15 (lead).
- Plaintext-credential storage: tracked as a separate security item, not fixed inline.
