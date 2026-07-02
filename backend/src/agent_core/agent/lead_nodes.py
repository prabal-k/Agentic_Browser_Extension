# backend/src/agent_core/agent/lead_nodes.py
"""Lead coordinator nodes.

The lead owns a living plan ledger (list[PlanItem]) and drives it: seed the plan
once, then repeatedly pick the next ready item to delegate, fold each worker's
ResultDigest back in, and finish when every non-skipped item is done or failed.
The lead has NO browser tools; only the compact ResultDigest informs the ledger.
"""

import uuid

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from agent_core.agent.llm_client import get_reasoning_llm
from agent_core.agent.nodes import _parse_llm_json
from agent_core.schemas.orchestrator import (
    PlanItem,
    WorkerRole,
)
from agent_core.schemas.orchestrator_state import LeadState

logger = structlog.get_logger("agent.lead")

_SEED_SYSTEM = """You break a browser task into 2-5 coarse subgoals for specialist workers.

Roles:
- navigator: move around (open pages, click, scroll)
- extractor: read text/data from the page
- form_filler: fill and submit form fields
- verifier: confirm something is true on the page
- auth: log in / handle credentials

Return ONLY JSON: {"items": [{"subgoal": str, "role": one of the roles, "done_criteria": str}, ...]}
Keep it to 2-5 items. done_criteria must be a checkable condition."""


def _new_id() -> str:
    return f"item_{uuid.uuid4().hex[:8]}"


def _coerce_items(raw: list) -> list[PlanItem]:
    """Turn parsed dicts into PlanItems, defaulting an unknown role to navigator."""
    items: list[PlanItem] = []
    prev_id: str | None = None
    for entry in raw:
        role_str = str(entry.get("role", "")).strip().lower()
        try:
            role = WorkerRole(role_str)
        except ValueError:
            role = WorkerRole.NAVIGATOR
        item = PlanItem(
            id=_new_id(),
            subgoal=str(entry.get("subgoal", "")).strip() or "complete the task",
            role=role,
            done_criteria=str(entry.get("done_criteria", "")).strip() or "subgoal complete",
            depends_on=[prev_id] if prev_id else [],
        )
        items.append(item)
        prev_id = item.id
    return items


def _fallback_plan(goal: str) -> list[PlanItem]:
    return [PlanItem(id=_new_id(), subgoal=goal, role=WorkerRole.NAVIGATOR,
                     done_criteria="the task is complete")]


async def seed_plan(state: LeadState) -> dict:
    """One reasoning call → 2-5 coarse PlanItems (model-seeded, replaces regex)."""
    goal = state["original_goal"]
    llm = get_reasoning_llm(model_name=state["model_name"], api_keys=state.get("api_keys"))
    try:
        resp = await llm.ainvoke([
            SystemMessage(content=_SEED_SYSTEM),
            HumanMessage(content=f"Task: {goal}"),
        ])
        data = _parse_llm_json(resp.content)
        raw = data.get("items", [])
        items = _coerce_items(raw) if isinstance(raw, list) and raw else []
    except Exception as exc:  # noqa: BLE001 — any parse/LLM failure → safe fallback
        logger.warning("seed_plan_fallback", error=str(exc))
        items = []
    if not items:
        items = _fallback_plan(goal)
    logger.info("seed_plan_done", item_count=len(items))
    return {"plan": items}
