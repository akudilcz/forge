"""Fixtures for the adversarial robustness harness (see ``harness.py``)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.config.models import ForgeConfig, ProjectConfig
from backend.core.phase_store import PhaseStore
from backend.graph.engine import ProjectGraph
from backend.pipeline.flow import ForgeFlow
from backend.tests.robustness.harness import write_spec


@pytest.fixture
async def graph(tmp_path: Path) -> ProjectGraph:
    g = ProjectGraph(tmp_path / "graph.db")
    await g.initialise()
    return g


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    write_spec(ws)
    return ws


@pytest.fixture
def flow(graph: ProjectGraph, workspace: Path, tmp_path: Path) -> ForgeFlow:
    config = ForgeConfig(
        project=ProjectConfig(
            name="robustness-test", forgemd="forge.md", workspace_dir=str(workspace)
        )
    )
    # Offline scripted test: every real checker seam is patched, but build_llm
    # still constructs a client — declare the endpoint keyless explicitly.
    config.llm.keyless = True
    pool = MagicMock()
    pool.get_agent_for_gap.return_value = MagicMock()
    return ForgeFlow(
        pool=pool,
        graph=graph,
        config=config,
        broadcaster=MagicMock(),
        phase_store=PhaseStore(str(tmp_path / "phases.db")),
        workspace=workspace,
    )
