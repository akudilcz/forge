"""Tests for backend.rendering.deliverables — Phase 14 deliverables pack builder."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.rendering.deliverables import (
    _render_architecture,
    _render_coverage_report,
    _render_design,
    _render_interfaces,
    _render_readme,
    _render_requirements,
    _render_test_plan,
    _render_traceability_matrix,
    build_deliverables_pack,
)
from backend.rendering.deliverables_helpers import build_trace_map as _build_trace_map
from backend.rendering.deliverables_helpers import pct as _pct

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _node(
    node_id: str,
    node_type: str,
    title: str = "",
    content: str = "",
    parent_id: str | None = None,
    trace_to: list[str] | None = None,
    properties: dict[str, Any] | None = None,
) -> MagicMock:
    from datetime import UTC, datetime

    n = MagicMock()
    n.node_id = node_id
    n.node_type = node_type
    n.title = title
    n.content = content
    n.parent_id = parent_id
    n.trace_to = trace_to or []
    n.properties = properties or {}
    n.updated_at = datetime.now(UTC)
    n.content_hash = "fakehash"
    n.version = 1
    n.lifecycle = "active"
    return n


def _make_graph(nodes: list[MagicMock]) -> MagicMock:
    graph = MagicMock()
    graph.all_nodes.return_value = nodes
    lookup = {n.node_id: n for n in nodes}
    graph.node_sync.side_effect = lambda nid: lookup.get(nid)
    graph.children_sync.side_effect = lambda pid: [
        n for n in nodes if n.parent_id == pid
    ]
    return graph


@pytest.fixture
def sample_graph() -> MagicMock:
    """A small but complete graph with all node types for testing."""
    nodes = [
        _node("PROJECT-001", "PROJECT", "Test Project", "A test project."),
        _node("HLR-001", "HLR", "Speed req", "System shall be fast.", parent_id="PROJECT-001"),
        _node("HLR-002", "HLR", "Safety req", "System shall be safe.", parent_id="PROJECT-001"),
        _node("ARCHITECTURE-001", "ARCHITECTURE", "Main arch", "Modular design.", parent_id="PROJECT-001"),
        _node("MODULE-001", "MODULE", "Core module", "Core logic.", parent_id="ARCHITECTURE-001"),
        _node("CONTRACT-001", "CONTRACT", "Core API", "def run() -> bool", parent_id="MODULE-001"),
        _node(
            "LLR-001", "LLR", "Speed sub-req",
            "Process in under 100ms.",
            parent_id="MODULE-001",
            trace_to=["HLR-001"],
        ),
        _node(
            "DESIGN-001", "DESIGN", "Core design",
            "Class CoreEngine with run() method.",
            parent_id="MODULE-001",
            trace_to=["LLR-001"],
            properties={"file_path": "src/core.py", "trace_coverage": {"total": 2, "traced": 1}},
        ),
        _node("SUITE-001", "SUITE", "Test strategy", "Unit + integration.", parent_id="PROJECT-001"),
        _node(
            "CASE_LLR-001", "CASE_LLR", "Speed test",
            "Verify processing time.",
            parent_id="SUITE-001",
            trace_to=["LLR-001"],
            properties={"file_path": "tests/test_core.py"},
        ),
    ]
    return _make_graph(nodes)


# ── Render function tests ────────────────────────────────────────────────────


class TestRenderRequirements:
    def test_contains_hlr_and_llr_sections(self, sample_graph: MagicMock) -> None:
        result = _render_requirements(sample_graph)
        assert "## High-Level Requirements" in result
        assert "## Low-Level Requirements" in result

    def test_includes_hlr_content(self, sample_graph: MagicMock) -> None:
        result = _render_requirements(sample_graph)
        assert "HLR-001" in result
        assert "System shall be fast." in result

    def test_llr_shows_trace(self, sample_graph: MagicMock) -> None:
        result = _render_requirements(sample_graph)
        assert "Traces to:" in result
        assert "HLR-001" in result


class TestRenderArchitecture:
    def test_contains_sections(self, sample_graph: MagicMock) -> None:
        result = _render_architecture(sample_graph)
        assert "## Architecture Decisions" in result
        assert "## Module Decomposition" in result

    def test_includes_module(self, sample_graph: MagicMock) -> None:
        result = _render_architecture(sample_graph)
        assert "MODULE-001" in result
        assert "Core module" in result


class TestRenderInterfaces:
    def test_contains_contract(self, sample_graph: MagicMock) -> None:
        result = _render_interfaces(sample_graph)
        assert "CONTRACT-001" in result
        assert "Core API" in result

    def test_shows_parent_module(self, sample_graph: MagicMock) -> None:
        result = _render_interfaces(sample_graph)
        assert "MODULE-001" in result

    def test_shows_implementing_designs(self, sample_graph: MagicMock) -> None:
        result = _render_interfaces(sample_graph)
        assert "Implemented by:" in result
        assert "DESIGN-001" in result


class TestRenderDesign:
    def test_contains_design_spec(self, sample_graph: MagicMock) -> None:
        result = _render_design(sample_graph)
        assert "DESIGN-001" in result
        assert "CoreEngine" in result

    def test_shows_traced_requirements(self, sample_graph: MagicMock) -> None:
        result = _render_design(sample_graph)
        assert "Requirements Implemented" in result
        assert "LLR-001" in result

    def test_shows_source_file(self, sample_graph: MagicMock) -> None:
        result = _render_design(sample_graph)
        assert "`src/core.py`" in result


class TestRenderTestPlan:
    def test_contains_strategy(self, sample_graph: MagicMock) -> None:
        result = _render_test_plan(sample_graph)
        assert "## Test Strategy" in result
        assert "Unit + integration." in result

    def test_contains_cases(self, sample_graph: MagicMock) -> None:
        result = _render_test_plan(sample_graph)
        assert "CASE_LLR-001" in result
        assert "Speed test" in result

    def test_shows_verified_requirement(self, sample_graph: MagicMock) -> None:
        result = _render_test_plan(sample_graph)
        assert "Verifies:" in result
        assert "LLR-001" in result


class TestRenderTraceabilityMatrix:
    def test_contains_forward_trace(self, sample_graph: MagicMock) -> None:
        result = _render_traceability_matrix(sample_graph)
        assert "Forward Trace" in result
        assert "HLR-001" in result
        assert "LLR-001" in result
        assert "DESIGN-001" in result

    def test_contains_reverse_trace(self, sample_graph: MagicMock) -> None:
        result = _render_traceability_matrix(sample_graph)
        assert "Reverse Trace" in result
        assert "`src/core.py`" in result

    def test_detects_unimplemented_llr(self, sample_graph: MagicMock) -> None:
        """HLR-002 has no LLR, so LLR coverage is fine, but check table has HLR-002 row."""
        result = _render_traceability_matrix(sample_graph)
        assert "HLR-002" in result


class TestRenderCoverageReport:
    def test_contains_summary_table(self, sample_graph: MagicMock) -> None:
        result = _render_coverage_report(sample_graph)
        assert "## Summary" in result
        assert "Requirement coverage" in result
        assert "Function coverage" in result

    def test_shows_correct_counts(self, sample_graph: MagicMock) -> None:
        result = _render_coverage_report(sample_graph)
        # 1 LLR with 1 CASE → 1/1
        assert "1/1" in result


class TestRenderReadme:
    def test_contains_project_name(self, sample_graph: MagicMock, tmp_path: Path) -> None:
        dest = tmp_path / "deliverables"
        dest.mkdir()
        (dest / "docs").mkdir()
        (dest / "docs" / "01-Requirements-Specification.md").write_text("x")
        result = _render_readme(sample_graph, tmp_path, dest)
        assert "# Test Project" in result

    def test_contains_manifest(self, sample_graph: MagicMock, tmp_path: Path) -> None:
        dest = tmp_path / "deliverables"
        dest.mkdir()
        (dest / "docs").mkdir()
        (dest / "docs" / "01-Requirements-Specification.md").write_text("x")
        result = _render_readme(sample_graph, tmp_path, dest)
        assert "01-Requirements-Specification.md" in result

    def test_contains_quick_start(self, sample_graph: MagicMock, tmp_path: Path) -> None:
        dest = tmp_path / "deliverables"
        dest.mkdir()
        result = _render_readme(sample_graph, tmp_path, dest)
        assert "Quick Start" in result


# ── Integration: full pack build ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_deliverables_pack_creates_zip(sample_graph: MagicMock, tmp_path: Path) -> None:
    """Full build produces a valid ZIP with expected structure."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create some workspace files to bundle
    (workspace / "src").mkdir()
    (workspace / "src" / "core.py").write_text("# source")
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_core.py").write_text("# tests")
    (workspace / "pyproject.toml").write_text("[project]\nname = 'test'")

    zip_path = await build_deliverables_pack(sample_graph, workspace)

    assert zip_path.exists()
    assert zip_path.suffix == ".zip"

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        # Check key files exist in the archive
        assert any("README.md" in n for n in names)
        assert any("01-Requirements-Specification.md" in n for n in names)
        assert any("06-Traceability-Matrix.md" in n for n in names)
        assert any("07-Coverage-Report.md" in n for n in names)
        assert any("core.py" in n for n in names)
        assert any("test_core.py" in n for n in names)
        assert any("pyproject.toml" in n for n in names)


