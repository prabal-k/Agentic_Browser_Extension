"""LangGraph state graph — the complete cognitive agent workflow.

This wires all cognitive nodes together into a directed graph with
conditional edges that route based on the agent's cognitive status.

Graph flow (REACTIVE — no upfront plan):
    START
      → analyze_and_plan (0 LLM calls: extract URLs, metadata only)
      → decide_action (1 LLM call: look at page + task → pick action)
          → execute_action_node
          → observe (0 LLM calls: deterministic diff)
          → smart_evaluate (0 LLM calls: deterministic ~70%)
              ├─ obvious → self_critique → decide_action (loop)
              └─ ambiguous → evaluate (1 LLM call) → self_critique → decide_action
          → agent calls done(findings) → finalize → END

    Typical 4-action task: 4 LLM calls (1 per action, 0 overhead)

Features:
- LangGraph interrupt() for human-in-the-loop (confirmation + clarification)
- Checkpointing via MemorySaver (swappable to persistent storage)
- Max iteration safety to prevent infinite loops
- Conditional routing based on CognitiveStatus
"""

import structlog
from typing import Literal

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command

from agent_core.schemas.agent import AgentState, CognitiveStatus, RetryContext
from agent_core.schemas.actions import ActionType
from agent_core.agent.nodes import (
    analyze_and_plan,
    analyze_goal,
    create_plan,
    critique_plan,
    reason,
    decide_action,
    observe,
    smart_evaluate,
    evaluate,
    self_critique_action,
    handle_retry,
    verify_goal,
    finalize,
)

logger = structlog.get_logger("agent.graph")


# ============================================================
# Interrupt Nodes (these use LangGraph interrupt())
# ============================================================

async def confirm_action(state: AgentState) -> dict:
    """Pause the graph and ask the user to confirm the planned action.

    Uses LangGraph's interrupt() to pause execution. The graph
    will resume when the user responds via the WebSocket.
    """
    action = state.get("current_action")
    if not action:
        return {"cognitive_status": CognitiveStatus.EXECUTING}

    # Build confirmation data that the frontend will render
    confirmation_data = {
        "action_id": action.action_id,
        "action_type": action.action_type.value,
        "element_id": action.element_id,
        "value": action.value,
        "description": action.description,
        "reasoning": action.reasoning,
        "confidence": action.confidence,
        "risk_level": action.risk_level,
    }

    # INTERRUPT: Pause graph, send confirmation request to user
    user_response = interrupt(confirmation_data)

    # Resume: user_response is the value passed when graph is resumed
    if isinstance(user_response, dict):
        confirmed = user_response.get("confirmed", False)
    elif isinstance(user_response, str):
        confirmed = user_response.lower() in ("yes", "true", "confirm", "y")
    else:
        confirmed = bool(user_response)

    if confirmed:
        return {"cognitive_status": CognitiveStatus.EXECUTING}
    else:
        # User rejected — go back to reasoning with feedback
        return {
            "cognitive_status": CognitiveStatus.REASONING,
            "current_reasoning": f"User rejected action: {action.description}. Need to reconsider.",
        }


async def ask_user_node(state: AgentState) -> dict:
    """Pause the graph to ask the user a clarification question.

    The agent needs information it can't determine from the page.
    Uses LangGraph interrupt() to pause and collect user input.
    """
    # Determine what to ask
    traces = state.get("reasoning_traces", [])
    latest_trace = traces[-1] if traces else None

    question = "I need more information to continue."
    if latest_trace and latest_trace.conclusion:
        question = latest_trace.conclusion

    # Check if the action itself is an ask_user
    action = state.get("current_action")
    if action and action.value:
        question = action.value

    ask_data = {
        "question": question,
        "context": state.get("current_reasoning", ""),
        "goal": state["goal"].original_text,
    }

    # INTERRUPT: Pause graph, ask user
    user_response = interrupt(ask_data)

    # Resume with user's answer incorporated into reasoning
    response_text = ""
    if isinstance(user_response, dict):
        response_text = str(user_response.get("answer", user_response))
    else:
        response_text = str(user_response)

    return {
        "cognitive_status": CognitiveStatus.REASONING,
        "current_reasoning": f"User clarified: {response_text}",
        "messages": [
            # We import here to avoid circular imports at module level
            __import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(
                content=f"User response: {response_text}"
            )
        ],
    }


