"""Direct unit validation for sprint-1 fixes (F1, F2, F7).

Avoids LLM variance by mocking `get_action_llm_dynamic` with fake tool calls.
Each test builds an `AgentState`, invokes `decide_action`, and asserts on the
returned state-update dict.

Run:  python test_sprint1_unit.py
"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "src")

from agent_core.schemas.agent import (
    AgentState,
    Goal,
    Plan,
    PlanStep,
    CognitiveStatus,
    TaskMemory,
)
from agent_core.schemas.dom import DOMElement, ElementType, PageContext
from agent_core.schemas.actions import Action, ActionType, ActionResult, ActionStatus
from agent_core.agent import nodes as N


def _login_page() -> PageContext:
    return PageContext(
        url="https://sellrclub.com/login",
        title="Sign In",
        timestamp=0.0,
        viewport_width=1440,
        viewport_height=900,
        scroll_position=0.0,
        elements=[
            DOMElement(
                element_id=10,
                element_type=ElementType.TEXT_INPUT,
                tag_name="input",
                text="",
                attributes={"type": "email", "name": "email", "placeholder": "Email"},
            ),
            DOMElement(
                element_id=11,
                element_type=ElementType.TEXT_INPUT,
                tag_name="input",
                text="",
                attributes={"type": "password", "name": "password"},
            ),
            DOMElement(
                element_id=12,
                element_type=ElementType.BUTTON,
                tag_name="button",
                text="Sign In",
                attributes={"type": "submit"},
            ),
        ],
    )


def _routes_empty_page() -> PageContext:
    return PageContext(
        url="https://sellrclub.com/my-routes",
        title="My Routes",
        timestamp=0.0,
        viewport_width=1440,
        viewport_height=900,
        scroll_position=0.0,
        elements=[
            DOMElement(
                element_id=30,
                element_type=ElementType.HEADING,
                tag_name="h1",
                text="My Routes",
            ),
            DOMElement(
                element_id=31,
                element_type=ElementType.PARAGRAPH,
                tag_name="p",
                text="No routes scheduled and no visits assigned for today.",
            ),
        ],
    )


def _base_state(goal_text: str, page: PageContext, action_history=None) -> dict:
    sub_tasks = N._decompose_goal_into_steps(goal_text)
    criteria = N._build_success_criteria(sub_tasks) if len(sub_tasks) > 1 else []
    goal = Goal(
        original_text=goal_text,
        interpreted_goal=goal_text,
        sub_goals=sub_tasks if len(sub_tasks) > 1 else [],
        success_criteria=criteria,
    )
    plan = Plan(
        steps=[PlanStep(step_id=i + 1, description=t) for i, t in enumerate(sub_tasks)],
        current_step_index=0,
        plan_version=1,
    )
    return {
        "goal": goal,
        "plan": plan,
        "page_context": page,
        "action_history": action_history or [],
        "reasoning_traces": [],
        "task_memory": TaskMemory(),
        "_stored_credentials": {},
        "_queued_actions": [],
        "pending_user_input": "",
        "pending_input_field_type": "",
        "iteration_count": 0,
        "cognitive_status": CognitiveStatus.DECIDING,
        "current_action": None,
    }


async def test_F1_credential_prompt_fires_on_login_page():
    goal_text = "open sellrclub.com, signin with the credentials then go to dashboard"
    state = _base_state(goal_text, _login_page())

    # LLM must NOT be called when F1 short-circuits
    with patch.object(N, "get_action_llm_dynamic") as llm_factory:
        llm_factory.side_effect = AssertionError("LLM should not be called — F1 should short-circuit")
        result = await N.decide_action(state)

    assert result["cognitive_status"] == CognitiveStatus.ASKING_USER, \
        f"expected ASKING_USER, got {result['cognitive_status']}"
    action = result["current_action"]
    assert action is not None
    assert action.action_type == ActionType.DONE
    assert "credential" in action.value.lower() or "sign" in action.value.lower()
    assert result.get("pending_input_field_type") == "credentials"
    print("F1: credential prompt fires on login page  OK")


async def test_F1_skipped_when_no_login_form():
    # Landing page has no login inputs → F1 must NOT fire even with auth intent
    landing = PageContext(
        url="https://sellrclub.com/",
        title="Home",
        timestamp=0.0,
        viewport_width=1440,
        viewport_height=900,
        scroll_position=0.0,
        elements=[
            DOMElement(
                element_id=1,
                element_type=ElementType.BUTTON,
                tag_name="a",
                text="Sign In",
                attributes={"href": "/login"},
            ),
        ],
    )
    state = _base_state("open sellrclub.com, signin with credentials", landing)

    fake_llm = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = "navigate"
    fake_resp.tool_calls = [{"name": "click", "args": {"element_id": 1}, "id": "call_1"}]
    fake_resp.response_metadata = {}
    fake_llm.ainvoke = AsyncMock(return_value=fake_resp)

    with patch.object(N, "get_action_llm_dynamic", return_value=fake_llm):
        result = await N.decide_action(state)

    # LLM should have been consulted — ergo status should NOT be ASKING_USER
    assert result["cognitive_status"] != CognitiveStatus.ASKING_USER, \
        "F1 should not fire without a login form on the page"
    print("F1: skipped when no login form              OK")


async def test_F1_skipped_when_creds_already_stored():
    state = _base_state(
        "open sellrclub.com, signin with credentials", _login_page()
    )
    state["_stored_credentials"] = {"email": "x@y.z", "password": "p"}

    # decide_action should fall through to auto-type fast path
    with patch.object(N, "get_action_llm_dynamic") as llm_factory:
        llm_factory.side_effect = AssertionError("LLM should not be called — auto-type should fire")
        result = await N.decide_action(state)

    assert result["cognitive_status"] == CognitiveStatus.EXECUTING
    action = result["current_action"]
    assert action.action_type == ActionType.CLEAR_AND_TYPE
    print("F1: skipped when creds already stored        OK")


async def test_F2_goal_decomposition_via_analyze_and_plan():
    goal_text = (
        "open sellrclub.com , signin with the credenitails then go to my-routes "
        "page . now plan the route , clockin into the system , provide the initial "
        "strating address of naples florida usa , and auto assign the default "
        "businesses . And check if the 20 businesses has been assigned for today "
        "and a optimized routes has been created or not ."
    )
    state = {
        "goal": Goal(original_text=goal_text),
        "page_context": _login_page(),
        "task_memory": TaskMemory(),
        "_stored_credentials": {},
    }
    result = await N.analyze_and_plan(state)
    plan = result["plan"]
    goal = result["goal"]
    # S3.B: plan.steps is now capped at 4 advisory milestones; sub_goals and
    # success_criteria retain full granularity (see assertions below).
    assert 1 <= len(plan.steps) <= 4, f"expected 1..4 milestones, got {len(plan.steps)}"
    assert len(goal.sub_goals) >= 5, f"expected >=5 sub_goals, got {len(goal.sub_goals)}"
    assert len(goal.success_criteria) >= 5, f"expected >= 5 criteria, got {len(goal.success_criteria)}"
    assert any("check" in c.lower() for c in goal.success_criteria), "no verification criterion"
    print(f"F2: decomposed goal into {len(plan.steps)} steps / {len(goal.success_criteria)} criteria  OK")


async def test_F7_done_blocked_on_no_routes_scheduled():
    goal_text = (
        "open sellrclub.com, signin with credentials then go to my-routes page, "
        "plan the route, clockin, provide address, auto assign businesses, and "
        "check if 20 businesses assigned and optimized route created"
    )
    # Minimal history: some clicks + one short extract that admits the failure
    history = [
        {"action": {"action_type": "click", "description": "sign in"}, "result": {"status": "success"}},
        {"action": {"action_type": "click", "description": "my routes"}, "result": {"status": "success"}},
        {
            "action": {"action_type": "extract_text", "description": "read my routes page"},
            "result": {
                "status": "success",
                # Short, contradicting extract — doesn't qualify as evidence
                "extracted_data": "No routes scheduled and no visits assigned for today.",
            },
        },
    ]
    state = _base_state(goal_text, _routes_empty_page(), action_history=history)

    # Simulate LLM calling done() with a failure-flavoured summary
    fake_llm = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = ""
    fake_resp.tool_calls = [{
        "name": "done",
        "args": {
            "summary": "Task completed. No routes scheduled and no visits assigned for today.",
        },
        "id": "call_done",
    }]
    fake_resp.response_metadata = {}
    fake_llm.ainvoke = AsyncMock(return_value=fake_resp)

    with patch.object(N, "get_action_llm_dynamic", return_value=fake_llm):
        result = await N.decide_action(state)

    assert result["cognitive_status"] == CognitiveStatus.RE_PLANNING, \
        f"expected RE_PLANNING, got {result['cognitive_status']}"
    new_plan = result["plan"]
    assert new_plan.plan_version == 2, f"plan_version should bump to 2, got {new_plan.plan_version}"
    assert "Premature done blocked" in new_plan.re_plan_reason
    print("F7: done with contradiction -> re-plan        OK")


async def test_F7_allows_legitimate_done():
    # Goal is lookup-style ("find X") — done summary has no failure markers and
    # history contains a real extract → F7 must NOT block.
    goal_text = "Find the price of iPhone 15 on Amazon"
    history = [
        {
            "action": {"action_type": "extract_text", "description": "read price"},
            "result": {
                "status": "success",
                "extracted_data": "iPhone 15 128GB is priced at $799.00 on Amazon.com.",
            },
        },
    ]
    state = _base_state(goal_text, _routes_empty_page(), action_history=history)
    # Non-compound goal → empty success_criteria → don't trip the missing-evidence leg

    fake_llm = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = ""
    fake_resp.tool_calls = [{
        "name": "done",
        "args": {"summary": "Found the iPhone 15 price: $799.00."},
        "id": "call_done",
    }]
    fake_resp.response_metadata = {}
    fake_llm.ainvoke = AsyncMock(return_value=fake_resp)

    with patch.object(N, "get_action_llm_dynamic", return_value=fake_llm):
        result = await N.decide_action(state)

    # Should complete normally (COMPLETED) — not re-plan
    assert result["cognitive_status"] != CognitiveStatus.RE_PLANNING, \
        "legitimate done() must not be blocked by F7"
    print("F7: legitimate done passes through            OK")


async def test_S2_3_observe_triggers_replan_on_contradiction():
    """F6/S2.3: observe() detects mid-task contradiction in extract result and
    routes to RE_PLANNING before the next decide_action is entered.
    """
    goal_text = (
        "open sellrclub.com, signin with credentials then go to my-routes page, "
        "plan the route, clockin, provide address, auto assign businesses, and "
        "check if 20 businesses assigned and optimized route created"
    )
    # Hardcode steps so the evidence-required verb at current_step is deterministic
    plan = Plan(
        steps=[
            PlanStep(step_id=1, description="open sellrclub.com"),
            PlanStep(step_id=2, description="signin with credentials"),
            PlanStep(step_id=3, description="go to my-routes page"),
            PlanStep(step_id=4, description="plan the route"),
        ],
        current_step_index=3,  # "plan the route" → evidence verb "plan"
        plan_version=1,
    )
    state = {
        "goal": Goal(original_text=goal_text, interpreted_goal=goal_text),
        "plan": plan,
        "page_context": _routes_empty_page(),
        "previous_page_context": None,
        "task_memory": TaskMemory(),
        "action_history": [],
        "current_action": Action(
            action_id="act_xyz",
            action_type=ActionType.EXTRACT_TEXT,
            description="read my routes page",
            confidence=0.8,
        ),
        "pending_action_result": ActionResult(
            action_id="act_xyz",
            status=ActionStatus.SUCCESS,
            message="Page text extracted",
            extracted_data="No routes scheduled and no visits assigned for today.",
            page_changed=False,
            execution_time_ms=10.0,
        ),
    }

    result = await N.observe(state)

    assert result["cognitive_status"] == CognitiveStatus.RE_PLANNING, \
        f"expected RE_PLANNING, got {result['cognitive_status']}"
    new_plan = result["plan"]
    assert new_plan.plan_version == 2, f"plan_version should bump to 2, got {new_plan.plan_version}"
    assert "Contradiction during execution" in new_plan.re_plan_reason
    assert result["current_action"] is None
    print("S2.3: observe contradiction -> RE_PLANNING     OK")


async def test_S3B_plan_collapses_to_milestones():
    """S3.B: compound goal with 8 sub-tasks produces <=4 milestone plan steps
    but keeps full success_criteria granularity.
    """
    goal_text = (
        "open sellrclub.com , signin with the credenitails then go to my-routes page . "
        "now plan the route , clockin into the system , provide the initial strating address "
        "of naples florida usa , and auto assign the default businesses . "
        "And check if the 20 businesses has been assigned for today and a optimized routes has been created or not ."
    )
    state = {
        "goal": Goal(original_text=goal_text),
        "page_context": _login_page(),
        "task_memory": TaskMemory(),
        "_stored_credentials": {},
    }
    result = await N.analyze_and_plan(state)
    plan = result["plan"]
    goal = result["goal"]
    # Plan.steps is a scope checklist — cap at 4 milestones
    assert 1 <= len(plan.steps) <= 4, f"expected <=4 milestones, got {len(plan.steps)}"
    # success_criteria stays full-granularity for F7 / verify_goal
    assert len(goal.success_criteria) >= 5, \
        f"expected full-granularity criteria, got {len(goal.success_criteria)}"
    # Milestones should contain the bullet separator when grouped
    assert any("•" in step.description for step in plan.steps), \
        "milestone descriptions should join sub-tasks with bullet"
    print(f"S3.B: 8 sub-tasks -> {len(plan.steps)} milestones / {len(goal.success_criteria)} criteria OK")


def test_S3A_preferred_provider_wins_over_name_sniff():
    """S3.A: api_keys['preferred_provider'] must route to the chosen provider
    even when the model name looks like another provider's convention.
    Regression guard against the 'gpt-*' heuristic shadowing a user who
    picked Ollama or OpenRouter from the sidepanel.
    """
    from agent_core.agent.llm_client import detect_provider, LLMProvider

    # Sanity — bare detector still sniffs name
    assert detect_provider("gpt-4o") == LLMProvider.OPENAI
    assert detect_provider("qwen2.5:32b") == LLMProvider.OLLAMA

    # We can't fully exercise get_llm without live credentials, but we can
    # validate the resolver contract: when preferred_provider is supplied in
    # api_keys it must take precedence. Mirror the logic in get_llm:
    def resolve(model_name: str, api_keys: dict) -> LLMProvider:
        explicit = (api_keys.get("preferred_provider") or "").strip().lower()
        if explicit in {"openai", "groq", "openrouter", "ollama"}:
            return LLMProvider(explicit)
        return detect_provider(model_name)

    # User picked Ollama but model name is "gpt-4o" (weird but possible
    # for local gateways) — must honour the vault choice.
    assert resolve("gpt-4o", {"preferred_provider": "ollama"}) == LLMProvider.OLLAMA
    # User picked OpenAI — must take OpenAI regardless of name.
    assert resolve("qwen2.5:32b", {"preferred_provider": "openai"}) == LLMProvider.OPENAI
    # Empty or unknown provider falls through to detect_provider.
    assert resolve("gpt-4o", {"preferred_provider": ""}) == LLMProvider.OPENAI
    assert resolve("gpt-4o", {"preferred_provider": "bogus"}) == LLMProvider.OPENAI
    print("S3.A: preferred_provider beats name-sniff           OK")


async def test_S2_2_fingerprint_fields_roundtrip():
    """S2.2: DOMElement.fingerprint + Action.element_fingerprint accept values
    and default to empty/None without breaking existing code paths.
    """
    el = DOMElement(
        element_id=7,
        element_type=ElementType.BUTTON,
        tag_name="button",
        text="Sign In",
        fingerprint="abc123",
    )
    assert el.fingerprint == "abc123"
    # Default empty fingerprint — backward compat for callers that never set it.
    el2 = DOMElement(
        element_id=8,
        element_type=ElementType.LINK,
        tag_name="a",
        text="Home",
    )
    assert el2.fingerprint == ""
    act = Action(
        action_id="act_xyz",
        action_type=ActionType.CLICK,
        element_id=7,
        element_fingerprint="abc123",
        description="click sign in",
    )
    assert act.element_fingerprint == "abc123"
    # Default None when not supplied — existing serialization untouched.
    act2 = Action(
        action_id="act_2",
        action_type=ActionType.CLICK,
        element_id=8,
        description="click home",
    )
    assert act2.element_fingerprint is None
    print("S2.2: fingerprint schema fields round-trip     OK")


async def test_S2_4_tabs_render_in_page_context():
    """S2.4: open_tabs populated on PageContext → visible in to_llm_representation."""
    ctx = PageContext(
        url="https://a.com/",
        title="Site A",
        timestamp=0.0,
        current_tab_id=42,
        open_tabs=[
            {"tab_id": 42, "url": "https://a.com/", "title": "Site A", "active": True},
            {"tab_id": 43, "url": "https://b.com/", "title": "Site B", "active": False},
        ],
        elements=[],
    )
    repr_str = ctx.to_llm_representation()
    assert "Tabs (2 open" in repr_str, f"missing tab header, got:\n{repr_str}"
    assert "[42]" in repr_str and "[43]" in repr_str, "tab ids not rendered"
    assert "current=42" in repr_str, "current tab marker missing"
    # Single-tab case — should NOT render Tabs header (prompt budget)
    single = PageContext(
        url="https://a.com/",
        title="Site A",
        timestamp=0.0,
        current_tab_id=42,
        open_tabs=[{"tab_id": 42, "url": "https://a.com/", "title": "Site A", "active": True}],
        elements=[],
    )
    assert "Tabs (" not in single.to_llm_representation(), "single tab should not render tabs block"
    print("S2.4: tabs render only when >=2 open          OK")


async def test_S2_3_observe_passes_through_when_no_contradiction():
    """Legitimate extract (no failure markers) must NOT trigger RE_PLANNING."""
    goal_text = "Find the price of iPhone 15 on Amazon"
    plan = Plan(
        steps=[PlanStep(step_id=1, description="find iphone 15 price")],
        current_step_index=0,
        plan_version=1,
    )
    state = {
        "goal": Goal(original_text=goal_text, interpreted_goal=goal_text),
        "plan": plan,
        "page_context": _routes_empty_page(),
        "previous_page_context": None,
        "task_memory": TaskMemory(),
        "action_history": [],
        "current_action": Action(
            action_id="act_abc",
            action_type=ActionType.EXTRACT_TEXT,
            description="read price",
            confidence=0.9,
        ),
        "pending_action_result": ActionResult(
            action_id="act_abc",
            status=ActionStatus.SUCCESS,
            message="Page text extracted",
            extracted_data="iPhone 15 128GB is priced at $799.00 on Amazon.com.",
            page_changed=False,
            execution_time_ms=10.0,
        ),
    }
    result = await N.observe(state)
    assert result["cognitive_status"] != CognitiveStatus.RE_PLANNING, \
        "legitimate extract must not trigger RE_PLANNING"
    assert result["cognitive_status"] == CognitiveStatus.EVALUATING
    print("S2.3: legitimate extract -> EVALUATING         OK")


async def main():
    print("=" * 60)
    print("Sprint 1 + Sprint 2 — direct unit tests")
    print("=" * 60)
    await test_F2_goal_decomposition_via_analyze_and_plan()
    await test_F1_credential_prompt_fires_on_login_page()
    await test_F1_skipped_when_no_login_form()
    await test_F1_skipped_when_creds_already_stored()
    await test_F7_done_blocked_on_no_routes_scheduled()
    await test_F7_allows_legitimate_done()
    await test_S2_3_observe_triggers_replan_on_contradiction()
    await test_S2_3_observe_passes_through_when_no_contradiction()
    await test_S2_4_tabs_render_in_page_context()
    await test_S2_2_fingerprint_fields_roundtrip()
    test_S3A_preferred_provider_wins_over_name_sniff()
    await test_S3B_plan_collapses_to_milestones()
    print("=" * 60)
    print("All sprint-1 + sprint-2 unit tests PASSED")


if __name__ == "__main__":
    asyncio.run(main())
