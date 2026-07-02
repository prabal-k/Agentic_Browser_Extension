# Orchestrator–Worker Agent Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat single-loop browser agent with a lead-coordinator / role-scoped-worker harness so a lead agent delegates subgoal chunks to workers that each see only their role's 4–6 tools.

**Architecture:** A LangGraph *lead graph* (no browser tools) owns a living plan ledger and delegates one subgoal at a time to a *worker subgraph* (role-scoped ReAct loop) compiled and mounted as a single node. Workers return a compact `ResultDigest`, never their transcript, keeping lead context clean. v1 is sequential-only. Full design: `docs/superpowers/specs/2026-07-02-orchestrator-worker-design.md`.

**Tech Stack:** Python 3.11+, LangGraph ≥0.4, LangChain ≥0.3, Pydantic ≥2.10, pytest + pytest-asyncio (`asyncio_mode=auto`), structlog. Import root: `agent_core` (src layout under `backend/src`).

## Global Constraints

- Python ≥ 3.11; package import root is `agent_core` (installed from `backend/src`, hatchling).
- Pydantic v2 models (`BaseModel`, `Field`); TypedDict for LangGraph state.
- Tests live in `backend/tests/`, classes named `Test*`, functions `test_*`; `asyncio_mode=auto` (no `@pytest.mark.asyncio` needed but harmless).
- Ruff line-length 100, target py311, rules `E,F,I,N,W,UP`.
- LangGraph interrupt() is the only browser I/O mechanism — unchanged from current code.
- No worker ever holds more than 8 tools. The lead holds ZERO browser tools.
- Only `ResultDigest` crosses worker→lead. Never pass `page_context` or `action_history` up.
- Model target is `gpt-4o-mini` (reliable native tool-calling) — prefer LLM reasoning over regex heuristics.
- Run all commands from `backend/` with the venv active: `cd backend` then `python -m pytest ...`.

## Scope Note (read before starting)

This plan is delivered in **phase order P1→P5**, matching the spec's phased migration. Each phase is independently shippable and testable.

- **P1 (Tasks 1–4)** is fully bite-sized with complete TDD code and can be executed immediately. It adds new schemas/constants **alongside** the existing agent with **zero behavior change** — nothing is deleted, the current graph keeps running.
- **P2–P5** are specified as task roadmaps: exact files, interfaces (signatures + types), a representative test, and a code sketch per task. They are intentionally **not** expanded to per-line steps yet because each depends on the concrete signatures produced by its predecessor; expanding them now would hard-code names that will drift. **When P1 lands, re-invoke `superpowers:writing-plans` to expand P2 into bite-sized steps against the real code**, and so on. This is the DRY/anti-drift call, stated explicitly per the plan's own "no speculative references" rule.

---

## File Structure

**New files (P1):**
- `backend/src/agent_core/schemas/orchestrator.py` — `PlanItem`, `PlanItemStatus`, `ResultDigest`, `WorkerRole` (data contracts crossing the lead↔worker boundary).
- `backend/src/agent_core/agent/budgets.py` — budget constants + `DESTRUCTIVE_ACTIONS` set (pure data, no imports of heavy modules).
- `backend/src/agent_core/schemas/orchestrator_state.py` — `LeadState`, `WorkerState` TypedDicts + `create_lead_state`, `create_worker_state` factories.
- `backend/src/agent_core/agent/roles.py` — `ROLE_TOOL_NAMES` static registry (role → list of tool-name strings) + `tools_for_role` accessor. Data only in P1; resolves to tool objects in P2.
- `backend/tests/test_orchestrator_schemas.py`, `backend/tests/test_budgets.py`, `backend/tests/test_orchestrator_state.py`, `backend/tests/test_roles.py` — P1 tests.

**New files (P2–P5, created in their phases):**
- `backend/src/agent_core/tools/consolidated_tools.py` — `read`, `see` consolidated tools (P2).
- `backend/src/agent_core/agent/worker_graph.py` — worker subgraph builder (P2/P3).
- `backend/src/agent_core/agent/lead_graph.py` — lead graph builder: `seed_plan`, `plan_step`, `integrate`, `dispatch` (P3).
- `backend/src/agent_core/agent/lead_nodes.py` — lead node implementations (P3).
- `backend/tests/test_worker_graph.py`, `backend/tests/test_lead_graph.py`, `backend/tests/test_migration_regression.py` (P2–P5).

**Modified files (P2–P5):**
- `agent/llm_client.py` — delete `select_tools_for_context`; add `get_worker_llm(role, model, api_keys)` (P2).
- `agent/nodes.py` — extract worker-usable nodes; delete regex heuristics + legacy nodes (P4).
- `agent/graph.py` — `create_agent_graph` becomes a thin wrapper that returns the lead graph (P3/P4).
- `server/ws_handler.py` — drive the lead graph; nested LangSmith `run_name`/metadata (P3/P5).
- `schemas/agent.py` — remove dead `PlanStep.depends_on`/`can_parallelize` (P4).

---

# PHASE 1 — State shapes, digest contract, budgets, role registry

