# backend/src/agent_core/agent/worker_nodes.py
"""Lean worker nodes operating on WorkerState.

These are NOT the AgentState-bound decide_action/observe/smart_evaluate (the P1
review proved those are too coupled to reuse). The only thing reused from the
old graph is the interrupt() execution contract in worker_execute — identical
payload/resume shape, so the browser side (ws_handler/orchestrator) is unchanged.
"""

import uuid

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from agent_core.agent.budgets import WORKER_ACTION_CAP, is_destructive
from agent_core.agent.llm_client import get_worker_llm
from agent_core.schemas.actions import Action, ActionResult, ActionStatus, ActionType
from agent_core.schemas.dom import PageContext
from agent_core.schemas.orchestrator import ResultDigest
from agent_core.schemas.orchestrator_state import WorkerState

logger = structlog.get_logger("agent.worker")

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
    element_id = None
    if isinstance(raw_eid, bool):
        element_id = None
    elif isinstance(raw_eid, (int, float)):
        element_id = int(raw_eid)
    elif isinstance(raw_eid, str) and raw_eid.strip().isdigit():
        element_id = int(raw_eid)

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
    data = args.get("data")
    if data is not None and not isinstance(data, dict):
        data = {"value": data}
    return ResultDigest(
        status="done",
        summary=args.get("summary", ""),
        data=data,
        actions_used=actions_used,
    )


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


def _format_observations(action_history: list, limit: int = 5) -> str:
    """Render recent action results (incl. extracted_data) so the LLM can see
    what its tools returned — read/see/extract results arrive as extracted_data,
    not as a new page, so without this the worker is blind to what it read."""
    if not action_history:
        return "No actions taken yet."
    lines = []
    for entry in action_history[-limit:]:
        act = entry.get("action", {})
        res = entry.get("result", {})
        desc = act.get("description") or act.get("action_type") or "action"
        status = res.get("status", "?")
        line = f"- {desc} -> {status}"
        data = res.get("extracted_data")
        if data:
            line += f"; result: {str(data)[:800]}"
        lines.append(line)
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
    human = (
        f"{_page_summary(state.get('page_context'))}\n\n"
        f"Observations from your actions so far:\n"
        f"{_format_observations(state.get('action_history', []))}"
    )

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

    return {"current_action": action}


def _resolve_fingerprint(action, page_context) -> str | None:
    """Resolve an element's stable fingerprint for stale-id recovery.

    Mirrors execute_action_node (graph.py): if the action carries no fingerprint,
    look it up from page_context by element_id so the browser can recover when a
    numeric element_id goes stale between decide and execute.
    """
    element_fingerprint = action.element_fingerprint
    if element_fingerprint is None and action.element_id is not None:
        if page_context and hasattr(page_context, "elements"):
            for el in page_context.elements:
                if getattr(el, "element_id", None) == action.element_id:
                    fp = getattr(el, "fingerprint", "")
                    if fp:
                        element_fingerprint = fp
                    break
    return element_fingerprint


def _parse_execution_result(
    action, execution_result: dict
) -> tuple[ActionResult, PageContext | None]:
    """Parse the interrupt resume dict into (ActionResult, new PageContext | None).

    Mirrors execute_action_node (graph.py) so the browser side is unchanged.
    """
    if not isinstance(execution_result, dict):
        return (
            ActionResult(
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                message="invalid execution result (not a dict)",
            ),
            None,
        )
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
        "element_fingerprint": _resolve_fingerprint(action, state.get("page_context")),
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
