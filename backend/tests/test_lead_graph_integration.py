# backend/tests/test_lead_graph_integration.py
"""ws_handler/session wiring for the lead graph (opt-in)."""

from unittest.mock import patch

import pytest


class TestSessionBuildsLeadGraph:
    def test_session_uses_lead_graph_when_enabled(self):
        from agent_core.server import session as session_mod

        with patch.object(session_mod.settings, "use_lead_graph", True):
            mgr = session_mod.SessionManager()
            sess = mgr.create_session()
            # the lead graph exposes nodes seed_plan/plan_step; the agent graph does not
            node_names = set(sess.graph.get_graph().nodes)
            assert {"seed_plan", "plan_step"} <= node_names
            assert sess.is_lead_graph is True

    def test_session_uses_agent_graph_when_disabled(self):
        from agent_core.server import session as session_mod

        with patch.object(session_mod.settings, "use_lead_graph", False):
            mgr = session_mod.SessionManager()
            sess = mgr.create_session()
            node_names = set(sess.graph.get_graph().nodes)
            assert "decide_action" in node_names  # the classic agent
            assert sess.is_lead_graph is False


class TestSendDoneLead:
    @pytest.mark.asyncio
    async def test_completed_ledger_reports_success(self):
        from unittest.mock import MagicMock, patch

        from agent_core.schemas.orchestrator import PlanItem, PlanItemStatus, WorkerRole
        from agent_core.server import ws_handler
        plan = [
            PlanItem(id="a", subgoal="s1", role=WorkerRole.NAVIGATOR, done_criteria="d",
                     status=PlanItemStatus.DONE, result_digest="opened"),
            PlanItem(id="b", subgoal="s2", role=WorkerRole.EXTRACTOR, done_criteria="d",
                     status=PlanItemStatus.DONE, result_digest="read $9", data={"price": "$9"}),
        ]
        final_values = {"plan": plan, "original_goal": "g", "last_page_context": None}
        session = MagicMock(is_lead_graph=True, session_id="s", action_count=5,
                            created_at=0, current_goal="g")
        captured = {}
        async def fake_send(ws_, msg_type, **kw):
            captured["type"] = msg_type
            captured.update(kw)
        with patch.object(ws_handler, "send_msg", side_effect=fake_send), \
             patch("agent_core.memory.store.get_memory", return_value=MagicMock()):
            await ws_handler._send_done(MagicMock(), session, final_values)
        assert captured["type"] == "server_done"
        assert captured["success"] is True
        assert captured["steps_total"] == 2
        assert captured["steps_completed"] == 2

    @pytest.mark.asyncio
    async def test_failed_item_reports_failure(self):
        from unittest.mock import MagicMock, patch

        from agent_core.schemas.orchestrator import PlanItem, PlanItemStatus, WorkerRole
        from agent_core.server import ws_handler
        plan = [
            PlanItem(id="a", subgoal="s1", role=WorkerRole.NAVIGATOR, done_criteria="d",
                     status=PlanItemStatus.DONE, result_digest="ok"),
            PlanItem(id="b", subgoal="s2", role=WorkerRole.EXTRACTOR, done_criteria="d",
                     status=PlanItemStatus.FAILED),
        ]
        final_values = {"plan": plan, "original_goal": "g", "last_page_context": None}
        session = MagicMock(is_lead_graph=True, session_id="s", action_count=3,
                            created_at=0, current_goal="g")
        captured = {}
        async def fake_send(ws_, msg_type, **kw):
            captured["type"] = msg_type
            captured.update(kw)
        with patch.object(ws_handler, "send_msg", side_effect=fake_send), \
             patch("agent_core.memory.store.get_memory", return_value=MagicMock()):
            await ws_handler._send_done(MagicMock(), session, final_values)
        assert captured["success"] is False
        assert captured["steps_completed"] == 1