No behavior change. Pure additive foundations. Fully testable without an LLM.

---

### Task 1: Data contracts — `PlanItem`, `PlanItemStatus`, `ResultDigest`, `WorkerRole`

**Files:**
- Create: `backend/src/agent_core/schemas/orchestrator.py`
- Test: `backend/tests/test_orchestrator_schemas.py`

**Interfaces:**
- Consumes: nothing (leaf module; only stdlib + pydantic).
- Produces:
  - `class WorkerRole(str, Enum)` with members `NAVIGATOR="navigator"`, `EXTRACTOR="extractor"`, `FORM_FILLER="form_filler"`, `VERIFIER="verifier"`, `AUTH="auth"`.
  - `class PlanItemStatus(str, Enum)` with `PENDING`, `ACTIVE`, `DONE`, `FAILED`, `BLOCKED`, `SKIPPED`.
  - `class PlanItem(BaseModel)`: `id: str`, `subgoal: str`, `role: WorkerRole`, `done_criteria: str`, `status: PlanItemStatus = PENDING`, `depends_on: list[str] = []`, `result_digest: str = ""`.
  - `class ResultDigest(BaseModel)`: `status: Literal["done","failed","needs_user"]`, `summary: str`, `data: dict | None = None`, `needs_user: bool = False`, `question: str | None = None`, `tab_id: str | None = None`, `actions_used: int = 0`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_orchestrator_schemas.py
"""Tests for the lead↔worker data contracts."""

from agent_core.schemas.orchestrator import (
    WorkerRole,
    PlanItemStatus,
    PlanItem,
    ResultDigest,
)


class TestWorkerRole:
    def test_has_five_roles(self):
        assert {r.value for r in WorkerRole} == {
            "navigator", "extractor", "form_filler", "verifier", "auth"
        }


class TestPlanItem:
    def test_defaults(self):
        item = PlanItem(
            id="item_1",
            subgoal="Open the login page",
            role=WorkerRole.NAVIGATOR,
            done_criteria="URL contains /login",
        )
        assert item.status == PlanItemStatus.PENDING
        assert item.depends_on == []
        assert item.result_digest == ""

    def test_round_trips_through_json(self):
        item = PlanItem(
            id="item_2",
            subgoal="Log in",
            role=WorkerRole.AUTH,
            done_criteria="Account menu visible",
            depends_on=["item_1"],
        )
        restored = PlanItem.model_validate_json(item.model_dump_json())
        assert restored == item
        assert restored.role is WorkerRole.AUTH


class TestResultDigest:
    def test_minimal_done_digest(self):
        d = ResultDigest(status="done", summary="Logged in")
        assert d.needs_user is False
        assert d.data is None
        assert d.actions_used == 0

    def test_needs_user_digest_round_trips(self):
        d = ResultDigest(
            status="needs_user",
            summary="Found a Delete Account button",
            needs_user=True,
            question="Confirm account deletion?",
            actions_used=3,
        )
        restored = ResultDigest.model_validate_json(d.model_dump_json())
        assert restored == d
        assert restored.question == "Confirm account deletion?"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_orchestrator_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.schemas.orchestrator'`

- [ ] **Step 3: Write the module**

```python
# backend/src/agent_core/schemas/orchestrator.py
"""Data contracts that cross the lead↔worker boundary.

These are deliberately small. Only ResultDigest travels worker→lead; the
worker's page_context and action_history never leave the worker subgraph
(the context-isolation invariant from the design spec).
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class WorkerRole(str, Enum):
    """The role a worker plays. Role determines its tool menu (see agent/roles.py)."""

    NAVIGATOR = "navigator"
    EXTRACTOR = "extractor"
    FORM_FILLER = "form_filler"
    VERIFIER = "verifier"
    AUTH = "auth"


class PlanItemStatus(str, Enum):
    """Lifecycle of a single plan item in the living ledger."""

    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class PlanItem(BaseModel):
    """One coarse subgoal in the lead's living plan ledger.

    A subgoal is a *chunk* (roughly 3–8 worker actions), not an atomic click.
    done_criteria is a natural-language, checkable condition the worker (and
    the lead's integrate step) uses to decide completion.
    """

    id: str
    subgoal: str
    role: WorkerRole
    done_criteria: str
    status: PlanItemStatus = PlanItemStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)
    result_digest: str = ""


class ResultDigest(BaseModel):
    """The compact contract a worker returns to the lead.

    No transcript, no DOM — just outcome + extracted data + any escalation.
    """

    status: Literal["done", "failed", "needs_user"]
    summary: str
    data: dict | None = None
    needs_user: bool = False
    question: str | None = None
    tab_id: str | None = None
    actions_used: int = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_orchestrator_schemas.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/schemas/orchestrator.py backend/tests/test_orchestrator_schemas.py
git commit -m "feat(orchestrator): add PlanItem/ResultDigest/WorkerRole data contracts"
```

---

### Task 2: Budget constants + destructive-action set

**Files:**
- Create: `backend/src/agent_core/agent/budgets.py`
- Test: `backend/tests/test_budgets.py`

