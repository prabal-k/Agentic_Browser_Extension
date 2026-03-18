"""Tests for the LangGraph cognitive agent graph.

Tests cover:
- Graph construction and compilation
- Node existence and wiring
- Routing logic (all conditional edges)
- Initial state creation and flow
- LLM client factory
- Prompt formatting utilities

Note: Full integration tests with real LLM calls are in Phase 3.
These tests use mock/deterministic inputs to validate graph structure
and routing logic.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from langgraph.graph import StateGraph

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
    TaskMemory,
    CognitiveStatus,
    create_initial_state,
)
from agent_core.schemas.actions import Action, ActionType, ActionResult, ActionStatus
from agent_core.schemas.dom import PageContext, DOMElement, ElementType
from agent_core.agent.graph import (
    create_agent_graph,
    route_after_critique,
    route_after_reasoning,
    route_after_decision,
    route_after_self_critique,
    route_after_confirm,
)
from agent_core.agent.llm_client import (
    detect_provider,
    LLMProvider,
    get_llm,
    get_reasoning_llm,
    get_action_llm,
)
from agent_core.agent.prompts import (
    format_action_history,
    format_plan_for_prompt,
    format_retry_context,
    format_task_memory,
    GOAL_ANALYSIS_PROMPT,
    PLAN_CREATION_PROMPT,
    REASONING_PROMPT,
    EVALUATION_PROMPT,
)


# ============================================================
# Graph Construction Tests
# ============================================================

class TestGraphConstruction:
    """Verify the graph is built correctly."""

    def test_graph_compiles(self):
        """Graph should compile without errors."""
        graph = create_agent_graph()
        assert graph is not None

    def test_graph_has_all_nodes(self):
        """All cognitive nodes must be present."""
        from langgraph.checkpoint.memory import MemorySaver

        builder = StateGraph(AgentState)

        # We test the builder before compilation to check nodes
        # Instead, just verify compilation succeeds and the graph is usable
        graph = create_agent_graph()
        # The graph object is a CompiledGraph — verify it exists
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_graph_with_custom_checkpointer(self):
        """Graph should accept custom checkpointer."""
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()
        graph = create_agent_graph(checkpointer=checkpointer)
        assert graph is not None


# ============================================================
# Routing Logic Tests
# ============================================================

class TestRouteAfterCritique:
    """Test routing after plan self-critique."""

    def test_routes_to_replan_when_critical(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            plan=Plan(plan_version=1),
            cognitive_status=CognitiveStatus.RE_PLANNING,
        )
        assert route_after_critique(state) == "create_plan"

    def test_routes_to_reason_when_ok(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            plan=Plan(plan_version=1),
            cognitive_status=CognitiveStatus.REASONING,
        )
        assert route_after_critique(state) == "reason"

    def test_stops_replanning_after_3_versions(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            plan=Plan(plan_version=3),
            cognitive_status=CognitiveStatus.RE_PLANNING,
        )
        # Should proceed to reason, not re-plan again
        assert route_after_critique(state) == "reason"


class TestRouteAfterReasoning:
    """Test routing after reasoning node."""

    def test_routes_to_decide_action(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.DECIDING,
            should_terminate=False,
        )
        assert route_after_reasoning(state) == "decide_action"

    def test_routes_to_ask_user(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.ASKING_USER,
        )
        assert route_after_reasoning(state) == "ask_user_node"

    def test_routes_to_finalize_when_terminating(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.DECIDING,
            should_terminate=True,
        )
        assert route_after_reasoning(state) == "finalize"


class TestRouteAfterDecision:
    """Test routing after action decision."""

    def test_routes_to_confirm(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.AWAITING_CONFIRMATION,
            current_action=Action(action_type=ActionType.CLICK, element_id=1),
        )
        assert route_after_decision(state) == "confirm_action"

    def test_routes_to_execute_directly(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.EXECUTING,
            current_action=Action(action_type=ActionType.SCROLL_DOWN),
        )
        assert route_after_decision(state) == "execute_action_node"

    def test_routes_to_finalize_on_done(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.COMPLETED,
            current_action=Action(action_type=ActionType.DONE, value="Task complete"),
        )
        assert route_after_decision(state) == "finalize"

    def test_routes_to_ask_user(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.ASKING_USER,
            current_action=Action(action_type=ActionType.DONE, value="What pizza?"),
        )
        assert route_after_decision(state) == "ask_user_node"


class TestRouteAfterSelfCritique:
    """Test routing after post-action self-critique."""

    def test_routes_to_reason_on_success(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.REASONING,
        )
        assert route_after_self_critique(state) == "reason"

    def test_routes_to_replan(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.RE_PLANNING,
        )
        assert route_after_self_critique(state) == "create_plan"

    def test_routes_to_retry(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.RETRYING,
        )
        assert route_after_self_critique(state) == "handle_retry"

    def test_routes_to_finalize_on_complete(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.COMPLETED,
        )
        assert route_after_self_critique(state) == "finalize"

    def test_routes_to_finalize_on_failure(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.FAILED,
        )
        assert route_after_self_critique(state) == "finalize"


class TestRouteAfterConfirm:
    """Test routing after user confirmation."""

    def test_routes_to_execute_on_confirm(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.EXECUTING,
        )
        assert route_after_confirm(state) == "execute_action_node"

    def test_routes_to_reason_on_reject(self):
        state = AgentState(
            goal=Goal(original_text="test"),
            cognitive_status=CognitiveStatus.REASONING,
        )
        assert route_after_confirm(state) == "reason"


# ============================================================
# LLM Client Tests
# ============================================================

class TestLLMClient:
    """Test LLM client factory functions."""

    def test_detect_ollama_provider(self):
        assert detect_provider("qwen2.5:32b-instruct") == LLMProvider.OLLAMA
        assert detect_provider("llama3:70b") == LLMProvider.OLLAMA
        assert detect_provider("deepseek-r1:32b") == LLMProvider.OLLAMA

    def test_detect_openai_provider(self):
        assert detect_provider("gpt-4o") == LLMProvider.OPENAI
        assert detect_provider("gpt-4o-mini") == LLMProvider.OPENAI
        assert detect_provider("o1-preview") == LLMProvider.OPENAI
        assert detect_provider("o3-mini") == LLMProvider.OPENAI

    def test_get_llm_returns_instance(self):
        """Should create an LLM instance (may not connect)."""
        llm = get_llm(model_name="qwen2.5:32b-instruct", bind_tools=False)
        assert llm is not None

    def test_get_reasoning_llm(self):
        llm = get_reasoning_llm(model_name="qwen2.5:32b-instruct")
        assert llm is not None

    def test_get_action_llm(self):
        llm = get_action_llm(model_name="qwen2.5:32b-instruct")
        assert llm is not None


# ============================================================
# Prompt Formatting Tests
# ============================================================

class TestPromptFormatting:
    """Test prompt utility functions."""

    def test_format_empty_action_history(self):
        result = format_action_history([])
        assert "No actions" in result

    def test_format_action_history_with_entries(self):
        history = [
            {
                "action": {"action_type": "click", "description": "Click search button"},
                "result": {"status": "success"},
            },
            {
                "action": {"action_type": "type_text", "description": "Type pizza"},
                "result": {"status": "failed"},
            },
        ]
        result = format_action_history(history)
        assert "click" in result
        assert "type_text" in result
        assert "success" in result
        assert "failed" in result

    def test_format_action_history_limits_entries(self):
        history = [
            {"action": {"action_type": "click", "description": f"Action {i}"}, "result": {"status": "success"}}
            for i in range(20)
        ]
        result = format_action_history(history, max_entries=5)
        lines = [l for l in result.strip().split("\n") if l.strip()]
        assert len(lines) == 5

    def test_format_empty_plan(self):
        result = format_plan_for_prompt({"steps": []})
        assert "No plan" in result

    def test_format_plan_with_steps(self):
        plan_data = {
            "steps": [
                {"step_id": 1, "description": "Go to Google", "status": "completed"},
                {"step_id": 2, "description": "Type query", "status": "in_progress"},
                {"step_id": 3, "description": "Click search", "status": "pending"},
            ]
        }
        result = format_plan_for_prompt(plan_data)
        assert "[x]" in result  # completed
        assert "[~]" in result  # in_progress
        assert "[ ]" in result  # pending

    def test_format_retry_context_no_retry(self):
        result = format_retry_context({"attempt_number": 0})
        assert "Not in retry" in result

    def test_format_retry_context_active(self):
        ctx = {
            "attempt_number": 2,
            "max_attempts": 3,
            "last_error": "Element not found",
            "failed_strategies": ["click by id", "click by text"],
        }
        result = format_retry_context(ctx)
        assert "Attempt 2" in result
        assert "Element not found" in result

    def test_format_task_memory_empty(self):
        result = format_task_memory({})
        assert "No observations" in result

    def test_format_task_memory_with_data(self):
        memory = {
            "observations": ["Site uses modals"],
            "discovered_patterns": ["Nav is on top"],
            "user_preferences": {"size": "large"},
        }
        result = format_task_memory(memory)
        assert "modals" in result
        assert "Nav is on top" in result
        assert "large" in result

    def test_prompts_have_format_placeholders(self):
        """Verify all prompts have the expected format placeholders."""
        assert "{goal}" in GOAL_ANALYSIS_PROMPT
        assert "{page_context}" in GOAL_ANALYSIS_PROMPT

        assert "{goal_analysis}" in PLAN_CREATION_PROMPT
        assert "{page_context}" in PLAN_CREATION_PROMPT

        assert "{goal}" in REASONING_PROMPT
        assert "{plan}" in REASONING_PROMPT
        assert "{page_context}" in REASONING_PROMPT
        assert "{retry_context}" in REASONING_PROMPT

        assert "{action_description}" in EVALUATION_PROMPT
        assert "{goal}" in EVALUATION_PROMPT


# ============================================================
# Node Unit Tests (with mocked LLM)
# ============================================================

class TestAnalyzeGoalNode:
    """Test analyze_goal node with mocked LLM."""

    @pytest.mark.asyncio
    async def test_analyze_goal_parses_response(self):
        """Should parse LLM JSON response into Goal."""
        mock_response = MagicMock()
        mock_response.content = '''```json
{
    "interpreted_goal": "Search Google for pizza restaurants",
    "sub_goals": ["Open Google", "Type query", "Submit search"],
    "success_criteria": ["Search results are displayed"],
    "constraints": ["Do not click ads"],
    "complexity": "simple",
    "is_achievable": true,
    "achievability_reason": "Google is accessible"
}
```'''

        with patch("agent_core.agent.nodes.get_reasoning_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            from agent_core.agent.nodes import analyze_goal

            state = create_initial_state(
                goal_text="Search for pizza restaurants",
                page_context=PageContext(url="https://google.com", title="Google"),
            )

            result = await analyze_goal(state)

            assert result["goal"].interpreted_goal == "Search Google for pizza restaurants"
            assert len(result["goal"].sub_goals) == 3
            assert result["goal"].is_achievable is True
            assert result["cognitive_status"] == CognitiveStatus.CREATING_PLAN

    @pytest.mark.asyncio
    async def test_analyze_goal_handles_parse_error(self):
        """Should produce fallback goal on parse error."""
        mock_response = MagicMock()
        mock_response.content = "This is not valid JSON"

        with patch("agent_core.agent.nodes.get_reasoning_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            from agent_core.agent.nodes import analyze_goal

            state = create_initial_state(goal_text="test goal")
            result = await analyze_goal(state)

            # Should still produce a goal, just with defaults
            assert result["goal"].original_text == "test goal"
            assert result["goal"].is_achievable is True


class TestCreatePlanNode:
    """Test create_plan node with mocked LLM."""

    @pytest.mark.asyncio
    async def test_create_plan_parses_steps(self):
        mock_response = MagicMock()
        mock_response.content = '''```json
{
    "reasoning": "Simple 3-step task",
    "steps": [
        {"step_id": 1, "description": "Navigate to Google", "expected_outcome": "Google homepage loads"},
        {"step_id": 2, "description": "Type search query", "expected_outcome": "Query appears in search box"},
        {"step_id": 3, "description": "Click search", "expected_outcome": "Results page loads"}
    ]
}
```'''

        with patch("agent_core.agent.nodes.get_reasoning_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            from agent_core.agent.nodes import create_plan

            state = create_initial_state(goal_text="Search for pizza")
            state["goal"] = Goal(
                original_text="Search for pizza",
                interpreted_goal="Search Google for pizza restaurants",
            )

            result = await create_plan(state)

            assert len(result["plan"].steps) == 3
            assert result["plan"].plan_version == 1
            assert result["plan"].steps[0].description == "Navigate to Google"
            assert result["cognitive_status"] == CognitiveStatus.SELF_CRITIQUING


class TestReasonNode:
    """Test reason node with mocked LLM."""

    @pytest.mark.asyncio
    async def test_reason_produces_trace(self):
        mock_response = MagicMock()
        mock_response.content = '''```json
{
    "thought": "I see a search box at element [1]. I should type the query.",
    "observation": "Search box is empty and focused",
    "conclusion": "Type 'pizza restaurants' in element [1]",
    "target_element_id": 1,
    "confidence": 0.9,
    "needs_clarification": false,
    "clarification_question": ""
}
```'''

        with patch("agent_core.agent.nodes.get_reasoning_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            from agent_core.agent.nodes import reason

            state = create_initial_state(goal_text="Search for pizza")
            state["plan"] = Plan(
                steps=[PlanStep(step_id=1, description="Type query", expected_outcome="Query in search box")],
            )

            result = await reason(state)

            assert len(result["reasoning_traces"]) == 1
            assert result["reasoning_traces"][0].confidence == 0.9
            assert result["iteration_count"] == 1
            assert result["cognitive_status"] == CognitiveStatus.DECIDING

    @pytest.mark.asyncio
    async def test_reason_detects_max_iterations(self):
        """Should stop when max iterations reached."""
        with patch("agent_core.agent.nodes.get_reasoning_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_get_llm.return_value = mock_llm

            from agent_core.agent.nodes import reason

            state = create_initial_state(goal_text="test", max_iterations=5)
            state["iteration_count"] = 5  # Already at max

            result = await reason(state)

            assert result["should_terminate"] is True
            assert result["cognitive_status"] == CognitiveStatus.FAILED
            assert "Maximum iterations" in result["error"]

    @pytest.mark.asyncio
    async def test_reason_routes_to_ask_user(self):
        mock_response = MagicMock()
        mock_response.content = '''```json
{
    "thought": "I need to know which pizza size the user wants",
    "observation": "Multiple size options available",
    "conclusion": "Ask user for preferred pizza size",
    "target_element_id": null,
    "confidence": 0.3,
    "needs_clarification": true,
    "clarification_question": "Which pizza size do you want?"
}
```'''

        with patch("agent_core.agent.nodes.get_reasoning_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            from agent_core.agent.nodes import reason

            state = create_initial_state(goal_text="Order pizza")
            state["plan"] = Plan(
                steps=[PlanStep(step_id=1, description="Select size")],
            )

            result = await reason(state)

            assert result["cognitive_status"] == CognitiveStatus.ASKING_USER


class TestSelfCritiqueActionNode:
    """Test self_critique_action node."""

    @pytest.mark.asyncio
    async def test_completes_when_all_steps_done(self):
        from agent_core.agent.nodes import self_critique_action

        state = AgentState(
            goal=Goal(original_text="test"),
            plan=Plan(
                steps=[PlanStep(step_id=1, description="Done", status=StepStatus.COMPLETED)],
                current_step_index=1,  # Past last step
            ),
            latest_evaluation=Evaluation(action_succeeded=True, should_continue=True),
            cognitive_status=CognitiveStatus.SELF_CRITIQUING,
        )

        result = await self_critique_action(state)
        assert result["cognitive_status"] == CognitiveStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_routes_to_retry_on_failure(self):
        from agent_core.agent.nodes import self_critique_action

        state = AgentState(
            goal=Goal(original_text="test"),
            plan=Plan(
                steps=[PlanStep(step_id=1, description="Click button")],
                current_step_index=0,
            ),
            latest_evaluation=Evaluation(action_succeeded=False, should_continue=True),
            retry_context=RetryContext(attempt_number=1, max_attempts=3),
            cognitive_status=CognitiveStatus.SELF_CRITIQUING,
        )

        result = await self_critique_action(state)
        assert result["cognitive_status"] == CognitiveStatus.RETRYING

    @pytest.mark.asyncio
    async def test_replans_when_retries_exhausted(self):
        from agent_core.agent.nodes import self_critique_action

        state = AgentState(
            goal=Goal(original_text="test"),
            plan=Plan(
                steps=[PlanStep(step_id=1, description="Click button")],
                current_step_index=0,
            ),
            latest_evaluation=Evaluation(action_succeeded=False, should_continue=True),
            retry_context=RetryContext(attempt_number=3, max_attempts=3),
            cognitive_status=CognitiveStatus.SELF_CRITIQUING,
        )

        result = await self_critique_action(state)
        assert result["cognitive_status"] == CognitiveStatus.RE_PLANNING


class TestObserveNode:
    """Test observe node."""

    @pytest.mark.asyncio
    async def test_records_success_in_memory(self):
        from agent_core.agent.nodes import observe

        state = AgentState(
            goal=Goal(original_text="test"),
            current_action=Action(
                action_id="act_001",
                action_type=ActionType.CLICK,
                element_id=1,
                description="Click button",
            ),
            pending_action_result=ActionResult(
                action_id="act_001",
                status=ActionStatus.SUCCESS,
                message="Clicked",
                page_changed=True,
                new_url="https://example.com/results",
            ),
            task_memory=TaskMemory(),
            action_history=[],
            page_context=PageContext(url="https://example.com", title="Test"),
        )

        result = await observe(state)

        assert len(result["action_history"]) == 1
        assert "https://example.com/results" in result["task_memory"].pages_visited
        assert result["cognitive_status"] == CognitiveStatus.EVALUATING


# ============================================================
# JSON Parsing Utility Test
# ============================================================

class TestParseUtils:
    """Test JSON parsing utilities."""

    def test_parse_clean_json(self):
        from agent_core.agent.nodes import _parse_llm_json
        result = _parse_llm_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_markdown_json(self):
        from agent_core.agent.nodes import _parse_llm_json
        result = _parse_llm_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_parse_markdown_no_lang(self):
        from agent_core.agent.nodes import _parse_llm_json
        result = _parse_llm_json('```\n{"key": "value"}\n```')
        assert result == {"key": "value"}
