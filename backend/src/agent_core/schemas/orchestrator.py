"""Data contracts that cross the lead↔worker boundary.

These are deliberately small. Only ResultDigest travels worker→lead; the
worker's page_context and action_history never leave the worker subgraph
(the context-isolation invariant from the design spec).
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class WorkerRole(str, Enum):
    """The role a worker plays. Role determines its tool menu (see agent/roles.py)."""

    NAVIGATOR = "navigator"
    EXTRACTOR = "extractor"
    FORM_FILLER = "form_filler"
    VERIFIER = "verifier"
    AUTH = "auth"


class PlanItemStatus(str, Enum):
    """Lifecycle of a single plan item in the living ledger."""

    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class PlanItem(BaseModel):
    """One coarse subgoal in the lead's living plan ledger.

    A subgoal is a *chunk* (roughly 3–8 worker actions), not an atomic click.
    done_criteria is a natural-language, checkable condition the worker (and
    the lead's integrate step) uses to decide completion.
    """

    id: str
    subgoal: str
    role: WorkerRole
    done_criteria: str
    status: PlanItemStatus = PlanItemStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)
    result_digest: str = ""
    # structured values the worker extracted (e.g. prices), folded from ResultDigest.data
    data: dict | None = None


class ResultDigest(BaseModel):
    """The compact contract a worker returns to the lead.

    No transcript, no DOM — just outcome + extracted data + any escalation.
    """

    status: Literal["done", "failed", "needs_user"]
    summary: str
    data: dict | None = None
    needs_user: bool = False
    question: str | None = None
    tab_id: str | None = None
    actions_used: int = 0
