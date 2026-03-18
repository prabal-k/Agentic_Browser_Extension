"""Schema definitions for the agentic browser extension.

All Pydantic models and TypedDicts that define the data flowing
through the cognitive agent pipeline.
"""

from agent_core.schemas.dom import DOMElement, PageContext, ElementType
from agent_core.schemas.actions import (
    Action,
    ActionType,
    ActionResult,
    ActionStatus,
)
from agent_core.schemas.agent import (
    AgentState,
    Goal,
    Plan,
    PlanStep,
    StepStatus,
    ReasoningTrace,
    SelfCritique,
    Evaluation,
    RetryContext,
    CognitiveStatus,
    TaskMemory,
)
from agent_core.schemas.messages import (
    WSMessage,
    WSMessageType,
    GoalMessage,
    ReasoningMessage,
    ActionRequestMessage,
    ActionResultMessage,
    InterruptMessage,
    InterruptResponse,
    StatusMessage,
    ErrorMessage,
    InputFieldType,
    InputFieldDefinition,
)

__all__ = [
    # DOM
    "DOMElement",
    "PageContext",
    "ElementType",
    # Actions
    "Action",
    "ActionType",
    "ActionResult",
    "ActionStatus",
    # Agent State
    "AgentState",
    "Goal",
    "Plan",
    "PlanStep",
    "StepStatus",
    "ReasoningTrace",
    "SelfCritique",
    "Evaluation",
    "RetryContext",
    "CognitiveStatus",
    "TaskMemory",
    # Messages
    "WSMessage",
    "WSMessageType",
    "GoalMessage",
    "ReasoningMessage",
    "ActionRequestMessage",
    "ActionResultMessage",
    "InterruptMessage",
    "InterruptResponse",
    "StatusMessage",
    "ErrorMessage",
    "InputFieldType",
    "InputFieldDefinition",
]
