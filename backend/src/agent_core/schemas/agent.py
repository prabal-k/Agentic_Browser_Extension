"""Agent state schemas — The cognitive architecture of the agent.

This is NOT a simple tool-calling state. This defines a goal-based cognitive agent
with planning, reasoning, self-critique, evaluation, and adaptive retry.

The agent follows a cognitive loop:
    Goal Analysis → Plan → Self-Critique → [Reason → Decide → Act → Observe → Evaluate → Self-Critique] → Done

Key design principles:
1. The agent always has a GOAL it's working toward
2. The agent creates and maintains a PLAN (ordered steps)
3. Every action is preceded by REASONING (Chain of Thought)
4. After every action, the agent EVALUATES the outcome
5. The agent SELF-CRITIQUES its plan and actions
6. On failure, the agent RETRIES with a different strategy (not the same one)
7. The agent tracks CONFIDENCE and asks for human help when unsure
8. The agent maintains MEMORY of observations within the task
"""

from enum import Enum
from typing import Annotated
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

from agent_core.schemas.dom import PageContext
from agent_core.schemas.actions import Action, ActionResult


class CognitiveStatus(str, Enum):
    """What the agent is currently doing in its cognitive loop.

    This drives the graph's routing logic and tells the UI
    what state to display.
    """

    # Initial states
    ANALYZING_GOAL = "analyzing_goal"
    CREATING_PLAN = "creating_plan"

    # Execution loop
    REASONING = "reasoning"
    DECIDING = "deciding"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING = "executing"
    OBSERVING = "observing"
    EVALUATING = "evaluating"
    SELF_CRITIQUING = "self_critiquing"

    # Adaptive states
    RE_PLANNING = "re_planning"
    RETRYING = "retrying"
    ASKING_USER = "asking_user"
    GATHERING_INFO = "gathering_info"

    # Terminal states
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Status of a single step in the agent's plan."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class PlanStep(BaseModel):
    """A single step in the agent's plan.

    The agent creates these during planning. Each step represents
    a logical sub-task toward achieving the goal.
    """

    step_id: int = Field(description="Sequential step number")
    description: str = Field(
        description="What this step aims to accomplish. E.g., 'Navigate to the Dominos website'"
    )
    expected_outcome: str = Field(
        default="",
        description="What the page should look like after this step succeeds"
    )
    status: StepStatus = Field(default=StepStatus.PENDING)
    depends_on: list[int] = Field(
        default_factory=list,
        description="Step IDs that must complete before this step"
    )
    can_parallelize: bool = Field(
        default=False,
        description="Whether this step can run in parallel with siblings"
    )
    attempts: int = Field(
        default=0,
        description="Number of times this step has been attempted"
    )
    max_attempts: int = Field(
        default=3,
        description="Maximum retry attempts before marking as failed"
    )
    failure_reason: str = Field(
        default="",
        description="Why this step failed, if it did"
    )


class Plan(BaseModel):
    """The agent's plan for achieving the user's goal.

    Created during the planning phase, updated during execution.
    The agent can re-plan if the current plan is failing.
    """

    steps: list[PlanStep] = Field(
        default_factory=list,
        description="Ordered list of steps to achieve the goal"
    )
    current_step_index: int = Field(
        default=0,
        description="Index of the step currently being executed"
    )
    plan_version: int = Field(
        default=1,
        description="Incremented each time the agent re-plans"
    )
    original_reasoning: str = Field(
        default="",
        description="The agent's reasoning when it first created this plan"
    )
    re_plan_reason: str = Field(
        default="",
        description="Why the agent decided to re-plan (if plan_version > 1)"
    )

    @property
    def current_step(self) -> PlanStep | None:
        """Get the current step being executed."""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    @property
    def progress(self) -> float:
        """Calculate plan completion as a percentage."""
        if not self.steps:
            return 0.0
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        return completed / len(self.steps)

    @property
    def completed_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == StepStatus.COMPLETED]

    @property
    def pending_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == StepStatus.PENDING]

    @property
    def failed_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == StepStatus.FAILED]


class Goal(BaseModel):
    """The user's goal that the agent is working toward.

    The agent decomposes this into sub-goals and a plan.
    Goal tracking allows the agent to evaluate whether its
    actions are moving toward the objective.
    """

    original_text: str = Field(
        description="The user's original request, verbatim"
    )
    interpreted_goal: str = Field(
        default="",
        description=(
            "Agent's interpretation of the goal in clear, specific terms. "
            "E.g., user says 'get pizza' → interpreted as "
            "'Navigate to Dominos, select a pizza, add to cart, and begin checkout'"
        ),
    )
    sub_goals: list[str] = Field(
        default_factory=list,
        description="Goal decomposed into smaller sub-goals"
    )
    success_criteria: list[str] = Field(
        default_factory=list,
        description=(
            "Measurable criteria for determining if the goal is achieved. "
            "E.g., ['Pizza is in the cart', 'Checkout page is displayed']"
        ),
    )
    constraints: list[str] = Field(
        default_factory=list,
        description=(
            "Things the agent should NOT do while pursuing this goal. "
            "E.g., ['Do not submit payment', 'Do not change saved addresses']"
        ),
    )
    complexity: str = Field(
        default="medium",
        description="Assessed complexity: 'simple' (1-2 steps), 'medium' (3-5), 'complex' (6+)"
    )
    is_achievable: bool = Field(
        default=True,
        description="Whether the agent believes this goal can be achieved from the current page"
    )
    achievability_reason: str = Field(
        default="",
        description="Why the agent thinks this goal is/isn't achievable"
    )


