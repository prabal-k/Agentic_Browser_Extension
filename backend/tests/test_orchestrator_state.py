"""Tests for the split lead/worker state and their factories."""

from agent_core.schemas.orchestrator import PlanItem, WorkerRole
from agent_core.schemas.orchestrator_state import (
    create_lead_state,
    create_worker_state,
)


class TestCreateLeadState:
    def test_initializes_empty_ledger(self):
        s = create_lead_state(goal_text="Compare price of X on two sites",
                               model_name="gpt-4o-mini")
        assert s["original_goal"] == "Compare price of X on two sites"
        assert s["plan"] == []
        assert s["active_item_id"] is None
        assert s["delegations_used"] == 0
        assert s["tabs"] == {}
        assert s["stored_credentials"] == {}
        assert s["messages"] == []

    def test_plan_accepts_plan_items(self):
        s = create_lead_state(goal_text="g", model_name="gpt-4o-mini")
        s["plan"].append(
            PlanItem(id="i1", subgoal="open site", role=WorkerRole.NAVIGATOR,
                     done_criteria="page loaded")
        )
        assert s["plan"][0].role is WorkerRole.NAVIGATOR


class TestCreateWorkerState:
    def test_carries_delegation_context(self):
        s = create_worker_state(
            role=WorkerRole.EXTRACTOR,
            subgoal="read the price",
            done_criteria="price captured",
            model_name="gpt-4o-mini",
            tab_id="tab_a",
        )
        assert s["role"] is WorkerRole.EXTRACTOR
        assert s["subgoal"] == "read the price"
        assert s["tab_id"] == "tab_a"
        assert s["actions_used"] == 0
        assert s["result_digest"] is None
        assert s["action_history"] == []

    def test_isolation_fields_start_empty(self):
        # The big/noisy fields never come from the lead — they start empty.
        s = create_worker_state(
            role=WorkerRole.NAVIGATOR, subgoal="go", done_criteria="there",
            model_name="gpt-4o-mini",
        )
        assert s["page_context"] is None
        assert s["action_history"] == []
