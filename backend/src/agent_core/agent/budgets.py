"""Loop-control budgets and the destructive-action safety gate.

Replaces the old global 25-iteration / 12-action counters with three tiers:
per-worker, per-item retries, and a whole-task lead ceiling.
"""

from agent_core.schemas.actions import ActionType

# --- Three-tier budgets (design §6) ---
WORKER_ACTION_CAP: int = 8      # one subgoal can't run away
ITEM_RETRY_CAP: int = 2         # limit re-delegation of a failing item
LEAD_DELEGATION_CAP: int = 15   # whole-task ceiling

# --- Safety gate: actions that always need human confirmation (design §6) ---
# Static classification, not an LLM risk judgment. A worker about to run one
# of these auto-bubbles needs_user unless pre-authorized. The ActionType enum
# has no submit_order/pay/send_email members, so we gate the concrete browser
# actions that can mutate external state irreversibly. Higher-level intents
# (place order, pay) are recognized by the worker safety gate in P5 via the
# tool's own risk_level; this set is the enum-level backstop.
DESTRUCTIVE_ACTIONS: frozenset[ActionType] = frozenset({
    ActionType.UPLOAD_FILE,
    ActionType.EVALUATE_JS,
    ActionType.HANDLE_DIALOG,
})


def is_destructive(action_type: ActionType) -> bool:
    """Return True if this action type must be human-confirmed by default."""
    return action_type in DESTRUCTIVE_ACTIONS
