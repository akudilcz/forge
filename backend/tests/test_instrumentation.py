"""Tests that instrumentation emits LogRecords through the sink.

Registers an in-memory capture sink on the ForgeLogger singleton and
asserts that the expected records flow through from each instrumented
surface.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.observability.log_record import LogRecord
from backend.server.forge_logger import forge_logger


class _CaptureSink:
    def __init__(self) -> None:
        self.records: list[LogRecord] = []

    def write(self, record: LogRecord) -> None:
        self.records.append(record)

    def close(self) -> None:
        pass


@pytest.fixture
def capture() -> Iterator[_CaptureSink]:
    sink = _CaptureSink()
    forge_logger.add_sink(sink)
    yield sink
    # Remove after the test.
    if sink in forge_logger._sinks:
        forge_logger._sinks.remove(sink)


def _records_by(capture: _CaptureSink, **filters: Any) -> list[LogRecord]:
    return [
        r for r in capture.records
        if all(getattr(r, k) == v for k, v in filters.items())
    ]


# ── Tool base instrumentation ────────────────────────────────────────────────


def test_tool_base_logs_call_result_and_duration(capture: _CaptureSink) -> None:
    from backend.tools.base import ForgeTool

    class _Echo(ForgeTool):
        name: str = "echo_tool"
        description: str = "test tool"

        def _execute(self, **kwargs: Any) -> str:
            return f"ok:{kwargs.get('x')}"

    tool = _Echo()
    result = tool._run(x=42)
    assert result == "ok:42"

    tool_records = _records_by(capture, category="TOOL")
    assert any(r.tool_name == "echo_tool" for r in tool_records)
    assert any(r.tool_name == "echo_tool" and r.duration_ms is not None
               for r in tool_records)


def test_tool_base_logs_error_with_traceback(capture: _CaptureSink) -> None:
    from backend.tools.base import ForgeTool

    class _Boom(ForgeTool):
        name: str = "boom_tool"
        description: str = "test tool"

        def _execute(self, **kwargs: Any) -> str:
            raise RuntimeError("kaboom")

    tool = _Boom()
    result = tool._run()
    assert result.startswith("TOOL_ERROR")

    errs = _records_by(capture, level="ERROR", category="TOOL")
    assert errs
    assert errs[0].error_type == "RuntimeError"
    assert errs[0].extras is not None
    assert "kaboom" in errs[0].extras.get("traceback", "")


# ── Context budget ──────────────────────────────────────────────────────────


def test_context_budget_logs_dropped_sections(capture: _CaptureSink) -> None:
    from backend.prompting.context_budget import Section, count_tokens, pack

    a = "alpha " * 50
    b = "bravo " * 50
    sections = [
        Section(90, "important", a),   # kept
        Section(10, "background", b),  # dropped
    ]
    budget = count_tokens(a) + 2  # fits only one
    out = pack(sections, budget_tokens=budget)
    assert "alpha" in out
    assert "bravo" not in out

    ctx_warns = _records_by(capture, category="CTX", level="WARN")
    assert ctx_warns
    assert ctx_warns[0].extras is not None
    assert "background" in ctx_warns[0].extras.get("dropped_sections", [])


# ── Graph engine writes ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_add_node_emits_graph_write(capture: _CaptureSink, tmp_path: Path) -> None:
    from backend.graph.engine import ProjectGraph
    from backend.graph.models import GraphNode, NodeType

    graph = ProjectGraph(str(tmp_path / "g.db"))
    await graph.initialise()
    await graph.add_node(
        GraphNode(
            node_id="PROJECT-0001",
            node_type=NodeType.PROJECT.value,
            content="test",
            created_by="test",
        )
    )

    graph_records = _records_by(capture, category="GRAPH")
    assert any(r.node_id == "PROJECT-0001" for r in graph_records)


# ── Semantic dedup decision ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_semantic_check_emits_decision(capture: _CaptureSink) -> None:
    from backend.crew.semantic_duplicate_check import create_semantic_checker

    llm = MagicMock()
    resp = MagicMock()
    resp.content = "UNIQUE - distinct"
    llm.ainvoke = AsyncMock(return_value=resp)

    graph = MagicMock()
    graph.delete_node = AsyncMock()

    check = create_semantic_checker(llm, graph, {})
    await check("HLR-0001", "The system shall log.", "[HLR-0002] The system shall cache.")

    decisions = _records_by(capture, category="DECIDE")
    assert any(
        r.extras and r.extras.get("decision_category") == "semantic_dedup"
        for r in decisions
    )


# ── WebSocket manager ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ws_manager_logs_connect_disconnect(capture: _CaptureSink) -> None:
    from backend.server.websocket.manager import WebSocketManager

    mgr = WebSocketManager()
    ws = MagicMock()
    ws.accept = AsyncMock()

    await mgr.connect(ws, "conn-xyz")
    assert any(
        r.category == "WS" and r.extras
        and r.extras.get("connection_id") == "conn-xyz"
        for r in capture.records
    )

    await mgr.disconnect(ws)
    assert sum(1 for r in capture.records if r.category == "WS") >= 2


# ── Correlation propagation ──────────────────────────────────────────────────


def test_log_context_propagates_to_tool_records(capture: _CaptureSink) -> None:
    from backend.observability import log_context
    from backend.tools.base import ForgeTool

    class _T(ForgeTool):
        name: str = "noop"
        description: str = "test"
        def _execute(self, **kwargs: Any) -> str:
            return "ok"

    tool = _T()
    with log_context(run_id="run-abc", phase=7, gap_type="UNDESIGNED"):
        tool._run(x=1)

    recs = _records_by(capture, category="TOOL", tool_name="noop")
    assert recs
    assert any(r.run_id == "run-abc" and r.phase == 7 for r in recs)
