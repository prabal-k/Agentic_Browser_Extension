"""Shared test fixtures for the agentic browser extension backend."""

import pytest
import json
from pathlib import Path

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
    WSMessageType,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "dom_snapshots"


# ============================================================
# DOM Element fixtures
# ============================================================

@pytest.fixture
def sample_button() -> DOMElement:
    """A simple button element."""
    return DOMElement(
        element_id=1,
        element_type=ElementType.BUTTON,
        tag_name="button",
        text="Add to Cart",
        attributes={"aria-label": "Add item to shopping cart"},
        is_visible=True,
        is_enabled=True,
        parent_context="inside form: Product Details",
        css_selector="button.add-to-cart",
    )


@pytest.fixture
def sample_input() -> DOMElement:
    """A text input element."""
    return DOMElement(
        element_id=2,
        element_type=ElementType.TEXT_INPUT,
        tag_name="input",
        text="",
        attributes={
            "placeholder": "Search products...",
            "name": "search",
            "type": "text",
        },
        is_visible=True,
        is_enabled=True,
        parent_context="inside nav bar",
        css_selector="input#search-box",
    )


@pytest.fixture
def sample_link() -> DOMElement:
    """A navigation link."""
    return DOMElement(
        element_id=3,
        element_type=ElementType.LINK,
        tag_name="a",
        text="View All Products",
        attributes={"href": "/products"},
        is_visible=True,
        is_enabled=True,
        parent_context="inside nav bar",
    )


@pytest.fixture
def disabled_button() -> DOMElement:
    """A disabled button (agent should not interact with)."""
    return DOMElement(
        element_id=4,
        element_type=ElementType.BUTTON,
        tag_name="button",
        text="Checkout",
        is_visible=True,
        is_enabled=False,
        parent_context="inside form: Cart",
    )


@pytest.fixture
def hidden_element() -> DOMElement:
    """A hidden element (not in viewport)."""
    return DOMElement(
        element_id=5,
        element_type=ElementType.BUTTON,
        tag_name="button",
        text="Load More",
        is_visible=False,
        is_enabled=True,
    )


# ============================================================
# Page Context fixtures
# ============================================================

@pytest.fixture
def sample_page_context(
    sample_button, sample_input, sample_link, disabled_button, hidden_element
) -> PageContext:
    """A complete page context with mixed elements."""
    return PageContext(
        url="https://example-shop.com/products",
        title="Example Shop - Products",
        meta_description="Browse our product catalog",
        page_text_summary="Welcome to Example Shop. Browse our wide selection of products.",
        elements=[
            sample_button,
            sample_input,
            sample_link,
            disabled_button,
            hidden_element,
            DOMElement(
                element_id=6,
                element_type=ElementType.HEADING,
                tag_name="h1",
                text="Our Products",
                is_visible=True,
            ),
        ],
        viewport_width=1920,
        viewport_height=1080,
        scroll_position=0.0,
        has_more_content_below=True,
        timestamp=1700000000.0,
    )


@pytest.fixture
def empty_page_context() -> PageContext:
    """A page with no interactive elements."""
    return PageContext(
        url="https://example.com/empty",
        title="Empty Page",
        elements=[],
    )


@pytest.fixture
def search_page_context() -> PageContext:
    """Google-like search page for testing search tasks."""
    return PageContext(
        url="https://www.google.com",
        title="Google",
        page_text_summary="Google Search",
        elements=[
            DOMElement(
                element_id=1,
                element_type=ElementType.TEXT_INPUT,
                tag_name="input",
                text="",
                attributes={
                    "placeholder": "Search Google or type a URL",
                    "name": "q",
                    "aria-label": "Search",
                },
                is_visible=True,
                is_enabled=True,
                parent_context="inside form: Search",
                css_selector="input[name='q']",
            ),
            DOMElement(
                element_id=2,
                element_type=ElementType.BUTTON,
                tag_name="input",
                text="Google Search",
                attributes={"type": "submit", "name": "btnK"},
                is_visible=True,
                is_enabled=True,
                parent_context="inside form: Search",
            ),
            DOMElement(
                element_id=3,
                element_type=ElementType.BUTTON,
                tag_name="input",
                text="I'm Feeling Lucky",
                attributes={"type": "submit", "name": "btnI"},
                is_visible=True,
                is_enabled=True,
                parent_context="inside form: Search",
            ),
            DOMElement(
                element_id=4,
                element_type=ElementType.LINK,
                tag_name="a",
                text="Gmail",
                attributes={"href": "https://mail.google.com"},
                is_visible=True,
                is_enabled=True,
                parent_context="inside nav bar",
            ),
            DOMElement(
                element_id=5,
                element_type=ElementType.LINK,
                tag_name="a",
                text="Images",
                attributes={"href": "https://images.google.com"},
                is_visible=True,
                is_enabled=True,
                parent_context="inside nav bar",
            ),
        ],
    )


# ============================================================
# Action fixtures
# ============================================================

@pytest.fixture
def sample_click_action() -> Action:
    return Action(
        action_id="act_001",
        action_type=ActionType.CLICK,
        element_id=1,
        description="Click 'Add to Cart' button",
        reasoning="Element [1] is the add to cart button for the current product",
        confidence=0.9,
        requires_confirmation=True,
        risk_level="low",
    )