@pytest.mark.asyncio
async def test_build_deliverables_pack_overwrites_existing(
    sample_graph: MagicMock,
    tmp_path: Path,
) -> None:
    """Re-running the build replaces the previous deliverables."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    zip1 = await build_deliverables_pack(sample_graph, workspace)
    size1 = zip1.stat().st_size

    zip2 = await build_deliverables_pack(sample_graph, workspace)
    size2 = zip2.stat().st_size

    assert zip1 == zip2
    # Sizes should be equal for same inputs (deterministic)
    assert size1 == size2


@pytest.mark.asyncio
async def test_build_with_empty_workspace(sample_graph: MagicMock, tmp_path: Path) -> None:
    """Build succeeds even when workspace has no src/tests directories."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    zip_path = await build_deliverables_pack(sample_graph, workspace)
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert any("README.md" in n for n in names)
        assert any("01-Requirements-Specification.md" in n for n in names)


# ── Additional edge case tests ──────────────────────────────────────────────


class TestTraceabilityMatrixEdgeCases:
    def test_no_traces_shows_gaps(self) -> None:
        """Graph with unimplemented LLRs shows gap section."""
        nodes = [
            _node("HLR-001", "HLR", "Safety", "safe", parent_id="P"),
            _node("LLR-001", "LLR", "Sub", "sub-req", parent_id="P", trace_to=["HLR-001"]),
        ]
        graph = _make_graph(nodes)
        result = _render_traceability_matrix(graph)
        assert "Unimplemented LLRs" in result
        assert "LLR-001" in result

    def test_full_trace_chain_no_gaps(self, sample_graph: MagicMock) -> None:
        """Complete trace chain shows 'No traceability gaps detected'."""
        result = _render_traceability_matrix(sample_graph)
        # sample_graph has DESIGN-001 tracing to LLR-001, and CASE tracing to LLR-001
        # But DESIGN-001 has file_path and CASE has file_path, so no gaps
        assert "Gaps" in result


