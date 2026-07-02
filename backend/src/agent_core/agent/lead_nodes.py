# backend/src/agent_core/agent/lead_nodes.py
"""Lead coordinator nodes.

The lead owns a living plan ledger (list[PlanItem]) and drives it: seed the plan
once, then repeatedly pick the next ready item to delegate, fold each worker's
ResultDigest back in, and finish when every non-skipped item is done or failed.
The lead has NO browser tools; only the compact ResultDigest informs the ledger.
"""

import json
import uuid
from typing import Literal

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from agent_core.agent.budgets import ITEM_RETRY_CAP, LEAD_DELEGATION_CAP
from agent_core.agent.llm_client import get_reasoning_llm
from agent_core.agent.nodes import _parse_llm_json
from agent_core.schemas.orchestrator import (
    PlanItem,
    PlanItemStatus,
    ResultDigest,
    WorkerRole,
)
from agent_core.schemas.orchestrator_state import LeadState

logger = structlog.get_logger("agent.lead")

_SEED_SYSTEM = """You break a browser task into 2-5 concrete, actionable subgoals for
specialist workers. Tie each subgoal to THIS specific task — no generic boilerplate.

Roles:
- navigator: open pages by URL, click, scroll. CANNOT type — to search the web it
  navigates directly to a query URL like https://www.google.com/search?q=...
- extractor: read text/data from the current page
- form_filler: fill and submit form fields (typing into inputs)
- verifier: confirm something is true on the page
- auth: log in / handle credentials

For an information lookup (prices, facts, news), the FIRST subgoal should navigate
directly to a URL that will SHOW the answer (e.g. a Google search-results URL), not
just a homepage. done_criteria must be a condition you can visibly check on the page.

Return ONLY JSON: {"items": [{"subgoal": str, "role": one of the roles, "done_criteria": str}, ...]}
Keep it to 2-5 items."""


def _new_id() -> str:
    return f"item_{uuid.uuid4().hex[:8]}"


def _loads_json_lenient(text: str) -> dict:
    """Parse a JSON object from an LLM response, tolerating prose/formatting.

    First tries _parse_llm_json (handles ```fences``` and <think> tags). If that
    fails, scans for the first balanced {...} object — models often wrap the JSON
    in a sentence, which json.loads alone rejects.
    """
    try:
        return _parse_llm_json(text)
    except Exception:
        start = text.find("{")
        if start == -1:
            raise
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
        raise


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
        data = _loads_json_lenient(resp.content)
        raw = data.get("items", [])
        items = _coerce_items(raw) if isinstance(raw, list) and raw else []
    except Exception as exc:  # noqa: BLE001 — any parse/LLM failure → safe fallback
        logger.warning("seed_plan_fallback", error=str(exc))
        items = []
    if not items:
        items = _fallback_plan(goal)
    logger.info("seed_plan_done", item_count=len(items))
    return {"plan": items}


def _active_item(state: LeadState) -> PlanItem | None:
    active_id = state.get("active_item_id")
    if not active_id:
        return None
    for item in state.get("plan", []):
        if item.id == active_id:
            return item
    return None


_TERMINAL = {PlanItemStatus.DONE, PlanItemStatus.FAILED, PlanItemStatus.SKIPPED}


def _next_ready(plan: list[PlanItem]) -> PlanItem | None:
    done_ids = {i.id for i in plan if i.status in {PlanItemStatus.DONE, PlanItemStatus.SKIPPED}}
    for item in plan:
        if item.status is PlanItemStatus.PENDING and all(d in done_ids for d in item.depends_on):
            return item
    return None


def plan_step(state: LeadState) -> dict:
    """Emit ONE coordination decision: delegate the next ready item, or finish.

    Deterministic (0-LLM): the plan was model-seeded; execution order follows
    dependencies. LLM-driven mid-run replanning is a later enhancement.
    """
    plan = list(state.get("plan", []))

    if state.get("delegations_used", 0) >= LEAD_DELEGATION_CAP:
        return {"lead_decision": {"action": "finish", "reason": "delegation cap reached"},
                "active_item_id": None, "plan": plan}

    ready = _next_ready(plan)
    if ready is not None:
        ready.status = PlanItemStatus.ACTIVE
        return {"lead_decision": {"action": "delegate", "item_id": ready.id},
                "active_item_id": ready.id, "plan": plan}

    if all(i.status in _TERMINAL for i in plan):
        return {"lead_decision": {"action": "finish"}, "active_item_id": None, "plan": plan}

    # pending items remain but none are ready (unmet deps / blocked) → stop
    return {"lead_decision": {"action": "finish", "reason": "no ready items (blocked)"},
            "active_item_id": None, "plan": plan}


def integrate(state: LeadState) -> dict:
    """Fold the worker's ResultDigest into the active item; 0-LLM."""
    plan = list(state.get("plan", []))
    tabs = dict(state.get("tabs", {}))
    delegations = state.get("delegations_used", 0) + 1
    decision = dict(state.get("lead_decision", {}))
    digest: ResultDigest | None = decision.get("digest")

    item = None
    active_id = state.get("active_item_id")
    for i in plan:
        if i.id == active_id:
            item = i
            break

    if item is None or digest is None:
        return {"plan": plan, "tabs": tabs, "delegations_used": delegations}

    if digest.tab_id:
        tabs[digest.tab_id] = digest.summary[:60] or digest.tab_id

    if digest.status == "done":
        item.status = PlanItemStatus.DONE
        item.result_digest = digest.summary
        item.data = digest.data
    elif digest.status == "needs_user":
        # leave the item ACTIVE; route to ask_user
        decision = {"action": "ask_user", "question": digest.question or "Please confirm."}
        return {"plan": plan, "tabs": tabs, "delegations_used": delegations,
                "lead_decision": decision}
    else:  # failed
        if item.retries < ITEM_RETRY_CAP:
            item.retries += 1
            item.status = PlanItemStatus.PENDING  # retry
        else:
            item.status = PlanItemStatus.FAILED

    return {"plan": plan, "tabs": tabs, "delegations_used": delegations}


async def ask_user_node(state: LeadState) -> dict:
    """Bubble a worker escalation to the human (single HITL choke point)."""
    decision = state.get("lead_decision", {})
    question = decision.get("question", "The agent needs your input.")
    answer = interrupt({"question": question})
    # normalize the resume shape ({"answer": ...} per ws_handler) to text
    text = answer.get("answer", "") if isinstance(answer, dict) else str(answer)
    plan = list(state.get("plan", []))
    for i in plan:
        if i.id == state.get("active_item_id"):
            i.status = PlanItemStatus.PENDING  # re-attempt with the new info
            break
    return {"messages": [HumanMessage(content=text)], "plan": plan,
            "lead_decision": {"action": "resumed"}}


def route_after_plan_step(state: LeadState) -> Literal["worker", "ask_user_node", "__end__"]:
    action = state.get("lead_decision", {}).get("action")
    if action == "delegate":
        return "worker"
    if action == "ask_user":
        return "ask_user_node"
    return "__end__"


def route_after_integrate(state: LeadState) -> Literal["plan_step", "ask_user_node"]:
    if state.get("lead_decision", {}).get("action") == "ask_user":
        return "ask_user_node"
    return "plan_step"
