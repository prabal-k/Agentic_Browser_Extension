"""Split state for the orchestrator harness.

LeadState is small and survives the whole run. WorkerState is ephemeral and
thrown away after its ResultDigest is folded back into the ledger. The big,
noisy fields (page_context, action_history) live ONLY in WorkerState and never
enter LeadState — that is the context-isolation invariant.
"""

from typing import TypedDict

from agent_core.schemas.dom import PageContext
from agent_core.schemas.orchestrator import PlanItem, ResultDigest, WorkerRole


class LeadState(TypedDict):
    """Coordinator state. No browser tools, no DOM, no action history."""

    original_goal: str
    plan: list[PlanItem]            # the living ledger
    active_item_id: str | None
    lead_decision: dict             # last plan_step output
    delegations_used: int           # lead-level cap (see budgets.LEAD_DELEGATION_CAP)
    tabs: dict[str, str]            # tab_id -> what's there ("gmail inbox")
    stored_credentials: dict        # SECURITY: tracked separately (see spec §6)
    model_name: str
    api_keys: dict | None
    messages: list                  # lead's own short history


class WorkerState(TypedDict):
    """Ephemeral per-subgoal state. Discarded after result_digest is produced."""

    role: WorkerRole
    subgoal: str
    done_criteria: str
    tab_id: str | None
    page_context: PageContext | None   # big/noisy — never leaves the worker
    action_history: list               # big/noisy — never leaves the worker
    actions_used: int                  # per-worker budget counter
    result_digest: ResultDigest | None
    model_name: str
    api_keys: dict | None


def create_lead_state(
    goal_text: str,
    model_name: str,
    api_keys: dict | None = None,
    prior_messages: list | None = None,
) -> LeadState:
    """Fresh lead state for a new task."""
    return LeadState(
        original_goal=goal_text,
        plan=[],
        active_item_id=None,
        lead_decision={},
        delegations_used=0,
        tabs={},
        stored_credentials={},
        model_name=model_name,
        api_keys=api_keys,
        messages=prior_messages or [],
    )


def create_worker_state(
    role: WorkerRole,
    subgoal: str,
    done_criteria: str,
    model_name: str,
    tab_id: str | None = None,
    page_context: PageContext | None = None,
    api_keys: dict | None = None,
) -> WorkerState:
    """Fresh worker state for a single delegated subgoal."""
    return WorkerState(
        role=role,
        subgoal=subgoal,
        done_criteria=done_criteria,
        tab_id=tab_id,
        page_context=page_context,
        action_history=[],
        actions_used=0,
        result_digest=None,
        model_name=model_name,
        api_keys=api_keys,
    )