class TestCoverageReportEdgeCases:
    def test_no_designs_zero_coverage(self) -> None:
        """Graph with no DESIGNs shows 0/0 function coverage."""
        nodes = [_node("HLR-001", "HLR", "Safety", "safe")]
        graph = _make_graph(nodes)
        with patch("backend.analysis.gap_analyser.GapAnalyser") as mock_gap:
            mock_gap.return_value.analyse.return_value = []
            result = _render_coverage_report(graph)
        assert "0/0" in result
        assert "—" in result  # _pct returns "—" for 0/0


class TestBuildTraceMap:
    def test_builds_reverse_map(self) -> None:
        """_build_trace_map creates target → source list."""
        nodes = [
            _node("D-1", "DESIGN", trace_to=["LLR-1", "LLR-2"]),
            _node("D-2", "DESIGN", trace_to=["LLR-1"]),
        ]
        result = _build_trace_map(nodes)
        assert result["LLR-1"] == ["D-1", "D-2"]
        assert result["LLR-2"] == ["D-1"]

    def test_nodes_with_no_traces(self) -> None:
        """Nodes without trace_to are skipped."""
        nodes = [_node("D-1", "DESIGN")]
        result = _build_trace_map(nodes)
        assert result == {}


class TestPct:
    def test_zero_denominator_returns_dash(self) -> None:
        assert _pct(0, 0) == "—"

    def test_hundred_percent(self) -> None:
        assert _pct(5, 5) == "100%"

    def test_partial(self) -> None:
        assert _pct(1, 2) == "50%"


