"""Tests for OperatorService and operator tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.operator import OperatorService
from backend.tools.operator import (
    IngestDocumentTool,
    PurgeDerivedTool,
    RunPhaseTool,
    ScanGapsTool,
    StopAllAgentsTool,
    StopBuildTool,
    _OperatorTool,
)

# ── Fixtures ──────────────────────────────────────────────────────────


def _make_tool[ToolT: _OperatorTool](tool_cls: type[ToolT], service: MagicMock) -> ToolT:
    """Instantiate an operator tool with its service handle.

    ``_OperatorTool.__init__`` takes the service positionally, but pydantic's
    ``dataclass_transform`` makes mypy synthesise a keyword-only ``__init__``
    for every leaf model subclass, which hides the inherited one. The cast
    restores the constructor signature the class actually has at runtime.
    """
    ctor = cast(Callable[[Any], ToolT], tool_cls)
    return ctor(service)


def _make_app_state() -> MagicMock:
    """Build a mock app.state with all the objects OperatorService needs."""
    state = MagicMock()
    state.graph = MagicMock()
    state.config = MagicMock()
    state.agent_pool = MagicMock()
    state.broadcaster = MagicMock()
    state.phase_store = MagicMock()
    state.workspace = "/tmp/test-workspace"
    state.session = MagicMock()
    state.session.session_id = "test-session"
    state.flow_task = None
    state.flow = None
    return state


def _make_service(state: MagicMock | None = None) -> OperatorService:
    """Create an OperatorService with a mock app state and real event loop."""
    state = state or _make_app_state()
    loop = asyncio.new_event_loop()
    return OperatorService(state, loop)


# ── OperatorService unit tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_phase_single() -> None:
    """run_phase with no end_phase creates a single-phase flow task."""
    service = _make_service()
    mock_flow = MagicMock()
    mock_flow.run_phase = AsyncMock()
    mock_flow.state = MagicMock()

    with patch.object(service, "_make_flow", return_value=mock_flow):
        result = await service.run_phase(12)

    assert result["status"] == "started"
    assert "12" in result["phases"]


@pytest.mark.asyncio
async def test_run_phase_range() -> None:
    """run_phase with end_phase starts the full build flow."""
    service = _make_service()
    mock_flow = MagicMock()
    mock_flow.kickoff_async = AsyncMock()
    mock_flow.state = MagicMock()

    with patch.object(service, "_make_flow", return_value=mock_flow):
        result = await service.run_phase(3, end_phase=8)

    assert result["status"] == "started"
    assert result["phases"] == "3-8"


@pytest.mark.asyncio
async def test_stop_build_when_running() -> None:
    """stop_build cancels a running flow task."""
    state = _make_app_state()
    task = MagicMock()
    task.done.return_value = False
    state.flow_task = task

    service = _make_service(state)
    result = await service.stop_build()

    assert result["status"] == "stopped"
    task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_stop_build_when_idle() -> None:
    """stop_build returns not_running when nothing is active."""
    service = _make_service()
    result = await service.stop_build()
    assert result["status"] == "not_running"


@pytest.mark.asyncio
async def test_stop_all_cancels_both_tasks() -> None:
    """stop_all cancels both flow_task and console_task."""
    state = _make_app_state()
    flow_task = MagicMock()
    flow_task.done.return_value = False
    console_task = MagicMock()
    console_task.done.return_value = False
    state.flow_task = flow_task
    state.console_task = console_task

    service = _make_service(state)
    result = await service.stop_all()

    assert result["status"] == "stopped"
    assert "build" in result["cancelled"]
    assert "console" in result["cancelled"]
    flow_task.cancel.assert_called_once()
    console_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_stop_all_when_nothing_running() -> None:
    """stop_all returns empty cancelled list when nothing is active."""
    state = _make_app_state()
    state.console_task = None
    service = _make_service(state)
    result = await service.stop_all()

    assert result["status"] == "stopped"
    assert result["cancelled"] == []


@pytest.mark.asyncio
async def test_scan_gaps() -> None:
    """scan_gaps returns gap count and types."""
    service = _make_service()

    mock_gap = MagicMock()
    mock_gap.type = MagicMock()
    mock_gap.type.value = "UNCOVERED_PARA"
    mock_gap.model_dump.return_value = {}

    with patch("backend.analysis.gap_analyser.GapAnalyser.analyse", return_value=[mock_gap]), \
         patch("backend.pipeline.flow.GAP_TYPE_TO_PHASE", {mock_gap.type: 3}), \
         patch("backend.pipeline.flow._QUALITY_GAP_TYPES", set()):
        result = await service.scan_gaps(3)

    assert result["phase"] == 3
    assert result["gap_count"] == 1
    assert "UNCOVERED_PARA" in result["gap_types"]


@pytest.mark.asyncio
async def test_purge_derived() -> None:
    """purge_derived deletes non-PROJECT/DOCUMENT nodes."""
    state = _make_app_state()
    state.graph.nodes = AsyncMock(return_value=[
        MagicMock(node_id="PROJECT-0001", node_type="PROJECT"),
        MagicMock(node_id="HLR-0001", node_type="HLR"),
        MagicMock(node_id="LLR-0001", node_type="LLR"),
    ])
    state.graph.delete_node = AsyncMock()
    state.graph.reset_sequences = AsyncMock()
    state.phase_store.get_all.return_value = [
        {"phase_number": i, "status": "pending"} for i in range(2, 14)
    ]

    service = _make_service(state)
    result = await service.purge_derived()

    assert result["status"] == "purged"
    assert result["deleted_count"] == 2
    assert state.graph.delete_node.call_count == 2


@pytest.mark.asyncio
async def test_ingest_document_file_not_found() -> None:
    """ingest_document returns error when forge.md doesn't exist."""
    state = _make_app_state()
    state.config.project.forgemd = "forge.md"

    service = _make_service(state)

    with patch("backend.services.ingest.resolve_forgemd_path") as mock_resolve:
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_resolve.return_value = mock_path

        result = await service.ingest_document()

    assert result["status"] == "error"
    assert "not found" in result["detail"]


