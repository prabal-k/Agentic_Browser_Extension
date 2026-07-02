"""End-to-end worker subgraph tests (browser stubbed via Command(resume=...))."""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent_core.agent.worker_graph import build_worker_graph
from agent_core.schemas.orchestrator import WorkerRole
from agent_core.schemas.orchestrator_state import create_worker_state


def _tc(name, args):
    # A real AIMessage (not a bare MagicMock): worker_decide feeds the LLM
    # response into WorkerState["messages"], which is governed by langgraph's
    # add_messages reducer — that reducer requires a genuine BaseMessage (as a
    # real tool-bound llm.ainvoke() call returns), not an arbitrary mock object.
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "t"}])


CFG = {"configurable": {"thread_id": "w1"}}


class TestWorkerGraph:
    def test_compiles(self):
        assert build_worker_graph() is not None

    @pytest.mark.asyncio
    async def test_finishes_with_done_digest(self):
        # decide immediately calls finish_subgoal → no interrupt, done digest.
        graph = build_worker_graph(MemorySaver())
        state = create_worker_state(
            role=WorkerRole.EXTRACTOR, subgoal="read price",
            done_criteria="captured", model_name="gpt-4o-mini",
        )
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = _tc("finish_subgoal", {"summary": "done", "data": {"p": 1}})
            gw.return_value = llm
            out = await graph.ainvoke(state, CFG)
        assert out["result_digest"].status == "done"
        assert out["finished"] is True

    @pytest.mark.asyncio
    async def test_read_then_finish_two_turns(self):
        # turn 1: read (interrupt) → resume with extracted text → turn 2: finish.
        graph = build_worker_graph(MemorySaver())
        state = create_worker_state(
            role=WorkerRole.EXTRACTOR, subgoal="read price",
            done_criteria="captured", model_name="gpt-4o-mini",
        )
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.side_effect = [
                _tc("read", {"what": "price"}),
                _tc("finish_subgoal", {"summary": "price $38", "data": {"price": "$38"}}),
            ]
            gw.return_value = llm
            # first invoke pauses at the interrupt in worker_execute
            interim = await graph.ainvoke(state, CFG)
            assert "__interrupt__" in interim
            # resume with the browser's result
            out = await graph.ainvoke(
                Command(resume={"status": "success", "message": "ok",
                                "extracted_data": "$38", "page_changed": False}),
                CFG,
            )
        assert out["result_digest"].status == "done"
        assert out["result_digest"].data == {"price": "$38"}

    @pytest.mark.asyncio
    async def test_budget_exhaustion_fails(self):
        # LLM always clicks, never finishes → loop hits WORKER_ACTION_CAP.
        graph = build_worker_graph(MemorySaver())
        state = create_worker_state(
            role=WorkerRole.NAVIGATOR, subgoal="never done",
            done_criteria="impossible", model_name="gpt-4o-mini",
        )
        with patch("agent_core.agent.worker_nodes.get_worker_llm") as gw:
            llm = AsyncMock()
            llm.ainvoke.return_value = _tc("click", {"element_id": 1})
            gw.return_value = llm
            resume = {"status": "success", "message": "ok", "page_changed": True}
            result = await graph.ainvoke(state, CFG)
            # drive the loop: resume until the graph completes (no more interrupt)
            for _ in range(20):
                if "__interrupt__" not in result:
                    break
                result = await graph.ainvoke(Command(resume=resume), CFG)
        assert result["result_digest"].status == "failed"
        assert result["result_digest"].actions_used == 8
