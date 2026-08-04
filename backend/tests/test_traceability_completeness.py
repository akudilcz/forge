"""Traceability and deliverable-completeness regressions.

These four defects shared a shape: a mechanism that looked correct, logged
success, and did nothing. None of them could fail a build, because each one
produced a *plausible* artefact — an empty table, a 0% figure, a bundle missing
one directory, a reparent that reported ``None`` children.

Traceability is FORGE's headline guarantee, so a silently empty matrix is worse
than a crash: it ships a document asserting coverage that was never computed.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType


def _node(
    node_id: str,
    node_type: NodeType,
    *,
    parent_id: str | None = None,
    trace_to: list[str] | None = None,
    properties: dict[str, Any] | None = None,
    content: str = "content",
) -> GraphNode:
    return GraphNode(
        node_id=node_id,
        node_type=node_type.value,
        title=node_id,
        content=content,
        parent_id=parent_id,
        trace_to=trace_to or [],
        properties=properties or {},
        lifecycle=LifecycleState.ACTIVE,
    )


# ── Traceability matrix ──────────────────────────────────────────────────────


class TestForwardTraceMatrix:
    """The matrix must follow the links the pipeline actually creates.

    Phase 7 creates LLRs with ``parent_id=<hlr_id>`` and an empty ``trace_to``.
    Joining HLR→LLR on ``trace_to`` therefore matched nothing, and every row of
    the shipped 06-Traceability-Matrix.md rendered as
    ``| HLR-0001 | — | — | — | — | — |`` while the Gaps section reported
    "No traceability gaps detected".
    """

    def _render(self, llr: GraphNode) -> str:
        from backend.crew.deliverables import _render_forward_trace

        hlr = _node("HLR-0001", NodeType.HLR)
        design = _node(
            "DESIGN-0001",
            NodeType.DESIGN,
            trace_to=["LLR-0001"],
            properties={"file_path": "src/core.py"},
        )
        case = _node(
            "CASE_LLR-0001",
            NodeType.CASE_LLR,
            trace_to=["LLR-0001"],
            properties={"file_path": "tests/test_core.py"},
        )
        lines: list[str] = []
        _render_forward_trace(lines, [hlr], [llr], [design], [case])
        return "\n".join(lines)

    def test_resolves_hlr_to_llr_by_containment(self) -> None:
        """The shape the pipeline actually produces: parent_id, no trace_to."""
        llr = _node("LLR-0001", NodeType.LLR, parent_id="HLR-0001")

        table = self._render(llr)

        assert "LLR-0001" in table, (
            "the LLR is missing from the matrix — the HLR→LLR join is not "
            "following parent_id, so the shipped matrix is empty"
        )
        assert "DESIGN-0001" in table
        assert "`src/core.py`" in table
        assert "CASE_LLR-0001" in table
        assert "| HLR-0001 | — | — | — | — | — |" not in table

    def test_still_resolves_by_trace_to(self) -> None:
        """Graphs that do populate trace_to must keep working."""
        llr = _node("LLR-0001", NodeType.LLR, trace_to=["HLR-0001"])
        assert "LLR-0001" in self._render(llr)

    def test_resolves_when_both_are_set(self) -> None:
        llr = _node("LLR-0001", NodeType.LLR, parent_id="HLR-0001", trace_to=["HLR-0001"])
        table = self._render(llr)
        assert table.count("LLR-0001") >= 1
        assert "| HLR-0001 | — | — | — | — | — |" not in table

    def test_a_genuinely_unlinked_hlr_still_renders_as_a_gap(self) -> None:
        """The dash row must remain possible — it is a real signal."""
        llr = _node("LLR-0001", NodeType.LLR, parent_id="HLR-9999")
        assert "| HLR-0001 | — | — | — | — | — |" in self._render(llr)


class TestReverseTraceMatrix:
    def test_source_file_maps_back_to_its_hlr(self) -> None:
        from backend.crew.deliverables import _render_reverse_trace

        llr = _node("LLR-0001", NodeType.LLR, parent_id="HLR-0001")
        design = _node(
            "DESIGN-0001",
            NodeType.DESIGN,
            trace_to=["LLR-0001"],
            properties={"file_path": "src/core.py"},
        )
        lines: list[str] = []
        _render_reverse_trace(lines, [design], {"LLR-0001": llr})
        table = "\n".join(lines)

        assert "HLR-0001" in table, (
            "reverse trace lost the HLR — a reviewer cannot tell which "
            "requirement a source file implements"
        )


# ── Coverage report ──────────────────────────────────────────────────────────


class TestCoverageMetrics:
    def test_passing_results_are_counted(self) -> None:
        """`record_results` writes "passed"; the report compared against "pass".

        The mismatch meant the delivered Coverage Report always reported a 0%
        pass rate regardless of the real outcome.
        """
        from backend.crew.deliverables import _compute_coverage_metrics

        graph = _FakeGraph(
            [
                _node("RESULT-0001", NodeType.RESULT, properties={"status": "passed"}),
                _node("RESULT-0002", NodeType.RESULT, properties={"status": "passed"}),
                _node("RESULT-0003", NodeType.RESULT, properties={"status": "failed"}),
            ]
        )

        metrics = _compute_coverage_metrics(graph)

        assert metrics["total_results"] == 3
        assert metrics["passed"] == 2, (
            f"expected 2 passing results, got {metrics['passed']} — the report "
            "is comparing against the wrong status literal"
        )

    def test_status_literal_matches_what_the_recorder_writes(self) -> None:
        """Pin the contract so the two modules cannot drift apart again."""
        import inspect

        from backend.crew import deliverables, result_recorder

        recorder_src = inspect.getsource(result_recorder)
        assert '"passed"' in recorder_src or "'passed'" in recorder_src

        metrics_src = inspect.getsource(deliverables._compute_coverage_metrics)
        assert '== "pass"' not in metrics_src, (
            "coverage metrics compare against 'pass', but the recorder stores "
            "'passed'"
        )


class _FakeGraph:
    def __init__(self, nodes: list[GraphNode]) -> None:
        self._nodes = nodes

    def all_nodes(self) -> list[GraphNode]:
        return list(self._nodes)


# ── Deliverables bundle ──────────────────────────────────────────────────────


class TestBundleCompleteness:
    def test_bundle_includes_the_tracing_package(self, tmp_path: Path) -> None:
        """Every generated file starts `from tracing import traces`.

        Omitting the package shipped a bundle whose every module raised
        ImportError on its first line.
        """
        from backend.crew.deliverables import _copy_workspace_files

        ws = tmp_path / "workspace"
        (ws / "src").mkdir(parents=True)
        (ws / "tests").mkdir()
        (ws / "tracing").mkdir()
        (ws / "src" / "core.py").write_text("from tracing import traces\n")
        (ws / "tests" / "test_core.py").write_text("from tracing import traces\n")
        (ws / "tracing" / "__init__.py").write_text("from .decorator import traces\n")
        (ws / "tracing" / "decorator.py").write_text("def traces(*a, **k):\n    return lambda f: f\n")
        (ws / "requirements.txt").write_text("pytest\n")

        dest = tmp_path / "pack"
        dest.mkdir()
        _copy_workspace_files(ws, dest)

        assert (dest / "tracing" / "__init__.py").exists(), (
            "the tracing package is missing — the shipped code cannot be imported"
        )
        assert (dest / "tracing" / "decorator.py").exists()
        assert (dest / "requirements.txt").exists()

    def test_packed_sources_actually_import(self, tmp_path: Path) -> None:
        """The strongest form: extract the pack and import a generated module."""
        import subprocess
        import sys

        from backend.crew.deliverables import _copy_workspace_files, _create_zip

        ws = tmp_path / "workspace"
        (ws / "src").mkdir(parents=True)
        (ws / "tracing").mkdir()
        (ws / "src" / "core.py").write_text(
            "from tracing import traces\n\n\n@traces('LLR-0001')\ndef go():\n    return 1\n"
        )
        (ws / "tracing" / "__init__.py").write_text("from .decorator import traces\n")
        (ws / "tracing" / "decorator.py").write_text(
            "def traces(*ids, **kw):\n"
            "    def deco(fn):\n"
            "        fn._trace_llrs = list(ids)\n"
            "        return fn\n"
            "    return deco\n"
        )

        dest = tmp_path / "pack"
        dest.mkdir()
        _copy_workspace_files(ws, dest)
        zip_path = tmp_path / "deliverables.zip"
        _create_zip(dest, zip_path)

        extract = tmp_path / "extracted"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract)

        root = extract / dest.name
        proc = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); import src.core"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, (
            f"the delivered bundle does not import:\n{proc.stderr}"
        )


# ── Deletion safety ──────────────────────────────────────────────────────────


class TestDeleteNodeReparentsChildren:
    """Deletion must not strand a subtree.

    The reparent walked `self._g.successors(node_id)`, but the NetworkX graph
    only gains edges through `add_edge`, which no production path calls. So it
    always found zero children, the reparent never ran, and every deleted node
    left its subtree pointing at a row that no longer existed.
    """

    @pytest.fixture
    async def graph(self, tmp_path: Path) -> ProjectGraph:
        g = ProjectGraph(tmp_path / "g.db")
        await g.initialise()
        await g.add_node(_node("PARA-0001", NodeType.PARA))
        await g.add_node(_node("HLR-0002", NodeType.HLR, parent_id="PARA-0001"))
        await g.add_node(_node("LLR-0001", NodeType.LLR, parent_id="HLR-0002"))
        await g.add_node(_node("LLR-0002", NodeType.LLR, parent_id="HLR-0002"))
        return g

    async def test_children_are_reparented_to_the_grandparent(
        self, graph: ProjectGraph
    ) -> None:
        await graph.delete_node("HLR-0002")

        for child_id in ("LLR-0001", "LLR-0002"):
            child = await graph.node(child_id)
            assert child is not None, f"{child_id} vanished with its parent"
            assert child.parent_id == "PARA-0001", (
                f"{child_id}.parent_id is {child.parent_id!r} — it should have "
                "been reparented to the grandparent, not left dangling"
            )

    async def test_no_node_is_left_with_a_dangling_parent(
        self, graph: ProjectGraph
    ) -> None:
        await graph.delete_node("HLR-0002")

        ids = {n.node_id for n in graph.all_nodes()}
        dangling = [
            (n.node_id, n.parent_id)
            for n in graph.all_nodes()
            if n.parent_id is not None and n.parent_id not in ids
        ]
        assert dangling == [], f"deletion stranded nodes: {dangling}"

    async def test_deleting_a_root_child_leaves_children_at_the_root(
        self, graph: ProjectGraph
    ) -> None:
        """Reparent target may legitimately be None."""
        await graph.delete_node("PARA-0001")

        hlr = await graph.node("HLR-0002")
        assert hlr is not None
        assert hlr.parent_id is None

    async def test_deleting_a_leaf_is_unaffected(self, graph: ProjectGraph) -> None:
        await graph.delete_node("LLR-0001")

        assert await graph.node("LLR-0001") is None
        remaining = await graph.node("LLR-0002")
        assert remaining is not None
        assert remaining.parent_id == "HLR-0002"
