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
    WorkerRole.FORM_FILLER: [
        "fill_form",
        "select_option",
        "check",
        "upload_file",
        "press_key",
    ],
    WorkerRole.VERIFIER: ["read", "see", "finish_subgoal"],
    WorkerRole.AUTH: [
        "navigate",
        "fill_form",
        "click",
        "type_credential",
        "submit",
        "see",
    ],
}

# The coordinator has ZERO browser tools — four coordination tools only.
LEAD_TOOL_NAMES: list[str] = ["delegate", "update_plan", "ask_user", "finish"]


def tools_for_role(role: WorkerRole) -> list[str]:
    """Return the tool-name menu for a role."""
    return ROLE_TOOL_NAMES[role]