@pytest.mark.asyncio
async def test_ingest_document_success(tmp_path: Path) -> None:
    """ingest_document ingests the file and marks phase 1 complete."""
    state = _make_app_state()
    state.config.project.forgemd = "forge.md"

    service = _make_service(state)

    forgemd = tmp_path / "forge.md"
    forgemd.write_text("# Spec\n")

    with patch("backend.services.ingest.resolve_forgemd_path", return_value=forgemd), \
         patch("backend.services.ingest.ingest_forgemd", new_callable=AsyncMock):
        result = await service.ingest_document()

    assert result["status"] == "ingested"
    state.phase_store.set_status.assert_called_with(1, "complete")


# ── Tool wrapper tests ────────────────────────────────────────────────


def test_run_phase_tool_delegates_to_service() -> None:
    """RunPhaseTool calls service.run_phase via run_on_main_loop."""
    service = MagicMock()
    service.run_on_main_loop.return_value = {"status": "started", "phases": "12-12"}

    tool = _make_tool(RunPhaseTool, service)
    result = json.loads(tool._execute(phase=12))

    assert result["status"] == "started"
    service.run_on_main_loop.assert_called_once()


def test_stop_build_tool_delegates_to_service() -> None:
    """StopBuildTool calls service.stop_build via run_on_main_loop."""
    service = MagicMock()
    service.run_on_main_loop.return_value = {"status": "stopped"}

    tool = _make_tool(StopBuildTool, service)
    result = json.loads(tool._execute())

    assert result["status"] == "stopped"


def test_stop_all_agents_tool_delegates_to_service() -> None:
    """StopAllAgentsTool calls service.stop_all via run_on_main_loop."""
    service = MagicMock()
    service.run_on_main_loop.return_value = {"status": "stopped", "cancelled": ["build"]}

    tool = _make_tool(StopAllAgentsTool, service)
    result = json.loads(tool._execute())

    assert result["status"] == "stopped"
    assert "build" in result["cancelled"]


def test_scan_gaps_tool_delegates_to_service() -> None:
    """ScanGapsTool calls service.scan_gaps via run_on_main_loop."""
    service = MagicMock()
    service.run_on_main_loop.return_value = {"phase": 3, "gap_count": 5, "gap_types": []}

    tool = _make_tool(ScanGapsTool, service)
    result = json.loads(tool._execute(phase=3))

    assert result["gap_count"] == 5


def test_purge_derived_tool_delegates_to_service() -> None:
    """PurgeDerivedTool calls service.purge_derived via run_on_main_loop."""
    service = MagicMock()
    service.run_on_main_loop.return_value = {"status": "purged", "deleted_count": 42}

    tool = _make_tool(PurgeDerivedTool, service)
    result = json.loads(tool._execute())

    assert result["deleted_count"] == 42


def test_ingest_document_tool_delegates_to_service() -> None:
    """IngestDocumentTool calls service.ingest_document via run_on_main_loop."""
    service = MagicMock()
    service.run_on_main_loop.return_value = {"status": "ingested", "path": "/ws/forge.md"}

    tool = _make_tool(IngestDocumentTool, service)
    result = json.loads(tool._execute())

    assert result["status"] == "ingested"


def test_all_operator_tools_are_console_only() -> None:
    """Operator tool names must appear only in the Console role permissions."""
    from backend.agents.definitions import AgentRole
    from backend.tools.registry import ToolRegistry

    registry = ToolRegistry()
    op_names = {
        "run_phase", "stop_build", "stop_all_agents", "scan_gaps",
        "scan_quality", "qual_check", "purge_derived", "ingest_document",
    }

    for role in AgentRole:
        allowed = registry._role_permissions.get(role, set())
        if role == AgentRole.CONSOLE:
            assert op_names.issubset(allowed), f"Console missing: {op_names - allowed}"
        else:
            assert not (op_names & allowed), f"{role.value} should not have: {op_names & allowed}"


# ── residual operator tool coverage ───────────────────────────────────


def test_operator_tool_invoke_not_implemented() -> None:
    tool = _make_tool(_OperatorTool, MagicMock())
    with pytest.raises(NotImplementedError):
        tool._execute()


def test_scan_quality_tool_invokes_service() -> None:
    from backend.tools.operator import ScanQualityTool

    service = MagicMock()
    service.run_on_main_loop = MagicMock(return_value={"quality_gaps": 3})
    tool = _make_tool(ScanQualityTool, service)
    result = json.loads(tool._invoke(phase=4))
    assert result == {"quality_gaps": 3}
    service.scan_quality.assert_called_once_with(4)


def test_qual_check_tool_invokes_service() -> None:
    from backend.tools.operator import QualCheckTool

    service = MagicMock()
    service.run_on_main_loop = MagicMock(return_value={"dispatched": True})
    tool = _make_tool(QualCheckTool, service)
    result = json.loads(tool._invoke(phase=2))
    assert result == {"dispatched": True}
    service.qual_check.assert_called_once_with(2)
