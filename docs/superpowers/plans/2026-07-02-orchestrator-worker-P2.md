# Orchestrator–Worker Phase 2: Worker Subgraph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a role-scoped worker subgraph that, given a `WorkerState`, runs a bounded ReAct loop with ONLY its role's tools and returns a compact `ResultDigest` — tested in isolation with a stubbed browser `interrupt`.

**Architecture:** New **lean worker nodes** on `WorkerState` (NOT the AgentState-bound `decide_action`/`observe`/`smart_evaluate`, which the P1 code review proved are too coupled to reuse). The worker reuses exactly ONE thing from the existing graph: the `interrupt()` execution contract (same `execution_request` dict shape as `execute_action_node`), so the ws_handler/orchestrator browser side needs no change. `read`/`see`/`extract_table` reuse existing executor markers (`__READ_PAGE__`, `__VISUAL_CHECK__|q`, `__EXTRACT_LISTINGS__`) — no `action_executor.py` change.

**Tech Stack:** Python 3.11+, LangGraph ≥0.4 (`StateGraph`, `interrupt`, `Command`, `MemorySaver`), LangChain ≥0.3 (`@tool`, `.bind_tools`, `AIMessage.tool_calls`), Pydantic ≥2.10, pytest (`asyncio_mode=auto`). Import root `agent_core` (src layout under `backend/src`).

## Global Constraints

