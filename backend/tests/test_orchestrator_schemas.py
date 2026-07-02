"""Tests for the lead↔worker data contracts."""

from agent_core.schemas.orchestrator import (
    WorkerRole,
    PlanItemStatus,
    PlanItem,
    ResultDigest,
)


class TestWorkerRole:
    def test_has_five_roles(self):
        assert {r.value for r in WorkerRole} == {
            "navigator", "extractor", "form_filler", "verifier", "auth"
        }


class TestPlanItem:
    def test_defaults(self):
        item = PlanItem(
            id="item_1",
            subgoal="Open the login page",
            role=WorkerRole.NAVIGATOR,
            done_criteria="URL contains /login",
        )
        assert item.status == PlanItemStatus.PENDING
        assert item.depends_on == []
        assert item.result_digest == ""

    def test_round_trips_through_json(self):
        item = PlanItem(
            id="item_2",
            subgoal="Log in",
            role=WorkerRole.AUTH,
            done_criteria="Account menu visible",
            depends_on=["item_1"],
        )
        restored = PlanItem.model_validate_json(item.model_dump_json())
        assert restored == item
        assert restored.role is WorkerRole.AUTH

    def test_carries_structured_data(self):
        item = PlanItem(
            id="i3", subgoal="read price", role=WorkerRole.EXTRACTOR,
            done_criteria="price captured", data={"price": "$38"},
        )
        restored = PlanItem.model_validate_json(item.model_dump_json())
        assert restored.data == {"price": "$38"}

    def test_data_defaults_to_none(self):
        item = PlanItem(
            id="i4", subgoal="go", role=WorkerRole.NAVIGATOR, done_criteria="there",
        )
        assert item.data is None


class TestResultDigest:
    def test_minimal_done_digest(self):
        d = ResultDigest(status="done", summary="Logged in")
        assert d.needs_user is False
        assert d.data is None
        assert d.actions_used == 0

    def test_needs_user_digest_round_trips(self):
        d = ResultDigest(
            status="needs_user",
            summary="Found a Delete Account button",
            needs_user=True,
            question="Confirm account deletion?",
            actions_used=3,
        )
        restored = ResultDigest.model_validate_json(d.model_dump_json())
        assert restored == d
        assert restored.question == "Confirm account deletion?"
