"""Smoke tests for ForgeBuilder — covers production dependency wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config.models import ForgeConfig
from backend.core.forge_builder import ForgeBuilder


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def config() -> ForgeConfig:
    return ForgeConfig()


def test_init_stores_inputs(workspace: Path, config: ForgeConfig) -> None:
    b = ForgeBuilder(config=config, workspace=workspace)
    assert b._workspace == workspace
    assert b._config is config
    assert b._broadcaster is not None
    assert b._db_path.endswith("forge.db")
    assert b.graph is None
    assert b.pool is None


def test_init_custom_db_and_broadcaster(workspace: Path, config: ForgeConfig) -> None:
    b = ForgeBuilder(
        config=config,
        workspace=workspace,
        broadcaster="bcast-sentinel",
        db_path=workspace / "custom.db",
    )
    assert b._broadcaster == "bcast-sentinel"
    assert b._db_path == str(workspace / "custom.db")


def test_ensure_dirs_creates_workspace_layout(workspace: Path, config: ForgeConfig) -> None:
    b = ForgeBuilder(config=config, workspace=workspace)
    b._ensure_dirs()
    assert workspace.is_dir()
    assert (workspace / "src").is_dir()
    assert (workspace / "tests").is_dir()
    assert Path(b._db_path).parent.is_dir()


def test_build_tools_includes_mission_feedback_tools(
    workspace: Path, config: ForgeConfig
) -> None:
    """Rank-3: the builder path must register the same mission feedback
    tools as the server path (lifespan.py) — evaluate_progress,
    check_trace_quality, workspace_doctor."""
    b = ForgeBuilder(config=config, workspace=workspace)
    b._ensure_dirs()
    tools = b._build_tools(MagicMock())
    names = {t.name for t in tools}
    assert {"evaluate_progress", "check_trace_quality", "workspace_doctor"} <= names


@pytest.mark.asyncio
async def test_build_wires_graph_pool_and_flow(
    workspace: Path, config: ForgeConfig
) -> None:
    """Full build path with all heavyweight deps mocked."""
    b = ForgeBuilder(config=config, workspace=workspace)

    fake_graph = MagicMock()
    fake_graph.initialise = AsyncMock()
    fake_pool = MagicMock()

    with (
        patch("backend.core.forge_builder.ProjectGraph", return_value=fake_graph),
        patch("backend.core.forge_builder.AgentFactory") as fac_cls,
        patch("backend.core.forge_builder.PhaseStore") as ps_cls,
        patch("backend.core.forge_builder.ForgeFlow") as flow_cls,
    ):
        fac_instance = MagicMock()
        fac_instance.create_pool = AsyncMock(return_value=fake_pool)
        fac_cls.return_value = fac_instance
        ps_cls.return_value = MagicMock()
        flow_cls.return_value = "flow-sentinel"

        # ForgeFlow is patched, so build() actually hands back the sentinel.
        flow: object = await b.build()

    assert flow == "flow-sentinel"
    assert b.graph is fake_graph
    fake_graph.initialise.assert_awaited_once()
    flow_cls.assert_called_once()
