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

from langchain_core.tools import BaseTool

from agent_core.schemas.orchestrator import WorkerRole
from agent_core.tools.consolidated_tools import WORKER_TOOL_OBJECTS

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
    WorkerRole.VERIFIER: ["read", "see"],
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


def resolve_role_tools(role: WorkerRole) -> list[BaseTool]:
    """Resolve a role's tool NAMES to their @tool objects.

    Raises KeyError (with the offending name) if a role references a tool name
    that has no registered object — this is the drift guard: a typo in
    ROLE_TOOL_NAMES fails loudly instead of silently binding nothing.
    """
    # finish_subgoal is UNIVERSAL: every worker must be able to end its subgoal.
    # Without it a role can only loop until the anti-loop guard or action budget
    # forces a FAILED digest — it can never report success (this silently failed
    # every non-verifier subgoal and threw away the answer the worker had found).
    # Append it here (deduped) so completion is always reachable for all roles.
    names = list(ROLE_TOOL_NAMES[role])
    if "finish_subgoal" not in names:
        names.append("finish_subgoal")

    resolved: list[BaseTool] = []
    for name in names:
        try:
            resolved.append(WORKER_TOOL_OBJECTS[name])
        except KeyError as exc:
            raise KeyError(
                f"role {role.value!r} references tool {name!r} with no object "
                f"in WORKER_TOOL_OBJECTS"
            ) from exc
    return resolved