**Interfaces:**
- Consumes: `ActionType` from `agent_core.schemas.actions`.
- Produces:
  - `WORKER_ACTION_CAP: int = 8` (max actions one worker may take on one subgoal).
  - `ITEM_RETRY_CAP: int = 2` (max re-delegations of a failing item).
  - `LEAD_DELEGATION_CAP: int = 15` (whole-task ceiling on delegations).
  - `DESTRUCTIVE_ACTIONS: frozenset[ActionType]` — actions that always require human confirmation.
  - `is_destructive(action_type: ActionType) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_budgets.py
"""Tests for worker/lead budgets and the destructive-action gate."""

from agent_core.agent.budgets import (
    WORKER_ACTION_CAP,
    ITEM_RETRY_CAP,
    LEAD_DELEGATION_CAP,
    DESTRUCTIVE_ACTIONS,
    is_destructive,
)
from agent_core.schemas.actions import ActionType


class TestBudgetValues:
    def test_caps_match_spec(self):
        assert WORKER_ACTION_CAP == 8
        assert ITEM_RETRY_CAP == 2
        assert LEAD_DELEGATION_CAP == 15


class TestDestructiveGate:
    def test_navigate_is_not_destructive(self):
        assert is_destructive(ActionType.NAVIGATE) is False
        assert ActionType.NAVIGATE not in DESTRUCTIVE_ACTIONS

    def test_upload_file_is_destructive(self):
        # Uploading a file mutates external state — must be gated.
        assert is_destructive(ActionType.UPLOAD_FILE) is True

    def test_evaluate_js_is_destructive(self):
        assert is_destructive(ActionType.EVALUATE_JS) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_budgets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.agent.budgets'`

- [ ] **Step 3: Write the module**

```python
# backend/src/agent_core/agent/budgets.py
"""Loop-control budgets and the destructive-action safety gate.

Replaces the old global 25-iteration / 12-action counters with three tiers:
per-worker, per-item retries, and a whole-task lead ceiling.
"""

from agent_core.schemas.actions import ActionType

# --- Three-tier budgets (design §6) ---
WORKER_ACTION_CAP: int = 8      # one subgoal can't run away
ITEM_RETRY_CAP: int = 2         # limit re-delegation of a failing item
LEAD_DELEGATION_CAP: int = 15   # whole-task ceiling

# --- Safety gate: actions that always need human confirmation (design §6) ---
# Static classification, not an LLM risk judgment. A worker about to run one
# of these auto-bubbles needs_user unless pre-authorized. The ActionType enum
# has no submit_order/pay/send_email members, so we gate the concrete browser
# actions that can mutate external state irreversibly. Higher-level intents
# (place order, pay) are recognized by the worker safety gate in P5 via the
# tool's own risk_level; this set is the enum-level backstop.
DESTRUCTIVE_ACTIONS: frozenset[ActionType] = frozenset({
    ActionType.UPLOAD_FILE,
    ActionType.EVALUATE_JS,
    ActionType.HANDLE_DIALOG,
})


def is_destructive(action_type: ActionType) -> bool:
    """Return True if this action type must be human-confirmed by default."""
    return action_type in DESTRUCTIVE_ACTIONS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_budgets.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/agent/budgets.py backend/tests/test_budgets.py
git commit -m "feat(orchestrator): add three-tier budgets and destructive-action gate"
```

> **Note for the implementer:** the spec's illustrative set `{submit_order, delete, pay, send_email, post, confirm_purchase}` is intent-level, not enum-level — those members do not exist in `ActionType`. P5's worker safety gate maps those *intents* using each tool's `risk_level` / `requires_confirmation` fields (already on `Action`, see conftest fixtures). This module is the enum-level backstop only. Do not invent new `ActionType` members here.

---

### Task 3: `LeadState` / `WorkerState` TypedDicts + factories

**Files:**
- Create: `backend/src/agent_core/schemas/orchestrator_state.py`
- Test: `backend/tests/test_orchestrator_state.py`

**Interfaces:**
- Consumes: `PlanItem`, `ResultDigest`, `WorkerRole` (Task 1); `PageContext` from `agent_core.schemas.dom`.
- Produces:
  - `class LeadState(TypedDict)`: `original_goal: str`, `plan: list[PlanItem]`, `active_item_id: str | None`, `lead_decision: dict`, `delegations_used: int`, `tabs: dict[str, str]`, `stored_credentials: dict`, `model_name: str`, `api_keys: dict | None`, `messages: list`.
  - `class WorkerState(TypedDict)`: `role: WorkerRole`, `subgoal: str`, `done_criteria: str`, `tab_id: str | None`, `page_context: PageContext | None`, `action_history: list`, `actions_used: int`, `result_digest: ResultDigest | None`, `model_name: str`, `api_keys: dict | None`.
  - `create_lead_state(goal_text, model_name, api_keys=None, prior_messages=None) -> LeadState`.
  - `create_worker_state(role, subgoal, done_criteria, model_name, tab_id=None, page_context=None, api_keys=None) -> WorkerState`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_orchestrator_state.py