- Python ≥ 3.11; import root `agent_core` (installed from `backend/src`, hatchling).
- Pydantic v2 models; TypedDict for LangGraph state; `total=False` on state TypedDicts.
- Tests: `backend/tests/`, classes `Test*`, functions `test_*`; pytest `asyncio_mode=auto`.
- Run all commands from `backend/` with the venv python: `venv/Scripts/python.exe -m pytest ...` and `venv/Scripts/python.exe -m ruff check ...`. Work from repo root `C:\Users\praba\Desktop\Prabal\Agentic_Browser_Extension`.
- Ruff line-length 100, target py311. New files must be ruff-clean.
- No worker ever binds more than its role's menu (≤8 tools). Reuse `ROLE_TOOL_NAMES` from `agent/roles.py` (P1) — do not hardcode menus elsewhere.
- Only `ResultDigest` leaves the worker. `WorkerState.page_context`/`action_history` never propagate to the lead (P3 concern; here they stay inside the worker).
- `WORKER_ACTION_CAP` (8) from `agent/budgets.py` (P1) bounds the loop. `is_destructive` from `agent/budgets.py` gates escalation.
- Model target `gpt-4o-mini` (reliable tool-calling). Worker prompts are terse; no smart model-routing (that was `decide_action`'s concern, deliberately dropped).

## Existing P1 code this phase builds on (already committed on branch)

- `agent_core.schemas.orchestrator`: `WorkerRole` (navigator/extractor/form_filler/verifier/auth), `ResultDigest{status: Literal["done","failed","needs_user"], summary, data, needs_user, question, tab_id, actions_used}`, `PlanItem`.
- `agent_core.schemas.orchestrator_state`: `WorkerState` (TypedDict, total=False) + `create_worker_state(role, subgoal, done_criteria, model_name, tab_id=None, page_context=None, api_keys=None)`.
- `agent_core.agent.roles`: `ROLE_TOOL_NAMES: dict[WorkerRole, list[str]]`, `LEAD_TOOL_NAMES`, `MAX_TOOLS_PER_ROLE=8`, `tools_for_role(role)`.
- `agent_core.agent.budgets`: `WORKER_ACTION_CAP=8`, `is_destructive(action_type)`, `DESTRUCTIVE_ACTIONS`.

## Existing infrastructure facts (verbatim, from code)

- **Marker mechanism:** a `@tool` body is never executed. The decide node maps the LLM's `tool_call["name"]` → `ActionType` and re-injects markers itself. Worker replicates this in `_build_worker_action` (Task 4).
- **Existing executor markers** (`playwright/action_executor.py`): `value=="__READ_PAGE__"` → visible text; `value=="__EXTRACT_LISTINGS__"` → structured listings; `value.startswith("__VISUAL_CHECK__")` (`__VISUAL_CHECK__|<query>`) → screenshot+vision; `value` `__FILL_FORM__|<fields>|<SUBMIT|NO_SUBMIT>` handled in the CLEAR_AND_TYPE branch.
- **Interrupt contract** (`graph.py:273-305`): payload dict `{action_id, action_type (str value), element_id, element_fingerprint, value, description}`; resume value is a dict with keys `status, message, error, extracted_data, page_changed, new_url, execution_time_ms` and optional `new_dom` (a PageContext dict).
- **`ActionResult`** (`schemas/actions.py`): `action_id, status:ActionStatus, message, error, extracted_data, page_changed, new_url, execution_time_ms`.
- **`Action`** (`schemas/actions.py`): `action_id, action_type:ActionType, element_id, element_fingerprint, value, description, reasoning, confidence, requires_confirmation, is_reversible, risk_level`.
- **`ActionType`** members used here: `NAVIGATE, CLICK, SCROLL_DOWN, WAIT, GO_BACK, SELECT_OPTION, CHECK, UPLOAD_FILE, PRESS_KEY, CLEAR_AND_TYPE, EXTRACT_TEXT, TAKE_SCREENSHOT, TYPE_TEXT, DONE`.
- **LLM factory** (`agent/llm_client.py`): `get_llm(model_name, temperature=0.1, streaming=True, bind_tools=True, api_keys=None, max_tokens=None)`. Bind a custom subset via `get_llm(..., bind_tools=False).bind_tools(tool_list)`.

## Scope & deferrals (explicit)

- **In scope:** navigator, extractor, verifier, form_filler workers fully; the bounded loop; budget-exhaust and destructive-escalation digests; `finish_subgoal` terminal; `read`/`see`/`extract_table` tools; `get_worker_llm`; worker-specific `WorkerState` fields.
- **Deferred to P3 (noted, not built):** the Auth role's **live** credential injection (`type_credential` needs `LeadState.stored_credentials` + the interrupt round-trip — the worker is isolated from lead creds by design). In P2 the auth tools' *schemas* are defined so `get_worker_llm(AUTH)` resolves without error, and `submit` works (PRESS_KEY Enter), but `type_credential`'s executor-side injection is a P3 task. This is `log()`-ged in the plan, not silently dropped.
- The lead graph, `seed_plan`/`plan_step`/`integrate`, and mounting the worker as a subgraph-node are **P3**.

---

## File Structure

**New files:**
- `backend/src/agent_core/tools/consolidated_tools.py` — `read`, `see`, `extract_table`, `submit`, `type_credential`, `finish_subgoal` `@tool` schemas + `WORKER_TOOL_OBJECTS: dict[str, BaseTool]` registry (existing tools + new).
- `backend/src/agent_core/agent/worker_nodes.py` — `_build_worker_action`, `_parse_execution_result`, `worker_decide`, `worker_execute`, `worker_evaluate` node fns + routing helpers.
- `backend/src/agent_core/agent/worker_graph.py` — `build_worker_graph(checkpointer=None)`.
- `backend/tests/test_consolidated_tools.py`, `backend/tests/test_worker_llm.py`, `backend/tests/test_worker_nodes.py`, `backend/tests/test_worker_graph.py`.

**Modified files:**
- `backend/src/agent_core/agent/roles.py` — add `resolve_role_tools(role) -> list[BaseTool]`.
- `backend/src/agent_core/agent/llm_client.py` — add `get_worker_llm(role, model_name, api_keys)`.
- `backend/src/agent_core/schemas/orchestrator_state.py` — add ReAct-loop fields to `WorkerState`.

---

### Task 1: Consolidated tool schemas + worker tool registry

**Files:**
- Create: `backend/src/agent_core/tools/consolidated_tools.py`
- Test: `backend/tests/test_consolidated_tools.py`

**Interfaces:**
- Consumes: `Action`, `ActionType` from `agent_core.schemas.actions`; existing tools from `agent_core.tools.browser_tools` (`navigate, click, scroll_down, wait, go_back, select_option, check, upload_file, press_key, fill_form`).
- Produces:
  - `@tool read(what: str) -> str`, `@tool see(question: str) -> str`, `@tool extract_table(what: str) -> str`, `@tool submit() -> str`, `@tool type_credential(field: str) -> str`, `@tool finish_subgoal(summary: str, data: dict | None = None) -> str`. (Bodies are decorative — they return a short string; the real Action is built in Task 4. What matters is the schema: name, args, docstring.)
  - `WORKER_TOOL_OBJECTS: dict[str, BaseTool]` mapping every tool NAME appearing in `ROLE_TOOL_NAMES` to its `@tool` object.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_consolidated_tools.py
"""Tests for consolidated worker tools + the name→object registry."""

from agent_core.tools.consolidated_tools import (
    read, see, extract_table, submit, type_credential, finish_subgoal,
    WORKER_TOOL_OBJECTS,
)
from agent_core.agent.roles import ROLE_TOOL_NAMES


class TestToolSchemas:
    def test_tools_have_expected_names(self):
        assert read.name == "read"
        assert see.name == "see"
        assert extract_table.name == "extract_table"
        assert submit.name == "submit"
        assert type_credential.name == "type_credential"
        assert finish_subgoal.name == "finish_subgoal"

    def test_read_takes_what_arg(self):
        # LangChain derives the arg schema from the signature.
        assert "what" in read.args

    def test_finish_subgoal_takes_summary_and_optional_data(self):
        assert "summary" in finish_subgoal.args
        assert "data" in finish_subgoal.args


class TestRegistry:
    def test_registry_covers_every_role_tool_name(self):
        needed = {name for names in ROLE_TOOL_NAMES.values() for name in names}
        missing = needed - set(WORKER_TOOL_OBJECTS)
        assert missing == set(), f"registry missing objects for: {missing}"

    def test_registry_values_are_tools_with_matching_names(self):
        for name, obj in WORKER_TOOL_OBJECTS.items():
            assert obj.name == name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_consolidated_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.tools.consolidated_tools'`

- [ ] **Step 3: Write the module**

```python
# backend/src/agent_core/tools/consolidated_tools.py
"""Consolidated worker tools and the name→object registry.

Design: a @tool body is NEVER executed at decision time — it exists only as a
schema the LLM sees. The real Action (with its executor marker) is built from
the tool NAME in worker_nodes._build_worker_action. So these bodies return a
short sentinel string only to satisfy the type; nothing reads it.

read  → reuses the existing __READ_PAGE__ marker (visible text).
see   → reuses __VISUAL_CHECK__ (screenshot + vision).
extract_table → reuses __EXTRACT_LISTINGS__ (structured rows).
No action_executor.py change is required for these three.
"""

from langchain_core.tools import BaseTool, tool

from agent_core.tools.browser_tools import (
    navigate, click, scroll_down, wait, go_back,
    select_option, check, upload_file, press_key, fill_form,
)


@tool
def read(what: str) -> str:
    """Extract text/data from the current page (visible text and DOM content).
    Use for anything you can get as text. Say what you are looking for.

    Args:
        what: what information you want from the page (e.g. "the article title").
    """
    return "__READ__"


@tool
def see(question: str) -> str:
    """Answer a question that requires LOOKING at the rendered pixels — layout,
    colors, images, chart shapes. Do NOT use for text you could read().

    Args:
        question: the specific visual question (e.g. "is the Submit button disabled?").
    """
    return "__SEE__"


@tool
def extract_table(what: str) -> str:
    """Extract structured rows from a table, product grid, search results, or any
    repeated card layout on the current page.

    Args:
        what: the kind of rows to extract (e.g. "laptop names and prices").
    """
    return "__EXTRACT_TABLE__"


@tool
def submit() -> str:
    """Submit the current form (presses Enter in the focused field)."""
    return "__SUBMIT__"


@tool
def type_credential(field: str) -> str:
    """Type a stored credential into a field WITHOUT the secret ever entering
    your context. Use for login username/password fields.

    Args:
        field: which credential to type — "email" or "password".
    """
    return "__TYPE_CREDENTIAL__"


@tool
def finish_subgoal(summary: str, data: dict | None = None) -> str:
    """Call this when the subgoal's done-criteria are met. This ENDS your work
    and returns your result to the coordinator.

    Args:
        summary: one or two lines describing what you accomplished.
        data: optional structured values you collected (e.g. {"price": "$38"}).
    """
    return "__FINISH_SUBGOAL__"


# Every tool NAME that appears in any role's menu must resolve to an object here.
WORKER_TOOL_OBJECTS: dict[str, BaseTool] = {
    # existing browser tools reused by roles
    "navigate": navigate,
    "click": click,
    "scroll_down": scroll_down,
    "wait": wait,
    "go_back": go_back,
    "select_option": select_option,
    "check": check,
    "upload_file": upload_file,
    "press_key": press_key,
    "fill_form": fill_form,
    # new consolidated / worker tools
    "read": read,
    "see": see,
    "extract_table": extract_table,
    "submit": submit,
    "type_credential": type_credential,
    "finish_subgoal": finish_subgoal,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_consolidated_tools.py -v`
Expected: PASS (5 tests). Then `venv/Scripts/python.exe -m ruff check src/agent_core/tools/consolidated_tools.py tests/test_consolidated_tools.py` — clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/tools/consolidated_tools.py backend/tests/test_consolidated_tools.py
git commit -m "feat(worker): add consolidated read/see tools + worker tool registry"
```

---

### Task 2: `resolve_role_tools` + `get_worker_llm`

**Files:**
- Modify: `backend/src/agent_core/agent/roles.py`
- Modify: `backend/src/agent_core/agent/llm_client.py`
- Test: `backend/tests/test_worker_llm.py`

**Interfaces:**
- Consumes: `WORKER_TOOL_OBJECTS` (Task 1); `ROLE_TOOL_NAMES`/`tools_for_role` (P1); `get_llm` (existing); `WorkerRole` (P1).
- Produces:
  - `roles.resolve_role_tools(role: WorkerRole) -> list[BaseTool]` — maps the role's names to objects; raises `KeyError` with a clear message if a name has no object.
  - `llm_client.get_worker_llm(role: WorkerRole, model_name: str, api_keys: dict | None = None) -> BaseChatModel` — `get_llm(bind_tools=False).bind_tools(resolve_role_tools(role))`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_worker_llm.py
"""Tests for role→tool resolution and the role-scoped worker LLM factory."""

import pytest

from agent_core.schemas.orchestrator import WorkerRole
from agent_core.agent.roles import resolve_role_tools


class TestResolveRoleTools:
    def test_extractor_resolves_to_its_three_tools(self):
        tools = resolve_role_tools(WorkerRole.EXTRACTOR)
        assert [t.name for t in tools] == ["read", "see", "extract_table"]

    def test_navigator_resolves_all_names(self):
        tools = resolve_role_tools(WorkerRole.NAVIGATOR)
        assert [t.name for t in tools] == ["navigate", "click", "scroll_down", "wait", "go_back"]

    def test_every_role_resolves_without_error(self):
        for role in WorkerRole:
            tools = resolve_role_tools(role)
            assert len(tools) >= 1


class TestGetWorkerLLM:
    def test_binds_only_role_tools(self):
        # get_llm with an Ollama-style name does not connect; binding is local.
        from agent_core.agent.llm_client import get_worker_llm
        llm = get_worker_llm(WorkerRole.EXTRACTOR, model_name="qwen2.5:32b-instruct")
        # bound tools live on the runnable; extract their names
        bound = llm.kwargs.get("tools") if hasattr(llm, "kwargs") else None
        assert bound is not None
        names = {t["function"]["name"] for t in bound}
        assert names == {"read", "see", "extract_table"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_llm.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_role_tools'`

- [ ] **Step 3a: Add `resolve_role_tools` to `roles.py`**

Add these imports at the top of `backend/src/agent_core/agent/roles.py` (below the existing `WorkerRole` import):

```python
from langchain_core.tools import BaseTool

from agent_core.tools.consolidated_tools import WORKER_TOOL_OBJECTS
```

Add this function at the end of `roles.py`:

```python
def resolve_role_tools(role: WorkerRole) -> list[BaseTool]:
    """Resolve a role's tool NAMES to their @tool objects.

    Raises KeyError (with the offending name) if a role references a tool name
    that has no registered object — this is the drift guard: a typo in
    ROLE_TOOL_NAMES fails loudly instead of silently binding nothing.
    """
    resolved: list[BaseTool] = []
    for name in ROLE_TOOL_NAMES[role]:
        try:
            resolved.append(WORKER_TOOL_OBJECTS[name])
        except KeyError as exc:
            raise KeyError(
                f"role {role.value!r} references tool {name!r} with no object "
                f"in WORKER_TOOL_OBJECTS"
            ) from exc
    return resolved
```

- [ ] **Step 3b: Add `get_worker_llm` to `llm_client.py`**

Add at the end of `backend/src/agent_core/agent/llm_client.py`:

```python
def get_worker_llm(
    role,
    model_name: str,
    api_keys: dict | None = None,
) -> BaseChatModel:
    """LLM bound to exactly one worker role's tool menu.

    Replaces get_action_llm_dynamic's keyword-guessing tool selection: the role
    (chosen by the lead at delegate-time) determines the tools deterministically.

    Args:
        role: a WorkerRole member.
        model_name: the model to use.
        api_keys: optional runtime keys (KeyVault).
    """
    # Local import avoids a module-load cycle (roles imports tools; llm_client
    # imports browser_tools). Import here, at call time.
    from agent_core.agent.roles import resolve_role_tools

    tools = resolve_role_tools(role)
    return get_llm(
        model_name=model_name,
        temperature=0.1,
        bind_tools=False,
        api_keys=api_keys,
        max_tokens=512,
    ).bind_tools(tools)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_llm.py -v`
Expected: PASS (4 tests).

Note on the bound-tools assertion: `ChatOllama.bind_tools` stores the converted tool specs in the runnable's `kwargs["tools"]`, each shaped `{"type":"function","function":{"name":...}}`. If a future LangChain version changes this internal shape and `test_binds_only_role_tools` breaks, assert via `len(bound) == 3` plus a `resolve_role_tools` name check instead — the behavioral guarantee (only role tools bound) is what matters.

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/agent/roles.py backend/src/agent_core/agent/llm_client.py backend/tests/test_worker_llm.py
git commit -m "feat(worker): resolve role tools + role-scoped get_worker_llm"
```

---

### Task 3: WorkerState ReAct-loop fields

**Files:**
- Modify: `backend/src/agent_core/schemas/orchestrator_state.py`
- Test: `backend/tests/test_orchestrator_state.py` (append)

**Interfaces:**
- Consumes: `Action`, `ActionResult` from `agent_core.schemas.actions`; `PageContext` (already imported).
- Produces: `WorkerState` gains `current_action: Action | None`, `pending_result: ActionResult | None`, `previous_page_context: PageContext | None`, `messages: Annotated[list, add_messages]`, `finished: bool`. `create_worker_state` initializes them (`current_action=None`, `pending_result=None`, `previous_page_context=None`, `messages=[]`, `finished=False`).

- [ ] **Step 1: Write the failing test** (append to `backend/tests/test_orchestrator_state.py`)

```python
class TestWorkerStateReactFields:
    def test_react_fields_initialized(self):
        from agent_core.schemas.orchestrator import WorkerRole
        from agent_core.schemas.orchestrator_state import create_worker_state
        s = create_worker_state(
            role=WorkerRole.NAVIGATOR, subgoal="go", done_criteria="there",
            model_name="gpt-4o-mini",
        )
        assert s["current_action"] is None
        assert s["pending_result"] is None
        assert s["previous_page_context"] is None
        assert s["messages"] == []
        assert s["finished"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_orchestrator_state.py::TestWorkerStateReactFields -v`
Expected: FAIL — `KeyError: 'current_action'`

- [ ] **Step 3: Update `orchestrator_state.py`**

Add imports near the top (with the existing schema imports):

```python
from agent_core.schemas.actions import Action, ActionResult
```

Add these fields inside `class WorkerState(TypedDict, total=False):` (after `result_digest`):

```python
    current_action: Action | None          # action chosen this turn, awaiting execution
    pending_result: ActionResult | None     # result of the last executed action
    previous_page_context: PageContext | None
    finished: bool                          # set when finish_subgoal is called
```

`messages` is already appropriate to add — insert it too:

```python
    messages: Annotated[list, add_messages]  # worker's own ReAct tool-call thread
```

Add the `Annotated`/`add_messages` imports if not present (they already are from the P1 fix):

```python
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages
```

Update `create_worker_state` to initialize the new fields — add these keys to the returned `WorkerState(...)`:

```python
        current_action=None,
        pending_result=None,
        previous_page_context=None,
        finished=False,
        messages=[],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_orchestrator_state.py -v`
Expected: PASS (all, including the new class). Ruff clean on the file.

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/schemas/orchestrator_state.py backend/tests/test_orchestrator_state.py
git commit -m "feat(worker): add ReAct-loop fields to WorkerState"
```

---

### Task 4: `_build_worker_action` — tool-call → Action (+ finish detection)

**Files:**
- Create: `backend/src/agent_core/agent/worker_nodes.py`
- Test: `backend/tests/test_worker_nodes.py`

**Interfaces:**
- Consumes: `Action`, `ActionType` from `agent_core.schemas.actions`; `ResultDigest` from `agent_core.schemas.orchestrator`.
- Produces:
  - `WORKER_TOOL_TO_ACTION: dict[str, ActionType]`.
  - `_build_worker_action(tool_name: str, args: dict) -> Action | None` — returns the browser `Action`, or `None` when `tool_name == "finish_subgoal"` (terminal, no browser action).
  - `_digest_from_finish(args: dict, actions_used: int) -> ResultDigest`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_worker_nodes.py
"""Tests for worker node helpers and nodes."""

from agent_core.schemas.actions import ActionType
from agent_core.agent.worker_nodes import (
    _build_worker_action, _digest_from_finish,
)


class TestBuildWorkerAction:
    def test_read_maps_to_read_page_marker(self):
        a = _build_worker_action("read", {"what": "the price"})
        assert a.action_type == ActionType.EXTRACT_TEXT
        assert a.value == "__READ_PAGE__"

    def test_see_maps_to_visual_check_marker(self):
        a = _build_worker_action("see", {"question": "is it red?"})
        assert a.action_type == ActionType.TAKE_SCREENSHOT
        assert a.value == "__VISUAL_CHECK__|is it red?"

    def test_extract_table_maps_to_listings_marker(self):
        a = _build_worker_action("extract_table", {"what": "rows"})
        assert a.action_type == ActionType.EXTRACT_TEXT
        assert a.value == "__EXTRACT_LISTINGS__"

    def test_navigate_carries_url(self):
        a = _build_worker_action("navigate", {"url": "https://x.com"})
        assert a.action_type == ActionType.NAVIGATE
        assert a.value == "https://x.com"

    def test_click_carries_element_id(self):
        a = _build_worker_action("click", {"element_id": 7})
        assert a.action_type == ActionType.CLICK
        assert a.element_id == 7

    def test_submit_is_enter_keypress(self):
        a = _build_worker_action("submit", {})
        assert a.action_type == ActionType.PRESS_KEY
        assert a.value == "Enter"

    def test_finish_subgoal_returns_none(self):
        assert _build_worker_action("finish_subgoal", {"summary": "done"}) is None


class TestDigestFromFinish:
    def test_builds_done_digest_with_data(self):
        d = _digest_from_finish({"summary": "got price", "data": {"price": "$38"}}, actions_used=3)
        assert d.status == "done"
        assert d.summary == "got price"
        assert d.data == {"price": "$38"}
        assert d.actions_used == 3

    def test_missing_data_is_none(self):
        d = _digest_from_finish({"summary": "ok"}, actions_used=1)
        assert d.data is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_nodes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.agent.worker_nodes'`

- [ ] **Step 3: Write the helpers** (start `worker_nodes.py`)

```python
# backend/src/agent_core/agent/worker_nodes.py
"""Lean worker nodes operating on WorkerState.

These are NOT the AgentState-bound decide_action/observe/smart_evaluate (the P1
review proved those are too coupled to reuse). The only thing reused from the
old graph is the interrupt() execution contract in worker_execute — identical
payload/resume shape, so the browser side (ws_handler/orchestrator) is unchanged.
"""

import uuid

from agent_core.schemas.actions import Action, ActionType
from agent_core.schemas.orchestrator import ResultDigest

# Worker tool name → ActionType. Only the tools any role can bind appear here.
WORKER_TOOL_TO_ACTION: dict[str, ActionType] = {
    "navigate": ActionType.NAVIGATE,
    "click": ActionType.CLICK,
    "scroll_down": ActionType.SCROLL_DOWN,
    "wait": ActionType.WAIT,
    "go_back": ActionType.GO_BACK,
    "select_option": ActionType.SELECT_OPTION,
    "check": ActionType.CHECK,
    "upload_file": ActionType.UPLOAD_FILE,
    "press_key": ActionType.PRESS_KEY,
    "fill_form": ActionType.CLEAR_AND_TYPE,
    "read": ActionType.EXTRACT_TEXT,
    "see": ActionType.TAKE_SCREENSHOT,
    "extract_table": ActionType.EXTRACT_TEXT,
    "submit": ActionType.PRESS_KEY,
    "type_credential": ActionType.TYPE_TEXT,
}

_VALUE_ARG_NAMES = ("url", "text", "value", "key", "keys", "css_selector", "file_path")


def _build_worker_action(tool_name: str, args: dict) -> Action | None:
    """Turn an LLM tool call into a browser Action, re-injecting executor markers.

    Returns None for finish_subgoal (terminal — no browser action).
    """
    if tool_name == "finish_subgoal":
        return None

    action_type = WORKER_TOOL_TO_ACTION.get(tool_name, ActionType.WAIT)

    # Marker re-injection (bodies of @tool are never executed; we build here).
    if tool_name == "read":
        value = "__READ_PAGE__"
    elif tool_name == "extract_table":
        value = "__EXTRACT_LISTINGS__"
    elif tool_name == "see":
        value = f"__VISUAL_CHECK__|{args.get('question', '')}"
    elif tool_name == "submit":
        value = "Enter"
    elif tool_name == "fill_form":
        fields = args.get("fields", "")
        do_submit = "SUBMIT" if args.get("submit") else "NO_SUBMIT"
        value = f"__FILL_FORM__|{fields}|{do_submit}"
    elif tool_name == "type_credential":
        # P3 wires live credential injection; the marker names the field.
        value = f"__CREDENTIAL__|{args.get('field', '')}"
    else:
        value = None
        for name in _VALUE_ARG_NAMES:
            if name in args and args[name] not in (None, ""):
                value = str(args[name])
                break

    raw_eid = args.get("element_id")
    element_id = int(raw_eid) if isinstance(raw_eid, (int, str)) and str(raw_eid).isdigit() else None

    return Action(
        action_id=f"act_{uuid.uuid4().hex[:8]}",
        action_type=action_type,
        element_id=element_id,
        value=value,
        description=args.get("description", tool_name),
        risk_level="low",
    )


def _digest_from_finish(args: dict, actions_used: int) -> ResultDigest:
    """Build the terminal ResultDigest from a finish_subgoal tool call."""
    return ResultDigest(
        status="done",
        summary=args.get("summary", ""),
        data=args.get("data"),
        actions_used=actions_used,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_nodes.py -v`
Expected: PASS (9 tests). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/agent/worker_nodes.py backend/tests/test_worker_nodes.py
git commit -m "feat(worker): add tool-call→Action builder + finish digest helper"
```

---

### Task 5: `worker_decide` node + `_parse_execution_result` helper

**Files:**
- Modify: `backend/src/agent_core/agent/worker_nodes.py`
- Test: `backend/tests/test_worker_nodes.py` (append)

**Interfaces:**
- Consumes: `get_worker_llm` (Task 2); `_build_worker_action`/`_digest_from_finish` (Task 4); `WorkerState` (Task 3); `is_destructive` from `agent_core.agent.budgets`; `ActionResult`, `ActionStatus` from `agent_core.schemas.actions`; `PageContext` from `agent_core.schemas.dom`.
- Produces:
  - `async def worker_decide(state: WorkerState) -> dict` — one LLM call, role-scoped. On `finish_subgoal` → `{"finished": True, "result_digest": ResultDigest}`. On a destructive action → `{"finished": True, "result_digest": ResultDigest(status="needs_user", needs_user=True, question=...)}`. Otherwise `{"current_action": Action, "messages": [ai_msg]}`.
  - `_parse_execution_result(action, execution_result: dict) -> tuple[ActionResult, PageContext | None]` — pure, unit-testable resume-dict parser (mirrors `graph.py:287-305`).

- [ ] **Step 1: Write the failing tests** (append to `test_worker_nodes.py`)

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from agent_core.schemas.orchestrator import WorkerRole
from agent_core.schemas.orchestrator_state import create_worker_state
from agent_core.schemas.actions import ActionStatus


def _mock_tool_call(name, args):
    resp = MagicMock()
    resp.tool_calls = [{"name": name, "args": args, "id": "tc_1"}]
    resp.content = ""
    return resp


class TestWorkerDecide:
    @pytest.mark.asyncio
    async def test_read_call_sets_current_action(self):
        state = create_worker_state(
            role=WorkerRole.EXTRACTOR, subgoal="get price",
            done_criteria="price captured", model_name="gpt-4o-mini",
        )
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_tool_call("read", {"what": "price"})
            gw.return_value = llm
            from agent_core.agent.worker_nodes import worker_decide
            out = await worker_decide(state)
        assert out["current_action"].value == "__READ_PAGE__"
        assert not out.get("finished")

    @pytest.mark.asyncio
    async def test_finish_call_sets_digest(self):
        state = create_worker_state(
            role=WorkerRole.EXTRACTOR, subgoal="get price",
            done_criteria="price captured", model_name="gpt-4o-mini",
        )
        state["actions_used"] = 2
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_tool_call(
                "finish_subgoal", {"summary": "price is $38", "data": {"price": "$38"}})
            gw.return_value = llm
            from agent_core.agent.worker_nodes import worker_decide
            out = await worker_decide(state)
        assert out["finished"] is True
        assert out["result_digest"].status == "done"
        assert out["result_digest"].data == {"price": "$38"}

    @pytest.mark.asyncio
    async def test_destructive_action_bubbles_needs_user(self):
        state = create_worker_state(
            role=WorkerRole.FORM_FILLER, subgoal="upload the file",
            done_criteria="file uploaded", model_name="gpt-4o-mini",
        )
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_tool_call(
                "upload_file", {"file_path": "/etc/passwd"})
            gw.return_value = llm
            from agent_core.agent.worker_nodes import worker_decide
            out = await worker_decide(state)
        assert out["finished"] is True
        assert out["result_digest"].status == "needs_user"
        assert out["result_digest"].needs_user is True


class TestParseExecutionResult:
    def test_parses_success_and_new_dom(self):
        from agent_core.agent.worker_nodes import _parse_execution_result
        from agent_core.schemas.actions import Action, ActionType
        action = Action(action_id="a1", action_type=ActionType.CLICK, element_id=1)
        result, page = _parse_execution_result(action, {
            "status": "success", "message": "clicked", "page_changed": True,
            "new_url": "https://x.com/next",
            "new_dom": {"url": "https://x.com/next", "title": "Next"},
        })
        assert result.status == ActionStatus.SUCCESS
        assert result.new_url == "https://x.com/next"
        assert page is not None
        assert page.title == "Next"

    def test_parses_failure_without_dom(self):
        from agent_core.agent.worker_nodes import _parse_execution_result
        from agent_core.schemas.actions import Action, ActionType
        action = Action(action_id="a2", action_type=ActionType.CLICK, element_id=1)
        result, page = _parse_execution_result(action, {"status": "failed", "message": "nope"})
        assert result.status == ActionStatus.FAILED
        assert page is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_nodes.py::TestWorkerDecide tests/test_worker_nodes.py::TestParseExecutionResult -v`
Expected: FAIL — `ImportError: cannot import name 'worker_decide'`

- [ ] **Step 3: Add `worker_decide` + `_parse_execution_result`** (append to `worker_nodes.py`)

Add imports at the top of the file (with the existing imports):

```python
import structlog

from agent_core.agent.budgets import is_destructive
from agent_core.agent.llm_client import get_worker_llm
from agent_core.schemas.actions import ActionResult, ActionStatus
from agent_core.schemas.dom import PageContext
from langchain_core.messages import HumanMessage, SystemMessage

logger = structlog.get_logger("agent.worker")

_WORKER_SYSTEM = """You are a focused browser worker. Do ONE subgoal, then call finish_subgoal.

Subgoal: {subgoal}
Done when: {done_criteria}

Rules:
- Use only your available tools.
- Take the single best next action toward the subgoal.
- The moment the done-criteria are met, call finish_subgoal(summary, data).
- If you cannot proceed, call finish_subgoal with a summary explaining why.
"""


def _page_summary(page_context: PageContext | None) -> str:
    if page_context is None:
        return "No page loaded yet."
    lines = [f"URL: {page_context.url}", f"Title: {page_context.title}"]
    elements = getattr(page_context, "elements", []) or []
    for el in elements[:40]:
        eid = getattr(el, "element_id", "?")
        tag = getattr(el, "tag_name", "")
        text = (getattr(el, "text", "") or "")[:60]
        lines.append(f"[{eid}] {tag} {text}".rstrip())
    return "\n".join(lines)


async def worker_decide(state: WorkerState) -> dict:
    """One role-scoped LLM call → the next Action, or a terminal digest."""
    llm = get_worker_llm(
        role=state["role"],
        model_name=state["model_name"],
        api_keys=state.get("api_keys"),
    )
    system = _WORKER_SYSTEM.format(
        subgoal=state["subgoal"], done_criteria=state["done_criteria"],
    )
    history_note = ""
    if state.get("action_history"):
        history_note = f"\n\nActions so far: {len(state['action_history'])}."
    human = f"{_page_summary(state.get('page_context'))}{history_note}"

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=human),
    ])

    if not getattr(response, "tool_calls", None):
        # No tool call — treat as inability to proceed; end gracefully.
        return {
            "finished": True,
            "result_digest": ResultDigest(
                status="failed",
                summary="worker produced no tool call",
                actions_used=state.get("actions_used", 0),
            ),
        }

    call = response.tool_calls[0]
    name, args = call["name"], call.get("args", {})

    if name == "finish_subgoal":
        return {
            "finished": True,
            "result_digest": _digest_from_finish(args, state.get("actions_used", 0)),
        }

    action = _build_worker_action(name, args)
    if action is not None and is_destructive(action.action_type):
        return {
            "finished": True,
            "result_digest": ResultDigest(
                status="needs_user",
                summary=f"worker wants to run a destructive action: {name}",
                needs_user=True,
                question=f"Approve action '{name}' for subgoal: {state['subgoal']}?",
                actions_used=state.get("actions_used", 0),
            ),
        }

    return {"current_action": action, "messages": [response]}


def _parse_execution_result(action, execution_result: dict) -> tuple[ActionResult, PageContext | None]:
    """Parse the interrupt resume dict into (ActionResult, new PageContext | None).

    Mirrors execute_action_node (graph.py) so the browser side is unchanged.
    """
    result = ActionResult(
        action_id=action.action_id,
        status=ActionStatus(execution_result.get("status", "failed")),
        message=execution_result.get("message", ""),
        error=execution_result.get("error"),
        extracted_data=execution_result.get("extracted_data"),
        page_changed=execution_result.get("page_changed", False),
        new_url=execution_result.get("new_url"),
        execution_time_ms=execution_result.get("execution_time_ms", 0),
    )
    page = None
    new_dom = execution_result.get("new_dom")
    if new_dom:
        try:
            page = PageContext.model_validate(new_dom)
        except Exception:  # noqa: BLE001 — malformed DOM shouldn't crash the worker
            page = None
    return result, page
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_nodes.py -v`
Expected: PASS (all — 9 helper + 3 decide + 2 parse). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/agent/worker_nodes.py backend/tests/test_worker_nodes.py
git commit -m "feat(worker): add worker_decide node + execution-result parser"
```

---

### Task 6: `worker_execute` + `worker_finalize_budget` nodes

**Files:**
- Modify: `backend/src/agent_core/agent/worker_nodes.py`
- Test: `backend/tests/test_worker_nodes.py` (append)

**Interfaces:**
- Consumes: `_parse_execution_result` (Task 5); `interrupt` from `langgraph.types`; `WORKER_ACTION_CAP` from `agent_core.agent.budgets`.
- Produces:
  - `async def worker_execute(state: WorkerState) -> dict` — sends `interrupt(execution_request)`, parses the resume, records history, increments `actions_used`. Returns `{"pending_result", "action_history", "actions_used", "previous_page_context", "page_context"(if new)}`.
  - `def budget_exhausted(state: WorkerState) -> bool` — `state["actions_used"] >= WORKER_ACTION_CAP`.
  - `def _digest_budget_exhausted(state) -> ResultDigest`.

Because `worker_execute` calls `interrupt()`, it is exercised end-to-end in Task 7 (graph-level, with `Command(resume=...)`). Here we unit-test only the pure pieces (`budget_exhausted`, `_digest_budget_exhausted`).

- [ ] **Step 1: Write the failing test** (append)

```python
class TestBudget:
    def test_budget_exhausted_true_at_cap(self):
        from agent_core.agent.worker_nodes import budget_exhausted
        from agent_core.agent.budgets import WORKER_ACTION_CAP
        state = {"actions_used": WORKER_ACTION_CAP}
        assert budget_exhausted(state) is True

    def test_budget_not_exhausted_below_cap(self):
        from agent_core.agent.worker_nodes import budget_exhausted
        assert budget_exhausted({"actions_used": 0}) is False

    def test_budget_digest_is_failed(self):
        from agent_core.agent.worker_nodes import _digest_budget_exhausted
        d = _digest_budget_exhausted({"actions_used": 8})
        assert d.status == "failed"
        assert d.actions_used == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_nodes.py::TestBudget -v`
Expected: FAIL — `ImportError: cannot import name 'budget_exhausted'`

- [ ] **Step 3: Add the nodes/helpers** (append to `worker_nodes.py`)

Add import at top (with the others):

```python
from langgraph.types import interrupt
from agent_core.agent.budgets import WORKER_ACTION_CAP
```

Append:

```python
async def worker_execute(state: WorkerState) -> dict:
    """Send the current action to the browser via interrupt(), record the result.

    Uses the SAME execution_request/resume contract as execute_action_node so the
    ws_handler/orchestrator browser side needs no change.
    """
    action = state["current_action"]
    execution_request = {
        "action_id": action.action_id,
        "action_type": action.action_type.value,
        "element_id": action.element_id,
        "element_fingerprint": action.element_fingerprint,
        "value": action.value,
        "description": action.description,
    }
    execution_result = interrupt(execution_request)

    result, new_page = _parse_execution_result(action, execution_result)

    history = list(state.get("action_history", []))
    history.append({
        "action": {"action_type": action.action_type.value, "description": action.description},
        "result": {"status": result.status.value, "extracted_data": result.extracted_data},
    })

    out: dict = {
        "pending_result": result,
        "action_history": history,
        "actions_used": state.get("actions_used", 0) + 1,
        "previous_page_context": state.get("page_context"),
        "current_action": None,
    }
    if new_page is not None:
        out["page_context"] = new_page
    return out


def budget_exhausted(state: WorkerState) -> bool:
    """True when the worker has spent its per-subgoal action budget."""
    return state.get("actions_used", 0) >= WORKER_ACTION_CAP


def _digest_budget_exhausted(state: WorkerState) -> ResultDigest:
    return ResultDigest(
        status="failed",
        summary=f"subgoal not completed within {WORKER_ACTION_CAP} actions",
        actions_used=state.get("actions_used", 0),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_nodes.py -v`
Expected: PASS (all). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/agent_core/agent/worker_nodes.py backend/tests/test_worker_nodes.py
git commit -m "feat(worker): add worker_execute node + budget guard"
```

---

### Task 7: `build_worker_graph` — assemble + end-to-end tests

**Files:**
- Create: `backend/src/agent_core/agent/worker_graph.py`
- Test: `backend/tests/test_worker_graph.py`

**Interfaces:**
- Consumes: all Task 4–6 nodes; `WorkerState` (Task 3); `MemorySaver`, `StateGraph`, `START`, `END` from langgraph; `Command` from `langgraph.types`.
- Produces: `build_worker_graph(checkpointer=None) -> CompiledStateGraph`.

**Graph shape:**
```
START → worker_decide
worker_decide → (finished? → END) | (else → worker_execute)
worker_execute → (budget_exhausted? → finalize_budget → END) | (else → worker_decide)
finalize_budget sets result_digest, then END
```

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_worker_graph.py
"""End-to-end worker subgraph tests (browser stubbed via Command(resume=...))."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent_core.schemas.orchestrator import WorkerRole
from agent_core.schemas.orchestrator_state import create_worker_state
from agent_core.agent.worker_graph import build_worker_graph


def _tc(name, args):
    r = MagicMock()
    r.tool_calls = [{"name": name, "args": args, "id": "t"}]
    r.content = ""
    return r


CFG = {"configurable": {"thread_id": "w1"}}


class TestWorkerGraph:
    def test_compiles(self):
        assert build_worker_graph() is not None

    @pytest.mark.asyncio
    async def test_finishes_with_done_digest(self):
        # decide immediately calls finish_subgoal → no interrupt, done digest.
        graph = build_worker_graph(MemorySaver())
        state = create_worker_state(
            role=WorkerRole.EXTRACTOR, subgoal="read price",
            done_criteria="captured", model_name="gpt-4o-mini",
        )
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = _tc("finish_subgoal", {"summary": "done", "data": {"p": 1}})
            gw.return_value = llm
            out = await graph.ainvoke(state, CFG)
        assert out["result_digest"].status == "done"
        assert out["finished"] is True

    @pytest.mark.asyncio
    async def test_read_then_finish_two_turns(self):
        # turn 1: read (interrupt) → resume with extracted text → turn 2: finish.
        graph = build_worker_graph(MemorySaver())
        state = create_worker_state(
            role=WorkerRole.EXTRACTOR, subgoal="read price",
            done_criteria="captured", model_name="gpt-4o-mini",
        )
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.side_effect = [
                _tc("read", {"what": "price"}),
                _tc("finish_subgoal", {"summary": "price $38", "data": {"price": "$38"}}),
            ]
            gw.return_value = llm
            # first invoke pauses at the interrupt in worker_execute
            interim = await graph.ainvoke(state, CFG)
            assert "__interrupt__" in interim
            # resume with the browser's result
            out = await graph.ainvoke(
                Command(resume={"status": "success", "message": "ok",
                                "extracted_data": "$38", "page_changed": False}),
                CFG,
            )
        assert out["result_digest"].status == "done"
        assert out["result_digest"].data == {"price": "$38"}

    @pytest.mark.asyncio
    async def test_budget_exhaustion_fails(self):
        # LLM always clicks, never finishes → loop hits WORKER_ACTION_CAP.
        graph = build_worker_graph(MemorySaver())
        state = create_worker_state(
            role=WorkerRole.NAVIGATOR, subgoal="never done",
            done_criteria="impossible", model_name="gpt-4o-mini",
        )
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = _tc("click", {"element_id": 1})
            gw.return_value = llm
            resume = {"status": "success", "message": "ok", "page_changed": True}
            result = await graph.ainvoke(state, CFG)
            # drive the loop: resume until the graph completes (no more interrupt)
            for _ in range(20):
                if "__interrupt__" not in result:
                    break
                result = await graph.ainvoke(Command(resume=resume), CFG)
        assert result["result_digest"].status == "failed"
        assert result["result_digest"].actions_used == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.agent.worker_graph'`

- [ ] **Step 3: Write the graph builder**

```python
# backend/src/agent_core/agent/worker_graph.py
"""Worker subgraph: a bounded, role-scoped ReAct loop over WorkerState.

Returns a compact ResultDigest. Mounted as a single node inside the lead graph
in P3; here it runs standalone (browser stubbed via Command(resume=...)).
"""

from typing import Literal

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent_core.schemas.orchestrator_state import WorkerState
from agent_core.agent.worker_nodes import (
    worker_decide,
    worker_execute,
    budget_exhausted,
    _digest_budget_exhausted,
)

logger = structlog.get_logger("agent.worker_graph")


async def finalize_budget(state: WorkerState) -> dict:
    """Terminal node when the action budget is exhausted."""
    return {"finished": True, "result_digest": _digest_budget_exhausted(state)}


def _route_after_decide(state: WorkerState) -> Literal["worker_execute", "__end__"]:
    return END if state.get("finished") else "worker_execute"


def _route_after_execute(state: WorkerState) -> Literal["worker_decide", "finalize_budget"]:
    return "finalize_budget" if budget_exhausted(state) else "worker_decide"


def build_worker_graph(checkpointer=None):
    """Compile the worker subgraph. Needs a checkpointer for interrupt/resume."""
    if checkpointer is None:
        checkpointer = MemorySaver()

    builder = StateGraph(WorkerState)
    builder.add_node("worker_decide", worker_decide)
    builder.add_node("worker_execute", worker_execute)
    builder.add_node("finalize_budget", finalize_budget)

    builder.add_edge(START, "worker_decide")
    builder.add_conditional_edges("worker_decide", _route_after_decide,
                                  {"worker_execute": "worker_execute", END: END})
    builder.add_conditional_edges("worker_execute", _route_after_execute,
                                  {"worker_decide": "worker_decide", "finalize_budget": "finalize_budget"})
    builder.add_edge("finalize_budget", END)

    graph = builder.compile(checkpointer=checkpointer)
    logger.info("worker_graph_created", node_count=len(builder.nodes))
    return graph
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/Scripts/python.exe -m pytest tests/test_worker_graph.py -v`
Expected: PASS (4 tests).

If `test_read_then_finish_two_turns` sees the interrupt under a different key than `__interrupt__` (LangGraph version drift), inspect the interim state keys and adjust the assertion — the behavioral guarantee is "pauses for the browser, resumes on Command(resume=...)".

- [ ] **Step 5: Run the whole P2 suite + full backend suite, then commit**

Run:
```
cd backend && venv/Scripts/python.exe -m pytest tests/test_consolidated_tools.py tests/test_worker_llm.py tests/test_orchestrator_state.py tests/test_worker_nodes.py tests/test_worker_graph.py -v
venv/Scripts/python.exe -m pytest tests/ -q
venv/Scripts/python.exe -m ruff check src/agent_core/tools/consolidated_tools.py src/agent_core/agent/worker_nodes.py src/agent_core/agent/worker_graph.py src/agent_core/agent/roles.py src/agent_core/agent/llm_client.py src/agent_core/schemas/orchestrator_state.py
```
Expected: P2 suite green; no NEW failures in the full suite (the 16 pre-existing `test_graph.py`/`test_schemas.py` failures are unrelated); ruff clean on the listed files.

```bash
git add backend/src/agent_core/agent/worker_graph.py backend/tests/test_worker_graph.py
git commit -m "feat(worker): assemble bounded worker subgraph + end-to-end tests"
```

**P2 exit gate:** worker subgraph green in isolation; `create_agent_graph` untouched and still green; ws_handler/orchestrator unchanged; Auth role's live credential injection explicitly deferred to P3 (logged in this plan, `type_credential` marker defined but not executor-wired).

---

## Self-Review (against the P2 roadmap in the parent plan + P1 review)

**Roadmap coverage:**
- P2.1 (read/see) → Task 1. Also `extract_table` (role menu name) reconciled to the `__EXTRACT_LISTINGS__` marker. ✓
- P2.2 (executor dispatch) → **eliminated**: read/see/extract_table reuse existing markers; no `action_executor.py` change needed. Documented in Architecture + Task 1. ✓
- P2.3 (`get_worker_llm`) → Task 2. Deletes reliance on `select_tools_for_context` for workers (that deletion happens in P4). ✓
- P2.4 (auth tools `type_credential`/`submit`/`finish_subgoal`) → Task 1 schemas; `submit`/`finish_subgoal` fully wired; `type_credential` marker defined, live injection deferred to P3 (explicit). ✓
- P2.5 (worker subgraph) → Tasks 3–7. ✓
- P1-review follow-up "WorkerState needs a messages channel" → Task 3 adds `messages: Annotated[list, add_messages]` + `current_action`/`pending_result`/`previous_page_context`/`finished`. ✓

**Key deviation from roadmap (flagged for the human):** the roadmap said "reuse existing nodes almost as-is." The P1-code research proved `decide_action`/`observe`/`smart_evaluate` are bound to full `AgentState` (`goal:Goal`, `plan:Plan`, `task_memory`, `cognitive_status`, credential/queue scratch). Reusing them would drag ~20 AgentState keys into `WorkerState`, destroying the context-isolation the whole design exists for. This plan writes lean worker nodes instead, reusing only the `interrupt()` execution contract. This is the anti-drift call the parent plan's Scope Note anticipated ("expand against real code").

**Placeholder scan:** every code step contains complete code; every command is exact. No TBD/TODO.

**Type consistency:** `WorkerRole`/`ResultDigest`/`WorkerState`/`Action`/`ActionType`/`ActionResult` used identically across tasks. `_build_worker_action`→`worker_decide`→`worker_graph` signatures match. `get_worker_llm(role, model_name, api_keys)` patched at the same path (`agent_core.agent.worker_nodes.get_worker_llm`) in every test that mocks it. `budget_exhausted`/`_digest_budget_exhausted`/`finalize_budget` names consistent between `worker_nodes.py` and `worker_graph.py`.

**Known follow-ups (not gaps):** P3 wires `type_credential` live injection (needs `LeadState.stored_credentials`), mounts this subgraph as a lead node, and threads nested LangSmith run names. `read`'s table-vs-plain auto-detect is intentionally NOT built (extractor has a separate `extract_table` tool) — YAGNI until a real case needs it.
```