async def execute_action_node(state: AgentState) -> dict:
    """Send the action to the browser for execution.

    In the real flow, this triggers an interrupt that waits for
    the browser (extension or Playwright) to execute the action
    and report back with the result and new DOM.

    The frontend/Playwright executes the action and resumes
    the graph with an ActionResult.
    """
    action = state.get("current_action")
    if not action:
        return {
            "cognitive_status": CognitiveStatus.EVALUATING,
            "pending_action_result": None,
        }

    # Handle "done" action type — no browser execution needed
    if action.action_type == ActionType.DONE:
        return {
            "cognitive_status": CognitiveStatus.COMPLETED,
            "should_terminate": True,
        }

    # INTERRUPT: Send action to browser, wait for result
    execution_request = {
        "action_id": action.action_id,
        "action_type": action.action_type.value,
        "element_id": action.element_id,
        "value": action.value,
        "description": action.description,
    }

    execution_result = interrupt(execution_request)

    # Parse the result from the browser
    from agent_core.schemas.actions import ActionResult, ActionStatus

    if isinstance(execution_result, dict):
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
        # Update page context if new DOM was provided
        new_page = None
        if execution_result.get("new_dom"):
            from agent_core.schemas.dom import PageContext
            try:
                new_page = PageContext.model_validate(execution_result["new_dom"])
            except Exception:
                pass

        updates = {
            "pending_action_result": result,
            "cognitive_status": CognitiveStatus.OBSERVING,
        }
        if new_page:
            updates["page_context"] = new_page
        return updates
    else:
        return {
            "pending_action_result": ActionResult(
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                message="Invalid result from browser",
                error="Execution result was not a valid dict",
            ),
            "cognitive_status": CognitiveStatus.OBSERVING,
        }


# ============================================================
# Routing Functions
# ============================================================

def route_after_critique(state: AgentState) -> Literal["create_plan", "decide_action"]:
    """Route after plan critique: re-plan or proceed directly to action selection."""
    status = state.get("cognitive_status")
    if status == CognitiveStatus.RE_PLANNING:
        plan = state.get("plan")
        if plan and plan.plan_version >= 4:
            # Too many re-plans — just proceed
            logger.warning("max_replans_reached")
            return "decide_action"
        return "create_plan"
    # Skip reason node — go straight to decide_action
    return "decide_action"


def route_after_reasoning(
    state: AgentState,
) -> Literal["decide_action", "ask_user_node", "create_plan", "finalize"]:
    """Route after reasoning: decide action, ask user, re-plan, or give up."""
    status = state.get("cognitive_status")

    if status == CognitiveStatus.ASKING_USER:
        return "ask_user_node"

    if status == CognitiveStatus.RE_PLANNING:
        return "create_plan"

    if state.get("should_terminate"):
        return "finalize"

    return "decide_action"


def route_after_decision(
    state: AgentState,
) -> Literal["confirm_action", "execute_action_node", "ask_user_node", "finalize"]:
    """Route after action decision: confirm, execute directly, ask, or finish."""
    status = state.get("cognitive_status")
    action = state.get("current_action")

    # Ask user takes priority — agent needs clarification before proceeding
    if status == CognitiveStatus.ASKING_USER:
        return "ask_user_node"

    if status == CognitiveStatus.COMPLETED or (action and action.action_type == ActionType.DONE):
        return "finalize"

    if status == CognitiveStatus.AWAITING_CONFIRMATION:
        return "confirm_action"

    # Execute directly (auto-confirmed or no-confirm needed)
    return "execute_action_node"


def route_after_self_critique(
    state: AgentState,
) -> Literal["reason", "decide_action", "create_plan", "handle_retry", "verify_goal", "finalize"]:
    """Route after post-action self-critique."""
    status = state.get("cognitive_status")

    if status == CognitiveStatus.COMPLETED:
        # Don't go straight to finalize — verify the goal first
        return "verify_goal"

    if status == CognitiveStatus.FAILED:
        return "finalize"

    if status == CognitiveStatus.RE_PLANNING:
        return "create_plan"

    if status == CognitiveStatus.RETRYING:
        return "handle_retry"

    # FAST PATH: skip reason, go straight to decide_action
    if status == CognitiveStatus.DECIDING:
        return "decide_action"

    # Default: continue reasoning for next step
    return "reason"


