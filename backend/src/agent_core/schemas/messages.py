"""WebSocket message schemas — Communication protocol between backend and frontend.

These define every message that flows over the WebSocket connection between
the agent backend and the frontend (test dashboard or browser extension).

Design decisions:
- All messages are JSON with a "type" discriminator field
- Server → Client messages include streaming support for real-time reasoning display
- Interrupt messages include InputFieldDefinition — the server tells the client
  WHAT kind of input to render (text, select, confirm, etc.), not just "ask a question"
- This enables the frontend to render dynamic, type-appropriate input fields
  (like Lovable AI does), not a generic text box
"""

from enum import Enum
from pydantic import BaseModel, Field

from agent_core.schemas.actions import Action, ActionResult
from agent_core.schemas.agent import CognitiveStatus


# ============================================================
# Input field definitions for interrupt UI
# ============================================================

class InputFieldType(str, Enum):
    """Types of input fields the server can request from the client.

    The frontend renders the appropriate ShadCN component based on this type.
    """

    TEXT = "text"                    # ShadCN Input
    TEXTAREA = "textarea"           # ShadCN Textarea
    NUMBER = "number"               # ShadCN Input type=number
    SELECT = "select"               # ShadCN Select
    MULTI_SELECT = "multi_select"   # ShadCN multi-select with checkboxes
    CONFIRM = "confirm"             # ShadCN AlertDialog with Yes/No buttons
    DATE = "date"                   # ShadCN DatePicker
    PASSWORD = "password"           # ShadCN Input type=password
    URL = "url"                     # ShadCN Input type=url
    EMAIL = "email"                 # ShadCN Input type=email
    RADIO = "radio"                 # ShadCN RadioGroup
    TOGGLE = "toggle"               # ShadCN Switch


class InputFieldDefinition(BaseModel):
    """Defines a single input field for human-in-the-loop interaction.

    The server sends this to the client, which renders the appropriate
    UI component. This is NOT a text chat box — it's a structured input
    tailored to the data type the agent needs.

    Examples:
        Confirm an action:
            InputFieldDefinition(
                field_type=CONFIRM,
                label="Proceed with order?",
                description="Total: $15.99. Delivery to 123 Main St.",
                options=["Yes, place order", "No, cancel"]
            )

        Choose an option:
            InputFieldDefinition(
                field_type=SELECT,
                label="Which pizza size?",
                options=["Small", "Medium", "Large", "Extra Large"],
                default_value="Medium"
            )

        Enter text:
            InputFieldDefinition(
                field_type=TEXT,
                label="Delivery address",
                placeholder="Enter your delivery address",
                required=True
            )
    """

    field_id: str = Field(
        default="",
        description="Unique ID for this field. Set by the orchestrator."
    )
    field_type: InputFieldType = Field(
        description="What type of input to render"
    )
    label: str = Field(
        description="Label/question displayed above the input"
    )
    description: str = Field(
        default="",
        description="Additional context or help text below the label"
    )
    placeholder: str = Field(
        default="",
        description="Placeholder text for text inputs"
    )
    options: list[str] = Field(
        default_factory=list,
        description="Available options for select, multi_select, radio, confirm types"
    )
    default_value: str = Field(
        default="",
        description="Pre-filled default value"
    )
    required: bool = Field(
        default=True,
        description="Whether the user must provide a value"
    )
    validation_pattern: str = Field(
        default="",
        description="Regex pattern for validation (for text/number inputs)"
    )
    min_value: float | None = Field(
        default=None,
        description="Minimum value for number inputs"
    )
    max_value: float | None = Field(
        default=None,
        description="Maximum value for number inputs"
    )


# ============================================================
# WebSocket message types
# ============================================================

class WSMessageType(str, Enum):
    """All possible WebSocket message types.

    Prefixed with direction:
    - CLIENT_* : Sent from frontend to backend
    - SERVER_* : Sent from backend to frontend
    """

    # Client → Server
    CLIENT_GOAL = "client_goal"
    CLIENT_USER_RESPONSE = "client_user_response"
    CLIENT_ACTION_RESULT = "client_action_result"
    CLIENT_DOM_UPDATE = "client_dom_update"
    CLIENT_CANCEL = "client_cancel"

    # Server → Client
    SERVER_REASONING = "server_reasoning"
    SERVER_PLAN = "server_plan"
    SERVER_ACTION_REQUEST = "server_action_request"
    SERVER_INTERRUPT = "server_interrupt"
    SERVER_EVALUATION = "server_evaluation"
    SERVER_STATUS = "server_status"
    SERVER_ERROR = "server_error"
    SERVER_DONE = "server_done"


# ============================================================
# Base message
# ============================================================

class WSMessage(BaseModel):
    """Base WebSocket message. All messages extend this."""

    type: WSMessageType
    session_id: str = Field(default="", description="WebSocket session identifier")
    timestamp: float = Field(default=0.0, description="Unix timestamp")


# ============================================================
# Client → Server messages
# ============================================================

