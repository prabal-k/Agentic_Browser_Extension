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