@pytest.fixture
def sample_type_action() -> Action:
    return Action(
        action_id="act_002",
        action_type=ActionType.TYPE_TEXT,
        element_id=2,
        value="pizza",
        description="Type 'pizza' in search box",
        reasoning="Element [2] is the search input field",
        confidence=0.95,
        risk_level="low",
    )


@pytest.fixture
def sample_navigate_action() -> Action:
    return Action(
        action_id="act_003",
        action_type=ActionType.NAVIGATE,
        value="https://dominos.com",
        description="Navigate to Dominos website",
        confidence=0.99,
        risk_level="low",
    )


@pytest.fixture
def sample_action_result_success() -> ActionResult:
    return ActionResult(
        action_id="act_001",
        status=ActionStatus.SUCCESS,
        message="Button clicked successfully",
        page_changed=True,
        new_url="https://example-shop.com/cart",
        execution_time_ms=150.0,
    )


@pytest.fixture
def sample_action_result_failure() -> ActionResult:
    return ActionResult(
        action_id="act_001",
        status=ActionStatus.ELEMENT_NOT_FOUND,
        message="Element not found on page",
        error="Element with ID 1 no longer exists in the DOM",
        page_changed=False,
        execution_time_ms=50.0,
    )


# ============================================================
# Agent state fixtures
# ============================================================

@pytest.fixture
def sample_goal() -> Goal:
    return Goal(
        original_text="Search for pizza restaurants near me",
        interpreted_goal="Navigate to Google, search for 'pizza restaurants near me', and present the results",
        sub_goals=[
            "Open Google search page",
            "Type search query",
            "Submit search",
            "Review results",
        ],
        success_criteria=[
            "Google search results page is displayed",
            "Results contain pizza restaurant listings",
        ],
        constraints=["Do not click on any ads"],
        complexity="simple",
        is_achievable=True,
    )


@pytest.fixture
def sample_plan() -> Plan:
    return Plan(
        steps=[
            PlanStep(
                step_id=1,
                description="Navigate to google.com",
                expected_outcome="Google homepage is loaded",
                status=StepStatus.COMPLETED,
            ),
            PlanStep(
                step_id=2,
                description="Type 'pizza restaurants near me' in search box",
                expected_outcome="Search query is entered in the search input",
                status=StepStatus.IN_PROGRESS,
                depends_on=[1],
            ),
            PlanStep(
                step_id=3,
                description="Click the search button",
                expected_outcome="Search results page is displayed",
                status=StepStatus.PENDING,
                depends_on=[2],
            ),
        ],
        current_step_index=1,
        plan_version=1,
        original_reasoning="Simple 3-step search task on Google",
    )


@pytest.fixture
def sample_initial_state(search_page_context):
    """A fully initialized agent state ready for testing."""
    return create_initial_state(
        goal_text="Search for pizza restaurants near me",
        page_context=search_page_context,
        model_name="qwen2.5:32b-instruct",
    )


# ============================================================
# Interrupt fixtures
# ============================================================

@pytest.fixture
def sample_confirm_interrupt() -> InterruptMessage:
    """Interrupt asking user to confirm an action."""
    return InterruptMessage(
        type=WSMessageType.SERVER_INTERRUPT,
        interrupt_id="int_001",
        title="Confirm Action",
        context="The agent wants to submit the order form.",
        fields=[
            InputFieldDefinition(
                field_id="confirm",
                field_type=InputFieldType.CONFIRM,
                label="Proceed with order?",
                description="Total: $15.99 for 1 medium pizza",
                options=["Yes, place order", "No, cancel"],
            ),
        ],
        urgency="warning",
    )


@pytest.fixture
def sample_input_interrupt() -> InterruptMessage:
    """Interrupt asking user for text input."""
    return InterruptMessage(
        type=WSMessageType.SERVER_INTERRUPT,
        interrupt_id="int_002",
        title="Information Needed",
        context="The agent needs your delivery address to continue.",
        fields=[
            InputFieldDefinition(
                field_id="address",
                field_type=InputFieldType.TEXT,
                label="Delivery Address",
                placeholder="Enter your full address",
                required=True,
            ),
            InputFieldDefinition(
                field_id="phone",
                field_type=InputFieldType.TEXT,
                label="Phone Number",
                placeholder="10-digit phone number",
                validation_pattern=r"^\d{10}$",
                required=True,
            ),
        ],
        urgency="normal",
    )


@pytest.fixture
def sample_select_interrupt() -> InterruptMessage:
    """Interrupt asking user to choose from options."""
    return InterruptMessage(
        type=WSMessageType.SERVER_INTERRUPT,
        interrupt_id="int_003",
        title="Choose Option",
        context="Multiple pizza sizes are available.",
        fields=[
            InputFieldDefinition(
                field_id="size",
                field_type=InputFieldType.SELECT,
                label="Pizza Size",
                options=["Small", "Medium", "Large", "Extra Large"],
                default_value="Medium",
            ),
            InputFieldDefinition(
                field_id="crust",
                field_type=InputFieldType.RADIO,
                label="Crust Type",
                options=["Thin", "Regular", "Stuffed"],
                default_value="Regular",
            ),
        ],
        urgency="normal",
    )