class GoalMessage(WSMessage):
    """User submits a new goal for the agent to pursue."""

    type: WSMessageType = WSMessageType.CLIENT_GOAL
    goal: str = Field(description="The user's goal in natural language")
    dom_snapshot: dict | None = Field(
        default=None,
        description="Current page DOM snapshot (PageContext as dict)"
    )
    model_override: str | None = Field(
        default=None,
        description="Override the default LLM model for this task"
    )


class InterruptResponse(WSMessage):
    """User responds to an interrupt request from the agent."""

    type: WSMessageType = WSMessageType.CLIENT_USER_RESPONSE
    interrupt_id: str = Field(description="ID of the interrupt being responded to")
    values: dict[str, str] = Field(
        description=(
            "User's responses keyed by field_id. "
            "E.g., {'field_1': 'Medium', 'field_2': '123 Main St'}"
        ),
    )


class ActionResultMessage(WSMessage):
    """Frontend reports the result of executing an action on the page."""

    type: WSMessageType = WSMessageType.CLIENT_ACTION_RESULT
    action_result: dict = Field(
        description="ActionResult as dict"
    )
    new_dom_snapshot: dict | None = Field(
        default=None,
        description="Updated page DOM snapshot after the action"
    )


# ============================================================
# Server → Client messages
# ============================================================

class ReasoningMessage(WSMessage):
    """Agent streams its reasoning to the frontend.

    This is sent token-by-token for real-time display of the
    agent's Chain of Thought reasoning.
    """

    type: WSMessageType = WSMessageType.SERVER_REASONING
    content: str = Field(description="Reasoning text (may be a chunk if streaming)")
    is_streaming: bool = Field(
        default=False,
        description="True if this is a streaming chunk, False if complete"
    )
    is_final: bool = Field(
        default=False,
        description="True if this is the last chunk in a streaming sequence"
    )
    reasoning_type: str = Field(
        default="thinking",
        description=(
            "Type of reasoning: 'thinking' (CoT), 'planning', "
            "'self_critique', 'evaluation', 'observation'"
        ),
    )


class PlanMessage(WSMessage):
    """Agent shares its plan with the frontend."""

    type: WSMessageType = WSMessageType.SERVER_PLAN
    steps: list[dict] = Field(
        description="Plan steps as list of PlanStep dicts"
    )
    plan_version: int = Field(default=1)
    reasoning: str = Field(
        default="",
        description="Why the agent created/updated this plan"
    )


class ActionRequestMessage(WSMessage):
    """Agent requests the frontend to execute an action on the page."""

    type: WSMessageType = WSMessageType.SERVER_ACTION_REQUEST
    action: dict = Field(description="Action to execute as dict")
    requires_confirmation: bool = Field(
        default=True,
        description="Whether the user must confirm before execution"
    )
    step_number: int = Field(
        default=0,
        description="Which plan step this action belongs to"
    )
    total_steps: int = Field(
        default=0,
        description="Total number of steps in the plan"
    )


class InterruptMessage(WSMessage):
    """Agent pauses execution to ask the user for input.

    This is a PROPER interrupt using LangGraph's interrupt() —
    the graph is paused and will only resume when the user responds.

    The fields list defines WHAT input the frontend should render.
    This enables dynamic, type-appropriate input forms, not a text box.
    """

    type: WSMessageType = WSMessageType.SERVER_INTERRUPT
    interrupt_id: str = Field(description="Unique ID for this interrupt")
    title: str = Field(
        default="Input Required",
        description="Title for the interrupt dialog"
    )
    context: str = Field(
        default="",
        description="Explanation of why the agent is asking"
    )
    fields: list[InputFieldDefinition] = Field(
        description="Input fields the user needs to fill out"
    )
    urgency: str = Field(
        default="normal",
        description="'normal', 'warning' (action has risk), 'critical' (destructive action)"
    )


class EvaluationMessage(WSMessage):
    """Agent shares its evaluation of the last action."""

    type: WSMessageType = WSMessageType.SERVER_EVALUATION
    action_succeeded: bool = Field(default=True)
    progress_percentage: float = Field(default=0.0)
    summary: str = Field(default="")
    next_step: str = Field(default="")


class StatusMessage(WSMessage):
    """Agent status update."""

    type: WSMessageType = WSMessageType.SERVER_STATUS
    cognitive_status: CognitiveStatus
    message: str = Field(default="")
    iteration: int = Field(default=0)
    plan_progress: float = Field(
        default=0.0,
        description="Plan completion percentage (0.0–1.0)"
    )


class ErrorMessage(WSMessage):
    """Error message from the agent."""

    type: WSMessageType = WSMessageType.SERVER_ERROR
    message: str = Field(description="Error description")
    recoverable: bool = Field(
        default=True,
        description="Whether the agent can continue after this error"
    )
    suggestion: str = Field(
        default="",
        description="Suggested action for the user"
    )


class DoneMessage(WSMessage):
    """Agent has completed (or given up on) the task."""

    type: WSMessageType = WSMessageType.SERVER_DONE
    success: bool = Field(description="Whether the goal was achieved")
    summary: str = Field(description="Summary of what was accomplished")
    steps_completed: int = Field(default=0)
    steps_total: int = Field(default=0)
    total_actions: int = Field(default=0)
