"""Tests for AgentFactory and AgentPool."""

from unittest.mock import MagicMock, patch

from backend.agents.definitions import AGENT_REGISTRY, GAP_AGENT_MAPPING, AgentRole
from backend.analysis.gaps import GapType


def test_gap_agent_mapping_covers_all_gap_types() -> None:
    # UNSYNCED_DESIGN/TEST are handled by workspace_sync (no agent)
    # EMPTY_TRACE/CIRCULAR_TRACE are structural validations (no agent)
    programmatic = {
        GapType.UNSYNCED_DESIGN,
        GapType.UNSYNCED_TEST,
        GapType.EMPTY_TRACE,
        GapType.CIRCULAR_TRACE,
    }
    for gap_type in GapType:
        if gap_type in programmatic:
            continue
        assert gap_type in GAP_AGENT_MAPPING, f"GapType.{gap_type.name} has no agent mapping"


def test_all_roles_in_registry() -> None:
    for role in AgentRole:
        assert role in AGENT_REGISTRY, f"AgentRole.{role.name} not in AGENT_REGISTRY"


def test_key_agent_tool_permissions() -> None:
    """Tool permissions are defined in ToolRegistry, not AgentDefinition."""
    from backend.tools.registry import ToolRegistry

    registry = ToolRegistry()
    qa_tools = registry._role_permissions[AgentRole.QUALITY_AUDITOR]
    da_tools = registry._role_permissions[AgentRole.DESIGN_ARCHITECT]
    assert "file_read" in qa_tools
    assert "graph_read" in da_tools
    assert "graph_add_node" in da_tools


def test_agent_factory_creates_langgraph_agent_with_config() -> None:
    from backend.agents.factory import AgentFactory

    mock_config = MagicMock()
    mock_config.llm.base_url = "http://localhost:11434/v1"
    mock_config.llm.api_key_env = "OLLAMA_API_KEY"
    mock_config.llm.request_timeout = 120
    mock_config.llm.options.temperature = 0.8
    mock_config.llm.agents = {"Software Engineer": "test-model-123"}

    mock_registry = MagicMock()
    mock_registry.get_tools_for_role.return_value = []

    factory = AgentFactory(mock_registry, mock_config)
    defn = AGENT_REGISTRY[AgentRole.SOFTWARE_ENGINEER]
    mock_graph = MagicMock()

    with (
        patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm,
        patch("backend.agents.factory.create_react_agent", return_value=mock_graph) as mock_cra,
    ):
        result = factory.create_agent(defn)

        llm_kwargs = mock_llm.call_args[1]
        assert llm_kwargs["base_url"] == "http://localhost:11434/v1"
        # timeout is an httpx.Timeout with read=config value
        import httpx

        assert isinstance(llm_kwargs["timeout"], httpx.Timeout)
        assert llm_kwargs["timeout"].read == 120.0
        model = llm_kwargs["model"]
        assert model == "test-model-123"
        assert mock_cra.called
        assert result is mock_graph
