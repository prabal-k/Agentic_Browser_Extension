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

    def test_blocked_item_not_selected(self):
        # Only item b remains; its dependency 'a' is not present/done -> not ready -> finish.
        from agent_core.agent.lead_nodes import _next_ready, plan_step
        from agent_core.schemas.orchestrator import PlanItem, WorkerRole
        b = PlanItem(id="b", subgoal="s2", role=WorkerRole.EXTRACTOR,
                     done_criteria="d", depends_on=["a"])
        state = create_lead_state("g", "gpt-4o-mini")
        state["plan"] = [b]
        assert _next_ready([b]) is None
        out = plan_step(state)
        assert out["lead_decision"]["action"] == "finish"
        assert out["active_item_id"] is None

    def test_ready_item_chosen_over_blocked_one(self):
        # c has an unmet dependency and appears first; b's dep 'a' is DONE.
        # _next_ready must SKIP c and return b -> proves the depends_on check matters.
        from agent_core.agent.lead_nodes import _next_ready
        from agent_core.schemas.orchestrator import PlanItem, PlanItemStatus, WorkerRole
        a = PlanItem(id="a", subgoal="s", role=WorkerRole.NAVIGATOR,
                     done_criteria="d", status=PlanItemStatus.DONE)
        b = PlanItem(id="b", subgoal="s2", role=WorkerRole.EXTRACTOR,
                     done_criteria="d", depends_on=["a"])
        c = PlanItem(id="c", subgoal="s3", role=WorkerRole.EXTRACTOR,
                     done_criteria="d", depends_on=["zzz"])  # unmet dep
        plan = [a, c, b]
        ready = _next_ready(plan)
        assert ready is not None
        assert ready.id == "b"


class TestIntegrate:
    def _state_with_active(self, digest):
        from agent_core.schemas.orchestrator import PlanItem, PlanItemStatus, WorkerRole
        state = create_lead_state("g", "gpt-4o-mini")
        item = PlanItem(id="a", subgoal="s", role=WorkerRole.EXTRACTOR,
                        done_criteria="d", status=PlanItemStatus.ACTIVE)
        state["plan"] = [item]
        state["active_item_id"] = "a"
        state["lead_decision"] = {"action": "delegate", "item_id": "a", "digest": digest}
        return state

    def test_done_digest_marks_item_done(self):
        from agent_core.agent.lead_nodes import integrate
        from agent_core.schemas.orchestrator import PlanItemStatus, ResultDigest
        digest = ResultDigest(status="done", summary="got it", data={"p": "$1"}, actions_used=3)
        out = integrate(self._state_with_active(digest))
        item = out["plan"][0]
        assert item.status is PlanItemStatus.DONE
        assert item.result_digest == "got it"
        assert item.data == {"p": "$1"}
        assert out["delegations_used"] == 1

    def test_failed_digest_retries_then_fails(self):
        from agent_core.agent.lead_nodes import integrate
        from agent_core.schemas.orchestrator import PlanItemStatus, ResultDigest
        digest = ResultDigest(status="failed", summary="nope", actions_used=8)
        # first failure → back to PENDING (retry)
        s = self._state_with_active(digest)
        out = integrate(s)
        assert out["plan"][0].status is PlanItemStatus.PENDING
        assert out["plan"][0].retries == 1
        # exhaust retries
        out["plan"][0].status = PlanItemStatus.ACTIVE
        s2 = create_lead_state("g", "gpt-4o-mini")
        s2["plan"] = out["plan"]
        s2["active_item_id"] = "a"
        s2["lead_decision"] = {"digest": digest}
        out["plan"][0].retries = 2  # at cap
        out2 = integrate(s2)
        assert out2["plan"][0].status is PlanItemStatus.FAILED

    def test_needs_user_routes_to_ask(self):
        from agent_core.agent.lead_nodes import integrate, route_after_integrate
        from agent_core.schemas.orchestrator import ResultDigest
        digest = ResultDigest(status="needs_user", summary="confirm?",
                              needs_user=True, question="Delete account?")
        out = integrate(self._state_with_active(digest))
        assert out["lead_decision"]["action"] == "ask_user"
        # route helper sees the updated decision
        st = create_lead_state("g", "gpt-4o-mini")
        st["lead_decision"] = out["lead_decision"]
        assert route_after_integrate(st) == "ask_user_node"

    def test_records_tab(self):
        from agent_core.agent.lead_nodes import integrate
        from agent_core.schemas.orchestrator import ResultDigest
        digest = ResultDigest(status="done", summary="ok", tab_id="tab_b")
        out = integrate(self._state_with_active(digest))
        assert out["tabs"]["tab_b"]


class TestLeadRouting:
    def test_route_after_plan_step_delegate(self):
        from agent_core.agent.lead_nodes import route_after_plan_step
        st = create_lead_state("g", "gpt-4o-mini")
        st["lead_decision"] = {"action": "delegate", "item_id": "a"}
        assert route_after_plan_step(st) == "worker"

    def test_route_after_plan_step_finish(self):
        from agent_core.agent.lead_nodes import route_after_plan_step
        st = create_lead_state("g", "gpt-4o-mini")
        st["lead_decision"] = {"action": "finish"}
        assert route_after_plan_step(st) == "__end__"
