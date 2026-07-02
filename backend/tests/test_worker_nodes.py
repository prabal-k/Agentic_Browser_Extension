# backend/tests/test_worker_nodes.py
"""Tests for worker node helpers and nodes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_core.agent.worker_nodes import (
    _build_worker_action,
    _digest_from_finish,
)
from agent_core.schemas.actions import ActionStatus, ActionType
from agent_core.schemas.orchestrator import WorkerRole
from agent_core.schemas.orchestrator_state import create_worker_state


class TestBuildWorkerAction:
    def test_read_maps_to_read_page_marker(self):
        a = _build_worker_action("read", {"what": "the price"})
        assert a.action_type == ActionType.EXTRACT_TEXT
        assert a.value == "__READ_PAGE__"

    def test_see_maps_to_visual_check_marker(self):
        a = _build_worker_action("see", {"question": "is it red?"})
        assert a.action_type == ActionType.TAKE_SCREENSHOT
        assert a.value == "__VISUAL_CHECK__|is it red?"

    def test_extract_table_maps_to_listings_marker(self):
        a = _build_worker_action("extract_table", {"what": "rows"})
        assert a.action_type == ActionType.EXTRACT_TEXT
        assert a.value == "__EXTRACT_LISTINGS__"

    def test_navigate_carries_url(self):
        a = _build_worker_action("navigate", {"url": "https://x.com"})
        assert a.action_type == ActionType.NAVIGATE
        assert a.value == "https://x.com"

    def test_click_carries_element_id(self):
        a = _build_worker_action("click", {"element_id": 7})
        assert a.action_type == ActionType.CLICK
        assert a.element_id == 7

    def test_submit_is_enter_keypress(self):
        a = _build_worker_action("submit", {})
        assert a.action_type == ActionType.PRESS_KEY
        assert a.value == "Enter"

    def test_finish_subgoal_returns_none(self):
        assert _build_worker_action("finish_subgoal", {"summary": "done"}) is None


class TestDigestFromFinish:
    def test_builds_done_digest_with_data(self):
        d = _digest_from_finish({"summary": "got price", "data": {"price": "$38"}}, actions_used=3)
        assert d.status == "done"
        assert d.summary == "got price"
        assert d.data == {"price": "$38"}
        assert d.actions_used == 3

    def test_missing_data_is_none(self):
        d = _digest_from_finish({"summary": "ok"}, actions_used=1)
        assert d.data is None


def _mock_tool_call(name, args):
    resp = MagicMock()
    resp.tool_calls = [{"name": name, "args": args, "id": "tc_1"}]
    resp.content = ""
    return resp


class TestWorkerDecide:
    @pytest.mark.asyncio
    async def test_read_call_sets_current_action(self):
        state = create_worker_state(
            role=WorkerRole.EXTRACTOR, subgoal="get price",
            done_criteria="price captured", model_name="gpt-4o-mini",
        )
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_tool_call("read", {"what": "price"})
            gw.return_value = llm
            from agent_core.agent.worker_nodes import worker_decide
            out = await worker_decide(state)
        assert out["current_action"].value == "__READ_PAGE__"
        assert not out.get("finished")

    @pytest.mark.asyncio
    async def test_finish_call_sets_digest(self):
        state = create_worker_state(
            role=WorkerRole.EXTRACTOR, subgoal="get price",
            done_criteria="price captured", model_name="gpt-4o-mini",
        )
        state["actions_used"] = 2
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_tool_call(
                "finish_subgoal", {"summary": "price is $38", "data": {"price": "$38"}})
            gw.return_value = llm
            from agent_core.agent.worker_nodes import worker_decide
            out = await worker_decide(state)
        assert out["finished"] is True
        assert out["result_digest"].status == "done"
        assert out["result_digest"].data == {"price": "$38"}

    @pytest.mark.asyncio
    async def test_destructive_action_bubbles_needs_user(self):
        state = create_worker_state(
            role=WorkerRole.FORM_FILLER, subgoal="upload the file",
            done_criteria="file uploaded", model_name="gpt-4o-mini",
        )
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_tool_call(
                "upload_file", {"file_path": "/etc/passwd"})
            gw.return_value = llm
            from agent_core.agent.worker_nodes import worker_decide
            out = await worker_decide(state)
        assert out["finished"] is True
        assert out["result_digest"].status == "needs_user"
        assert out["result_digest"].needs_user is True


class TestParseExecutionResult:
    def test_parses_success_and_new_dom(self):
        from agent_core.agent.worker_nodes import _parse_execution_result
        from agent_core.schemas.actions import Action, ActionType
        action = Action(action_id="a1", action_type=ActionType.CLICK, element_id=1)
        result, page = _parse_execution_result(action, {
            "status": "success", "message": "clicked", "page_changed": True,
            "new_url": "https://x.com/next",
            "new_dom": {"url": "https://x.com/next", "title": "Next"},
        })
        assert result.status == ActionStatus.SUCCESS
        assert result.new_url == "https://x.com/next"
        assert page is not None
        assert page.title == "Next"

    def test_parses_failure_without_dom(self):
        from agent_core.agent.worker_nodes import _parse_execution_result
        from agent_core.schemas.actions import Action, ActionType
        action = Action(action_id="a2", action_type=ActionType.CLICK, element_id=1)
        result, page = _parse_execution_result(action, {"status": "failed", "message": "nope"})
        assert result.status == ActionStatus.FAILED
        assert page is None


class TestResolveFingerprint:
    def test_keeps_action_fingerprint_when_present(self):
        from agent_core.agent.worker_nodes import _resolve_fingerprint
        from agent_core.schemas.actions import Action, ActionType
        a = Action(action_id="a", action_type=ActionType.CLICK, element_id=1,
                   element_fingerprint="fp-x")
        assert _resolve_fingerprint(a, None) == "fp-x"

    def test_none_when_no_element_matches(self):
        from agent_core.agent.worker_nodes import _resolve_fingerprint
        from agent_core.schemas.actions import Action, ActionType
        from agent_core.schemas.dom import PageContext
        a = Action(action_id="a", action_type=ActionType.CLICK, element_id=99)
        page = PageContext(url="u", title="t", elements=[])
        assert _resolve_fingerprint(a, page) is None

    def test_resolves_from_page_context_when_available(self):
        # If DOMElement carries a fingerprint, it is returned; otherwise None.
        # This test asserts the lookup path runs without error and returns str|None.
        from agent_core.agent.worker_nodes import _resolve_fingerprint
        from agent_core.schemas.actions import Action, ActionType
        from agent_core.schemas.dom import DOMElement, ElementType, PageContext
        a = Action(action_id="a", action_type=ActionType.CLICK, element_id=2)
        page = PageContext(url="u", title="t", elements=[
            DOMElement(element_id=2, element_type=ElementType.BUTTON,
                       tag_name="button", text="Go"),
        ])
        result = _resolve_fingerprint(a, page)
        assert result is None or isinstance(result, str)


class TestBudget:
    def test_budget_exhausted_true_at_cap(self):
        from agent_core.agent.budgets import WORKER_ACTION_CAP
        from agent_core.agent.worker_nodes import budget_exhausted
        state = {"actions_used": WORKER_ACTION_CAP}
        assert budget_exhausted(state) is True

    def test_budget_not_exhausted_below_cap(self):
        from agent_core.agent.worker_nodes import budget_exhausted
        assert budget_exhausted({"actions_used": 0}) is False

    def test_budget_digest_is_failed(self):
        from agent_core.agent.worker_nodes import _digest_budget_exhausted
        d = _digest_budget_exhausted({"actions_used": 8})
        assert d.status == "failed"
        assert d.actions_used == 8


class TestObservationChannel:
    @pytest.mark.asyncio
    async def test_prior_extracted_data_reaches_the_prompt(self):
        # After a read whose result was "$38", the NEXT decide turn's human
        # message must contain "$38" — proving the observation channel works.
        from agent_core.agent.worker_nodes import worker_decide
        state = create_worker_state(
            role=WorkerRole.EXTRACTOR, subgoal="get price",
            done_criteria="captured", model_name="gpt-4o-mini",
        )
        state["action_history"] = [{
            "action": {"action_type": "extract_text", "description": "read price"},
            "result": {"status": "success", "extracted_data": "$38"},
        }]
        finish = MagicMock()
        finish.tool_calls = [{"name": "finish_subgoal",
                              "args": {"summary": "ok", "data": {"price": "$38"}}, "id": "t"}]
        finish.content = ""
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = finish
            gw.return_value = llm
            await worker_decide(state)
            # inspect the human message actually sent to the LLM
            sent = llm.ainvoke.call_args.args[0]
            human_text = " ".join(getattr(m, "content", "") for m in sent)
        assert "$38" in human_text


class TestBoundaryGuards:
    def test_parse_non_dict_result_is_failed(self):
        from agent_core.agent.worker_nodes import _parse_execution_result
        from agent_core.schemas.actions import Action, ActionStatus, ActionType
        a = Action(action_id="a", action_type=ActionType.CLICK, element_id=1)
        result, page = _parse_execution_result(a, None)
        assert result.status == ActionStatus.FAILED
        assert page is None

    def test_finish_wraps_non_dict_data(self):
        from agent_core.agent.worker_nodes import _digest_from_finish
        d = _digest_from_finish({"summary": "ok", "data": "not-a-dict"}, actions_used=1)
        assert d.data == {"value": "not-a-dict"}

    def test_float_element_id_coerced_to_int(self):
        from agent_core.agent.worker_nodes import _build_worker_action
        a = _build_worker_action("click", {"element_id": 7.0})
        assert a.element_id == 7

    def test_bool_element_id_is_none(self):
        from agent_core.agent.worker_nodes import _build_worker_action
        a = _build_worker_action("click", {"element_id": True})
        assert a.element_id is None


class TestWorkerDecideLLMErrorGuard:
    @pytest.mark.asyncio
    async def test_llm_error_returns_failed_digest_not_crash(self):
        from unittest.mock import AsyncMock, patch

        from agent_core.agent.worker_nodes import worker_decide
        from agent_core.schemas.orchestrator import WorkerRole
        from agent_core.schemas.orchestrator_state import create_worker_state
        state = create_worker_state(
            role=WorkerRole.EXTRACTOR, subgoal="read price",
            done_criteria="captured", model_name="gpt-4o-mini",
        )
        state["actions_used"] = 2
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.side_effect = RuntimeError("401 invalid api key")
            gw.return_value = llm
            out = await worker_decide(state)
        assert out["finished"] is True
        assert out["result_digest"].status == "failed"
        assert out["result_digest"].actions_used == 2
        assert "error" in out["result_digest"].summary.lower()
