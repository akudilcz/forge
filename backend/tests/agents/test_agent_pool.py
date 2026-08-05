"""Tests for AgentPool compilation and queries."""

from unittest.mock import MagicMock

import pytest

from backend.agents.pool import AgentPool, UnknownAgentError


@pytest.fixture
def pool() -> AgentPool:
    broadcaster = MagicMock()
    return AgentPool(broadcaster)


@pytest.fixture
def initialised_pool(pool: AgentPool) -> AgentPool:
    pool._agents["Test Agent"] = MagicMock(role="Test Agent")
    return pool


def test_empty_pool_ids(pool: AgentPool) -> None:
    assert pool.all_ids() == []


def test_get_unknown_agent_raises(pool: AgentPool) -> None:
    with pytest.raises(UnknownAgentError):
        pool.get("nonexistent")


def test_get_agent_for_gap_no_mapping(pool: AgentPool) -> None:
    from backend.analysis.gaps import GapType

    result = pool.get_agent_for_gap(GapType.UNCHUNKED_DOCUMENT)
    assert result is None


def test_gap_agent_mapping_covers_all_gap_types() -> None:
    from backend.agents.definitions import GAP_AGENT_MAPPING
    from backend.analysis.gaps import GapType

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
        assert gap_type in GAP_AGENT_MAPPING, f"GapType.{gap_type.name} not mapped"


def test_get_agent_for_gap_returns_agent_when_registered() -> None:
    from backend.analysis.gaps import GapType

    broadcaster = MagicMock()
    pool = AgentPool(broadcaster)
    mock_agent = MagicMock()
    pool._gap_agents[GapType.UNCHUNKED_DOCUMENT.value] = mock_agent

    result = pool.get_agent_for_gap(GapType.UNCHUNKED_DOCUMENT)
    assert result is mock_agent


def test_pool_introspection_with_multiple_agents() -> None:
    broadcaster = MagicMock()
    pool = AgentPool(broadcaster)
    for name in ["agent-a", "agent-b", "agent-c"]:
        pool._agents[name] = MagicMock(role=name)

    assert len(pool.all_ids()) == 3
