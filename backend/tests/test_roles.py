"""Tests for the role→tool static registry (the 'don't dump all tools' guarantee)."""

from agent_core.agent.roles import (
    LEAD_TOOL_NAMES,
    MAX_TOOLS_PER_ROLE,
    ROLE_TOOL_NAMES,
    tools_for_role,
)
from agent_core.schemas.orchestrator import WorkerRole


class TestRoleRegistry:
    def test_every_role_has_a_menu(self):
        for role in WorkerRole:
            assert role in ROLE_TOOL_NAMES
            assert len(ROLE_TOOL_NAMES[role]) >= 1

    def test_no_role_exceeds_the_cap(self):
        for role, names in ROLE_TOOL_NAMES.items():
            assert len(names) <= MAX_TOOLS_PER_ROLE, f"{role} has too many tools"

    def test_lead_has_no_browser_tools(self):
        # The coordinator's menu is exactly four coordination tools.
        assert LEAD_TOOL_NAMES == ["delegate", "update_plan", "ask_user", "finish"]
        browser_names = {n for names in ROLE_TOOL_NAMES.values() for n in names}
        assert not (set(LEAD_TOOL_NAMES) & browser_names)

    def test_tools_for_role_accessor(self):
        assert tools_for_role(WorkerRole.EXTRACTOR) == ["read", "see", "extract_table"]

    def test_no_role_menu_has_duplicates(self):
        for role, names in ROLE_TOOL_NAMES.items():
            assert len(names) == len(set(names)), f"{role} has duplicate tools"