"""Tests for the split lead/worker state and their factories."""

from agent_core.schemas.orchestrator import WorkerRole, ResultDigest, PlanItem
from agent_core.schemas.orchestrator_state import (
    LeadState,
    WorkerState,
    create_lead_state,
    create_worker_state,
)


class TestCreateLeadState:
    def test_initializes_empty_ledger(self):
        s = create_lead_state(goal_text="Compare price of X on two sites",
                               model_name="gpt-4o-mini")
        assert s["original_goal"] == "Compare price of X on two sites"
        assert s["plan"] == []
        assert s["active_item_id"] is None
        assert s["delegations_used"] == 0
        assert s["tabs"] == {}
        assert s["stored_credentials"] == {}
        assert s["messages"] == []

    def test_plan_accepts_plan_items(self):
        s = create_lead_state(goal_text="g", model_name="gpt-4o-mini")
        s["plan"].append(
            PlanItem(id="i1", subgoal="open site", role=WorkerRole.NAVIGATOR,
                     done_criteria="page loaded")
        )
        assert s["plan"][0].role is WorkerRole.NAVIGATOR


class TestCreateWorkerState:
    def test_carries_delegation_context(self):
        s = create_worker_state(
            role=WorkerRole.EXTRACTOR,
            subgoal="read the price",
            done_criteria="price captured",
            model_name="gpt-4o-mini",
            tab_id="tab_a",
        )
        assert s["role"] is WorkerRole.EXTRACTOR
        assert s["subgoal"] == "read the price"
        assert s["tab_id"] == "tab_a"
        assert s["actions_used"] == 0
        assert s["result_digest"] is None
        assert s["action_history"] == []

    def test_isolation_fields_start_empty(self):
        # The big/noisy fields never come from the lead — they start empty.
        s = create_worker_state(
            role=WorkerRole.NAVIGATOR, subgoal="go", done_criteria="there",
            model_name="gpt-4o-mini",
        )
        assert s["page_context"] is None
        assert s["action_history"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_orchestrator_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.schemas.orchestrator_state'`

- [ ] **Step 3: Write the module**

```python
# backend/src/agent_core/schemas/orchestrator_state.py
"""Split state for the orchestrator harness.

LeadState is small and survives the whole run. WorkerState is ephemeral and
thrown away after its ResultDigest is folded back into the ledger. The big,
noisy fields (page_context, action_history) live ONLY in WorkerState and never
enter LeadState — that is the context-isolation invariant.
"""

from typing import TypedDict

from agent_core.schemas.dom import PageContext
from agent_core.schemas.orchestrator import PlanItem, ResultDigest, WorkerRole


class LeadState(TypedDict):
    """Coordinator state. No browser tools, no DOM, no action history."""

    original_goal: str
    plan: list[PlanItem]            # the living ledger
    active_item_id: str | None
    lead_decision: dict             # last plan_step output
    delegations_used: int           # lead-level cap (see budgets.LEAD_DELEGATION_CAP)
    tabs: dict[str, str]            # tab_id -> what's there ("gmail inbox")
    stored_credentials: dict        # SECURITY: tracked separately (see spec §6)
    model_name: str
    api_keys: dict | None
    messages: list                  # lead's own short history


class WorkerState(TypedDict):
    """Ephemeral per-subgoal state. Discarded after result_digest is produced."""

    role: WorkerRole
    subgoal: str
    done_criteria: str
    tab_id: str | None
    page_context: PageContext | None   # big/noisy — never leaves the worker
    action_history: list               # big/noisy — never leaves the worker
    actions_used: int                  # per-worker budget counter
    result_digest: ResultDigest | None
    model_name: str
    api_keys: dict | None


def create_lead_state(
    goal_text: str,
    model_name: str,
    api_keys: dict | None = None,
    prior_messages: list | None = None,
) -> LeadState:
    """Fresh lead state for a new task."""
    return LeadState(
        original_goal=goal_text,
        plan=[],
        active_item_id=None,
        lead_decision={},
        delegations_used=0,
        tabs={},
        stored_credentials={},
        model_name=model_name,
        api_keys=api_keys,
        messages=prior_messages or [],
    )


def create_worker_state(
    role: WorkerRole,
    subgoal: str,
    done_criteria: str,
    model_name: str,
    tab_id: str | None = None,
    page_context: PageContext | None = None,
    api_keys: dict | None = None,
) -> WorkerState:
    """Fresh worker state for a single delegated subgoal."""
    return WorkerState(
        role=role,
        subgoal=subgoal,
        done_criteria=done_criteria,
        tab_id=tab_id,
        page_context=page_context,
        action_history=[],
        actions_used=0,
        result_digest=None,
        model_name=model_name,
        api_keys=api_keys,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_orchestrator_state.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/schemas/orchestrator_state.py backend/tests/test_orchestrator_state.py
git commit -m "feat(orchestrator): add split LeadState/WorkerState and factories"
```

---

### Task 4: Role → tool-name static registry

**Files:**
- Create: `backend/src/agent_core/agent/roles.py`
- Test: `backend/tests/test_roles.py`

**Interfaces:**
- Consumes: `WorkerRole` (Task 1); `WORKER_ACTION_CAP` is unrelated — this task caps *tool count*, not actions.
- Produces:
  - `ROLE_TOOL_NAMES: dict[WorkerRole, list[str]]` — maps each role to the tool NAMES it may use. Names are strings here (decoupled from tool objects, which P2 resolves). Target names, using the P2-consolidated `read`/`see`:
    - navigator: `["navigate", "click", "scroll_down", "wait", "go_back"]`
    - extractor: `["read", "see", "extract_table"]`
    - form_filler: `["fill_form", "select_option", "check", "upload_file", "press_key"]`
    - verifier: `["read", "see", "finish_subgoal"]`
    - auth: `["navigate", "fill_form", "click", "type_credential", "submit", "see"]`
  - `LEAD_TOOL_NAMES: list[str] = ["delegate", "update_plan", "ask_user", "finish"]`
  - `MAX_TOOLS_PER_ROLE: int = 8`
  - `tools_for_role(role: WorkerRole) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_roles.py
"""Tests for the role→tool static registry (the 'don't dump all tools' guarantee)."""

from agent_core.schemas.orchestrator import WorkerRole
from agent_core.agent.roles import (
    ROLE_TOOL_NAMES,
    LEAD_TOOL_NAMES,
    MAX_TOOLS_PER_ROLE,
    tools_for_role,
)


class TestRoleRegistry:
    def test_every_role_has_a_menu(self):
        for role in WorkerRole:
            assert role in ROLE_TOOL_NAMES
            assert len(ROLE_TOOL_NAMES[role]) >= 1

    def test_no_role_exceeds_the_cap(self):
        for role, names in ROLE_TOOL_NAMES.items():
            assert len(names) <= MAX_TOOLS_PER_ROLE, f"{role} has too many tools"

    def test_lead_has_no_browser_tools(self):
        # The coordinator's menu is exactly four coordination tools.
        assert LEAD_TOOL_NAMES == ["delegate", "update_plan", "ask_user", "finish"]
        browser_names = {n for names in ROLE_TOOL_NAMES.values() for n in names}
        assert not (set(LEAD_TOOL_NAMES) & browser_names)

    def test_tools_for_role_accessor(self):
        assert tools_for_role(WorkerRole.EXTRACTOR) == ["read", "see", "extract_table"]

    def test_no_role_menu_has_duplicates(self):
        for role, names in ROLE_TOOL_NAMES.items():
            assert len(names) == len(set(names)), f"{role} has duplicate tools"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_roles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.agent.roles'`

- [ ] **Step 3: Write the module**

```python
# backend/src/agent_core/agent/roles.py
"""Static role→tool registry.

This is THE mechanism that keeps tool menus small: a worker binds only its
role's tools, and the lead binds none of them. Role is chosen by the model at
delegate-time; tools follow deterministically from this dict — replacing the
old keyword-guessing select_tools_for_context.

Names here are strings, intentionally decoupled from the @tool objects. P2's
get_worker_llm resolves these names to real tool objects (including the
consolidated `read`/`see`). Keeping names as data lets P1 test the shape
without importing the tool layer.
"""

from agent_core.schemas.orchestrator import WorkerRole

MAX_TOOLS_PER_ROLE: int = 8

ROLE_TOOL_NAMES: dict[WorkerRole, list[str]] = {
    WorkerRole.NAVIGATOR: ["navigate", "click", "scroll_down", "wait", "go_back"],
    WorkerRole.EXTRACTOR: ["read", "see", "extract_table"],
    WorkerRole.FORM_FILLER: ["fill_form", "select_option", "check", "upload_file", "press_key"],
    WorkerRole.VERIFIER: ["read", "see", "finish_subgoal"],
    WorkerRole.AUTH: ["navigate", "fill_form", "click", "type_credential", "submit", "see"],
}

# The coordinator has ZERO browser tools — four coordination tools only.
LEAD_TOOL_NAMES: list[str] = ["delegate", "update_plan", "ask_user", "finish"]


def tools_for_role(role: WorkerRole) -> list[str]:
    """Return the tool-name menu for a role."""
    return ROLE_TOOL_NAMES[role]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_roles.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full P1 suite + commit**

Run: `cd backend && python -m pytest tests/test_orchestrator_schemas.py tests/test_budgets.py tests/test_orchestrator_state.py tests/test_roles.py -v`
Expected: PASS (18 tests total). Then:

```bash
git add backend/src/agent_core/agent/roles.py backend/tests/test_roles.py
git commit -m "feat(orchestrator): add static role→tool registry (progressive tool disclosure)"
```

**P1 exit gate:** all four new test files green; existing `tests/test_graph.py` / `tests/test_schemas.py` still green (`python -m pytest tests/` from `backend/`); no existing file modified.

---

# PHASE 2 — Worker subgraph + role-scoped tools + read/see consolidation

**Goal of phase:** a worker subgraph that, given a `WorkerState`, runs a bounded ReAct loop with ONLY its role's tools and returns a `ResultDigest`. No lead yet — tested in isolation with a stubbed browser `interrupt`.

> Expand to bite-sized steps via `superpowers:writing-plans` once P1 is merged and the real tool names/signatures are fixed.

### Task P2.1 — Consolidate read tools → `read` + `see`
- **Files:** Create `tools/consolidated_tools.py`; reference existing markers in `tools/browser_tools.py`.
- **Interfaces produced:** `read(what: str) -> str` (emits an Action carrying `__READ__|what`; internally the executor picks table vs listing vs plain — see Task 2.2), `see(question: str) -> str` (emits `__SEE__|question`, the vision path). Both are LangChain `@tool` objects exported in a `CONSOLIDATED_TOOLS` list.
- **Representative test:** `test_read_tool_emits_read_marker` — call `read.invoke({"what": "all prices"})`, assert the returned Action string / tool schema name is `read`; `test_see_tool_is_vision_only` — assert `see` schema documents pixels-only use.
- **Code sketch:**
  ```python
  from langchain_core.tools import tool
  @tool
  def read(what: str) -> str:
      """Extract text/data from the current page. Auto-detects tables and
      listings. Use this for anything you can get from the DOM/text."""
      return f"__READ__|{what}"
  @tool
  def see(question: str) -> str:
      """Answer a question that requires LOOKING at the rendered pixels
      (layout, colors, images). Do NOT use for text you can read()."""
      return f"__SEE__|{question}"
  CONSOLIDATED_TOOLS = [read, see]
  ```
- **Migration note:** `read_page`/`extract_text`/`extract_listings` remain in `BROWSER_TOOLS` until P4 cutover; do not delete yet.

### Task P2.2 — Executor handling for `__READ__` / `__SEE__`
- **Files:** Modify `playwright/action_executor.py` (already touched in working tree) + the extension-facing `ws_handler` vision path (`server/ws_handler.py:740`).
- **Interfaces:** `__READ__|<what>` routes to the existing text/table/listing extractors, auto-selecting by page shape; `__SEE__|<question>` routes to the existing vision httpx call.
- **Representative test:** `test_read_marker_dispatches_to_extractor` (unit, mock page) and `test_see_marker_dispatches_to_vision` (mock httpx).

### Task P2.3 — `get_worker_llm(role, model, api_keys)` resolving role→tool objects
- **Files:** Modify `agent/llm_client.py`.
- **Interfaces produced:** `get_worker_llm(role: WorkerRole, model_name: str, api_keys: dict | None) -> BaseChatModel` — resolves `tools_for_role(role)` names to actual `@tool` objects (from `BROWSER_TOOLS` + `CONSOLIDATED_TOOLS` + the auth-only `type_credential`/`submit`/`finish_subgoal` tools defined here), then `.bind_tools(resolved)`.
- **Consumes:** `ROLE_TOOL_NAMES` (Task 4), `CONSOLIDATED_TOOLS` (Task 2.1).
- **Representative test:** `test_worker_llm_binds_only_role_tools` — patch the provider, assert the bound tool set equals the role's menu; `test_unknown_tool_name_raises` — a name with no object raises a clear error (guards drift).
- **Note:** this task **adds** `get_worker_llm`; do NOT delete `select_tools_for_context` yet (P4).

### Task P2.4 — Auth-only tools: `type_credential`, `submit`, `finish_subgoal`
- **Files:** Create in `tools/consolidated_tools.py` (or `tools/worker_tools.py`).
- **Interfaces:** `type_credential(field: str)` (emits marker; the executor injects the stored credential value, so the plaintext secret never enters the LLM prompt), `submit()`, `finish_subgoal(summary: str, data: dict | None = None)` (worker's terminal tool → becomes the `ResultDigest`).
- **Representative test:** `test_finish_subgoal_shapes_digest` — the marker parses into a `ResultDigest(status="done", ...)`.

### Task P2.5 — Worker subgraph builder
- **Files:** Create `agent/worker_graph.py`; reuse trimmed nodes from `agent/nodes.py` (`decide_action`, `execute_action_node`, `observe`, `smart_evaluate`).
- **Interfaces produced:** `build_worker_graph(checkpointer=None) -> CompiledGraph` over `WorkerState`. Flow: `decide_action → execute_action_node → observe → smart_evaluate → (loop | finish_subgoal)`. Enforces `WORKER_ACTION_CAP`: when `actions_used >= 8` and not done, emit `ResultDigest(status="failed", summary="budget exhausted", actions_used=8)`.
- **Representative tests (browser stubbed via interrupt):**
  - `test_worker_returns_done_digest_on_finish` — worker calls `finish_subgoal` → digest.status == "done".
  - `test_worker_binds_only_role_tools` — extractor worker never emits a `click` action.
  - `test_worker_stops_at_action_cap` — force 8 non-terminal actions → digest.status == "failed", actions_used == 8.
  - `test_worker_bubbles_needs_user` — destructive action (is_destructive True) → digest.needs_user True.
- **Phase exit gate:** worker subgraph green in isolation; the old `create_agent_graph` still untouched and green.

---

# PHASE 3 — Lead graph + wire worker as subgraph-node

**Goal of phase:** the lead loop (`seed_plan → plan_step → dispatch → integrate → …`) with the P2 worker mounted as a single node. End-to-end orchestration with mocked workers first, then the real worker node.

### Task P3.1 — `seed_plan` node (model-seeded plan)
- **Files:** Create `agent/lead_nodes.py`.
- **Interfaces produced:** `async def seed_plan(state: LeadState) -> dict` — one LLM call with `.with_structured_output(SeedPlanOutput)` where `SeedPlanOutput = BaseModel{ items: list[PlanItem] }` (2–5 coarse items). Replaces `_decompose_goal_into_steps`.
- **Representative test:** `test_seed_plan_populates_ledger` — mock structured LLM returns 3 items → `result["plan"]` has 3 `PlanItem`s each with a `role` and `done_criteria`.

### Task P3.2 — `plan_step` node (one decision)
- **Interfaces produced:** `async def plan_step(state: LeadState) -> dict` — one LLM call, structured output `LeadDecision = BaseModel{ action: Literal["delegate","update_plan","ask_user","finish"], ... }`. Emits exactly one decision. `finish` allowed only when every non-`skipped` item is `done`/`failed` (validated in the node; if not, it re-delegates the next `pending` item).
- **Representative tests:** `test_plan_step_delegates_next_pending`, `test_plan_step_blocks_premature_finish`, `test_plan_step_finish_when_all_done`.

### Task P3.3 — `dispatch` conditional edge + `integrate` node
- **Interfaces produced:** `route_dispatch(state) -> Literal["worker","ask_user","finish","plan_step"]`; `def integrate(state: LeadState) -> dict` — folds the worker's `ResultDigest` into the active `PlanItem` (status, `result_digest` summary), records any new `tab_id` into `state["tabs"]`, increments `delegations_used`. 0-LLM.
- **Representative tests:** `test_integrate_marks_item_done`, `test_integrate_records_new_tab`, `test_integrate_marks_failed_and_counts_retry`.

### Task P3.4 — Mount worker subgraph as a node + assemble `build_lead_graph`
- **Files:** Create `agent/lead_graph.py`.
- **Interfaces produced:** `build_lead_graph(checkpointer=None) -> CompiledGraph` over `LeadState`. A `worker_node(state: LeadState) -> dict` adapter builds a `WorkerState` from the active item (via `create_worker_state`), invokes the compiled worker subgraph, and returns `{"lead_decision": {"digest": <ResultDigest>}}` for `integrate`. Interrupts bubble across the subgraph boundary (LangGraph native).
- **Representative tests:** `test_lead_runs_single_item_to_finish` (mock worker node returns a done digest), `test_lead_respects_delegation_cap` (force cap → finish/fail path, no infinite loop), `test_multi_item_sequential_order` (two items run in dependency order).

### Task P3.5 — `ws_handler` drives the lead graph
- **Files:** Modify `server/ws_handler.py` (`_handle_goal`, the `astream` loop, interrupt extract/handle).
- **Interfaces:** swap `create_agent_graph` for `build_lead_graph`; build `LeadState` via `create_lead_state`; keep the existing interrupt/resume plumbing (unchanged mechanism).
- **Representative test:** `test_ws_handler_starts_lead_graph` (mock graph, assert `create_lead_state` used and `astream` invoked).
- **Phase exit gate:** a mocked end-to-end goal completes through the lead loop; the branchy Playwright integration test (design §8 case 2) passes manually.

---

# PHASE 4 — Delete regex heuristics + legacy nodes; cut over

**Goal of phase:** remove the scaffolding the new harness replaces. Each deletion is paired with a regression test proving the LLM path covers the old case.

### Task P4.1 — Regression safety net BEFORE deleting
- **Files:** Create `backend/tests/test_migration_regression.py`.
- **Content:** golden cases that used to be handled by regex, now asserted against the LLM path (mocked structured outputs): auth-intent goal → seed_plan emits an `auth` item; compound goal → 2–5 items; contradicted-done text → item stays `pending`/`failed`, not `done`.
- **Why first:** proves parity before removal (TDD safety).

### Task P4.2 — Delete regex heuristics from `nodes.py`
- **Delete:** `_KNOWN_SITES`, `_build_direct_url`, `_decompose_goal_into_steps`, `_detect_auth_intent`, `_page_has_login_fields`, `_CONTRADICTION_MARKERS`, `_looks_like_contradicted_done`, `_action_history_has_evidence`, `_build_success_criteria`, `_collapse_to_milestones` (all in `agent/nodes.py`, lines per spec §7).
- **Test:** `test_deleted_helpers_are_gone` — `import agent_core.agent.nodes as n; assert not hasattr(n, "_decompose_goal_into_steps")` (etc.). Full suite stays green.

### Task P4.3 — Delete `select_tools_for_context` + `get_action_llm_dynamic`
- **Files:** `agent/llm_client.py`. Replaced by `get_worker_llm` (Task 2.3).
- **Test:** `test_dynamic_selector_removed`; grep-guard that no module imports it.

### Task P4.4 — Delete legacy nodes + dead PlanStep fields
- **Delete nodes:** `analyze_goal`, `create_plan`, `critique_plan`, `reason` and their routing in `agent/graph.py`; remove `PlanStep.depends_on` and `PlanStep.can_parallelize` (`schemas/agent.py:89,93`) and update `conftest.py`/`test_graph.py` fixtures that reference them.
- **Files:** `agent/nodes.py`, `agent/graph.py`, `schemas/agent.py`, `tests/conftest.py`, `tests/test_graph.py`.
- **Test:** update `test_graph.py` to target `build_lead_graph`; delete now-obsolete routing tests (`route_after_critique`, etc.) or repoint them.
- **Note:** this is the cutover — `create_agent_graph` becomes a thin alias returning `build_lead_graph()` for backward compat, or is removed if no caller remains (grep first).

### Task P4.5 — Full-suite green + manual smoke
- Run `python -m pytest tests/` from `backend/`; run the simple Playwright integration case (design §8 case 1).
- **Phase exit gate:** ~10 heuristic blocks + 4 legacy nodes gone; suite green; simple + branchy integration cases pass.

---

# PHASE 5 — LangSmith nesting, budget enforcement polish, safety gate

### Task P5.1 — Nested LangSmith runs
- **Files:** `server/ws_handler.py` (`run_name=f"agent_{session_id}"` at ~line 375), `agent/lead_graph.py` worker node.
- **Interfaces:** tag each worker invocation with `config={"run_name": f"worker:{role}[{item_id}]", "metadata": {"role": role, "item_id": item_id}}`.
- **Test:** `test_worker_run_name_tagged` (assert config threaded to the subgraph invoke).

### Task P5.2 — Worker safety gate (intent-level)
- **Files:** `agent/worker_graph.py`.
- **Interfaces:** before executing an action, if `is_destructive(action.action_type)` OR `action.requires_confirmation` OR `action.risk_level in {"high","critical"}` → short-circuit to `ResultDigest(status="needs_user", needs_user=True, question=...)`. This bridges the enum-level backstop (Task 2) with the intent-level `Action` fields (place order/pay/delete).
- **Tests:** `test_high_risk_action_bubbles_needs_user`, `test_low_risk_action_executes`.

### Task P5.3 — Budget accounting end-to-end
- **Interfaces:** `integrate` increments `delegations_used`; a failed item under `ITEM_RETRY_CAP` is re-delegated by `plan_step`, beyond it is marked `failed`.
- **Tests:** `test_item_retried_up_to_cap`, `test_item_failed_after_cap`, `test_lead_stops_at_delegation_cap`.
- **Phase exit gate:** all design §8 integration cases (simple, branchy, multi-tab, failure, HITL) pass; LangSmith shows the nested tree.

---

## Self-Review (performed against the spec)

**Spec coverage check:**
- §1 topology (lead no tools) → Task 4 (`LEAD_TOOL_NAMES`), Task 3.4. ✓
- §2 living ledger + model-seeded plan → Task 1 (`PlanItem`), Task 3.1 (`seed_plan`), Task 3.2 (`finish` gating). ✓
- §3 five roles + read/see + static registry → Task 4, Task 2.1, Task 2.3. ✓
- §4 split state + subgraph-as-node → Task 3, Task 3.4. ✓
- §5 sequential-only + tab registry → `tabs` in `LeadState` (Task 3), `integrate` records tabs (Task 3.3). Parallelism deliberately absent. ✓
- §6 HITL choke point + DESTRUCTIVE set + budgets 8/2/15 → Task 2, Task 3.3, Task 5.2. ✓
- §7 deletions → Phase 4 (each paired with a regression test, Task 4.1 first). ✓
- §8 testing (per-phase + Playwright + eval) → each phase exit gate + Task 4.1 golden set. ✓
- §6 credential security finding → carried as `stored_credentials` with a spec pointer; `type_credential` (Task 2.4) keeps the plaintext out of the LLM prompt. Full at-rest fix remains a tracked separate item, per approved decision. ✓

**Placeholder scan:** P1 (Tasks 1–4) contains complete code and exact commands, no TBD/TODO. P2–P5 use interface+sketch form by explicit design (anti-drift), flagged in the Scope Note, not as hidden placeholders.

**Type consistency:** `WorkerRole`/`PlanItemStatus`/`PlanItem`/`ResultDigest` names are used identically across Tasks 1, 3, 4 and the P2–P5 interfaces. `ROLE_TOOL_NAMES` keys are `WorkerRole` members. `create_lead_state`/`create_worker_state` signatures match their consumers in Task 3.4/3.5. `tools_for_role` used by Task 2.3.

**Known follow-ups (not gaps):** P2–P5 bite-sizing is deferred to per-phase `writing-plans` passes against merged code — this is intentional and stated in the Scope Note.
