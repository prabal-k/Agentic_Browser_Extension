"""Tests for worker/lead budgets and the destructive-action gate."""

from agent_core.agent.budgets import (
    DESTRUCTIVE_ACTIONS,
    ITEM_RETRY_CAP,
    LEAD_DELEGATION_CAP,
    WORKER_ACTION_CAP,
    is_destructive,
)
from agent_core.schemas.actions import ActionType


class TestBudgetValues:
    def test_caps_match_spec(self):
        assert WORKER_ACTION_CAP == 8
        assert ITEM_RETRY_CAP == 2
        assert LEAD_DELEGATION_CAP == 15


class TestDestructiveGate:
    def test_navigate_is_not_destructive(self):
        assert is_destructive(ActionType.NAVIGATE) is False
        assert ActionType.NAVIGATE not in DESTRUCTIVE_ACTIONS

    def test_upload_file_is_destructive(self):
        # Uploading a file mutates external state — must be gated.
        assert is_destructive(ActionType.UPLOAD_FILE) is True

    def test_evaluate_js_is_destructive(self):
        assert is_destructive(ActionType.EVALUATE_JS) is True
