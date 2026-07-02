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
