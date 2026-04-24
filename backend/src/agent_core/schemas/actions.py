"""Action schemas — What the agent can DO on a web page.

Actions are the agent's "hands". After reasoning about the goal and
analyzing the page context, the agent produces an Action that gets
sent to the browser (via extension or Playwright) for execution.

Design decisions:
- Actions are declarative: the agent says WHAT to do, not HOW
- Each action targets an element by element_id (from the DOM snapshot)
- ActionResult captures what happened, including the new page state
- The agent never directly manipulates the DOM — it sends instructions
"""

from enum import Enum
from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """All possible browser actions the agent can perform."""

    # Element interactions
    CLICK = "click"
    TYPE_TEXT = "type_text"
    CLEAR_AND_TYPE = "clear_and_type"
    SELECT_OPTION = "select_option"
    CHECK = "check"
    UNCHECK = "uncheck"
    HOVER = "hover"

    # Navigation
    NAVIGATE = "navigate"
    GO_BACK = "go_back"
    GO_FORWARD = "go_forward"
    REFRESH = "refresh"

    # Page interaction
    SCROLL_DOWN = "scroll_down"
    SCROLL_UP = "scroll_up"
    SCROLL_TO_ELEMENT = "scroll_to_element"

    # Keyboard
    PRESS_KEY = "press_key"
    KEY_COMBO = "key_combo"

    # Tab management
    NEW_TAB = "new_tab"
    CLOSE_TAB = "close_tab"
    SWITCH_TAB = "switch_tab"

    # Information gathering (no side effects)
    EXTRACT_TEXT = "extract_text"
    EXTRACT_TABLE = "extract_table"
    TAKE_SCREENSHOT = "take_screenshot"
    GET_CONSOLE_LOGS = "get_console_logs"
    GET_NETWORK_LOG = "get_network_log"

    # JavaScript execution
    EVALUATE_JS = "evaluate_js"

    # Dialog handling
    HANDLE_DIALOG = "handle_dialog"

    # File & drag
    UPLOAD_FILE = "upload_file"
    DRAG = "drag"

    # Special
    WAIT = "wait"
    WAIT_FOR_SELECTOR = "wait_for_selector"
    WAIT_FOR_NAVIGATION = "wait_for_navigation"
    DONE = "done"


class Action(BaseModel):
    """A single action the agent wants to perform on the page.

    The agent produces this after reasoning. It gets sent to the browser
    for execution. The browser returns an ActionResult.

    Examples:
        Action(type=CLICK, element_id=14, description="Click 'Add to Cart' button")
        Action(type=TYPE_TEXT, element_id=15, value="pizza", description="Type 'pizza' in search box")
        Action(type=NAVIGATE, url="https://dominos.com", description="Open Dominos website")
    """

    action_id: str = Field(
        default="",
        description="Unique ID for this action instance. Set by the orchestrator."
    )
    action_type: ActionType = Field(
        description="What type of action to perform"
    )
    element_id: int | None = Field(
        default=None,
        description="Target element ID from the DOM snapshot. Required for element interactions."
    )
    element_fingerprint: str | None = Field(
        default=None,
        description=(
            "Optional stable identity hash of the target element. When "
            "supplied, the extension falls back to this if element_id is "
            "stale. Passed through from DOMElement.fingerprint."
        ),
    )
    value: str | None = Field(
        default=None,
        description=(
            "Value for the action. Used by: "
            "type_text (text to type), select_option (option value), "
            "navigate (URL), press_key (key name), key_combo (e.g. 'Ctrl+A'), "
            "wait (seconds as string), switch_tab (tab index as string)"
        ),
    )
    description: str = Field(
        default="",
        description=(
            "Human-readable description of what this action does and WHY. "
            "Shown to the user for confirmation. "
            "E.g., 'Click the search button to submit the pizza search query'"
        ),
    )
    reasoning: str = Field(
        default="",
        description=(
            "Agent's reasoning for choosing this specific action. "
            "E.g., 'Element [14] is the search submit button based on its label and position next to the search input.'"
        ),
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Agent's confidence that this action will succeed and move toward the goal. "
            "0.0 = no confidence, 1.0 = certain. "
            "Below 0.6 should trigger user confirmation even in auto-mode."
        ),
    )
    requires_confirmation: bool = Field(
        default=True,
        description="Whether this action needs user approval before execution"
    )
    is_reversible: bool = Field(
        default=True,
        description=(
            "Whether this action can be undone. "
            "Clicks are generally reversible, form submissions and payments are not."
        ),
    )
    risk_level: str = Field(
        default="low",
        description="Risk assessment: 'low' (navigation), 'medium' (form fill), 'high' (submit, payment)"
    )


class ActionStatus(str, Enum):
    """Result status of an executed action."""

    SUCCESS = "success"
    FAILED = "failed"
    ELEMENT_NOT_FOUND = "element_not_found"
    ELEMENT_NOT_VISIBLE = "element_not_visible"
    ELEMENT_DISABLED = "element_disabled"
    TIMEOUT = "timeout"
    NAVIGATION_ERROR = "navigation_error"
    BLOCKED = "blocked"
    CANCELLED_BY_USER = "cancelled_by_user"


class ActionResult(BaseModel):
    """Result of executing an action on the page.

    Returned by the browser after executing an Action.
    Contains the outcome and optionally the new page state,
    which the agent uses to reason about its next step.
    """

    action_id: str = Field(
        description="ID of the action that was executed"
    )
    status: ActionStatus = Field(
        description="Whether the action succeeded or failed"
    )
    message: str = Field(
        default="",
        description="Human-readable description of what happened"
    )
    error: str | None = Field(
        default=None,
        description="Error message if the action failed"
    )
    extracted_data: str | None = Field(
        default=None,
        description="Data extracted by extract_text or extract_table actions"
    )
    page_changed: bool = Field(
        default=False,
        description="Whether the page URL or content changed significantly after the action"
    )
    new_url: str | None = Field(
        default=None,
        description="New page URL if navigation occurred"
    )
    execution_time_ms: float = Field(
        default=0.0,
        description="How long the action took to execute in milliseconds"
    )