class ReasoningTrace(BaseModel):
    """A single reasoning step in the agent's Chain of Thought.

    These are logged so the user can see HOW the agent thinks,
    not just what it does. Transparency builds trust.
    """

    step_number: int = Field(description="Sequential reasoning step")
    thought: str = Field(
        description=(
            "The agent's internal reasoning. "
            "E.g., 'I see a search box [15] and a search button [16]. "
            "I should first type the query, then click search.'"
        ),
    )
    observation: str = Field(
        default="",
        description="What the agent observed from the page or action result"
    )
    conclusion: str = Field(
        default="",
        description="The conclusion drawn from this reasoning step"
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence in this reasoning (0.0–1.0)"
    )


class SelfCritique(BaseModel):
    """Agent's self-evaluation of its own plan or action.

    This is what separates a cognitive agent from a simple tool caller.
    The agent asks itself: "Is this right? What could go wrong?"
    """

    target: str = Field(
        description="What is being critiqued: 'plan', 'action', 'strategy', 'overall_progress'"
    )
    critique: str = Field(
        description=(
            "The agent's honest assessment. "
            "E.g., 'My plan assumes the search returns results on the same page, "
            "but this site might use a separate results page. I should check after searching.'"
        ),
    )
    severity: str = Field(
        default="info",
        description="'info' (noting something), 'warning' (potential issue), 'critical' (plan is wrong)"
    )
    suggestion: str = Field(
        default="",
        description="What the agent suggests doing differently"
    )
    should_re_plan: bool = Field(
        default=False,
        description="Whether the critique is severe enough to warrant creating a new plan"
    )


class Evaluation(BaseModel):
    """Agent's evaluation of an action outcome against the goal.

    After every action, the agent evaluates:
    - Did the action work?
    - Are we closer to the goal?
    - Do we need to adjust the plan?
    """

    action_succeeded: bool = Field(
        description="Did the action execute successfully?"
    )
    goal_progress: str = Field(
        default="",
        description=(
            "Assessment of progress toward the goal. "
            "E.g., 'Step 2/5 complete. Search results are showing. "
            "The correct pizza is visible in the results.'"
        ),
    )
    progress_percentage: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Estimated progress toward goal completion (0.0–1.0)"
    )
    unexpected_results: str = Field(
        default="",
        description=(
            "Anything unexpected that happened. "
            "E.g., 'A popup modal appeared asking for location permissions'"
        ),
    )
    next_action_suggestion: str = Field(
        default="",
        description="What the agent thinks it should do next"
    )
    should_continue: bool = Field(
        default=True,
        description="Whether the agent should continue executing or stop"
    )
    should_re_plan: bool = Field(
        default=False,
        description="Whether the plan needs to be revised based on this evaluation"
    )
    re_plan_reason: str = Field(
        default="",
        description="Why re-planning is needed"
    )


class RetryContext(BaseModel):
    """Tracks retry state for adaptive retry logic.

    The agent doesn't just retry the same action — it tries
    a DIFFERENT strategy each time based on what failed.
    """

    attempt_number: int = Field(
        default=0,
        description="Current attempt number (0 = first attempt)"
    )
    max_attempts: int = Field(
        default=3,
        description="Maximum attempts before giving up"
    )
    failed_strategies: list[str] = Field(
        default_factory=list,
        description=(
            "Strategies that have already been tried and failed. "
            "E.g., ['click by element_id', 'click by text match', 'scroll and retry']. "
            "The agent must choose a DIFFERENT strategy."
        ),
    )
    last_error: str = Field(
        default="",
        description="Error message from the most recent failed attempt"
    )
    escalation_needed: bool = Field(
        default=False,
        description="Whether the agent should ask the user for help after exhausting retries"
    )


