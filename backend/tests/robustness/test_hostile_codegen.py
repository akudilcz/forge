"""Hostile phase-12 behaviour — write-path validation, evidence gates, tooling.

A hostile mission agent may write syntactically invalid Python, claim
completion without producing evidence (tests/coverage/traces), or be wired
without its required tools. The write path must reject bad Python before it
reaches disk, the gap finder must refuse the completion claim, the mission
loop must stay bounded, and a missing required tool must be a RuntimeError
at construction — never a degraded run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.codegen.gap_finder import GapKind, find_gaps
from backend.codegen.mission_agent import (
    MAX_MISSION_PASSES,
    create_mission_agent,
    run_mission_agent,
)
from backend.config.models import ForgeConfig
from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState
from backend.pipeline.special_phases import SpecialPhaseHandlers
from backend.tools.file_write import FileWriteTool
from backend.workspace.scanner import WorkspaceState

# ── 1. Write-path validation rejects invalid Python ──────────────────────────


class TestWritePathValidation:
    def test_invalid_python_is_rejected_and_never_written(self, tmp_path: Path) -> None:
        tool = FileWriteTool(str(tmp_path))

        result = tool._execute(path="src/bad.py", content="def broken(:\n    pass\n")

        assert result.startswith("REJECTED"), f"expected rejection, got {result!r}"
        assert not (tmp_path / "src" / "bad.py").exists(), (
            "syntactically invalid Python reached the workspace"
        )

    def test_valid_python_still_writes(self, tmp_path: Path) -> None:
        tool = FileWriteTool(str(tmp_path))

        result = tool._execute(path="src/good.py", content="def fine() -> int:\n    return 1\n")

        assert result.startswith("OK:")
        assert (tmp_path / "src" / "good.py").exists()

    def test_workspace_escape_is_refused(self, tmp_path: Path) -> None:
        tool = FileWriteTool(str(tmp_path / "ws"))
        (tmp_path / "ws").mkdir()

        with pytest.raises(ValueError, match="workspace"):
            tool._execute(path="../outside.py", content="x = 1\n")


# ── 2. Completion claims require evidence ────────────────────────────────────


async def _seed_codegen_graph(db_path: Path) -> ProjectGraph:
    graph = ProjectGraph(db_path)
    await graph.initialise()
    for node_id, node_type, title in (
        ("LLR-0001", "LLR", "Increment behaviour"),
        ("DESIGN-0001", "DESIGN", "Counter core"),
        ("CASE_LLR-0001", "CASE_LLR", "Increment case"),
    ):
        await graph.add_node(
            GraphNode(
                node_id=node_id,
                node_type=node_type,
                title=title,
                content=f"Content of {node_id}.",
                lifecycle=LifecycleState.ACTIVE,
            )
        )
    return graph


class TestCompletionRequiresEvidence:
    async def test_empty_workspace_yields_structural_gaps(self, tmp_path: Path) -> None:
        """No files, no tests, no coverage — the gate must report open gaps,
        so ``all_gaps_closed`` can never be claimed on an empty workspace."""
        graph = await _seed_codegen_graph(tmp_path / "g.db")

        gaps = find_gaps({}, {}, [], graph, test_run_error="")

        kinds = {g.kind for g in gaps}
        assert GapKind.MISSING_SOURCE in kinds
        assert GapKind.MISSING_TEST in kinds

    async def test_broken_test_environment_is_a_gap_not_a_pass(
        self, tmp_path: Path
    ) -> None:
        graph = await _seed_codegen_graph(tmp_path / "g.db")

        gaps = find_gaps({}, {}, [], graph, test_run_error="bazel exploded")

        env = [g for g in gaps if g.kind is GapKind.TEST_ENV_BROKEN]
        assert len(env) == 1
        assert "bazel exploded" in env[0].details


class TestMissionLoopIsBoundedAgainstDoNothingAgents:
    async def test_agent_that_claims_done_but_does_nothing_hits_max_passes(
        self, tmp_path: Path
    ) -> None:
        """A hostile agent burns every pass without closing a gap. The loop
        must stop at MAX_MISSION_PASSES with an honest stop reason and a
        non-zero remaining gap count — never a spin, never a fake success."""
        graph = await _seed_codegen_graph(tmp_path / "g.db")
        workspace = tmp_path / "ws"
        workspace.mkdir()
        config = ForgeConfig()
        config.llm.keyless = True

        do_nothing = AsyncMock(return_value=0)
        with (
            patch(
                "backend.codegen.mission_agent.build_mission_context",
                return_value="CONTEXT",
            ),
            patch(
                "backend.codegen.mission_agent.scan_workspace",
                new=AsyncMock(return_value=WorkspaceState()),
            ),
            patch(
                "backend.codegen.mission_agent.create_mission_agent",
                return_value=MagicMock(),
            ),
            patch("backend.codegen.mission_agent._run_agent_iteration", new=do_nothing),
        ):
            ws_state, stats = await run_mission_agent(workspace, graph, config, [], "")

        assert do_nothing.await_count == MAX_MISSION_PASSES, (
            "mission loop pass count is not bounded"
        )
        assert stats.stop_reason == f"max_passes_reached_after_{MAX_MISSION_PASSES}"
        assert stats.final_gap_count > 0, (
            "a do-nothing agent was scored as having closed every gap"
        )
        assert stats.final_score == 0.0
        assert ws_state.test_results == []


# ── 3. Missing required tools fail loudly at construction ────────────────────


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


class TestRequiredMissionTooling:
    def test_no_tools_at_all_raises_runtime_error(self) -> None:
        config = ForgeConfig()
        config.llm.keyless = True

        with pytest.raises(RuntimeError, match="evaluate_progress"):
            create_mission_agent(config, [], None)

    def test_error_names_every_missing_required_tool(self) -> None:
        config = ForgeConfig()
        config.llm.keyless = True
        tools: list[Any] = [_NamedTool("file_write")]

        with pytest.raises(RuntimeError) as excinfo:
            create_mission_agent(config, tools, None)

        message = str(excinfo.value)
        assert "evaluate_progress" in message
        assert "shell_exec" in message
        assert "file_write" not in message.split("missing required tool(s): ")[1].split(".")[0]

    def test_phase_12_refuses_to_run_without_a_tool_registry(self) -> None:
        handlers = SpecialPhaseHandlers()
        handlers.pool = object()  # no _factory._registry chain

        with pytest.raises(RuntimeError, match="cannot run"):
            handlers._get_tool_instances()
