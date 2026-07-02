"""Tests for consolidated worker tools + the name→object registry."""

from agent_core.agent.roles import ROLE_TOOL_NAMES
from agent_core.tools.consolidated_tools import (
    WORKER_TOOL_OBJECTS,
    extract_table,
    finish_subgoal,
    read,
    see,
    submit,
    type_credential,
)


class TestToolSchemas:
    def test_tools_have_expected_names(self):
        assert read.name == "read"
        assert see.name == "see"
        assert extract_table.name == "extract_table"
        assert submit.name == "submit"
        assert type_credential.name == "type_credential"
        assert finish_subgoal.name == "finish_subgoal"

    def test_read_takes_what_arg(self):
        # LangChain derives the arg schema from the signature.
        assert "what" in read.args

    def test_finish_subgoal_takes_summary_and_optional_data(self):
        assert "summary" in finish_subgoal.args
        assert "data" in finish_subgoal.args


class TestRegistry:
    def test_registry_covers_every_role_tool_name(self):
        needed = {name for names in ROLE_TOOL_NAMES.values() for name in names}
        missing = needed - set(WORKER_TOOL_OBJECTS)
        assert missing == set(), f"registry missing objects for: {missing}"

    def test_registry_values_are_tools_with_matching_names(self):
        for name, obj in WORKER_TOOL_OBJECTS.items():
            assert obj.name == name
