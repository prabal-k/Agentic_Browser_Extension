# backend/tests/test_lead_graph_integration.py
"""ws_handler/session wiring for the lead graph (opt-in)."""

from unittest.mock import patch


class TestSessionBuildsLeadGraph:
    def test_session_uses_lead_graph_when_enabled(self):
        from agent_core.server import session as session_mod

        with patch.object(session_mod.settings, "use_lead_graph", True):
            mgr = session_mod.SessionManager()
            sess = mgr.create_session()
            # the lead graph exposes nodes seed_plan/plan_step; the agent graph does not
            node_names = set(sess.graph.get_graph().nodes)
            assert "seed_plan" in node_names or "plan_step" in node_names
            assert sess.is_lead_graph is True

    def test_session_uses_agent_graph_when_disabled(self):
        from agent_core.server import session as session_mod

        with patch.object(session_mod.settings, "use_lead_graph", False):
            mgr = session_mod.SessionManager()
            sess = mgr.create_session()
            node_names = set(sess.graph.get_graph().nodes)
            assert "decide_action" in node_names  # the classic agent
            assert sess.is_lead_graph is False
