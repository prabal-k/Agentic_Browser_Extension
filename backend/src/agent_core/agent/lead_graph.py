# backend/src/agent_core/agent/lead_graph.py
"""The lead graph: seed_plan → plan_step → [worker] → integrate → ... → END.

The worker_node adapter mounts the P2 worker subgraph (compiled WITHOUT its own
checkpointer) and invokes it per delegated item. Browser interrupts raised inside
the worker propagate to the top-level stream and resume via Command(resume=...).
"""

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent_core.agent.lead_nodes import (
    _active_item,
    ask_user_node,
    integrate,
    plan_step,
    route_after_integrate,
    route_after_plan_step,
    seed_plan,
)
from agent_core.agent.worker_graph import build_worker_graph
from agent_core.schemas.orchestrator import ResultDigest
from agent_core.schemas.orchestrator_state import LeadState, create_worker_state

logger = structlog.get_logger("agent.lead_graph")

# Compile the worker ONCE, without a checkpointer (inherits the lead's).
_WORKER = build_worker_graph(checkpointer=None)


def _lead_serde():
    """JsonPlusSerializer with every agent_core.schemas class allowlisted.

    Silences LangGraph's "Deserializing unregistered type ..." checkpoint
    warnings for our Pydantic models and keeps checkpointing forward-compatible
    with strict msgpack. Dynamic (grabs every class defined in the schemas
    package) so no type is missed; langchain message types stay covered by
    LangGraph's own safe-type registry.
    """
    import importlib
    import inspect
    import pkgutil

    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    import agent_core.schemas as _schemas

    allow: list[type] = []
    for mod_info in pkgutil.iter_modules(_schemas.__path__, _schemas.__name__ + "."):
        mod = importlib.import_module(mod_info.name)
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if getattr(obj, "__module__", "") == mod_info.name:
                allow.append(obj)
    return JsonPlusSerializer(allowed_msgpack_modules=allow)


# Schema classes never change at runtime — build the allowlisted serde once.
_LEAD_SERDE = _lead_serde()


async def worker_node(state: LeadState) -> dict:
    """Delegate the active PlanItem to the worker subgraph; return its digest."""
    item = _active_item(state)
    if item is None:
        return {"lead_decision": {**state.get("lead_decision", {}),
                                  "digest": ResultDigest(status="failed",
                                                         summary="no active item")}}
    ws = create_worker_state(
        role=item.role,
        subgoal=item.subgoal,
        done_criteria=item.done_criteria,
        model_name=state["model_name"],
        tab_id=None,
        page_context=state.get("last_page_context"),
        api_keys=state.get("api_keys"),
    )
    # Tag the worker run so LangSmith nests it under the lead run, labelled by
    # role + item. run_name/metadata/tags merge onto the ambient parent config
    # (thread_id/checkpoint_ns preserved), so interrupt/resume still works.
    worker_config = {
        "run_name": f"worker:{item.role.value}[{item.id}]",
        "tags": ["worker", item.role.value],
        "metadata": {
            "role": item.role.value,
            "item_id": item.id,
            "subgoal": item.subgoal[:120],
        },
    }
    result = await _WORKER.ainvoke(ws, config=worker_config)  # completes across browser interrupts
    digest = result.get("result_digest") or ResultDigest(status="failed",
                                                          summary="worker returned no digest")
    out = {"lead_decision": {**state.get("lead_decision", {}), "digest": digest}}
    new_page = result.get("page_context")
    if new_page is not None:
        out["last_page_context"] = new_page
    return out


def build_lead_graph(checkpointer=None):
    """Compile the lead graph. Needs a checkpointer for interrupt/resume."""
    if checkpointer is None:
        checkpointer = MemorySaver(serde=_LEAD_SERDE)

    builder = StateGraph(LeadState)
    builder.add_node("seed_plan", seed_plan)
    builder.add_node("plan_step", plan_step)
    builder.add_node("worker", worker_node)
    builder.add_node("integrate", integrate)
    builder.add_node("ask_user_node", ask_user_node)

    builder.add_edge(START, "seed_plan")
    builder.add_edge("seed_plan", "plan_step")
    builder.add_conditional_edges("plan_step", route_after_plan_step,
                                  {"worker": "worker", "ask_user_node": "ask_user_node", END: END})
    builder.add_edge("worker", "integrate")
    builder.add_conditional_edges("integrate", route_after_integrate,
                                  {"plan_step": "plan_step", "ask_user_node": "ask_user_node"})
    builder.add_edge("ask_user_node", "plan_step")

    graph = builder.compile(checkpointer=checkpointer)
    logger.info("lead_graph_created", node_count=len(builder.nodes))
    return graph
