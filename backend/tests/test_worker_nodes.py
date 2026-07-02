# backend/tests/test_worker_nodes.py
"""Tests for worker node helpers and nodes."""

from agent_core.agent.worker_nodes import (
    _build_worker_action,
    _digest_from_finish,
)
from agent_core.schemas.actions import ActionType


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
