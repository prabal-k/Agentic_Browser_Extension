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
    check,
    click,
    fill_form,
    go_back,
    navigate,
    press_key,
    scroll_down,
    select_option,
    upload_file,
    wait,
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
