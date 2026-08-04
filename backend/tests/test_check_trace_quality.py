"""Tests for the check_trace_quality tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tools.check_trace_quality import (
    CheckTraceQualityTool,
    _build_prompt,
    _gather_llr_texts,
)


def _make_graph(llr_texts: dict[str, str] | None = None) -> MagicMock:
    graph = MagicMock()
    nodes = []
    for lid, text in (llr_texts or {}).items():
        n = MagicMock()
        n.node_id = lid
        n.node_type = "LLR"
        n.content = text
        n.title = ""
        nodes.append(n)
    graph.all_nodes.return_value = nodes
    return graph


class TestGatherLlrTexts:
    def test_extracts_llr_nodes(self) -> None:
        graph = _make_graph({"LLR-001": "compute path", "LLR-002": "validate input"})
        result = _gather_llr_texts(graph)
        assert result == {"LLR-001": "compute path", "LLR-002": "validate input"}

    def test_empty_graph(self) -> None:
        graph = _make_graph({})
        assert _gather_llr_texts(graph) == {}


class TestBuildPrompt:
    def test_includes_requirements_and_functions(self) -> None:
        trace = MagicMock()
        trace.symbol = "plan"
        trace.start = 10
        trace.end = 25
        trace.llr_ids = ["LLR-001"]

        result = _build_prompt("def plan(): pass", [trace], {"LLR-001": "compute path"})
        assert "LLR-001" in result
        assert "compute path" in result
        assert "`plan`" in result
        assert "lines 10-25" in result


class TestCheckTraceQualityTool:
    def test_init_stores_attributes(self) -> None:
        graph = MagicMock()
        config = MagicMock()
        tool = CheckTraceQualityTool(workspace="/tmp/ws", graph=graph, config=config)
        assert tool._workspace == "/tmp/ws"
        assert tool._graph is graph
        assert tool.name == "check_trace_quality"

    @pytest.mark.asyncio
    async def test_missing_file_returns_error(self) -> None:
        tool = CheckTraceQualityTool(workspace="/nonexistent", graph=MagicMock(), config=MagicMock())
        result = await tool._async_check("src/missing.py")
        assert "TOOL_ERROR" in result

    @pytest.mark.asyncio
    async def test_no_traces_returns_message(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "empty.py").write_text("x = 1")

        tool = CheckTraceQualityTool(workspace=str(tmp_path), graph=MagicMock(), config=MagicMock())
        result = await tool._async_check("src/empty.py")
        assert "No traced functions" in result

    @pytest.mark.asyncio
    async def test_no_llrs_returns_message(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        code = 'from tracing.decorator import traces\n\n@traces("LLR-001")\ndef plan(): pass\n'
        (src / "planner.py").write_text(code)

        graph = _make_graph({})
        tool = CheckTraceQualityTool(workspace=str(tmp_path), graph=graph, config=MagicMock())
        with patch("backend.crew.trace_parser.analyse_traces") as mock_analyse:
            trace = MagicMock()
            trace.symbol = "plan"
            trace.start = 3
            trace.end = 4
            trace.llr_ids = ["LLR-001"]
            mock_analyse.return_value = MagicMock(traces=[trace])
            result = await tool._async_check("src/planner.py")
        assert "No LLR nodes" in result

    @pytest.mark.asyncio
    async def test_calls_llm_and_returns_text(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "planner.py").write_text("def plan(): pass")

        graph = _make_graph({"LLR-001": "compute path"})
        config = MagicMock()
        config.llm.model_for_phase.return_value = "test-model"

        mock_response = MagicMock()
        mock_response.content = "PASS: plan — implements path computation"

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        tool = CheckTraceQualityTool(workspace=str(tmp_path), graph=graph, config=config)
        with (
            patch("backend.crew.trace_parser.analyse_traces") as mock_analyse,
            patch("backend.agents.factory.build_llm", return_value=mock_llm),
        ):
            trace = MagicMock()
            trace.symbol = "plan"
            trace.start = 1
            trace.end = 1
            trace.llr_ids = ["LLR-001"]
            mock_analyse.return_value = MagicMock(traces=[trace])
            result = await tool._async_check("src/planner.py")

        assert "PASS: plan" in result
        mock_llm.ainvoke.assert_awaited_once()