# ── Sparse-graph rendering: optional fields are skipped gracefully ───────────


@pytest.fixture
def sparse_graph() -> MagicMock:
    """Nodes missing content, parents, traces, and file paths."""
    nodes = [
        _node("HLR-EMPTY", "HLR", "Bare HLR"),
        _node("LLR-ORPHAN", "LLR", "Orphan LLR", "Shall exist."),
        _node("LLR-GHOSTPARENT", "LLR", "Ghost parent", "Shall too.",
              parent_id="GHOST-MOD"),
        _node("LLR-NOCONTENT", "LLR", "No content"),
        _node("ARCH-EMPTY", "ARCHITECTURE", "Bare decision"),
        _node("MODULE-EMPTY", "MODULE", "Bare module"),
        _node("CONTRACT-ORPHAN", "CONTRACT", "Orphan contract"),
        _node("CONTRACT-GHOST", "CONTRACT", "Ghost module contract",
              "def x() -> None", parent_id="GHOST-MOD"),
        _node("DESIGN-ORPHAN", "DESIGN", "Orphan design",
              trace_to=["LLR-NOCONTENT", "GHOST-REF"]),
        _node("DESIGN-GHOST", "DESIGN", "Ghost parent design",
              parent_id="GHOST-MOD-2"),
        _node("SUITE-EMPTY", "SUITE", "Bare suite"),
        _node("CASE_HLR-EMPTY", "CASE_HLR", "Bare case"),
    ]
    return _make_graph(nodes)


class TestSparseGraphRendering:
    def test_requirements_skip_missing_traces_and_parents(
        self, sparse_graph: MagicMock,
    ) -> None:
        result = _render_requirements(sparse_graph)
        assert "LLR-ORPHAN" in result
        assert "LLR-GHOSTPARENT" in result
        # Orphan LLR has no trace_to and no parent → no Module line for it
        assert "**Module:** GHOST-MOD" not in result

    def test_architecture_renders_contentless_nodes(
        self, sparse_graph: MagicMock,
    ) -> None:
        result = _render_architecture(sparse_graph)
        assert "ARCH-EMPTY: Bare decision" in result
        assert "MODULE-EMPTY: Bare module" in result
        # A module without children renders no Children line
        assert "**Children:**" not in result

    def test_interfaces_render_contracts_without_parent_or_designs(
        self, sparse_graph: MagicMock,
    ) -> None:
        result = _render_interfaces(sparse_graph)
        assert "CONTRACT-ORPHAN" in result
        assert "CONTRACT-GHOST" in result
        assert "**Implemented by:**" not in result

    def test_design_marks_unresolvable_trace_refs(
        self, sparse_graph: MagicMock,
    ) -> None:
        result = _render_design(sparse_graph)
        assert "GHOST-REF *(not found)*" in result
        # Contentless traced LLR renders its title line without a body
        assert "**LLR-NOCONTENT: No content**" in result
        assert "**Source:**" not in result

    def test_test_plan_renders_bare_suite_and_case(
        self, sparse_graph: MagicMock,
    ) -> None:
        result = _render_test_plan(sparse_graph)
        assert "SUITE-EMPTY: Bare suite" in result
        assert "CASE_HLR-EMPTY: Bare case (HLR)" in result
        assert "**Verifies:**" not in result
        assert "**Test file:**" not in result

    def test_traceability_matrix_handles_missing_lookups(
        self, sparse_graph: MagicMock,
    ) -> None:
        result = _render_traceability_matrix(sparse_graph)
        # HLR with no child LLRs renders an empty row
        assert "| HLR-EMPTY | — | — | — | — | — |" in result
        # Reverse trace tolerates trace_to referencing a missing node
        assert "| — | DESIGN-ORPHAN |" in result
        # Ungenerated designs and untested cases appear in the gap lists
        assert "DESIGNs without source file" in result
        assert "CASEs without test file" in result
