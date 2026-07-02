# backend/tests/test_lead_graph.py
"""End-to-end lead graph tests (worker + reasoning LLMs mocked; browser via Command)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent_core.agent.lead_graph import build_lead_graph
from agent_core.schemas.orchestrator_state import create_lead_state

CFG = {"configurable": {"thread_id": "lead1"}}


def _reasoning(content):
    r = MagicMock()
    r.content = content
    return r


def _tool(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "t"}])


class TestLeadGraph:
    def test_compiles(self):
        assert build_lead_graph() is not None

    @pytest.mark.asyncio
    async def test_single_item_delegated_and_finished(self):
        # seed → 1 extractor item; worker immediately finishes → lead finishes.
        graph = build_lead_graph(MemorySaver())
        state = create_lead_state("read the price", "gpt-4o-mini")
        seed_json = (
            '{"items": [{"subgoal": "read price", "role": "extractor", '
            '"done_criteria": "captured"}]}'
        )
        with patch("agent_core.agent.lead_nodes.get_reasoning_llm") as gr, \
             patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            rl = AsyncMock()
            rl.ainvoke.return_value = _reasoning(seed_json)
            gr.return_value = rl
            wl = AsyncMock()
            wl.ainvoke.return_value = _tool(
                "finish_subgoal", {"summary": "price $9", "data": {"price": "$9"}}
            )
            gw.return_value = wl
            out = await graph.ainvoke(state, CFG)
        done = [i for i in out["plan"] if i.status.value == "done"]
        assert len(done) == 1
        assert done[0].data == {"price": "$9"}
        assert out["delegations_used"] == 1

    @pytest.mark.asyncio
    async def test_worker_browser_action_bubbles_and_resumes(self):
        # worker clicks (interrupt) → lead pauses → resume → worker finishes.
        graph = build_lead_graph(MemorySaver())
        state = create_lead_state("open then done", "gpt-4o-mini")
        seed_json = (
            '{"items": [{"subgoal": "open page", "role": "navigator", '
            '"done_criteria": "loaded"}]}'
        )
        with patch("agent_core.agent.lead_nodes.get_reasoning_llm") as gr, \
             patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            rl = AsyncMock()
            rl.ainvoke.return_value = _reasoning(seed_json)
            gr.return_value = rl
            wl = AsyncMock()
            wl.ainvoke.side_effect = [
                _tool("navigate", {"url": "https://x.com"}),
                _tool("finish_subgoal", {"summary": "opened"}),
            ]
            gw.return_value = wl
            interim = await graph.ainvoke(state, CFG)
            assert "__interrupt__" in interim
            out = await graph.ainvoke(
                Command(resume={"status": "success", "message": "ok", "page_changed": True}),
                CFG,
            )
        assert any(i.status.value == "done" for i in out["plan"])
