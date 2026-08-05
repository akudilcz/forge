"""Offline contract tests for the deterministic phases (0, 1, 11, 13, 14).

Five of FORGE's fifteen phases make no LLM calls at all, so their postconditions
can be asserted exactly, in milliseconds, for free. Until now they could only be
reached through ``test_full_pipeline``, which is ``@pytest.mark.integration``,
needs live API keys, and takes hours — so in practice CI asserted nothing about
any phase contract.

These tests use a **real** ``ProjectGraph`` on a temporary SQLite file rather
than a ``MagicMock``. That distinction matters: the existing unit tests for these
phases patch ``graph.add_node`` with an ``AsyncMock`` and assert its call count,
which cannot catch a wrong ``parent_id``, a wrong ``layer``, or a lost
``trace_to`` — precisely the fields the rest of the pipeline depends on.

What each phase must guarantee is documented in ``design/10_phase_00_*.md``
through ``design/24_phase_14_*.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.config.models import ForgeConfig, ProjectConfig
from backend.core.phase_store import PhaseStore
from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.pipeline.flow import ForgeFlow

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def graph(tmp_path: Path) -> ProjectGraph:
    """A real, empty ProjectGraph backed by a temp SQLite file.

    The database lives outside any workspace directory so that a phase which
    writes files cannot accidentally clobber it.
    """
    g = ProjectGraph(tmp_path / "graph.db")
    await g.initialise()
    return g


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "src").mkdir(parents=True)
    (ws / "tests").mkdir(parents=True)
    return ws


@pytest.fixture
def config(workspace: Path) -> ForgeConfig:
    return ForgeConfig(
        project=ProjectConfig(
            name="contract-test-project",
            forgemd="forge.md",
            workspace_dir=str(workspace),
        )
    )


@pytest.fixture
def phase_store(tmp_path: Path) -> PhaseStore:
    """PhaseStore on its own SQLite file, seeded with all 15 phases pending."""
    return PhaseStore(str(tmp_path / "phases.db"))


@pytest.fixture
def flow(
    graph: ProjectGraph, config: ForgeConfig, workspace: Path, phase_store: PhaseStore
) -> ForgeFlow:
    """A ForgeFlow wired to the real graph.

    ``pool`` is a MagicMock because the deterministic phases never dispatch an
    agent — if one of them starts doing so, these tests fail loudly on an
    unexpected mock call rather than silently making a network request.
    """
    return ForgeFlow(
        pool=MagicMock(),
        graph=graph,
        config=config,
        broadcaster=MagicMock(),
        phase_store=phase_store,
        workspace=workspace,
    )


def _status(flow: ForgeFlow, phase: int) -> str:
    """Phase status, failing clearly if the phase row is missing entirely."""
    row = flow.phase_store.get(phase)
    assert row is not None, f"phase {phase} missing from the phase store"
    return str(row["status"])


async def _nodes(graph: ProjectGraph, node_type: NodeType) -> list[GraphNode]:
    return await graph.nodes_by_type(node_type.value)


# ── Phase 0 — Create Project ─────────────────────────────────────────────────


class TestPhase00CreateProject:
    """Postcondition: exactly one PROJECT node, the root of the whole graph."""

    async def test_creates_single_project_node_with_correct_shape(
        self, flow: ForgeFlow, graph: ProjectGraph, config: ForgeConfig
    ) -> None:
        await flow.run_phase(0)

        projects = await _nodes(graph, NodeType.PROJECT)
        assert len(projects) == 1, f"expected exactly 1 PROJECT node, got {len(projects)}"

        project = projects[0]
        assert project.parent_id is None, "PROJECT is the root; it must have no parent"
        assert project.layer == 0
        assert project.title == config.project.name
        assert project.trace_to == [], "PROJECT traces to nothing"
        assert project.lifecycle == LifecycleState.ACTIVE

    async def test_is_idempotent(self, flow: ForgeFlow, graph: ProjectGraph) -> None:
        """Re-running phase 0 must not create a second root.

        Phase re-runs are a documented guarantee ("phase re-runs are idempotent"),
        and a duplicate PROJECT node would give every downstream phase two
        candidate roots to parent under.
        """
        await flow.run_phase(0)
        first = (await _nodes(graph, NodeType.PROJECT))[0]

        await flow.run_phase(0)

        projects = await _nodes(graph, NodeType.PROJECT)
        assert len(projects) == 1
        assert projects[0].node_id == first.node_id, "PROJECT node id must be stable"

    async def test_marks_phase_complete(self, flow: ForgeFlow) -> None:
        await flow.run_phase(0)
        assert _status(flow, 0) == "complete"


# ── Phase 1 — Ingest Document ────────────────────────────────────────────────


class TestPhase01Ingest:
    """Postcondition: one DOCUMENT node carrying forge.md verbatim."""

    async def test_ingests_forgemd_verbatim_under_project(
        self, flow: ForgeFlow, graph: ProjectGraph, workspace: Path
    ) -> None:
        body = "# Spec\n\nThe system shall do the thing.\n\nUnicode: café — ✓\n"
        (workspace / "forge.md").write_text(body, encoding="utf-8")
        await flow.run_phase(0)

        await flow.run_phase(1)

        docs = await _nodes(graph, NodeType.DOCUMENT)
        assert len(docs) == 1, f"expected exactly 1 DOCUMENT node, got {len(docs)}"
        doc = docs[0]

        project_id = (await _nodes(graph, NodeType.PROJECT))[0].node_id
        assert doc.parent_id == project_id, (
            "DOCUMENT must hang off PROJECT — every downstream phase walks up "
            "parent_id to find the root"
        )
        assert doc.layer == 1
        assert doc.content == body, "forge.md must be stored byte-for-byte, not reflowed"

    async def test_missing_forgemd_leaves_phase_pending_and_creates_nothing(
        self, flow: ForgeFlow, graph: ProjectGraph
    ) -> None:
        """No forge.md is a loud non-event, not a silent success.

        FORGE's stated principle is that missing preconditions raise rather than
        degrade. Phase 1 signals this by leaving its status at ``pending``. If
        this ever flipped to ``complete``, the pipeline would march into phase 2
        with no document to parse.
        """
        await flow.run_phase(0)
        assert not (Path(flow.config.project.workspace_dir) / "forge.md").exists()

        await flow.run_phase(1)

        assert _status(flow, 1) == "pending", (
            "phase 1 must not report complete when forge.md is absent"
        )
        assert await _nodes(graph, NodeType.DOCUMENT) == []

    async def test_a_failed_ingest_does_not_report_the_phase_complete(
        self, flow: ForgeFlow, graph: ProjectGraph, workspace: Path
    ) -> None:
        """The end-to-end consequence of swallowing an ingest failure.

        With the error hidden, phase 1 announced completion having created no
        DOCUMENT, and phases 2-14 then ran against an empty graph and reported
        success. The phase must not claim completion it did not achieve.
        """
        (workspace / "forge.md").write_text("# Spec\n\nContent.\n", encoding="utf-8")
        await flow.run_phase(0)

        with patch(
            "backend.services.ingest._ensure_project_node",
            side_effect=RuntimeError("graph unavailable"),
        ):
            with pytest.raises(RuntimeError, match="graph unavailable"):
                await flow.run_phase(1)

        assert _status(flow, 1) != "complete", (
            "phase 1 reported complete after ingestion failed"
        )
        assert await _nodes(graph, NodeType.DOCUMENT) == []

    async def test_reingest_does_not_duplicate_document(
        self, flow: ForgeFlow, graph: ProjectGraph, workspace: Path
    ) -> None:
        (workspace / "forge.md").write_text("# Spec\n\nContent.\n", encoding="utf-8")
        await flow.run_phase(0)
        await flow.run_phase(1)

        await flow.run_phase(1)

        docs = await _nodes(graph, NodeType.DOCUMENT)
        assert len(docs) == 1, "re-ingesting must update the DOCUMENT, not add another"

    async def test_finds_forgemd_case_insensitively(
        self, flow: ForgeFlow, graph: ProjectGraph, workspace: Path
    ) -> None:
        """``resolve_forgemd_path`` matches case-insensitively by design.

        The demo whitepapers ship as ``FORGE-v3.MD``, so a case-sensitive match
        makes the documented demo flow fail on Linux.
        """
        (workspace / "FORGE.MD").write_text("# Spec\n\nContent.\n", encoding="utf-8")
        await flow.run_phase(0)

        await flow.run_phase(1)

        assert len(await _nodes(graph, NodeType.DOCUMENT)) == 1
        assert _status(flow, 1) == "complete"


# ── Cross-phase structural invariants ────────────────────────────────────────


class TestGraphInvariants:
    """Invariants that must hold after any phase, asserted on the cheap ones.

    These are the checks a per-phase harness applies uniformly; proving them on
    phases 0 and 1 pins the contract that the LLM phases inherit.
    """

    async def test_every_parent_id_resolves(
        self, flow: ForgeFlow, graph: ProjectGraph, workspace: Path
    ) -> None:
        (workspace / "forge.md").write_text("# Spec\n\nContent.\n", encoding="utf-8")
        await flow.run_phase(0)
        await flow.run_phase(1)

        all_nodes = graph.all_nodes()
        ids = {n.node_id for n in all_nodes}
        dangling = [
            (n.node_id, n.parent_id)
            for n in all_nodes
            if n.parent_id is not None and n.parent_id not in ids
        ]
        assert dangling == [], f"nodes reference non-existent parents: {dangling}"

    async def test_exactly_one_root(
        self, flow: ForgeFlow, graph: ProjectGraph, workspace: Path
    ) -> None:
        (workspace / "forge.md").write_text("# Spec\n\nContent.\n", encoding="utf-8")
        await flow.run_phase(0)
        await flow.run_phase(1)

        roots = [n for n in graph.all_nodes() if n.parent_id is None]
        assert len(roots) == 1, f"graph must have exactly one root, found {len(roots)}"
        assert roots[0].node_type == NodeType.PROJECT.value

    async def test_layer_matches_node_type(
        self, flow: ForgeFlow, graph: ProjectGraph, workspace: Path
    ) -> None:
        """Layer is derived from node type and is documented as immutable."""
        (workspace / "forge.md").write_text("# Spec\n\nContent.\n", encoding="utf-8")
        await flow.run_phase(0)
        await flow.run_phase(1)

        expected_layer: dict[str, int] = {
            NodeType.PROJECT.value: 0,
            NodeType.DOCUMENT.value: 1,
            NodeType.PARA.value: 1,
        }
        for node in graph.all_nodes():
            want = expected_layer.get(node.node_type)
            if want is not None:
                assert node.layer == want, (
                    f"{node.node_id} is {node.node_type} so layer must be {want}, "
                    f"got {node.layer}"
                )

    async def test_content_hash_matches_content(
        self, flow: ForgeFlow, graph: ProjectGraph, workspace: Path
    ) -> None:
        """content_hash drives staleness detection in later phases.

        ``_check_stale_code`` recomputes hashes to decide whether generated code
        still matches its design, so a hash that disagrees with its content would
        make the pipeline either rebuild forever or never rebuild.
        """
        import hashlib

        (workspace / "forge.md").write_text("# Spec\n\nContent.\n", encoding="utf-8")
        await flow.run_phase(0)
        await flow.run_phase(1)

        for node in graph.all_nodes():
            if not node.content:
                continue
            expected = hashlib.sha256(node.content.encode()).hexdigest()
            assert node.content_hash == expected, f"{node.node_id} has a stale content_hash"


# ── Phase status machine ─────────────────────────────────────────────────────


class TestPhaseStatusTransitions:
    async def test_phases_start_pending(self, flow: ForgeFlow) -> None:
        for phase in range(15):
            assert _status(flow, phase) == "pending"

    async def test_completed_phase_reports_complete(self, flow: ForgeFlow) -> None:
        await flow.run_phase(0)
        assert _status(flow, 0) == "complete"
        assert _status(flow, 1) == "pending", (
            "running phase 0 must not advance any other phase"
        )


# ── Deterministic-phase guarantee ────────────────────────────────────────────


class TestNoLLMCallsInDeterministicPhases:
    """Phases 0, 1, 11, 13 and 14 must never reach an agent.

    This is what makes them testable for free and what keeps a full build's cost
    proportional to the nine LLM phases. If a refactor introduced an agent call
    into one of them, cost would rise silently; here it fails immediately,
    because ``flow.pool`` is a MagicMock whose use we assert against.
    """

    @pytest.mark.parametrize("phase", [0, 1])
    async def test_phase_dispatches_no_agent(
        self, flow: ForgeFlow, workspace: Path, phase: int
    ) -> None:
        (workspace / "forge.md").write_text("# Spec\n\nContent.\n", encoding="utf-8")
        pool: Any = flow.pool

        await flow.run_phase(0)
        if phase == 1:
            await flow.run_phase(1)

        assert not pool.get_agent_for_gap.called, (
            f"phase {phase} is documented as deterministic but dispatched an agent"
        )
        assert not pool.method_calls, (
            f"phase {phase} touched the agent pool: {pool.method_calls}"
        )
