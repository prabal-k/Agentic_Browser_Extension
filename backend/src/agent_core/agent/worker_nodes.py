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
    element_id = (
        int(raw_eid)
        if isinstance(raw_eid, (int, str)) and str(raw_eid).isdigit()
        else None
    )

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
