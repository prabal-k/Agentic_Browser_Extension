# backend/tests/test_worker_llm.py
"""Tests for role→tool resolution and the role-scoped worker LLM factory."""


from agent_core.agent.roles import resolve_role_tools
from agent_core.schemas.orchestrator import WorkerRole


class TestResolveRoleTools:
    def test_extractor_resolves_to_its_three_tools(self):
        tools = resolve_role_tools(WorkerRole.EXTRACTOR)
        assert [t.name for t in tools] == ["read", "see", "extract_table"]

    def test_navigator_resolves_all_names(self):
        tools = resolve_role_tools(WorkerRole.NAVIGATOR)
        assert [t.name for t in tools] == ["navigate", "click", "scroll_down", "wait", "go_back"]

    def test_every_role_resolves_without_error(self):
        for role in WorkerRole:
            tools = resolve_role_tools(role)
            assert len(tools) >= 1


class TestGetWorkerLLM:
    def test_binds_only_role_tools(self):
        # get_llm with an Ollama-style name does not connect; binding is local.
        from agent_core.agent.llm_client import get_worker_llm
        llm = get_worker_llm(WorkerRole.EXTRACTOR, model_name="qwen2.5:32b-instruct")
        # bound tools live on the runnable; extract their names
        bound = llm.kwargs.get("tools") if hasattr(llm, "kwargs") else None
        assert bound is not None
        names = {t["function"]["name"] for t in bound}
        assert names == {"read", "see", "extract_table"}
