"""Worker subgraph: a bounded, role-scoped ReAct loop over WorkerState.

Returns a compact ResultDigest. Mounted as a single node inside the lead graph
in P3; here it runs standalone (browser stubbed via Command(resume=...)).
"""

from typing import Literal

import structlog
from langgraph.graph import END, START, StateGraph

from agent_core.agent.worker_nodes import (
    _digest_budget_exhausted,
    budget_exhausted,
    worker_decide,
    worker_execute,
)
from agent_core.schemas.orchestrator_state import WorkerState

logger = structlog.get_logger("agent.worker_graph")


async def finalize_budget(state: WorkerState) -> dict:
    """Terminal node when the action budget is exhausted."""
    return {"finished": True, "result_digest": _digest_budget_exhausted(state)}


def _route_after_decide(state: WorkerState) -> Literal["worker_execute", "__end__"]:
    return END if state.get("finished") else "worker_execute"


def _route_after_execute(state: WorkerState) -> Literal["worker_decide", "finalize_budget"]:
    return "finalize_budget" if budget_exhausted(state) else "worker_decide"


def build_worker_graph(checkpointer=None):
    """Compile the worker subgraph.

    checkpointer=None compiles WITHOUT a checkpointer — required when mounting
    this graph inside the lead graph (it inherits the lead's checkpointer).
    Pass an explicit MemorySaver for standalone interrupt/resume (e.g. tests).
    """
    builder = StateGraph(WorkerState)
    builder.add_node("worker_decide", worker_decide)
    builder.add_node("worker_execute", worker_execute)
    builder.add_node("finalize_budget", finalize_budget)

    builder.add_edge(START, "worker_decide")
    builder.add_conditional_edges(
        "worker_decide", _route_after_decide,
        {"worker_execute": "worker_execute", END: END},
    )
    builder.add_conditional_edges(
        "worker_execute", _route_after_execute,
        {"worker_decide": "worker_decide", "finalize_budget": "finalize_budget"},
    )
    builder.add_edge("finalize_budget", END)

    graph = builder.compile(checkpointer=checkpointer)
    logger.info("worker_graph_created", node_count=len(builder.nodes))
    return graph
