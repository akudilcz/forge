"""Tests for backend.crew.mission_agent — mission agent and helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.crew.gap_finder import Gap
from backend.crew.mission_agent import (
    MissionStats,
    _include_existing_files,
    _include_rendered_docs,
    _include_tracing_source,
    _score_breakdown,
    build_mission_context,
    compute_value,
    create_mission_agent,
    format_gaps,
    run_mission_agent,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


def _node(node_id: str, node_type: str, title: str = "", content: str = "",
          trace_to: list[str] | None = None) -> MagicMock:
    n = MagicMock()
    n.node_id = node_id
    n.node_type = node_type
    n.title = title
    n.content = content
    n.trace_to = trace_to or []
    return n


def _file_state(traces: list[Any] | None = None, total_fn: int = 1,
                traced_fn: int = 1, untraced: list[Any] | None = None) -> MagicMock:
    fs = MagicMock()
    fs.traces = traces or []
    fs.total_functions = total_fn
    fs.traced_functions = traced_fn
    fs.untraced_functions = untraced or []
    return fs


def _ws_state(test_results: list[Any] | None = None,
              source_files: dict[str, Any] | None = None,
              coverage_pct: float = 100.0, branch_pct: float = 100.0) -> MagicMock:
    ws = MagicMock()
    ws.test_results = test_results or []
    ws.source_files = source_files or {}
    ws.coverage_pct = coverage_pct
    ws.branch_coverage_pct = branch_pct
    ws.test_run_error = ""
    ws.test_files = {}
    ws.coverage_by_file = {}
    ws.uncovered_lines = {}
    return ws


def _gap(kind_name: str = "MISSING_TEST", file_path: str = "",
         node_id: str = "", details: str = "",
         context: dict[str, Any] | None = None) -> MagicMock:
    g = MagicMock()
    g.kind.name = kind_name
    g.file_path = file_path
    g.node_id = node_id
    g.details = details
    g.context = context or {}
    return g


# ── build_mission_context ───────────────────────────────────────────────────


class TestBuildMissionContext:
    def test_assembles_sections_from_graph(self, tmp_path: Path) -> None:
        nodes = [
            _node("ARCH-1", "ARCHITECTURE", "Arch", "modular design"),
            _node("MOD-1", "MODULE", "Core", "core logic"),
            _node("DESIGN-1", "DESIGN", "D1", "class Foo", trace_to=["LLR-1"]),
            _node("LLR-1", "LLR", "Speed", "100ms"),
            _node("HLR-1", "HLR", "Safety", "safe"),
            _node("CASE_LLR-1", "CASE_LLR", "Test speed", "verify timing", trace_to=["LLR-1"]),
        ]
        graph = MagicMock()
        graph.all_nodes.return_value = nodes
        graph.children_sync.return_value = []

        result = build_mission_context(graph, tmp_path)
        assert "ARCHITECTURE: Arch" in result
        assert "MODULE: MOD-1" in result
        assert "DESIGN: DESIGN-1" in result
        assert "LLR-1" in result
        assert "TEST CASES" in result

    def test_empty_graph_returns_empty(self, tmp_path: Path) -> None:
        graph = MagicMock()
        graph.all_nodes.return_value = []
        result = build_mission_context(graph, tmp_path)
        assert result == ""


# ── _include_rendered_docs ──────────────────────────────────────────────────


class TestIncludeRenderedDocs:
    def test_reads_useful_docs(self, tmp_path: Path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "07-LLR.md").write_text("llr content")
        sections: list[str] = []
        _include_rendered_docs(tmp_path, sections)
        assert len(sections) == 1
        assert "llr content" in sections[0]

    def test_missing_docs_dir_is_no_op(self, tmp_path: Path) -> None:
        sections: list[str] = []
        _include_rendered_docs(tmp_path, sections)
        assert sections == []


# ── _include_tracing_source ─────────────────────────────────────────────────


class TestIncludeTracingSource:
    def test_reads_decorator(self, tmp_path: Path) -> None:
        tracing = tmp_path / "tracing"
        tracing.mkdir()
        (tracing / "decorator.py").write_text("def traces(): pass")
        sections: list[str] = []
        _include_tracing_source(tmp_path, sections)
        assert len(sections) == 1
        assert "def traces()" in sections[0]

    def test_missing_file_is_no_op(self, tmp_path: Path) -> None:
        sections: list[str] = []
        _include_tracing_source(tmp_path, sections)
        assert sections == []


# ── _include_existing_files ─────────────────────────────────────────────────


class TestIncludeExistingFiles:
    def test_reads_src_and_tests(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "core.py").write_text("class Core: pass")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_core.py").write_text("def test(): pass")
        sections: list[str] = []
        _include_existing_files(tmp_path, sections)
        assert len(sections) == 2

    def test_truncates_large_files(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "big.py").write_text("x" * 25_000)
        sections: list[str] = []
        _include_existing_files(tmp_path, sections)
        assert "truncated" in sections[0]


# ── compute_value ───────────────────────────────────────────────────────────


class TestComputeValue:
    def test_returns_minimum_of_dimensions(self) -> None:
        trace = MagicMock()
        trace.llr_ids = ["LLR-1"]
        fs = _file_state(traces=[trace], total_fn=1, traced_fn=1)
        ws = _ws_state(
            test_results=[MagicMock(status="passed")],
            source_files={"core.py": fs},
            coverage_pct=50.0, branch_pct=100.0,
        )
        graph = MagicMock()
        graph.all_nodes.return_value = [_node("LLR-1", "LLR")]
        assert compute_value(ws, graph) == 0.5

    def test_zero_denominators_return_zero(self) -> None:
        ws = _ws_state(coverage_pct=0.0, branch_pct=0.0)
        graph = MagicMock()
        graph.all_nodes.return_value = []
        assert compute_value(ws, graph) == 0.0

    def test_perfect_score(self) -> None:
        trace = MagicMock()
        trace.llr_ids = ["LLR-1"]
        fs = _file_state(traces=[trace], total_fn=1, traced_fn=1)
        ws = _ws_state(
            test_results=[MagicMock(status="passed")],
            source_files={"core.py": fs},
            coverage_pct=100.0, branch_pct=100.0,
        )
        graph = MagicMock()
        graph.all_nodes.return_value = [_node("LLR-1", "LLR")]
        assert compute_value(ws, graph) == 1.0


# ── format_gaps ─────────────────────────────────────────────────────────────


class TestFormatGaps:
    def test_empty_gaps_returns_success(self) -> None:
        assert "No gaps remaining" in format_gaps([])

    def test_groups_by_kind(self) -> None:
        gaps: list[Gap] = [
            _gap("MISSING_TEST", node_id="LLR-1"),
            _gap("MISSING_TEST", node_id="LLR-2"),
            _gap("SYNTAX_ERROR", file_path="src/core.py"),
        ]
        result = format_gaps(gaps)
        assert "MISSING_TEST (2)" in result
        assert "SYNTAX_ERROR (1)" in result

    def test_includes_error_context(self) -> None:
        gaps: list[Gap] = [_gap("FAILING_TEST", context={"error_message": "AssertionError"})]
        result = format_gaps(gaps)
        assert "AssertionError" in result


# ── _score_breakdown ────────────────────────────────────────────────────────


class TestScoreBreakdown:
    def test_shows_missing_llrs(self) -> None:
        ws = _ws_state()
        graph = MagicMock()
        graph.all_nodes.return_value = [_node("LLR-1", "LLR"), _node("LLR-2", "LLR")]
        result = _score_breakdown(ws, graph)
        assert "MISSING" in result
        assert "LLR-1" in result

    def test_shows_untraced_functions(self) -> None:
        uf = MagicMock()
        uf.class_name = "Foo"
        uf.name = "bar"
        fs = _file_state(total_fn=2, traced_fn=1, untraced=[uf])
        ws = _ws_state(source_files={"core.py": fs})
        graph = MagicMock()
        graph.all_nodes.return_value = []
        result = _score_breakdown(ws, graph)
        assert "UNTRACED" in result
        assert "Foo.bar" in result


# ── create_mission_agent ────────────────────────────────────────────────────


def _named_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


def _required_tools() -> list[MagicMock]:
    return [_named_tool(n) for n in ("file_write", "shell_exec", "evaluate_progress")]


class TestCreateMissionAgent:
    def test_filters_to_mission_tools(self) -> None:
        config = MagicMock()
        config.llm.model_for_phase.return_value = "gpt-4"

        required = _required_tools()
        graph_tool = _named_tool("graph_read")

        with (
            patch("backend.crew.mission_agent.build_llm"),
            patch("backend.crew.mission_agent.create_react_agent") as mock_create,
        ):
            create_mission_agent(config, [*required, graph_tool])
            _, kwargs = mock_create.call_args
            for tool in required:
                assert tool in kwargs["tools"]
            assert graph_tool not in kwargs["tools"]

    def test_evaluate_progress_included(self) -> None:
        config = MagicMock()
        config.llm.model_for_phase.return_value = "gpt-4"

        tools = _required_tools()
        eval_tool = next(t for t in tools if t.name == "evaluate_progress")

        with (
            patch("backend.crew.mission_agent.build_llm"),
            patch("backend.crew.mission_agent.create_react_agent") as mock_create,
        ):
            create_mission_agent(config, tools)
            _, kwargs = mock_create.call_args
            assert eval_tool in kwargs["tools"]

    def test_missing_evaluate_progress_raises(self) -> None:
        """Rank-3 live-run repro: the e2e path registered no
        evaluate_progress at all and the agent ran blind. Tooling must
        never be silently optional."""
        config = MagicMock()
        config.llm.model_for_phase.return_value = "gpt-4"

        tools = [_named_tool("file_write"), _named_tool("shell_exec")]

        with (
            patch("backend.crew.mission_agent.build_llm"),
            patch("backend.crew.mission_agent.create_react_agent"),
            pytest.raises(RuntimeError, match="evaluate_progress"),
        ):
            create_mission_agent(config, tools)

    def test_empty_tool_instances_raises(self) -> None:
        config = MagicMock()
        config.llm.model_for_phase.return_value = "gpt-4"

        with (
            patch("backend.crew.mission_agent.build_llm"),
            patch("backend.crew.mission_agent.create_react_agent"),
            pytest.raises(RuntimeError, match="file_write"),
        ):
            create_mission_agent(config, [])


# ── run_mission_agent ───────────────────────────────────────────────────────


class TestRunMissionAgent:
    @pytest.mark.asyncio
    async def test_returns_workspace_state_and_stats(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.llm.model_for_phase.return_value = "test"
        ws = _ws_state()

        with (
            patch("backend.crew.mission_agent.build_mission_context", return_value="ctx"),
            patch("backend.crew.mission_agent.create_mission_agent"),
            patch("backend.crew.mission_agent.scan_workspace", new_callable=AsyncMock, return_value=ws),
            patch("backend.crew.mission_agent.find_gaps", return_value=[]),
            patch("backend.crew.mission_agent.iter_agent_turns", return_value=aiter_empty()),
            patch("backend.crew.mission_agent.work_queue"),
            patch("backend.crew.mission_agent.forge_logger"),
        ):
            ws_result, stats = await run_mission_agent(tmp_path, MagicMock(), config, [])
            assert ws_result is ws
            assert stats.stop_reason == "all_gaps_closed"
            assert stats.final_gap_count == 0

    @pytest.mark.asyncio
    async def test_handles_agent_error_gracefully(self, tmp_path: Path) -> None:
        """Errors during the single agent invocation are caught."""
        config = MagicMock()
        config.llm.model_for_phase.return_value = "test"
        ws = _ws_state()

        async def failing_iter(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            raise RuntimeError("LLM timeout")
            yield  # unreachable

        with (
            patch("backend.crew.mission_agent.build_mission_context", return_value="ctx"),
            patch("backend.crew.mission_agent.create_mission_agent"),
            patch("backend.crew.mission_agent.scan_workspace", new_callable=AsyncMock, return_value=ws),
            patch("backend.crew.mission_agent.find_gaps", return_value=[_gap()]),
            patch("backend.crew.mission_agent.iter_agent_turns", side_effect=failing_iter),
            patch("backend.crew.mission_agent.work_queue"),
            patch("backend.crew.mission_agent.forge_logger"),
        ):
            _, stats = await run_mission_agent(tmp_path, MagicMock(), config, [])
            # With the convergence loop, a persistently failing agent exhausts
            # MAX_MISSION_PASSES before giving up rather than stopping after
            # a single failed pass.
            assert stats.stop_reason.startswith("max_passes_reached")
            assert stats.final_gap_count == 1

    @pytest.mark.asyncio
    async def test_tool_calls_accumulate(self, tmp_path: Path) -> None:
        """stats.total_tool_calls sums tool_calls across all turns."""
        config = MagicMock()
        config.llm.model_for_phase.return_value = "test"
        ws = _ws_state()

        turn1 = MagicMock()
        turn1.tool_calls = [MagicMock(), MagicMock()]  # 2 calls
        turn2 = MagicMock()
        turn2.tool_calls = [MagicMock()]  # 1 call

        async def multi_turn_iter(*args: Any, **kwargs: Any) -> AsyncIterator[MagicMock]:
            yield turn1
            yield turn2

        with (
            patch("backend.crew.mission_agent.build_mission_context", return_value="ctx"),
            patch("backend.crew.mission_agent.create_mission_agent"),
            patch("backend.crew.mission_agent.scan_workspace", new_callable=AsyncMock, return_value=ws),
            patch("backend.crew.mission_agent.find_gaps", return_value=[]),
            patch("backend.crew.mission_agent.iter_agent_turns", return_value=multi_turn_iter()),
            patch("backend.crew.mission_agent.work_queue"),
            patch("backend.crew.mission_agent.forge_logger"),
        ):
            _, stats = await run_mission_agent(tmp_path, MagicMock(), config, [])
            assert stats.total_tool_calls == 3

    @pytest.mark.asyncio
    async def test_final_score_and_gap_count_set(self, tmp_path: Path) -> None:
        """stats.final_score and final_gap_count are set from post-run evaluation."""
        config = MagicMock()
        config.llm.model_for_phase.return_value = "test"

        trace = MagicMock()
        trace.llr_ids = ["LLR-1"]
        fs = _file_state(traces=[trace], total_fn=1, traced_fn=1)
        ws = _ws_state(
            test_results=[MagicMock(status="passed")],
            source_files={"core.py": fs},
            coverage_pct=80.0,
            branch_pct=90.0,
        )

        remaining_gaps = [_gap(), _gap()]

        call_count = {"n": 0}

        def find_gaps_side_effect(*args: Any, **kwargs: Any) -> list[MagicMock]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [_gap()]  # pre-run: 1 gap (used for prompt)
            return remaining_gaps  # post-run: 2 gaps remain

        graph = MagicMock()
        graph.all_nodes.return_value = [_node("LLR-1", "LLR")]

        with (
            patch("backend.crew.mission_agent.build_mission_context", return_value="ctx"),
            patch("backend.crew.mission_agent.create_mission_agent"),
            patch("backend.crew.mission_agent.scan_workspace", new_callable=AsyncMock, return_value=ws),
            patch("backend.crew.mission_agent.find_gaps", side_effect=find_gaps_side_effect),
            patch("backend.crew.mission_agent.iter_agent_turns", return_value=aiter_empty()),
            patch("backend.crew.mission_agent.work_queue"),
            patch("backend.crew.mission_agent.forge_logger"),
        ):
            _, stats = await run_mission_agent(tmp_path, graph, config, [])
            assert stats.final_gap_count == 2
            assert stats.final_score > 0.0


# ── MissionStats ────────────────────────────────────────────────────────────


class TestMissionStats:
    def test_default_initialization(self) -> None:
        stats = MissionStats()
        assert stats.total_tool_calls == 0
        assert stats.total_elapsed_s == 0.0
        assert stats.final_score == 0.0
        assert stats.final_gap_count == 0
        assert stats.stop_reason == ""

    def test_fields_are_correct_types(self) -> None:
        stats = MissionStats()
        assert isinstance(stats.total_tool_calls, int)
        assert isinstance(stats.total_elapsed_s, float)
        assert isinstance(stats.final_score, float)
        assert isinstance(stats.final_gap_count, int)
        assert isinstance(stats.stop_reason, str)

    def test_fields_are_mutable(self) -> None:
        stats = MissionStats()
        stats.total_tool_calls = 42
        stats.total_elapsed_s = 12.5
        stats.final_score = 0.95
        stats.final_gap_count = 3
        stats.stop_reason = "all_gaps_closed"

        assert stats.total_tool_calls == 42
        assert stats.total_elapsed_s == 12.5
        assert stats.final_score == 0.95
        assert stats.final_gap_count == 3
        assert stats.stop_reason == "all_gaps_closed"


# ── Helpers ─────────────────────────────────────────────────────────────────


async def aiter_empty() -> AsyncIterator[Any]:
    """An empty async iterator for mocking iter_agent_turns."""
    return
    yield  # unreachable — makes this an async generator


# ── Coverage: context sections, OSError handling, prompts, breakdown ────────


class TestContextSections:
    def test_module_contract_child_included(self, tmp_path: Path) -> None:
        """A CONTRACT child of a MODULE is rendered under the module."""
        mod = _node("MOD-1", "MODULE", "Core", "core logic")
        contract = _node("CON-1", "CONTRACT", "API", "def run() -> bool")
        graph = MagicMock()
        graph.all_nodes.return_value = [mod, contract]
        graph.children_sync.return_value = [contract]
        result = build_mission_context(graph, tmp_path)
        assert "CONTRACT: CON-1" in result
        assert "def run() -> bool" in result

    def test_suite_strategy_included(self, tmp_path: Path) -> None:
        suite = _node("SUITE-1", "SUITE", "Strategy", "unit + integration")
        graph = MagicMock()
        graph.all_nodes.return_value = [suite]
        graph.children_sync.return_value = []
        result = build_mission_context(graph, tmp_path)
        assert "TEST STRATEGY (SUITE SUITE-1)" in result


class TestOsErrorHandling:
    def test_rendered_doc_read_error_is_skipped(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "07-LLR.md").write_text("llr text", encoding="utf-8")
        sections: list[str] = []
        with patch.object(Path, "read_text", side_effect=OSError("io")):
            _include_rendered_docs(tmp_path, sections)
        assert sections == []

    def test_tracing_source_read_error_is_skipped(self, tmp_path: Path) -> None:
        tracing = tmp_path / "tracing"
        tracing.mkdir()
        (tracing / "decorator.py").write_text("def traces(): ...", encoding="utf-8")
        sections: list[str] = []
        with patch.object(Path, "read_text", side_effect=OSError("io")):
            _include_tracing_source(tmp_path, sections)
        assert sections == []

    def test_existing_file_read_error_is_skipped(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("x = 1", encoding="utf-8")
        sections: list[str] = []
        with patch.object(Path, "read_text", side_effect=OSError("io")):
            _include_existing_files(tmp_path, sections)
        assert sections == []


class TestFormatGapsDetails:
    def test_gap_details_are_rendered(self) -> None:
        gap = _gap(
            kind_name="FAILING_TESTS", file_path="tests/test_a.py",
            details="2 failing test(s)",
        )
        out = format_gaps([gap])
        assert "2 failing test(s)" in out


class TestExtraPrompt:
    @pytest.mark.asyncio
    async def test_extra_prompt_is_appended_to_mission_prompt(
        self, tmp_path: Path,
    ) -> None:
        config = MagicMock()
        config.llm.model_for_phase.return_value = "test"
        ws = _ws_state()

        with (
            patch("backend.crew.mission_agent.build_mission_context", return_value="ctx"),
            patch("backend.crew.mission_agent.create_mission_agent"),
            patch(
                "backend.crew.mission_agent.scan_workspace",
                new_callable=AsyncMock, return_value=ws,
            ),
            patch("backend.crew.mission_agent.find_gaps", return_value=[]),
            patch(
                "backend.crew.mission_agent._run_agent_iteration",
                new_callable=AsyncMock, return_value=0,
            ) as run_iter,
            patch("backend.crew.mission_agent.work_queue"),
            patch("backend.crew.mission_agent.forge_logger"),
        ):
            await run_mission_agent(
                tmp_path, MagicMock(), config, [], extra_prompt="FOCUS ON X",
            )
        prompt = run_iter.await_args_list[0].args[1]
        assert "FOCUS ON X" in prompt


class TestBuildFollowupPrompt:
    def test_uncovered_requirements_highlighted(self) -> None:
        """Uncovered-requirement gaps get their own priority section."""
        from backend.crew.mission_agent import _build_followup_prompt

        gaps = [
            _gap(
                kind_name="UNCOVERED_REQUIREMENT", node_id="LLR-7",
                details="no passing traced test",
            ),
        ]
        out = _build_followup_prompt("ctx", gaps, 2)
        assert "UNCOVERED REQUIREMENTS (1)" in out
        assert "LLR-7: no passing traced test" in out
        assert "OTHER REMAINING GAPS" not in out

    def test_other_gaps_rendered_without_uncovered_section(self) -> None:
        from backend.crew.mission_agent import _build_followup_prompt

        gaps = [_gap(kind_name="MISSING_TEST", file_path="tests/test_a.py")]
        out = _build_followup_prompt("ctx", gaps, 3)
        assert "OTHER REMAINING GAPS (1)" in out
        assert "UNCOVERED REQUIREMENTS" not in out


class TestScoreBreakdownDetails:
    def test_failed_tests_and_uncovered_lines_reported(self) -> None:
        result_pass = MagicMock(status="passed")
        result_fail = MagicMock(status="failed")
        ws = _ws_state(test_results=[result_pass, result_fail])
        ws.coverage_pct = 50.0
        ws.branch_coverage_pct = None
        ws.uncovered_lines = {"src/a.py": [3, 4], "src/empty.py": []}
        graph = MagicMock()
        graph.all_nodes.return_value = []
        out = _score_breakdown(ws, graph)
        assert "(1 FAILING)" in out
        assert "UNCOVERED in src/a.py: lines 3, 4" in out
        assert "src/empty.py" not in out
        assert "MC/DC" not in out
