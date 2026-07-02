# backend/tests/test_lead_nodes.py
"""Tests for the lead coordinator nodes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_core.schemas.orchestrator import PlanItemStatus, WorkerRole
from agent_core.schemas.orchestrator_state import create_lead_state


def _mock_reasoning(content: str):
    resp = MagicMock()
    resp.content = content
    return resp


class TestSeedPlan:
    @pytest.mark.asyncio
    async def test_parses_items(self):
        state = create_lead_state(goal_text="log in then read the dashboard",
                                  model_name="gpt-4o-mini")
        content = '''```json
{"items": [
  {"subgoal": "log in", "role": "auth", "done_criteria": "account menu visible"},
  {"subgoal": "read dashboard", "role": "extractor", "done_criteria": "metrics captured"}
]}
```'''
        with patch("agent_core.agent.lead_nodes.get_reasoning_llm") as gr:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_reasoning(content)
            gr.return_value = llm
            from agent_core.agent.lead_nodes import seed_plan
            out = await seed_plan(state)
        plan = out["plan"]
        assert len(plan) == 2
        assert plan[0].role is WorkerRole.AUTH
        assert plan[1].role is WorkerRole.EXTRACTOR
        assert plan[0].id and plan[1].id and plan[0].id != plan[1].id
        assert plan[0].status is PlanItemStatus.PENDING

    @pytest.mark.asyncio
    async def test_falls_back_on_bad_json(self):
        state = create_lead_state(goal_text="do the thing", model_name="gpt-4o-mini")
        with patch("agent_core.agent.lead_nodes.get_reasoning_llm") as gr:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_reasoning("not json at all")
            gr.return_value = llm
            from agent_core.agent.lead_nodes import seed_plan
            out = await seed_plan(state)
        plan = out["plan"]
        assert len(plan) == 1
        assert plan[0].role is WorkerRole.NAVIGATOR
        assert plan[0].subgoal == "do the thing"

    @pytest.mark.asyncio
    async def test_unknown_role_falls_back_to_navigator(self):
        state = create_lead_state(goal_text="g", model_name="gpt-4o-mini")
        content = '{"items": [{"subgoal": "x", "role": "wizard", "done_criteria": "done"}]}'
        with patch("agent_core.agent.lead_nodes.get_reasoning_llm") as gr:
            llm = AsyncMock()
            llm.ainvoke.return_value = _mock_reasoning(content)
            gr.return_value = llm
            from agent_core.agent.lead_nodes import seed_plan
            out = await seed_plan(state)
        assert out["plan"][0].role is WorkerRole.NAVIGATOR


class TestPlanStep:
    def _plan(self):
        from agent_core.schemas.orchestrator import PlanItem, WorkerRole
        a = PlanItem(id="a", subgoal="s1", role=WorkerRole.NAVIGATOR, done_criteria="d")
        b = PlanItem(id="b", subgoal="s2", role=WorkerRole.EXTRACTOR, done_criteria="d",
                     depends_on=["a"])
        return [a, b]

    def test_delegates_first_ready(self):
        from agent_core.agent.lead_nodes import plan_step
        from agent_core.schemas.orchestrator import PlanItemStatus
        state = create_lead_state("g", "gpt-4o-mini")
        state["plan"] = self._plan()
        out = plan_step(state)
        assert out["lead_decision"]["action"] == "delegate"
        assert out["active_item_id"] == "a"
        active = next(i for i in out["plan"] if i.id == "a")
        assert active.status is PlanItemStatus.ACTIVE

    def test_skips_item_with_unmet_dependency(self):
        # a is pending (not done), so b (depends on a) is NOT ready — a is chosen.
        from agent_core.agent.lead_nodes import plan_step
        state = create_lead_state("g", "gpt-4o-mini")
        state["plan"] = self._plan()
        out = plan_step(state)
        assert out["active_item_id"] == "a"

    def test_finishes_when_all_done(self):
        from agent_core.agent.lead_nodes import plan_step
        from agent_core.schemas.orchestrator import PlanItemStatus
        state = create_lead_state("g", "gpt-4o-mini")
        plan = self._plan()
        for i in plan:
            i.status = PlanItemStatus.DONE
        state["plan"] = plan
        out = plan_step(state)
        assert out["lead_decision"]["action"] == "finish"

    def test_finishes_at_delegation_cap(self):
        from agent_core.agent.lead_nodes import plan_step
        state = create_lead_state("g", "gpt-4o-mini")
        state["plan"] = self._plan()
        state["delegations_used"] = 15
        out = plan_step(state)
        assert out["lead_decision"]["action"] == "finish"
