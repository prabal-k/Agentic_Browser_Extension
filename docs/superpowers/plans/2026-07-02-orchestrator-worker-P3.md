# Orchestrator–Worker Phase 3: Lead Graph + Mount Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the lead graph (`seed_plan → plan_step → worker → integrate → …`) that model-seeds a plan, delegates each subgoal to the P2 worker subgraph mounted as a node, folds each `ResultDigest` back into a living ledger, and is drivable from `ws_handler` behind an opt-in flag (so the existing agent keeps working; cutover is P4).

**Architecture:** A LangGraph *lead graph* over `LeadState` with no browser tools. A `worker_node` adapter builds a `WorkerState` from the active `PlanItem` and invokes the compiled P2 worker subgraph (compiled WITHOUT its own checkpointer so it inherits the lead's). Browser `interrupt()`s raised inside the worker propagate to the top-level `astream` and resume via `Command(resume=...)` — **spike-verified**, and the resume/execution-request shapes already match what `ws_handler` sends/expects. `plan_step` is deterministic (pick next ready item; finish when all done/failed; retry failures up to `ITEM_RETRY_CAP`); LLM-driven mid-run replanning (`update_plan`) is deferred.

**Tech Stack:** Python 3.11+, LangGraph ≥0.4 (`StateGraph`, `interrupt`, `Command`, `MemorySaver`, subgraph-as-node), LangChain ≥0.3, Pydantic ≥2.10, pytest (`asyncio_mode=auto`), structlog. Import root `agent_core` (src under `backend/src`).

## Global Constraints

- Python ≥ 3.11; import root `agent_core`. Pydantic v2; TypedDict state with `total=False`.
- Tests: `backend/tests/`, classes `Test*`, functions `test_*`; `asyncio_mode=auto`.
- Run from `backend/` with the venv python: `venv/Scripts/python.exe -m pytest ...` and `venv/Scripts/python.exe -m ruff check ...`. Repo root `C:\Users\praba\Desktop\Prabal\Agentic_Browser_Extension`.
- Ruff line-length 100, target py311. New/changed files ruff-clean (pre-existing issues on untouched lines are out of scope).
- The lead holds ZERO browser tools. Only `ResultDigest` (compact) informs the lead's ledger; the worker's `page_context`/`action_history` never enter the lead's LLM prompt. (`last_page_context` is infrastructure DOM continuity, NOT fed to `plan_step`.)
- Budgets: `LEAD_DELEGATION_CAP=15`, `ITEM_RETRY_CAP=2`, `WORKER_ACTION_CAP=8` from `agent/budgets.py`.
- Do NOT break the existing agent: the lead graph is opt-in behind `settings.use_lead_graph` (default False). Cutover/deletion is P4.
- The worker subgraph mounted inside the lead MUST be compiled without its own checkpointer (Task 1) — it inherits the lead graph's checkpointer; giving it a second one breaks nested interrupt/resume.

## Existing code this phase builds on (all committed on master)

- `schemas/orchestrator_state.py`: `LeadState` (TypedDict total=False; fields `original_goal, plan: list[PlanItem], active_item_id, lead_decision, delegations_used, tabs, stored_credentials, model_name, api_keys, messages(add_messages)`), `create_lead_state(goal_text, model_name, api_keys=None, prior_messages=None)`, `WorkerState`, `create_worker_state(role, subgoal, done_criteria, model_name, tab_id=None, page_context=None, api_keys=None)`.
- `schemas/orchestrator.py`: `PlanItem{id, subgoal, role: WorkerRole, done_criteria, status: PlanItemStatus, depends_on: list[str], result_digest: str, data: dict|None}`, `PlanItemStatus{PENDING,ACTIVE,DONE,FAILED,BLOCKED,SKIPPED}`, `ResultDigest{status: Literal["done","failed","needs_user"], summary, data, needs_user, question, tab_id, actions_used}`, `WorkerRole`.
- `agent/worker_graph.py`: `build_worker_graph(checkpointer=None)` (currently defaults to `MemorySaver` — Task 1 changes this).
- `agent/worker_nodes.py`: `worker_decide`, `worker_execute`, `budget_exhausted`, etc.
- `agent/budgets.py`: `WORKER_ACTION_CAP`, `ITEM_RETRY_CAP`, `LEAD_DELEGATION_CAP`, `is_destructive`.
- `agent/llm_client.py`: `get_reasoning_llm(model_name=None, api_keys=None)` (temp 0.4, no tools, JSON).
- `agent/nodes.py`: `_parse_llm_json(content) -> dict` (strips ```` ``` ````/`<think>`, `json.loads`; does NOT catch `JSONDecodeError` — callers wrap).
- `server/session.py:57`: `graph = create_agent_graph()` (the swap point). `server/ws_handler.py`: `_handle_goal` (builds `create_initial_state`, drives `session.graph.astream(stream_mode="updates")`, `_extract_interrupt`/`_handle_interrupt` with `Command(resume=...)`, `_stream_node_output`, `_send_done`).

## Verified interrupt/resume compatibility (why ws_handler needs no interrupt-protocol change)

- Worker browser action → `worker_execute` calls `interrupt({action_id, action_type, element_id, element_fingerprint, value, description})`. `ws_handler._handle_interrupt` matches on `"action_id"` → sends `server_action_request(execute=True)` → browser → `CLIENT_ACTION_RESULT` → resume dict `{status, message, error, extracted_data, page_changed, new_url, execution_time_ms, new_dom}`. `worker_nodes._parse_execution_result` already consumes exactly that. ✓
- Worker escalation → lead `ask_user` node calls `interrupt({"question": ...})`. `_handle_interrupt` matches `"question"` → `server_interrupt` text field `answer` → resume `{"answer": ...}`. ✓

## Scope & deferrals (explicit)

- **In scope:** `seed_plan` (model-seeded), deterministic `plan_step` (+ retry + delegation cap), `integrate`, `ask_user`, the `worker_node` adapter, `build_lead_graph`, mounting the worker without a checkpointer, opt-in `ws_handler`/`session` wiring + lead-node WS emit mappings, DOM continuity via `last_page_context`.
- **Deferred to P4/P5:** deleting the old regex/legacy nodes and flipping `use_lead_graph` default (P4); LLM-driven `update_plan` mid-run replanning; parallel worker fan-out; `type_credential` live credential injection (still P3-optional — kept OUT of P3 core, tracked); nested LangSmith run-name tagging (P5).

---

## File Structure

**New files:**
- `backend/src/agent_core/agent/lead_nodes.py` — `seed_plan`, `plan_step`, `integrate`, `ask_user_node`, routing helpers, `_active_item`.
- `backend/src/agent_core/agent/lead_graph.py` — `worker_node` adapter + `build_lead_graph(checkpointer=None)`.
- `backend/tests/test_lead_nodes.py`, `backend/tests/test_lead_graph.py`.

**Modified files:**
- `backend/src/agent_core/agent/worker_graph.py` — `build_worker_graph`: `checkpointer=None` now means "no checkpointer" (mountable). (Task 1)
- `backend/src/agent_core/schemas/orchestrator_state.py` — `LeadState` gains `last_page_context: PageContext | None`; `create_lead_state` gains a `page_context=None` param. (Task 5)
- `backend/src/agent_core/config.py` (settings) — add `use_lead_graph: bool = False`. (Task 6)
- `backend/src/agent_core/server/session.py` — build lead graph when `settings.use_lead_graph`. (Task 6)
- `backend/src/agent_core/server/ws_handler.py` — `_handle_goal` builds `LeadState` when the session is lead-mode; `_stream_node_output` gains lead-node mappings; `_send_done` reads the ledger. (Task 6)

---

### Task 1: Make the worker subgraph mountable (no forced checkpointer)

**Files:**
- Modify: `backend/src/agent_core/agent/worker_graph.py`
- Test: `backend/tests/test_worker_graph.py` (append)

**Interfaces:**
- Produces: `build_worker_graph(checkpointer=None)` where `checkpointer=None` compiles with **no** checkpointer (so it can be mounted inside a parent that owns the checkpointer). Passing an explicit checkpointer (e.g. `MemorySaver()`) still works for standalone interrupt/resume.

**Why:** A compiled subgraph invoked inside a lead node must inherit the lead's checkpointer; a second checkpointer breaks nested interrupt/resume (spike-verified that no-checkpointer mounting works).

- [ ] **Step 1: Write the failing test** (append to `backend/tests/test_worker_graph.py`)

```python
class TestWorkerGraphMountable:
    def test_compiles_without_checkpointer(self):
        # When mounted inside the lead graph, the worker must compile with no
        # checkpointer of its own (it inherits the parent's).
        graph = build_worker_graph(checkpointer=None)
        assert graph is not None
        assert graph.checkpointer is None

    def test_explicit_checkpointer_still_used(self):
        from langgraph.checkpoint.memory import MemorySaver
        cp = MemorySaver()
        graph = build_worker_graph(cp)
        assert graph.checkpointer is cp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_graph.py::TestWorkerGraphMountable -v`
Expected: FAIL — `test_compiles_without_checkpointer` asserts `graph.checkpointer is None` but current code substitutes a `MemorySaver`.

- [ ] **Step 3: Change the default in `build_worker_graph`**

In `backend/src/agent_core/agent/worker_graph.py`, DELETE the auto-substitution:

```python
    if checkpointer is None:
        checkpointer = MemorySaver()
```

so the function passes `checkpointer` straight through to `builder.compile(checkpointer=checkpointer)`. Update the docstring line to:

```python
    """Compile the worker subgraph.

    checkpointer=None compiles WITHOUT a checkpointer — required when mounting
    this graph inside the lead graph (it inherits the lead's checkpointer).
    Pass an explicit MemorySaver for standalone interrupt/resume (e.g. tests).
    """
```

Keep the `from langgraph.checkpoint.memory import MemorySaver` import (still referenced by callers/tests? if ruff flags it as unused, remove it).

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_graph.py -v
```
Expected: PASS — all existing worker_graph tests still green (the e2e tests pass `MemorySaver()` explicitly, unaffected) plus the 2 new ones. Ruff clean on the file.

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/agent/worker_graph.py backend/tests/test_worker_graph.py
git commit -m "feat(lead): make worker subgraph mountable (checkpointer=None means none)"
```

---

### Task 2: `seed_plan` node (model-seeded plan)

**Files:**
- Create: `backend/src/agent_core/agent/lead_nodes.py`
- Test: `backend/tests/test_lead_nodes.py`

**Interfaces:**
- Consumes: `get_reasoning_llm` (`agent/llm_client.py`), `_parse_llm_json` (`agent/nodes.py`), `LeadState` (`schemas/orchestrator_state.py`), `PlanItem`/`WorkerRole`/`PlanItemStatus` (`schemas/orchestrator.py`).
- Produces: `async def seed_plan(state: LeadState) -> dict` — one reasoning-LLM call → 2–5 `PlanItem`s. On any parse/validation failure, falls back to a single navigator item covering the whole goal. Returns `{"plan": [PlanItem, ...]}`. Also produces `_coerce_items(raw: list) -> list[PlanItem]` and `_fallback_plan(goal: str) -> list[PlanItem]` helpers.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lead_nodes.py
"""Tests for the lead coordinator nodes."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent_core.schemas.orchestrator import WorkerRole, PlanItemStatus
from agent_core.schemas.orchestrator_state import create_lead_state


def _mock_reasoning(content: str):
    resp = MagicMock()
    resp.content = content
    return resp


class TestSeedPlan:
    @pytest.mark.asyncio
    async def test_parses_items(self):
        state = create_lead_state(goal_text="log in then read the dashboard",
                                  model_name="gpt-4o-mini")
        content = '''```json
{"items": [
  {"subgoal": "log in", "role": "auth", "done_criteria": "account menu visible"},
  {"subgoal": "read dashboard", "role": "extractor", "done_criteria": "metrics captured"}
]}
```'''
        with patch("agent_core.agent.lead_nodes.get_reasoning_llm") as gr:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_reasoning(content)
            gr.return_value = llm
            from agent_core.agent.lead_nodes import seed_plan
            out = await seed_plan(state)
        plan = out["plan"]
        assert len(plan) == 2
        assert plan[0].role is WorkerRole.AUTH
        assert plan[1].role is WorkerRole.EXTRACTOR
        assert plan[0].id and plan[1].id and plan[0].id != plan[1].id
        assert plan[0].status is PlanItemStatus.PENDING

    @pytest.mark.asyncio
    async def test_falls_back_on_bad_json(self):
        state = create_lead_state(goal_text="do the thing", model_name="gpt-4o-mini")
        with patch("agent_core.agent.lead_nodes.get_reasoning_llm") as gr:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_reasoning("not json at all")
            gr.return_value = llm
            from agent_core.agent.lead_nodes import seed_plan
            out = await seed_plan(state)
        plan = out["plan"]
        assert len(plan) == 1
        assert plan[0].role is WorkerRole.NAVIGATOR
        assert plan[0].subgoal == "do the thing"

    @pytest.mark.asyncio
    async def test_unknown_role_falls_back_to_navigator(self):
        state = create_lead_state(goal_text="g", model_name="gpt-4o-mini")
        content = '{"items": [{"subgoal": "x", "role": "wizard", "done_criteria": "done"}]}'
        with patch("agent_core.agent.lead_nodes.get_reasoning_llm") as gr:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_reasoning(content)
            gr.return_value = llm
            from agent_core.agent.lead_nodes import seed_plan
            out = await seed_plan(state)
        assert out["plan"][0].role is WorkerRole.NAVIGATOR
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_lead_nodes.py::TestSeedPlan -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.agent.lead_nodes'`

- [ ] **Step 3: Write `lead_nodes.py` (seed_plan portion)**

```python
# backend/src/agent_core/agent/lead_nodes.py
"""Lead coordinator nodes.

The lead owns a living plan ledger (list[PlanItem]) and drives it: seed the plan
once, then repeatedly pick the next ready item to delegate, fold each worker's
ResultDigest back in, and finish when every non-skipped item is done or failed.
The lead has NO browser tools; only the compact ResultDigest informs the ledger.
"""

import uuid

import structlog

from agent_core.agent.budgets import ITEM_RETRY_CAP, LEAD_DELEGATION_CAP
from agent_core.agent.llm_client import get_reasoning_llm
from agent_core.agent.nodes import _parse_llm_json
from agent_core.schemas.orchestrator import (
    PlanItem,
    PlanItemStatus,
    WorkerRole,
)
from agent_core.schemas.orchestrator_state import LeadState
from langchain_core.messages import HumanMessage, SystemMessage

logger = structlog.get_logger("agent.lead")

_SEED_SYSTEM = """You break a browser task into 2-5 coarse subgoals for specialist workers.

Roles:
- navigator: move around (open pages, click, scroll)
- extractor: read text/data from the page
- form_filler: fill and submit form fields
- verifier: confirm something is true on the page
- auth: log in / handle credentials

Return ONLY JSON: {"items": [{"subgoal": str, "role": one of the roles, "done_criteria": str}, ...]}
Keep it to 2-5 items. done_criteria must be a checkable condition."""


def _new_id() -> str:
    return f"item_{uuid.uuid4().hex[:8]}"


def _coerce_items(raw: list) -> list[PlanItem]:
    """Turn parsed dicts into PlanItems, defaulting an unknown role to navigator."""
    items: list[PlanItem] = []
    prev_id: str | None = None
    for entry in raw:
        role_str = str(entry.get("role", "")).strip().lower()
        try:
            role = WorkerRole(role_str)
        except ValueError:
            role = WorkerRole.NAVIGATOR
        item = PlanItem(
            id=_new_id(),
            subgoal=str(entry.get("subgoal", "")).strip() or "complete the task",
            role=role,
            done_criteria=str(entry.get("done_criteria", "")).strip() or "subgoal complete",
            depends_on=[prev_id] if prev_id else [],
        )
        items.append(item)
        prev_id = item.id
    return items


def _fallback_plan(goal: str) -> list[PlanItem]:
    return [PlanItem(id=_new_id(), subgoal=goal, role=WorkerRole.NAVIGATOR,
                     done_criteria="the task is complete")]


async def seed_plan(state: LeadState) -> dict:
    """One reasoning call → 2-5 coarse PlanItems (model-seeded, replaces regex)."""
    goal = state["original_goal"]
    llm = get_reasoning_llm(model_name=state["model_name"], api_keys=state.get("api_keys"))
    try:
        resp = await llm.ainvoke([
            SystemMessage(content=_SEED_SYSTEM),
            HumanMessage(content=f"Task: {goal}"),
        ])
        data = _parse_llm_json(resp.content)
        raw = data.get("items", [])
        items = _coerce_items(raw) if isinstance(raw, list) and raw else []
    except Exception as exc:  # noqa: BLE001 — any parse/LLM failure → safe fallback
        logger.warning("seed_plan_fallback", error=str(exc))
        items = []
    if not items:
        items = _fallback_plan(goal)
    logger.info("seed_plan_done", item_count=len(items))
    return {"plan": items}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_lead_nodes.py::TestSeedPlan -v`
Expected: PASS (3 tests). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/agent/lead_nodes.py backend/tests/test_lead_nodes.py
git commit -m "feat(lead): add model-seeded seed_plan node"
```

---

### Task 3: `plan_step` node (deterministic next-item selection)

**Files:**
- Modify: `backend/src/agent_core/agent/lead_nodes.py`
- Test: `backend/tests/test_lead_nodes.py` (append)

**Interfaces:**
- Consumes: `LeadState`, `PlanItem`/`PlanItemStatus`, `LEAD_DELEGATION_CAP`.
- Produces:
  - `_active_item(state) -> PlanItem | None` — the item whose `id == state["active_item_id"]`.
  - `_next_ready(plan) -> PlanItem | None` — first `PENDING` item whose `depends_on` are all `DONE`/`SKIPPED`.
  - `def plan_step(state: LeadState) -> dict` (0-LLM) — emits one decision into `lead_decision` and sets `active_item_id`:
    - if `delegations_used >= LEAD_DELEGATION_CAP` → `{"action": "finish", "reason": "delegation cap"}`.
    - elif a ready item exists → mark it `ACTIVE`, set `active_item_id`, `{"action": "delegate", "item_id": ...}`.
    - elif every non-`SKIPPED` item is `DONE`/`FAILED` → `{"action": "finish"}`.
    - else (blocked: pending items with unmet deps and nothing ready) → `{"action": "finish", "reason": "blocked"}`.
  - Returns `{"lead_decision": <dict>, "active_item_id": <id|None>, "plan": <updated plan>}`.

- [ ] **Step 1: Write the failing test** (append)

```python
class TestPlanStep:
    def _plan(self):
        from agent_core.schemas.orchestrator import PlanItem, WorkerRole
        a = PlanItem(id="a", subgoal="s1", role=WorkerRole.NAVIGATOR, done_criteria="d")
        b = PlanItem(id="b", subgoal="s2", role=WorkerRole.EXTRACTOR, done_criteria="d",
                     depends_on=["a"])
        return [a, b]

    def test_delegates_first_ready(self):
        from agent_core.agent.lead_nodes import plan_step
        from agent_core.schemas.orchestrator import PlanItemStatus
        state = create_lead_state("g", "gpt-4o-mini")
        state["plan"] = self._plan()
        out = plan_step(state)
        assert out["lead_decision"]["action"] == "delegate"
        assert out["active_item_id"] == "a"
        active = next(i for i in out["plan"] if i.id == "a")
        assert active.status is PlanItemStatus.ACTIVE

    def test_skips_item_with_unmet_dependency(self):
        # a is pending (not done), so b (depends on a) is NOT ready — a is chosen.
        from agent_core.agent.lead_nodes import plan_step
        state = create_lead_state("g", "gpt-4o-mini")
        state["plan"] = self._plan()
        out = plan_step(state)
        assert out["active_item_id"] == "a"

    def test_finishes_when_all_done(self):
        from agent_core.agent.lead_nodes import plan_step
        from agent_core.schemas.orchestrator import PlanItemStatus
        state = create_lead_state("g", "gpt-4o-mini")
        plan = self._plan()
        for i in plan:
            i.status = PlanItemStatus.DONE
        state["plan"] = plan
        out = plan_step(state)
        assert out["lead_decision"]["action"] == "finish"

    def test_finishes_at_delegation_cap(self):
        from agent_core.agent.lead_nodes import plan_step
        state = create_lead_state("g", "gpt-4o-mini")
        state["plan"] = self._plan()
        state["delegations_used"] = 15
        out = plan_step(state)
        assert out["lead_decision"]["action"] == "finish"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_lead_nodes.py::TestPlanStep -v`
Expected: FAIL — `ImportError: cannot import name 'plan_step'`

- [ ] **Step 3: Add `plan_step` + helpers** (append to `lead_nodes.py`)

```python
def _active_item(state: LeadState) -> PlanItem | None:
    active_id = state.get("active_item_id")
    if not active_id:
        return None
    for item in state.get("plan", []):
        if item.id == active_id:
            return item
    return None


_TERMINAL = {PlanItemStatus.DONE, PlanItemStatus.FAILED, PlanItemStatus.SKIPPED}


def _next_ready(plan: list[PlanItem]) -> PlanItem | None:
    done_ids = {i.id for i in plan if i.status in {PlanItemStatus.DONE, PlanItemStatus.SKIPPED}}
    for item in plan:
        if item.status is PlanItemStatus.PENDING and all(d in done_ids for d in item.depends_on):
            return item
    return None


def plan_step(state: LeadState) -> dict:
    """Emit ONE coordination decision: delegate the next ready item, or finish.

    Deterministic (0-LLM): the plan was model-seeded; execution order follows
    dependencies. LLM-driven mid-run replanning is a later enhancement.
    """
    plan = list(state.get("plan", []))

    if state.get("delegations_used", 0) >= LEAD_DELEGATION_CAP:
        return {"lead_decision": {"action": "finish", "reason": "delegation cap reached"},
                "active_item_id": None, "plan": plan}

    ready = _next_ready(plan)
    if ready is not None:
        ready.status = PlanItemStatus.ACTIVE
        return {"lead_decision": {"action": "delegate", "item_id": ready.id},
                "active_item_id": ready.id, "plan": plan}

    if all(i.status in _TERMINAL for i in plan):
        return {"lead_decision": {"action": "finish"}, "active_item_id": None, "plan": plan}

    # pending items remain but none are ready (unmet deps / blocked) → stop
    return {"lead_decision": {"action": "finish", "reason": "no ready items (blocked)"},
            "active_item_id": None, "plan": plan}
```

Add `ITEM_RETRY_CAP` is already imported (Task 2 import line) — it's used in Task 4. If ruff flags it unused until Task 4 lands, keep the import (Task 4 uses it) or add it in Task 4; prefer adding `ITEM_RETRY_CAP` to the import in Task 4 to avoid an interim unused-import. **Decision: remove `ITEM_RETRY_CAP` from the Task 2 import and add it in Task 4.** (Update Task 2's import line accordingly if you implement out of order.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_lead_nodes.py -v`
Expected: PASS (SeedPlan + PlanStep). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/agent/lead_nodes.py backend/tests/test_lead_nodes.py
git commit -m "feat(lead): add deterministic plan_step selection"
```

---

### Task 4: `integrate` node + `ask_user_node` + routing helpers

**Files:**
- Modify: `backend/src/agent_core/agent/lead_nodes.py`
- Test: `backend/tests/test_lead_nodes.py` (append)

**Interfaces:**
- Consumes: `LeadState`, `PlanItem`/`PlanItemStatus`, `ResultDigest`, `ITEM_RETRY_CAP`, `interrupt` (`langgraph.types`).
- Produces:
  - `def integrate(state: LeadState) -> dict` — folds `state["lead_decision"]["digest"]` (a `ResultDigest`) into the active item: on `done` → `DONE` + store `summary`/`data`; on `failed` → increment a per-item retry counter (tracked on the item via a module-level dict keyed by id is NOT allowed — instead reset to `PENDING` for retry if under cap, else `FAILED`); on `needs_user` → leave item `ACTIVE`, set `lead_decision` to route to ask_user. Records `digest.tab_id` into `state["tabs"]`. Increments `delegations_used`. Returns updated `plan`, `tabs`, `delegations_used`, and possibly `lead_decision`.
  - `async def ask_user_node(state: LeadState) -> dict` — `interrupt({"question": <the digest question>})`; on resume, store the answer into `stored_credentials`/messages and mark the active item `PENDING` for another attempt. Returns `{"messages": [HumanMessage(answer)], "plan": ...}`.
  - `route_after_plan_step(state) -> Literal["worker","ask_user_node","__end__"]`.
  - `route_after_integrate(state) -> Literal["plan_step","ask_user_node"]`.

Per-item retry counting: add a transient field to `PlanItem`? No — keep the schema stable. Track retries in `PlanItem.data` under a reserved key, OR add a `retries` count via the ledger. **Decision:** count retries by scanning — store an integer on the item via a new optional field is cleanest. Add `retries: int = 0` to `PlanItem` here (small schema addition, mirrors P1/P2's pattern of extending against real code).

- [ ] **Step 1: Add `retries` to PlanItem first** (schema change)

In `backend/src/agent_core/schemas/orchestrator.py`, add to `class PlanItem` after `data`:

```python
    retries: int = 0  # times this item was re-delegated after a failed digest
```

- [ ] **Step 2: Write the failing test** (append to `test_lead_nodes.py`)

```python
class TestIntegrate:
    def _state_with_active(self, digest):
        from agent_core.schemas.orchestrator import PlanItem, WorkerRole, PlanItemStatus
        state = create_lead_state("g", "gpt-4o-mini")
        item = PlanItem(id="a", subgoal="s", role=WorkerRole.EXTRACTOR,
                        done_criteria="d", status=PlanItemStatus.ACTIVE)
        state["plan"] = [item]
        state["active_item_id"] = "a"
        state["lead_decision"] = {"action": "delegate", "item_id": "a", "digest": digest}
        return state

    def test_done_digest_marks_item_done(self):
        from agent_core.agent.lead_nodes import integrate
        from agent_core.schemas.orchestrator import ResultDigest, PlanItemStatus
        digest = ResultDigest(status="done", summary="got it", data={"p": "$1"}, actions_used=3)
        out = integrate(self._state_with_active(digest))
        item = out["plan"][0]
        assert item.status is PlanItemStatus.DONE
        assert item.result_digest == "got it"
        assert item.data == {"p": "$1"}
        assert out["delegations_used"] == 1

    def test_failed_digest_retries_then_fails(self):
        from agent_core.agent.lead_nodes import integrate
        from agent_core.schemas.orchestrator import ResultDigest, PlanItemStatus
        digest = ResultDigest(status="failed", summary="nope", actions_used=8)
        # first failure → back to PENDING (retry)
        s = self._state_with_active(digest)
        out = integrate(s)
        assert out["plan"][0].status is PlanItemStatus.PENDING
        assert out["plan"][0].retries == 1
        # exhaust retries
        out["plan"][0].status = __import__("agent_core.schemas.orchestrator",
                                           fromlist=["PlanItemStatus"]).PlanItemStatus.ACTIVE
        s2 = create_lead_state("g", "gpt-4o-mini")
        s2["plan"] = out["plan"]
        s2["active_item_id"] = "a"
        s2["lead_decision"] = {"digest": digest}
        out["plan"][0].retries = 2  # at cap
        out2 = integrate(s2)
        assert out2["plan"][0].status is PlanItemStatus.FAILED

    def test_needs_user_routes_to_ask(self):
        from agent_core.agent.lead_nodes import integrate, route_after_integrate
        from agent_core.schemas.orchestrator import ResultDigest
        digest = ResultDigest(status="needs_user", summary="confirm?",
                              needs_user=True, question="Delete account?")
        out = integrate(self._state_with_active(digest))
        assert out["lead_decision"]["action"] == "ask_user"
        # route helper sees the updated decision
        st = create_lead_state("g", "gpt-4o-mini")
        st["lead_decision"] = out["lead_decision"]
        assert route_after_integrate(st) == "ask_user_node"

    def test_records_tab(self):
        from agent_core.agent.lead_nodes import integrate
        from agent_core.schemas.orchestrator import ResultDigest
        digest = ResultDigest(status="done", summary="ok", tab_id="tab_b")
        out = integrate(self._state_with_active(digest))
        assert out["tabs"]["tab_b"]


class TestLeadRouting:
    def test_route_after_plan_step_delegate(self):
        from agent_core.agent.lead_nodes import route_after_plan_step
        st = create_lead_state("g", "gpt-4o-mini")
        st["lead_decision"] = {"action": "delegate", "item_id": "a"}
        assert route_after_plan_step(st) == "worker"

    def test_route_after_plan_step_finish(self):
        from agent_core.agent.lead_nodes import route_after_plan_step
        st = create_lead_state("g", "gpt-4o-mini")
        st["lead_decision"] = {"action": "finish"}
        assert route_after_plan_step(st) == "__end__"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_lead_nodes.py::TestIntegrate -v`
Expected: FAIL — `ImportError: cannot import name 'integrate'`

- [ ] **Step 4: Add `integrate`, `ask_user_node`, routing** (append to `lead_nodes.py`)

First, add `ITEM_RETRY_CAP` and `ResultDigest` and `interrupt` to imports at the top of `lead_nodes.py`:
```python
from typing import Literal
from agent_core.agent.budgets import ITEM_RETRY_CAP, LEAD_DELEGATION_CAP  # extend existing line
from agent_core.schemas.orchestrator import PlanItem, PlanItemStatus, ResultDigest, WorkerRole  # extend
from langgraph.types import interrupt
```

Then append:

```python
def integrate(state: LeadState) -> dict:
    """Fold the worker's ResultDigest into the active item; 0-LLM."""
    plan = list(state.get("plan", []))
    tabs = dict(state.get("tabs", {}))
    delegations = state.get("delegations_used", 0) + 1
    decision = dict(state.get("lead_decision", {}))
    digest: ResultDigest | None = decision.get("digest")

    item = None
    active_id = state.get("active_item_id")
    for i in plan:
        if i.id == active_id:
            item = i
            break

    if item is None or digest is None:
        return {"plan": plan, "tabs": tabs, "delegations_used": delegations}

    if digest.tab_id:
        tabs[digest.tab_id] = digest.summary[:60] or digest.tab_id

    if digest.status == "done":
        item.status = PlanItemStatus.DONE
        item.result_digest = digest.summary
        item.data = digest.data
    elif digest.status == "needs_user":
        # leave the item ACTIVE; route to ask_user
        decision = {"action": "ask_user", "question": digest.question or "Please confirm."}
        return {"plan": plan, "tabs": tabs, "delegations_used": delegations,
                "lead_decision": decision}
    else:  # failed
        if item.retries < ITEM_RETRY_CAP:
            item.retries += 1
            item.status = PlanItemStatus.PENDING  # retry
        else:
            item.status = PlanItemStatus.FAILED

    return {"plan": plan, "tabs": tabs, "delegations_used": delegations}


async def ask_user_node(state: LeadState) -> dict:
    """Bubble a worker escalation to the human (single HITL choke point)."""
    decision = state.get("lead_decision", {})
    question = decision.get("question", "The agent needs your input.")
    answer = interrupt({"question": question})
    # normalize the resume shape ({"answer": ...} per ws_handler) to text
    text = answer.get("answer", "") if isinstance(answer, dict) else str(answer)
    plan = list(state.get("plan", []))
    for i in plan:
        if i.id == state.get("active_item_id"):
            i.status = PlanItemStatus.PENDING  # re-attempt with the new info
            break
    return {"messages": [HumanMessage(content=text)], "plan": plan,
            "lead_decision": {"action": "resumed"}}


def route_after_plan_step(state: LeadState) -> Literal["worker", "ask_user_node", "__end__"]:
    action = state.get("lead_decision", {}).get("action")
    if action == "delegate":
        return "worker"
    if action == "ask_user":
        return "ask_user_node"
    return "__end__"


def route_after_integrate(state: LeadState) -> Literal["plan_step", "ask_user_node"]:
    if state.get("lead_decision", {}).get("action") == "ask_user":
        return "ask_user_node"
    return "plan_step"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_lead_nodes.py tests/test_orchestrator_schemas.py -v`
Expected: PASS (lead_nodes all + orchestrator schemas still green with the new `retries` field). Ruff clean on both changed files.

- [ ] **Step 6: Commit**

```bash
git add backend/src/agent_core/agent/lead_nodes.py backend/src/agent_core/schemas/orchestrator.py backend/tests/test_lead_nodes.py
git commit -m "feat(lead): add integrate + ask_user + routing (retry/needs_user/tabs)"
```

---

### Task 5: `worker_node` adapter + `build_lead_graph`

**Files:**
- Create: `backend/src/agent_core/agent/lead_graph.py`
- Modify: `backend/src/agent_core/schemas/orchestrator_state.py` (add `last_page_context`)
- Test: `backend/tests/test_lead_graph.py`

**Interfaces:**
- Consumes: `build_worker_graph` (Task 1, mounted with no checkpointer); `seed_plan`/`plan_step`/`integrate`/`ask_user_node`/`route_after_plan_step`/`route_after_integrate`/`_active_item` (Tasks 2–4); `create_worker_state` (P2); `LeadState`/`ResultDigest`.
- Produces:
  - `LeadState` gains `last_page_context: PageContext | None`; `create_lead_state(..., page_context=None)` sets it.
  - `agent/lead_graph.py`: `async def worker_node(state: LeadState) -> dict` and `build_lead_graph(checkpointer=None) -> compiled graph`.

**worker_node adapter:** builds a `WorkerState` from the active `PlanItem` + `last_page_context`, invokes the mounted worker (`await _WORKER.ainvoke(ws)`), and returns `{"lead_decision": {**prev, "digest": result["result_digest"]}, "last_page_context": result.get("page_context")}`. The nested `ainvoke` runs to completion across browser interrupts (spike-verified: interrupts propagate to the top-level stream and resume threads back into the worker).

- [ ] **Step 1: Add `last_page_context` to LeadState** (schema)

In `backend/src/agent_core/schemas/orchestrator_state.py`:
- add to `class LeadState`: `last_page_context: PageContext | None` (place after `tabs`). `PageContext` is already imported.
- add a param + init to `create_lead_state`: signature becomes
  `def create_lead_state(goal_text, model_name, api_keys=None, prior_messages=None, page_context=None) -> LeadState:` and set `last_page_context=page_context` in the returned `LeadState(...)`.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_lead_graph.py
"""End-to-end lead graph tests (worker + reasoning LLMs mocked; browser via Command)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent_core.schemas.orchestrator_state import create_lead_state
from agent_core.agent.lead_graph import build_lead_graph


CFG = {"configurable": {"thread_id": "lead1"}}


def _reasoning(content):
    r = MagicMock(); r.content = content; return r


def _tool(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "t"}])


class TestLeadGraph:
    def test_compiles(self):
        assert build_lead_graph() is not None

    @pytest.mark.asyncio
    async def test_single_item_delegated_and_finished(self):
        # seed → 1 extractor item; worker immediately finishes → lead finishes.
        graph = build_lead_graph(MemorySaver())
        state = create_lead_state("read the price", "gpt-4o-mini")
        seed_json = '{"items": [{"subgoal": "read price", "role": "extractor", "done_criteria": "captured"}]}'
        with patch("agent_core.agent.lead_nodes.get_reasoning_llm") as gr, \
             patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            rl = AsyncMock(); rl.ainvoke.return_value = _reasoning(seed_json); gr.return_value = rl
            wl = AsyncMock()
            wl.ainvoke.return_value = _tool("finish_subgoal", {"summary": "price $9", "data": {"price": "$9"}})
            gw.return_value = wl
            out = await graph.ainvoke(state, CFG)
        done = [i for i in out["plan"] if i.status.value == "done"]
        assert len(done) == 1
        assert done[0].data == {"price": "$9"}
        assert out["delegations_used"] == 1

    @pytest.mark.asyncio
    async def test_worker_browser_action_bubbles_and_resumes(self):
        # worker clicks (interrupt) → lead pauses → resume → worker finishes.
        graph = build_lead_graph(MemorySaver())
        state = create_lead_state("open then done", "gpt-4o-mini")
        seed_json = '{"items": [{"subgoal": "open page", "role": "navigator", "done_criteria": "loaded"}]}'
        with patch("agent_core.agent.lead_nodes.get_reasoning_llm") as gr, \
             patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            rl = AsyncMock(); rl.ainvoke.return_value = _reasoning(seed_json); gr.return_value = rl
            wl = AsyncMock()
            wl.ainvoke.side_effect = [
                _tool("navigate", {"url": "https://x.com"}),
                _tool("finish_subgoal", {"summary": "opened"}),
            ]
            gw.return_value = wl
            interim = await graph.ainvoke(state, CFG)
            assert "__interrupt__" in interim
            out = await graph.ainvoke(
                Command(resume={"status": "success", "message": "ok", "page_changed": True}),
                CFG,
            )
        assert any(i.status.value == "done" for i in out["plan"])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_lead_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.agent.lead_graph'`

- [ ] **Step 4: Write `lead_graph.py`**

```python
# backend/src/agent_core/agent/lead_graph.py
"""The lead graph: seed_plan → plan_step → [worker] → integrate → ... → END.

The worker_node adapter mounts the P2 worker subgraph (compiled WITHOUT its own
checkpointer) and invokes it per delegated item. Browser interrupts raised inside
the worker propagate to the top-level stream and resume via Command(resume=...).
"""

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent_core.agent.lead_nodes import (
    _active_item,
    ask_user_node,
    integrate,
    plan_step,
    route_after_integrate,
    route_after_plan_step,
    seed_plan,
)
from agent_core.agent.worker_graph import build_worker_graph
from agent_core.schemas.orchestrator import ResultDigest
from agent_core.schemas.orchestrator_state import LeadState, create_worker_state

logger = structlog.get_logger("agent.lead_graph")

# Compile the worker ONCE, without a checkpointer (inherits the lead's).
_WORKER = build_worker_graph(checkpointer=None)


async def worker_node(state: LeadState) -> dict:
    """Delegate the active PlanItem to the worker subgraph; return its digest."""
    item = _active_item(state)
    if item is None:
        return {"lead_decision": {**state.get("lead_decision", {}),
                                  "digest": ResultDigest(status="failed",
                                                         summary="no active item")}}
    ws = create_worker_state(
        role=item.role,
        subgoal=item.subgoal,
        done_criteria=item.done_criteria,
        model_name=state["model_name"],
        tab_id=None,
        page_context=state.get("last_page_context"),
        api_keys=state.get("api_keys"),
    )
    result = await _WORKER.ainvoke(ws)  # runs to completion across browser interrupts
    digest = result.get("result_digest") or ResultDigest(status="failed",
                                                          summary="worker returned no digest")
    out = {"lead_decision": {**state.get("lead_decision", {}), "digest": digest}}
    new_page = result.get("page_context")
    if new_page is not None:
        out["last_page_context"] = new_page
    return out


def build_lead_graph(checkpointer=None):
    """Compile the lead graph. Needs a checkpointer for interrupt/resume."""
    if checkpointer is None:
        checkpointer = MemorySaver()

    builder = StateGraph(LeadState)
    builder.add_node("seed_plan", seed_plan)
    builder.add_node("plan_step", plan_step)
    builder.add_node("worker", worker_node)
    builder.add_node("integrate", integrate)
    builder.add_node("ask_user_node", ask_user_node)

    builder.add_edge(START, "seed_plan")
    builder.add_edge("seed_plan", "plan_step")
    builder.add_conditional_edges("plan_step", route_after_plan_step,
                                  {"worker": "worker", "ask_user_node": "ask_user_node", END: END})
    builder.add_edge("worker", "integrate")
    builder.add_conditional_edges("integrate", route_after_integrate,
                                  {"plan_step": "plan_step", "ask_user_node": "ask_user_node"})
    builder.add_edge("ask_user_node", "plan_step")

    graph = builder.compile(checkpointer=checkpointer)
    logger.info("lead_graph_created", node_count=len(builder.nodes))
    return graph
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_lead_graph.py tests/test_orchestrator_state.py -v`
Expected: PASS (lead graph e2e + orchestrator_state with the new `last_page_context`). Then the full P3 suite:
```
venv/Scripts/python.exe -m pytest tests/test_worker_graph.py tests/test_lead_nodes.py tests/test_lead_graph.py -v
venv/Scripts/python.exe -m pytest tests/ -q
```
Expected: green; no new failures beyond the 16 pre-existing. Ruff clean on `lead_graph.py` + `orchestrator_state.py`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/agent_core/agent/lead_graph.py backend/src/agent_core/schemas/orchestrator_state.py backend/tests/test_lead_graph.py
git commit -m "feat(lead): mount worker subgraph + assemble build_lead_graph"
```

---

### Task 6: Drive the lead graph from ws_handler (opt-in, non-breaking)

**Files:**
- Modify: `backend/src/agent_core/config.py` (add `use_lead_graph` setting)
- Modify: `backend/src/agent_core/server/session.py` (build lead graph when enabled)
- Modify: `backend/src/agent_core/server/ws_handler.py` (`_handle_goal` builds `LeadState`; `_stream_node_output` lead-node mappings)
- Test: `backend/tests/test_lead_graph_integration.py`

**Interfaces:**
- Consumes: `build_lead_graph` (Task 5), `create_lead_state` (P1+Task 5), `settings.use_lead_graph`.
- Produces: when `settings.use_lead_graph` is True, `SessionManager.create_session` stores a lead graph and `_handle_goal` seeds a `LeadState`; browser/HITL interrupts flow through the UNCHANGED `_handle_interrupt`/`Command(resume=...)` path (verified compatible). WS status emission gains lead-node handling.

**Safety:** default `use_lead_graph=False` → zero change to the shipped agent. This task is additive; P4 flips the default and deletes the old path.

- [ ] **Step 1: Add the setting**

In `backend/src/agent_core/config.py`, add to the `Settings` model (near the other `AGENT_*` fields — match the existing field style; find an existing `bool` field like `enable_evaluate_js` and mirror it):

```python
    use_lead_graph: bool = False  # opt-in orchestrator-worker lead graph (P3); default off
```

(If the project uses `pydantic-settings` with an env prefix `AGENT_`, this reads `AGENT_USE_LEAD_GRAPH`. Confirm by how `enable_evaluate_js` is declared and mirror it exactly.)

- [ ] **Step 2: Write the failing integration test**

```python
# backend/tests/test_lead_graph_integration.py
"""ws_handler/session wiring for the lead graph (opt-in)."""

import pytest
from unittest.mock import patch


class TestSessionBuildsLeadGraph:
    def test_session_uses_lead_graph_when_enabled(self):
        from agent_core.server import session as session_mod
        with patch.object(session_mod.settings, "use_lead_graph", True):
            mgr = session_mod.SessionManager()
            sess = mgr.create_session(session_id="s1")
            # the lead graph exposes nodes seed_plan/plan_step; the agent graph does not
            node_names = set(sess.graph.get_graph().nodes)
            assert "seed_plan" in node_names or "plan_step" in node_names

    def test_session_uses_agent_graph_when_disabled(self):
        from agent_core.server import session as session_mod
        with patch.object(session_mod.settings, "use_lead_graph", False):
            mgr = session_mod.SessionManager()
            sess = mgr.create_session(session_id="s2")
            node_names = set(sess.graph.get_graph().nodes)
            assert "decide_action" in node_names  # the classic agent
```

(If `create_session` requires more args, read `session.py` and adapt the call; the assertion on node names is the point.)

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_lead_graph_integration.py -v`
Expected: FAIL — session always builds `create_agent_graph()` today.

- [ ] **Step 4: Wire session.py**

In `backend/src/agent_core/server/session.py`, add the import and branch the graph construction (line ~57):

```python
from agent_core.agent.graph import create_agent_graph
from agent_core.agent.lead_graph import build_lead_graph
from agent_core.config import settings
```
Replace `graph = create_agent_graph()` with:
```python
        if settings.use_lead_graph:
            graph = build_lead_graph()
            self._is_lead = True
        else:
            graph = create_agent_graph()
```
Store a per-session flag `session.is_lead_graph = settings.use_lead_graph` (add the attribute on the `Session` object so `_handle_goal` can branch). If `Session` is a dataclass/model, add `is_lead_graph: bool = False` and set it here.

- [ ] **Step 5: Wire `_handle_goal` in ws_handler.py**

In `backend/src/agent_core/server/ws_handler.py`, at the initial-state construction (lines ~363-369), branch on the session flag:

```python
        if getattr(session, "is_lead_graph", False):
            from agent_core.schemas.orchestrator_state import create_lead_state
            initial_state = create_lead_state(
                goal_text=goal,
                model_name=model_name,
                api_keys=api_keys,
                prior_messages=prior_messages,
                page_context=page_context,
            )
        else:
            initial_state = create_initial_state(
                goal_text=goal,
                page_context=page_context,
                model_name=model_name,
                api_keys=api_keys,
                prior_messages=prior_messages,
            )
```

- [ ] **Step 6: Add lead-node WS emit mappings**

In `_stream_node_output` (ws_handler.py ~line 458), add handling for lead-graph node outputs so the frontend sees progress. Add near the existing node branches:

```python
        # --- lead graph nodes (P3) ---
        if node_name == "seed_plan" and "plan" in node_output:
            plan = node_output["plan"]
            await send_msg(ws, "server_plan", steps=[
                {"description": getattr(p, "subgoal", ""), "status": getattr(p, "status", "")
                 if isinstance(getattr(p, "status", ""), str) else getattr(p.status, "value", "")}
                for p in plan
            ])
            return
        if node_name == "plan_step" and "lead_decision" in node_output:
            dec = node_output["lead_decision"]
            await send_msg(ws, "server_status", status=f"lead: {dec.get('action', '')}")
            return
        if node_name == "integrate" and "plan" in node_output:
            plan = node_output["plan"]
            done = sum(1 for p in plan if getattr(p.status, "value", p.status) == "done")
            await send_msg(ws, "server_status", status=f"progress: {done}/{len(plan)} subgoals done")
            return
```

(Match `send_msg`'s actual signature — read the existing calls in `_stream_node_output` and mirror their kwargs.)

- [ ] **Step 7: Run tests + manual verification**

Run:
```
cd backend && venv/Scripts/python.exe -m pytest tests/test_lead_graph_integration.py -v
venv/Scripts/python.exe -m pytest tests/ -q
venv/Scripts/python.exe -m ruff check src/agent_core/config.py src/agent_core/server/session.py src/agent_core/server/ws_handler.py tests/test_lead_graph_integration.py
```
Expected: integration tests pass; no new failures; ruff clean on changed files.

- [ ] **Step 8: Commit**

```bash
git add backend/src/agent_core/config.py backend/src/agent_core/server/session.py backend/src/agent_core/server/ws_handler.py backend/tests/test_lead_graph_integration.py
git commit -m "feat(lead): drive lead graph from ws_handler behind use_lead_graph flag"
```

**P3 exit gate:** with `AGENT_USE_LEAD_GRAPH=true`, a goal runs end-to-end through the lead graph — seed → delegate → worker (browser via interrupt) → integrate → finish — verified against a live site with Playwright MCP (design §8 cases: simple + branchy). With the flag off, the classic agent is byte-for-byte unchanged. All unit/e2e tests green.

---

## Self-Review (against the P3 roadmap + spec)

**Roadmap coverage:**
- P3.1 seed_plan (model-seeded) → Task 2. ✓
- P3.2 plan_step (one decision) → Task 3 (deterministic; LLM-replan deferred, flagged). ✓
- P3.3 dispatch + integrate → Task 4 (`integrate`, routing, `ask_user_node`, tab registry, retry). ✓
- P3.4 mount worker as subgraph-node + build_lead_graph → Task 5 (adapter + Task 1's no-checkpointer worker). ✓
- P3.5 ws_handler drives the lead graph → Task 6 (opt-in, non-breaking). ✓
- Spec §5 tab registry → `integrate` records `digest.tab_id` into `state["tabs"]`. ✓
- Spec §6 HITL choke point → `ask_user_node`; worker→digest→lead→interrupt, reusing ws_handler's existing `"question"` branch. ✓
- Spec §6 budgets → `LEAD_DELEGATION_CAP` in `plan_step`; `ITEM_RETRY_CAP` in `integrate`; `WORKER_ACTION_CAP` already in the worker. ✓

**Deviations flagged for the human:**
1. **`plan_step` is deterministic, not an LLM call.** The roadmap/spec described "1 LLM call → one decision." For linear/dependency-ordered one-site-branchy plans, deterministic selection + retry + needs-user bubbling is simpler, deterministic, and testable; the LLM's value (adaptive mid-run replanning — insert a login step when a wall appears) is deferred as `update_plan`. Adaptivity in P3 still comes from retry + `ask_user`. If you want the LLM in the routing loop now, say so.
2. **DOM continuity via `last_page_context`** threaded by the `worker_node` adapter (reads the worker's final `page_context` after `ainvoke`). This is infrastructure state, NOT fed to `plan_step`'s reasoning, so the lead's context stays clean. It can be one DOM stale for the next worker's first decide; the worker's first action refreshes it. Acceptable for coarse delegation.
3. **`PlanItem.retries` field added** (schema extension) to count re-delegations without a side table — mirrors the P1/P2 pattern of extending schemas against real code.

**Placeholder scan:** every code step has complete code; commands are exact. Task 6's `config.py`/`session.py`/`ws_handler.py` edits reference reading the existing field/call style to mirror it — that's adaptation to real code, not a placeholder (the exact insertion code is given).

**Type consistency:** `LeadState`/`PlanItem`/`PlanItemStatus`/`ResultDigest`/`WorkerRole`/`WorkerState` used identically across tasks. `seed_plan`/`plan_step`/`integrate`/`ask_user_node`/`route_after_plan_step`/`route_after_integrate`/`_active_item`/`worker_node` names consistent between `lead_nodes.py`, `lead_graph.py`, and the tests. `get_reasoning_llm` patched at `agent_core.agent.lead_nodes.get_reasoning_llm`; `get_worker_llm` patched at `agent_core.agent.worker_nodes.get_worker_llm` — matching each module's import site.

**Known follow-ups (not gaps):** P4 flips `use_lead_graph` default + deletes legacy nodes/regex; P5 adds nested LangSmith run tagging; `type_credential` live injection and LLM `update_plan` remain deferred.
```
