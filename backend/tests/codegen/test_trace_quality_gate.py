"""Tests for trace quality — suspicious-name rule check (now in gap_finder)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from backend.codegen.gap_finder import Gap, GapKind, _check_suspicious_names

# ── _check_suspicious_names ──────────────────────────────────────────────────


class TestCheckSuspiciousNames:
    def _make_graph(self, llr_texts: dict[str, str]) -> MagicMock:
        graph = MagicMock()
        nodes = []
        for lid, text in llr_texts.items():
            n = MagicMock()
            n.node_id = lid
            n.node_type = "LLR"
            n.content = text
            n.title = ""
            nodes.append(n)
        graph.all_nodes.return_value = nodes
        return graph

    def _make_source(self, traces: list[MagicMock]) -> dict[str, Any]:
        fs = MagicMock()
        fs.traces = traces
        return {"src/planner.py": fs}

    def _make_trace(self, symbol: str, llr_ids: list[str]) -> MagicMock:
        t = MagicMock()
        t.symbol = symbol
        t.llr_ids = llr_ids
        return t

    def test_flags_fallback_function(self) -> None:
        trace = self._make_trace("_fallback_grid_path", ["LLR-001"])
        graph = self._make_graph({"LLR-001": "return path or raise PlanningError"})
        gaps: list[Gap] = []
        _check_suspicious_names(gaps, self._make_source([trace]), graph)
        assert len(gaps) == 1
        assert gaps[0].kind == GapKind.SCOPE_CREEP
        assert "fallback" in gaps[0].details

    def test_allows_fallback_when_requirement_mentions_it(self) -> None:
        trace = self._make_trace("_fallback_path", ["LLR-001"])
        graph = self._make_graph({"LLR-001": "use fallback BFS when A* fails"})
        gaps: list[Gap] = []
        _check_suspicious_names(gaps, self._make_source([trace]), graph)
        assert len(gaps) == 0

    def test_flags_retry_function(self) -> None:
        trace = self._make_trace("retry_connection", ["LLR-001"])
        graph = self._make_graph({"LLR-001": "connect to server"})
        gaps: list[Gap] = []
        _check_suspicious_names(gaps, self._make_source([trace]), graph)
        assert len(gaps) == 1
        assert "retry" in gaps[0].details

    def test_allows_normal_function_names(self) -> None:
        trace = self._make_trace("compute_path", ["LLR-001"])
        graph = self._make_graph({"LLR-001": "compute shortest path"})
        gaps: list[Gap] = []
        _check_suspicious_names(gaps, self._make_source([trace]), graph)
        assert len(gaps) == 0

    def test_flags_cache_function(self) -> None:
        trace = self._make_trace("_cache_results", ["LLR-001"])
        graph = self._make_graph({"LLR-001": "compute results"})
        gaps: list[Gap] = []
        _check_suspicious_names(gaps, self._make_source([trace]), graph)
        assert len(gaps) == 1
        assert "cache" in gaps[0].details

    def test_no_llrs_returns_empty(self) -> None:
        trace = self._make_trace("_fallback_thing", ["LLR-001"])
        graph = MagicMock()
        graph.all_nodes.return_value = []
        gaps: list[Gap] = []
        _check_suspicious_names(gaps, self._make_source([trace]), graph)
        assert len(gaps) == 0

    def test_skips_traces_without_symbol(self) -> None:
        trace = self._make_trace("", ["LLR-001"])
        graph = self._make_graph({"LLR-001": "compute path"})
        gaps: list[Gap] = []
        _check_suspicious_names(gaps, self._make_source([trace]), graph)
        assert len(gaps) == 0


class TestGapKindOrdering:
    def test_weak_trace_after_uncovered_requirement(self) -> None:
        assert GapKind.WEAK_TRACE > GapKind.UNCOVERED_REQUIREMENT

    def test_scope_creep_after_weak_trace(self) -> None:
        assert GapKind.SCOPE_CREEP > GapKind.WEAK_TRACE

    def test_weak_trace_value(self) -> None:
        # Shifted from 10 when UNIMPLEMENTED_REQUIREMENT (9) was inserted
        # before UNCOVERED_REQUIREMENT.
        assert GapKind.WEAK_TRACE.value == 11

    def test_scope_creep_value(self) -> None:
        assert GapKind.SCOPE_CREEP.value == 12
