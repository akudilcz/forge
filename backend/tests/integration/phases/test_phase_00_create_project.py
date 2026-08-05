"""Phase 0 (Create Project) in isolation, on a fully wired ForgeFlow.

Phase 0 is deterministic — no agent, no LLM call — so these tests are free to
run repeatedly. They differ from the offline contract tests in
``backend/tests/test_phase_contracts.py`` by exercising the *production* wiring
(``ForgeBuilder`` builds the real graph, tool registry, agent pool and phase
store) exactly as the integration pipeline does.

Design reference: design/10_phase_00_create_project.md
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.config.models import ForgeConfig
from backend.core.forge_builder import ForgeBuilder
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.pipeline.flow import ForgeFlow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(900)]

PROJECT_NAME = "phase00-integration"


@pytest.fixture
async def flow(integration_config: ForgeConfig, tmp_path: Path) -> ForgeFlow:
    """A production-wired ForgeFlow over an empty workspace.

    The graph DB lives outside the workspace so tooling cannot clobber it.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    config = integration_config.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = PROJECT_NAME

    builder = ForgeBuilder(config=config, workspace=workspace, db_path=tmp_path / "forge.db")
    return await builder.build()


async def _project_nodes(flow: ForgeFlow) -> list[GraphNode]:
    nodes: list[GraphNode] = await flow.graph.nodes_by_type(NodeType.PROJECT.value)
    return nodes


def _phase_status(flow: ForgeFlow, phase: int) -> str:
    row = flow.phase_store.get(phase)
    assert row is not None, f"phase {phase} missing from phase store"
    return str(row["status"])


# ── Happy path ───────────────────────────────────────────────────────────────


async def test_creates_single_project_node_with_correct_shape(flow: ForgeFlow) -> None:
    """Phase 0 must create exactly one PROJECT node: the root of the graph."""
    await flow.run_phase(0)

    projects = await _project_nodes(flow)
    assert len(projects) == 1, f"expected exactly 1 PROJECT node, got {len(projects)}"

    project = projects[0]
    assert project.parent_id is None, "PROJECT is the root; it must have no parent"
    assert project.layer == 0
    assert project.title == PROJECT_NAME
    assert project.trace_to == [], "PROJECT never traces to anything"
    assert project.lifecycle == LifecycleState.ACTIVE
    assert _phase_status(flow, 0) == "complete"


async def test_project_is_the_only_node_and_only_root(flow: ForgeFlow) -> None:
    """Phase 0 creates the PROJECT node and nothing else."""
    await flow.run_phase(0)

    all_nodes = flow.graph.all_nodes()
    assert len(all_nodes) == 1, (
        f"phase 0 must create only the PROJECT node, got "
        f"{[(n.node_id, n.node_type) for n in all_nodes]}"
    )
    roots = [n for n in all_nodes if n.parent_id is None]
    assert len(roots) == 1
    assert roots[0].node_type == NodeType.PROJECT.value


# ── Robustness: idempotent re-run ────────────────────────────────────────────


async def test_rerun_creates_no_duplicate_project(flow: ForgeFlow) -> None:
    """Re-running phase 0 must not mint a second root."""
    await flow.run_phase(0)
    first_id = (await _project_nodes(flow))[0].node_id

    await flow.run_phase(0)
    await flow.run_phase(0)

    projects = await _project_nodes(flow)
    assert len(projects) == 1, "phase 0 re-run duplicated the PROJECT node"
    assert projects[0].node_id == first_id, "PROJECT node id must be stable across re-runs"
    assert _phase_status(flow, 0) == "complete"


# ── Gap analyser: no phase-relevant structural gaps ──────────────────────────


async def test_gap_analyser_reports_no_phase_zero_gaps(flow: ForgeFlow) -> None:
    """Phase 0 has no gap type, so no gap may target its output.

    Downstream phases legitimately see gaps against the PROJECT node
    (UNARCHITECTED for phase 4, UNSUITED for phase 9) — those are the very
    holes later phases exist to fill. Anything else after phase 0 is a bug.
    """
    await flow.run_phase(0)

    gaps = GapAnalyser().analyse(flow.graph)
    downstream_only = {GapType.UNARCHITECTED, GapType.UNSUITED}
    unexpected = [g for g in gaps if g.type not in downstream_only]
    assert unexpected == [], (
        f"phase 0 left unexpected gaps: {[(g.type, g.node_id) for g in unexpected]}"
    )