def route_after_verify_goal(
    state: AgentState,
) -> Literal["finalize", "create_plan"]:
    """Route after goal verification: finalize or re-plan."""
    status = state.get("cognitive_status")

    if status == CognitiveStatus.RE_PLANNING:
        return "create_plan"

    # COMPLETED or FAILED → finalize
    return "finalize"


def route_after_smart_evaluate(
    state: AgentState,
) -> Literal["evaluate", "self_critique_action"]:
    """Route after smart evaluate: LLM evaluate if needed, or skip to self_critique."""
    status = state.get("cognitive_status")

    if status == CognitiveStatus.EVALUATING:
        # Smart evaluate couldn't determine outcome — need LLM
        return "evaluate"

    # Smart evaluate already produced evaluation — skip LLM
    return "self_critique_action"


def route_after_confirm(
    state: AgentState,
) -> Literal["execute_action_node", "reason"]:
    """Route after user confirmation: execute or go back to reasoning."""
    status = state.get("cognitive_status")

    if status == CognitiveStatus.EXECUTING:
        return "execute_action_node"

    # User rejected — re-reason
    return "reason"


# ============================================================
# Graph Builder
# ============================================================

def create_agent_graph(checkpointer=None) -> StateGraph:
    """Create the complete cognitive agent graph.

    Args:
        checkpointer: LangGraph checkpointer for state persistence.
                      Defaults to MemorySaver (in-memory).

    Returns:
        Compiled LangGraph StateGraph ready for execution.
    """
    if checkpointer is None:
        checkpointer = MemorySaver()

    # Build the graph
    builder = StateGraph(AgentState)

    # ---- Add all nodes ----
    builder.add_node("analyze_and_plan", analyze_and_plan)
    builder.add_node("create_plan", create_plan)  # Kept for re-planning
    builder.add_node("critique_plan", critique_plan)
    builder.add_node("reason", reason)
    builder.add_node("decide_action", decide_action)
    builder.add_node("confirm_action", confirm_action)
    builder.add_node("execute_action_node", execute_action_node)
    builder.add_node("observe", observe)
    builder.add_node("smart_evaluate", smart_evaluate)
    builder.add_node("evaluate", evaluate)
    builder.add_node("self_critique_action", self_critique_action)
    builder.add_node("handle_retry", handle_retry)
    builder.add_node("verify_goal", verify_goal)
    builder.add_node("ask_user_node", ask_user_node)
    builder.add_node("finalize", finalize)

    # ---- Entry point ----
    # Reactive: analyze_and_plan extracts metadata (no LLM), then straight to action
    builder.add_edge(START, "analyze_and_plan")

    # ---- Linear edges ----
    # analyze_and_plan → decide_action directly (skip critique)
    builder.add_edge("analyze_and_plan", "decide_action")
    # create_plan still connects to critique for re-plan path (rare)
    builder.add_edge("create_plan", "critique_plan")

    # ---- Conditional edges ----

    # After critique: re-plan or proceed (only reached via re-plan path)
    builder.add_conditional_edges("critique_plan", route_after_critique)

    # After reasoning: decide, ask, or finalize
    builder.add_conditional_edges("reason", route_after_reasoning)

    # After decision: confirm, execute, ask, or done
    builder.add_conditional_edges("decide_action", route_after_decision)

    # After confirmation: execute or re-reason
    builder.add_conditional_edges("confirm_action", route_after_confirm)

    # Execution → Observe → Smart Evaluate → [LLM Evaluate if needed] → Self-Critique
    builder.add_edge("execute_action_node", "observe")
    builder.add_edge("observe", "smart_evaluate")
    builder.add_conditional_edges("smart_evaluate", route_after_smart_evaluate)
    builder.add_edge("evaluate", "self_critique_action")

    # After self-critique: continue, re-plan, retry, or verify goal
    builder.add_conditional_edges("self_critique_action", route_after_self_critique)

    # After goal verification: finalize or re-plan
    builder.add_conditional_edges("verify_goal", route_after_verify_goal)

    # Retry → straight to action decision (retry strategy is in current_reasoning)
    builder.add_edge("handle_retry", "decide_action")

    # Ask user → back to reasoning (need to process user response)
    builder.add_edge("ask_user_node", "reason")

    # Finalize → END
    builder.add_edge("finalize", END)

    # Compile with checkpointer
    graph = builder.compile(checkpointer=checkpointer)

    logger.info("agent_graph_created", node_count=len(builder.nodes))

    return graph


# Type alias for the compiled graph
AgentGraph = StateGraph
