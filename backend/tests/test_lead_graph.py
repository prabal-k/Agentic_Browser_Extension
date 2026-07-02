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
            def worker_llm_side_effect(messages, *args, **kwargs):
                human = ""
                for m in messages:
                    if m.__class__.__name__ == "HumanMessage":
                        human = m.content
                # Only finish AFTER an action result is visible (proves worker_execute ran).
                if "No actions taken yet" in human:
                    return _tool("navigate", {"url": "https://x.com"})
                return _tool("finish_subgoal", {"summary": "opened"})

            wl = AsyncMock()
            wl.ainvoke.side_effect = worker_llm_side_effect
            gw.return_value = wl
            interim = await graph.ainvoke(state, CFG)
            assert "__interrupt__" in interim
            out = await graph.ainvoke(
                Command(resume={"status": "success", "message": "ok", "page_changed": True}),
                CFG,
            )
        assert any(i.status.value == "done" for i in out["plan"])
        # A single resume must be enough to finish: proves worker_execute consumed
        # the resume and the next worker_decide observed its result (not paused again).
        assert "__interrupt__" not in out


class TestLeadSerdeRoundtrip:
    def test_checkpointed_types_survive_roundtrip(self):
        # The serde's key risk: an explicit allowlist BLOCKS non-listed types,
        # degrading them to raw dicts (silent, surfaces as AttributeError on
        # resume). Assert every type actually stored in Lead/Worker state
        # round-trips as its real class, so a future schema addition fails loudly.
        from langchain_core.messages import HumanMessage

        from agent_core.agent.lead_graph import _lead_serde
        from agent_core.schemas.actions import (
            Action,
            ActionResult,
            ActionStatus,
            ActionType,
        )
        from agent_core.schemas.dom import DOMElement, ElementType, PageContext
        from agent_core.schemas.orchestrator import (
            PlanItem,
            PlanItemStatus,
            ResultDigest,
            WorkerRole,
        )
        serde = _lead_serde()
        samples = [
            PlanItem(id="a", subgoal="s", role=WorkerRole.EXTRACTOR, done_criteria="d"),
            ResultDigest(status="done", summary="ok", data={"p": 1}),
            WorkerRole.AUTH,
            PlanItemStatus.DONE,
            PageContext(url="u", title="t", elements=[
                DOMElement(element_id=1, element_type=ElementType.BUTTON,
                           tag_name="button", text="Go"),
            ]),
            Action(action_id="x", action_type=ActionType.CLICK, element_id=1),
            ActionResult(action_id="x", status=ActionStatus.SUCCESS, message="ok"),
            HumanMessage(content="hi"),
        ]
        for obj in samples:
            restored = serde.loads_typed(serde.dumps_typed(obj))
            assert type(restored) is type(obj), \
                f"{type(obj).__name__} degraded to {type(restored).__name__}"


class TestWorkerRunTagging:
    @pytest.mark.asyncio
    async def test_worker_invocation_is_tagged_for_langsmith(self):
        from unittest.mock import patch

        from agent_core.agent import lead_graph
        from agent_core.schemas.orchestrator import PlanItem, ResultDigest, WorkerRole
        from agent_core.schemas.orchestrator_state import create_lead_state
        item = PlanItem(id="i1", subgoal="open the page", role=WorkerRole.NAVIGATOR,
                        done_criteria="loaded")
        state = create_lead_state("g", "m")
        state["plan"] = [item]
        state["active_item_id"] = "i1"
        captured = {}

        async def fake_ainvoke(ws, config=None):
            captured["config"] = config
            return {"result_digest": ResultDigest(status="done", summary="ok")}

        with patch.object(lead_graph._WORKER, "ainvoke", side_effect=fake_ainvoke):
            await lead_graph.worker_node(state)
        cfg = captured["config"]
        assert cfg["run_name"] == "worker:navigator[i1]"
        assert cfg["metadata"]["role"] == "navigator"
        assert cfg["metadata"]["item_id"] == "i1"
        assert "worker" in cfg["tags"] and "navigator" in cfg["tags"]