class TaskMemory(BaseModel):
    """Short-term memory accumulated during task execution.

    This is NOT long-term memory across sessions — it's what the
    agent has learned DURING this specific task. Observations,
    page patterns, user preferences discovered during execution.
    """

    observations: list[str] = Field(
        default_factory=list,
        description=(
            "Things the agent has noticed during execution. "
            "E.g., 'This site uses modal popups for confirmation', "
            "'Search results load dynamically without page navigation'"
        ),
    )
    discovered_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Patterns discovered about the current website. "
            "E.g., 'Navigation is in the top bar', "
            "'Forms use inline validation'"
        ),
    )
    user_preferences: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "User preferences discovered during the task. "
            "E.g., {'pizza_type': 'vegetarian', 'delivery_address': '123 Main St'}"
        ),
    )
    important_data: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Data extracted during task that may be needed later. "
            "E.g., {'order_number': '12345', 'total_price': '$15.99'}"
        ),
    )
    pages_visited: list[str] = Field(
        default_factory=list,
        description="URLs of pages visited during this task"
    )


# ============================================================
# LangGraph AgentState — The central state object
# ============================================================

class AgentState:
    """LangGraph state for the cognitive agent.

    This is defined as annotation-based TypedDict-style for LangGraph.
    Every node in the graph reads from and writes to this state.

    The state captures the agent's complete cognitive context:
    - What is the goal?
    - What is the plan?
    - What has been tried?
    - What is the agent thinking?
    - What does the page look like?
    - What should happen next?
    """
    pass


# LangGraph requires TypedDict for state, not Pydantic.
# We use Annotated types for reducer functions where needed.

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """Central state for the LangGraph cognitive agent.

    Nodes read from and write to this state. LangGraph manages
    persistence and checkpointing automatically.

    Groups:
    - Goal & Plan: What we're trying to achieve
    - Page Context: What the agent currently sees
    - Cognitive Loop: Reasoning, decisions, evaluations
    - Execution: Actions taken and their results
    - Adaptation: Retry logic, strategy changes
    - Memory: Accumulated knowledge during this task
    - Messages: LangChain message history for LLM context
    - Control: Status, iteration tracking, configuration
    """

    # ---- Goal & Plan ----
    goal: Goal
    plan: Plan

    # ---- Page Context ----
    page_context: PageContext
    previous_page_context: PageContext | None

    # ---- Cognitive Loop ----
    reasoning_traces: list[ReasoningTrace]
    current_reasoning: str  # Latest CoT output from the LLM
    self_critiques: list[SelfCritique]
    latest_evaluation: Evaluation | None

    # ---- Execution ----
    current_action: Action | None
    action_history: list[dict]  # List of {action: Action, result: ActionResult} pairs
    pending_action_result: ActionResult | None

    # ---- Adaptation ----
    retry_context: RetryContext

    # ---- Memory ----
    task_memory: TaskMemory

    # ---- Messages (LangChain message history for LLM) ----
    messages: Annotated[list[BaseMessage], add_messages]

    # ---- Control ----
    cognitive_status: CognitiveStatus
    iteration_count: int  # How many reasoning loops have occurred
    max_iterations: int  # Safety limit to prevent infinite loops
    error: str | None  # Current error, if any
    should_terminate: bool  # Whether the agent should stop
    task_summary: str  # Final summary with findings (set by finalize node)

    # ---- Configuration ----
    model_name: str  # Which LLM model to use
    auto_confirm: bool  # Whether to skip user confirmation for low-risk actions
    confidence_threshold: float  # Below this, always ask the user
    api_keys: dict | None  # Runtime API keys from KeyVault (never logged)


# ============================================================
# Factory function to create initial state
# ============================================================

def create_initial_state(
    goal_text: str,
    page_context: PageContext | None = None,
    model_name: str = "qwen2.5:32b-instruct",
    max_iterations: int = 25,
    auto_confirm: bool = False,
    confidence_threshold: float = 0.6,
    api_keys: dict | None = None,
) -> AgentState:
    """Create a fresh AgentState for a new task.

    Args:
        goal_text: The user's request in natural language.
        page_context: Initial page state (if available).
        model_name: Ollama model name or OpenAI model name.
        max_iterations: Maximum reasoning loops before forced termination.
        auto_confirm: If True, skip confirmation for low-risk, high-confidence actions.
        confidence_threshold: Actions below this confidence always require confirmation.

    Returns:
        A fully initialized AgentState ready for the first graph node.
    """
    return AgentState(
        # Goal & Plan
        goal=Goal(original_text=goal_text),
        plan=Plan(),

        # Page Context
        page_context=page_context or PageContext(url="", title="No page loaded"),
        previous_page_context=None,

        # Cognitive Loop
        reasoning_traces=[],
        current_reasoning="",
        self_critiques=[],
        latest_evaluation=None,

        # Execution
        current_action=None,
        action_history=[],
        pending_action_result=None,

        # Adaptation
        retry_context=RetryContext(),

        # Memory
        task_memory=TaskMemory(),

        # Messages
        messages=[],

        # Control
        cognitive_status=CognitiveStatus.ANALYZING_GOAL,
        iteration_count=0,
        max_iterations=max_iterations,
        error=None,
        should_terminate=False,
        task_summary="",

        # Configuration
        model_name=model_name,
        auto_confirm=auto_confirm,
        confidence_threshold=confidence_threshold,
        api_keys=api_keys,
    )
