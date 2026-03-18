"""Unit tests for all schema definitions.

Tests cover:
- Valid data serialization/deserialization
- Invalid data rejection
- Edge cases (empty, max-size, boundary values)
- LLM representation generation
- Schema round-trips (model → dict → model)
"""

import json
import pytest
from pathlib import Path
from pydantic import ValidationError

from agent_core.schemas.dom import DOMElement, PageContext, ElementType, BoundingBox
from agent_core.schemas.actions import Action, ActionType, ActionResult, ActionStatus
from agent_core.schemas.agent import (
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
from agent_core.schemas.messages import (
    InputFieldDefinition,
    InputFieldType,
    InterruptMessage,
    InterruptResponse,
    WSMessageType,
    GoalMessage,
    ReasoningMessage,
    ActionRequestMessage,
    StatusMessage,
    ErrorMessage,
    DoneMessage,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "dom_snapshots"


# ============================================================
# DOM Schema Tests
# ============================================================

class TestDOMElement:
    """Tests for DOMElement model."""

    def test_create_valid_element(self, sample_button):
        assert sample_button.element_id == 1
        assert sample_button.element_type == ElementType.BUTTON
        assert sample_button.text == "Add to Cart"
        assert sample_button.is_visible is True
        assert sample_button.is_enabled is True

    def test_element_with_all_fields(self):
        element = DOMElement(
            element_id=99,
            element_type=ElementType.TEXT_INPUT,
            tag_name="input",
            text="",
            attributes={"placeholder": "Enter email", "name": "email", "type": "email"},
            is_visible=True,
            is_enabled=True,
            is_focused=True,
            bounding_box=BoundingBox(x=100, y=200, width=300, height=40),
            parent_context="inside form: Login",
            children_count=0,
            css_selector="input#email",
            xpath="//input[@name='email']",
        )
        assert element.is_focused is True
        assert element.bounding_box.width == 300

    def test_element_defaults(self):
        """Minimal element with only required fields."""
        element = DOMElement(
            element_id=1,
            element_type=ElementType.OTHER,
            tag_name="div",
        )
        assert element.text == ""
        assert element.attributes == {}
        assert element.is_visible is True
        assert element.is_enabled is True
        assert element.is_focused is False
        assert element.bounding_box is None

    def test_llm_representation_button(self, sample_button):
        repr_str = sample_button.to_llm_representation()
        assert "[1]" in repr_str
        assert "button" in repr_str
        assert "Add to Cart" in repr_str
        assert "visible" in repr_str
        assert "enabled" in repr_str

    def test_llm_representation_input(self, sample_input):
        repr_str = sample_input.to_llm_representation()
        assert "[2]" in repr_str
        assert "text_input" in repr_str
        assert "Search products..." in repr_str

    def test_llm_representation_disabled(self, disabled_button):
        repr_str = disabled_button.to_llm_representation()
        assert "disabled" in repr_str

    def test_llm_representation_hidden(self, hidden_element):
        repr_str = hidden_element.to_llm_representation()
        assert "hidden" in repr_str

    def test_llm_representation_long_text_truncated(self):
        element = DOMElement(
            element_id=1,
            element_type=ElementType.PARAGRAPH,
            tag_name="p",
            text="A" * 200,
        )
        repr_str = element.to_llm_representation()
        assert "..." in repr_str
        assert len(repr_str) < 300

    def test_element_serialization_roundtrip(self, sample_button):
        data = sample_button.model_dump()
        restored = DOMElement.model_validate(data)
        assert restored == sample_button

    def test_element_json_roundtrip(self, sample_button):
        json_str = sample_button.model_dump_json()
        restored = DOMElement.model_validate_json(json_str)
        assert restored == sample_button


class TestPageContext:
    """Tests for PageContext model."""

    def test_create_valid_page_context(self, sample_page_context):
        assert sample_page_context.url == "https://example-shop.com/products"
        assert len(sample_page_context.elements) == 6

    def test_interactive_elements_filter(self, sample_page_context):
        interactive = sample_page_context.interactive_elements
        # Should include: button (enabled), input, link
        # Should exclude: disabled button, hidden button (still enabled but hidden is ok for interaction), heading
        # Note: hidden_element is enabled but not visible — interactive_elements filters by type and enabled, not visibility
        element_types = {e.element_type for e in interactive}
        assert ElementType.HEADING not in element_types
        # Disabled button should be excluded
        disabled = [e for e in interactive if not e.is_enabled]
        assert len(disabled) == 0

    def test_empty_page_context(self, empty_page_context):
        assert len(empty_page_context.elements) == 0
        assert len(empty_page_context.interactive_elements) == 0

    def test_llm_representation(self, sample_page_context):
        repr_str = sample_page_context.to_llm_representation()
        assert "Current Page" in repr_str
        assert "example-shop.com" in repr_str
        assert "Interactive Elements" in repr_str
        assert "Add to Cart" in repr_str

    def test_llm_representation_empty_page(self, empty_page_context):
        repr_str = empty_page_context.to_llm_representation()
        assert "no interactive elements found" in repr_str

    def test_page_context_from_json_fixture(self):
        """Load a real DOM snapshot fixture and validate."""
        fixture_path = FIXTURES_DIR / "google_search.json"
        with open(fixture_path) as f:
            data = json.load(f)
        page = PageContext.model_validate(data)
        assert page.url == "https://www.google.com"
        assert page.title == "Google"
        assert len(page.elements) >= 5
        assert len(page.forms) == 1
        assert page.forms[0]["name"] == "Search"

    def test_wikipedia_fixture(self):
        fixture_path = FIXTURES_DIR / "wikipedia_article.json"
        with open(fixture_path) as f:
            data = json.load(f)
        page = PageContext.model_validate(data)
        assert "wikipedia" in page.url.lower()
        assert len(page.elements) > 5
        assert page.has_more_content_below is True

    def test_contact_form_fixture(self):
        fixture_path = FIXTURES_DIR / "contact_form.json"
        with open(fixture_path) as f:
            data = json.load(f)
        page = PageContext.model_validate(data)
        assert len(page.forms) == 1
        assert page.forms[0]["name"] == "Contact"
        # Should have multiple form fields
        form_field_ids = page.forms[0]["field_ids"]
        assert len(form_field_ids) >= 5

    def test_serialization_roundtrip(self, sample_page_context):
        data = sample_page_context.model_dump()
        restored = PageContext.model_validate(data)
        assert restored.url == sample_page_context.url
        assert len(restored.elements) == len(sample_page_context.elements)


# ============================================================
# Action Schema Tests
# ============================================================

class TestAction:
    """Tests for Action model."""

    def test_create_click_action(self, sample_click_action):
        assert sample_click_action.action_type == ActionType.CLICK
        assert sample_click_action.element_id == 1
        assert sample_click_action.confidence == 0.9

    def test_create_type_action(self, sample_type_action):
        assert sample_type_action.action_type == ActionType.TYPE_TEXT
        assert sample_type_action.value == "pizza"

    def test_create_navigate_action(self, sample_navigate_action):
        assert sample_navigate_action.action_type == ActionType.NAVIGATE
        assert sample_navigate_action.value == "https://dominos.com"
        assert sample_navigate_action.element_id is None

    def test_confidence_bounds(self):
        """Confidence must be between 0.0 and 1.0."""
        with pytest.raises(ValidationError):
            Action(
                action_type=ActionType.CLICK,
                element_id=1,
                confidence=1.5,
            )
        with pytest.raises(ValidationError):
            Action(
                action_type=ActionType.CLICK,
                element_id=1,
                confidence=-0.1,
            )

    def test_all_action_types_exist(self):
        """Verify all expected action types are defined."""
        expected = {
            "click", "type_text", "clear_and_type", "select_option",
            "check", "uncheck", "hover", "navigate", "go_back",
            "go_forward", "refresh", "scroll_down", "scroll_up",
            "scroll_to_element", "press_key", "key_combo",
            "new_tab", "close_tab", "switch_tab",
            "extract_text", "extract_table", "take_screenshot",
            "wait", "done",
        }
        actual = {at.value for at in ActionType}
        assert expected == actual

    def test_action_serialization_roundtrip(self, sample_click_action):
        data = sample_click_action.model_dump()
        restored = Action.model_validate(data)
        assert restored == sample_click_action


class TestActionResult:
    """Tests for ActionResult model."""

    def test_success_result(self, sample_action_result_success):
        assert sample_action_result_success.status == ActionStatus.SUCCESS
        assert sample_action_result_success.page_changed is True
        assert sample_action_result_success.error is None

    def test_failure_result(self, sample_action_result_failure):
        assert sample_action_result_failure.status == ActionStatus.ELEMENT_NOT_FOUND
        assert sample_action_result_failure.error is not None
        assert sample_action_result_failure.page_changed is False

    def test_all_status_types(self):
        expected = {
            "success", "failed", "element_not_found", "element_not_visible",
            "element_disabled", "timeout", "navigation_error", "blocked",
            "cancelled_by_user",
        }
        actual = {s.value for s in ActionStatus}
        assert expected == actual


# ============================================================
# Agent State Schema Tests
# ============================================================

class TestGoal:
    """Tests for Goal model."""

    def test_create_goal(self, sample_goal):
        assert sample_goal.original_text == "Search for pizza restaurants near me"
        assert len(sample_goal.sub_goals) == 4
        assert sample_goal.is_achievable is True

    def test_minimal_goal(self):
        goal = Goal(original_text="click the button")
        assert goal.interpreted_goal == ""
        assert goal.sub_goals == []
        assert goal.complexity == "medium"


class TestPlan:
    """Tests for Plan model."""

    def test_create_plan(self, sample_plan):
        assert len(sample_plan.steps) == 3
        assert sample_plan.current_step_index == 1
        assert sample_plan.plan_version == 1

    def test_plan_progress(self, sample_plan):
        progress = sample_plan.progress
        # 1 completed out of 3
        assert abs(progress - 1 / 3) < 0.01

    def test_current_step(self, sample_plan):
        current = sample_plan.current_step
        assert current is not None
        assert current.step_id == 2
        assert current.status == StepStatus.IN_PROGRESS

    def test_empty_plan(self):
        plan = Plan()
        assert plan.progress == 0.0
        assert plan.current_step is None
        assert plan.completed_steps == []

    def test_plan_step_dependencies(self, sample_plan):
        step_3 = sample_plan.steps[2]
        assert 2 in step_3.depends_on

    def test_plan_step_max_attempts(self):
        step = PlanStep(
            step_id=1,
            description="Test step",
            attempts=3,
            max_attempts=3,
        )
        assert step.attempts >= step.max_attempts


class TestReasoningTrace:
    def test_create_trace(self):
        trace = ReasoningTrace(
            step_number=1,
            thought="I see a search box at element [1]",
            observation="The search box is empty and focused",
            conclusion="I should type the search query",
            confidence=0.85,
        )
        assert trace.confidence == 0.85


class TestSelfCritique:
    def test_create_critique(self):
        critique = SelfCritique(
            target="plan",
            critique="The plan doesn't account for potential popups",
            severity="warning",
            suggestion="Add a step to dismiss popups if they appear",
            should_re_plan=False,
        )
        assert critique.severity == "warning"
        assert critique.should_re_plan is False

    def test_critical_critique(self):
        critique = SelfCritique(
            target="strategy",
            critique="Wrong website entirely",
            severity="critical",
            should_re_plan=True,
        )
        assert critique.should_re_plan is True


class TestEvaluation:
    def test_positive_evaluation(self):
        eval_ = Evaluation(
            action_succeeded=True,
            goal_progress="Step 2/5 complete. Search results showing.",
            progress_percentage=0.4,
            should_continue=True,
        )
        assert eval_.should_re_plan is False

    def test_negative_evaluation_triggers_replan(self):
        eval_ = Evaluation(
            action_succeeded=False,
            goal_progress="Wrong page loaded",
            unexpected_results="Redirected to login page",
            should_continue=True,
            should_re_plan=True,
            re_plan_reason="Need to handle login first",
        )
        assert eval_.should_re_plan is True


class TestRetryContext:
    def test_retry_tracking(self):
        ctx = RetryContext(
            attempt_number=2,
            max_attempts=3,
            failed_strategies=["click by element_id", "click by text"],
            last_error="Element not found",
        )
        assert len(ctx.failed_strategies) == 2
        assert ctx.attempt_number < ctx.max_attempts

    def test_escalation_on_max_retries(self):
        ctx = RetryContext(
            attempt_number=3,
            max_attempts=3,
            escalation_needed=True,
        )
        assert ctx.escalation_needed is True


class TestCreateInitialState:
    def test_creates_valid_state(self, sample_initial_state):
        state = sample_initial_state
        assert state["goal"].original_text == "Search for pizza restaurants near me"
        assert state["cognitive_status"] == CognitiveStatus.ANALYZING_GOAL
        assert state["iteration_count"] == 0
        assert state["should_terminate"] is False
        assert state["model_name"] == "qwen2.5:32b-instruct"

    def test_state_with_defaults(self):
        state = create_initial_state(goal_text="do something")
        assert state["plan"].steps == []
        assert state["action_history"] == []
        assert state["reasoning_traces"] == []
        assert state["error"] is None

    def test_state_with_custom_config(self):
        state = create_initial_state(
            goal_text="test",
            model_name="gpt-4o",
            max_iterations=10,
            auto_confirm=True,
            confidence_threshold=0.8,
        )
        assert state["model_name"] == "gpt-4o"
        assert state["max_iterations"] == 10
        assert state["auto_confirm"] is True
        assert state["confidence_threshold"] == 0.8


# ============================================================
# Message Schema Tests
# ============================================================

class TestInputFieldDefinition:
    def test_text_field(self):
        field = InputFieldDefinition(
            field_id="name",
            field_type=InputFieldType.TEXT,
            label="Your Name",
            placeholder="Enter your name",
        )
        assert field.field_type == InputFieldType.TEXT
        assert field.required is True

    def test_select_field(self):
        field = InputFieldDefinition(
            field_id="size",
            field_type=InputFieldType.SELECT,
            label="Size",
            options=["Small", "Medium", "Large"],
            default_value="Medium",
        )
        assert len(field.options) == 3
        assert field.default_value == "Medium"

    def test_confirm_field(self):
        field = InputFieldDefinition(
            field_id="confirm",
            field_type=InputFieldType.CONFIRM,
            label="Proceed?",
            options=["Yes", "No"],
        )
        assert field.field_type == InputFieldType.CONFIRM

    def test_all_field_types(self):
        expected = {
            "text", "textarea", "number", "select", "multi_select",
            "confirm", "date", "password", "url", "email", "radio", "toggle",
        }
        actual = {ft.value for ft in InputFieldType}
        assert expected == actual


class TestWSMessages:
    def test_goal_message(self):
        msg = GoalMessage(
            goal="Search for pizza",
            session_id="sess_001",
        )
        assert msg.type == WSMessageType.CLIENT_GOAL
        assert msg.goal == "Search for pizza"

    def test_reasoning_message_streaming(self):
        msg = ReasoningMessage(
            type=WSMessageType.SERVER_REASONING,
            content="I see a search box",
            is_streaming=True,
            is_final=False,
            reasoning_type="thinking",
        )
        assert msg.is_streaming is True
        assert msg.is_final is False

    def test_interrupt_message(self, sample_confirm_interrupt):
        assert sample_confirm_interrupt.type == WSMessageType.SERVER_INTERRUPT
        assert len(sample_confirm_interrupt.fields) == 1
        assert sample_confirm_interrupt.fields[0].field_type == InputFieldType.CONFIRM

    def test_interrupt_with_multiple_fields(self, sample_input_interrupt):
        assert len(sample_input_interrupt.fields) == 2
        types = {f.field_type for f in sample_input_interrupt.fields}
        assert InputFieldType.TEXT in types

    def test_interrupt_response(self):
        resp = InterruptResponse(
            interrupt_id="int_001",
            values={"confirm": "Yes, place order"},
        )
        assert resp.type == WSMessageType.CLIENT_USER_RESPONSE

    def test_done_message(self):
        msg = DoneMessage(
            type=WSMessageType.SERVER_DONE,
            success=True,
            summary="Successfully searched for pizza restaurants",
            steps_completed=3,
            steps_total=3,
            total_actions=5,
        )
        assert msg.success is True
        assert msg.steps_completed == msg.steps_total

    def test_error_message(self):
        msg = ErrorMessage(
            type=WSMessageType.SERVER_ERROR,
            message="LLM returned invalid response",
            recoverable=True,
            suggestion="Retrying with different prompt",
        )
        assert msg.recoverable is True

    def test_status_message(self):
        msg = StatusMessage(
            type=WSMessageType.SERVER_STATUS,
            cognitive_status=CognitiveStatus.REASONING,
            message="Analyzing page elements",
            iteration=3,
            plan_progress=0.4,
        )
        assert msg.cognitive_status == CognitiveStatus.REASONING

    def test_message_serialization_roundtrip(self, sample_confirm_interrupt):
        data = sample_confirm_interrupt.model_dump()
        restored = InterruptMessage.model_validate(data)
        assert restored.interrupt_id == sample_confirm_interrupt.interrupt_id
        assert len(restored.fields) == len(sample_confirm_interrupt.fields)


# ============================================================
# Edge Case Tests
# ============================================================

class TestEdgeCases:
    def test_page_with_500_elements(self):
        """Ensure schema handles large numbers of elements."""
        elements = [
            DOMElement(
                element_id=i,
                element_type=ElementType.BUTTON,
                tag_name="button",
                text=f"Button {i}",
            )
            for i in range(500)
        ]
        page = PageContext(
            url="https://example.com",
            title="Many Buttons",
            elements=elements,
        )
        assert len(page.elements) == 500
        repr_str = page.to_llm_representation()
        assert "500 available" in repr_str

    def test_element_with_empty_text(self):
        element = DOMElement(
            element_id=1,
            element_type=ElementType.ICON_BUTTON,
            tag_name="button",
            text="",
            attributes={"aria-label": "Close dialog"},
        )
        repr_str = element.to_llm_representation()
        assert "aria-label" in repr_str
        assert "Close dialog" in repr_str

    def test_element_with_very_long_href(self):
        element = DOMElement(
            element_id=1,
            element_type=ElementType.LINK,
            tag_name="a",
            text="Click here",
            attributes={"href": "https://example.com/" + "a" * 200},
        )
        repr_str = element.to_llm_representation()
        assert "..." in repr_str

    def test_nested_form_structure(self):
        page = PageContext(
            url="https://example.com",
            title="Nested Forms",
            elements=[],
            forms=[
                {"name": "Login", "action": "/login", "method": "POST", "field_ids": [1, 2, 3]},
                {"name": "Search", "action": "/search", "method": "GET", "field_ids": [4]},
            ],
        )
        assert len(page.forms) == 2

    def test_cognitive_status_values(self):
        """All cognitive statuses should be defined."""
        expected_statuses = {
            "analyzing_goal", "creating_plan", "reasoning", "deciding",
            "awaiting_confirmation", "executing", "observing", "evaluating",
            "self_critiquing", "re_planning", "retrying", "asking_user",
            "gathering_info", "completed", "failed", "cancelled",
        }
        actual = {s.value for s in CognitiveStatus}
        assert expected_statuses == actual

    def test_task_memory_accumulation(self):
        memory = TaskMemory()
        memory.observations.append("Site uses modal popups")
        memory.observations.append("Search results load dynamically")
        memory.discovered_patterns.append("Navigation is in top bar")
        memory.user_preferences["pizza_type"] = "vegetarian"
        memory.important_data["order_id"] = "12345"
        memory.pages_visited.append("https://dominos.com")

        assert len(memory.observations) == 2
        assert memory.user_preferences["pizza_type"] == "vegetarian"
