"""Trace manager — sync graph node trace data with files on disk.

Delegates entirely to code_gen's _build_result + _persist_traces so there
is exactly ONE code path for trace persistence. No separate parsing,
no "unchanged" optimisation, no discovery pass — just scan → persist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.server.forge_logger import forge_logger


async def sync_traces(graph: Any, workspace: Path) -> dict[str, Any]:
    """Re-scan workspace and persist traces — single code path with code_gen."""
    from backend.codegen.slice_gen import _build_result, _persist_traces

    forge_logger.emit("INFO", "CGEN ", f"Sync traces — workspace={workspace}")

    result = _build_result(workspace, graph)
    await _persist_traces(result, graph)

    src = len(result.source_files)
    test = len(result.test_files)
    total_traces = sum(len(g.line_traces) for g in result.source_files + result.test_files)
    forge_logger.emit(
        "INFO", "CGEN ",
        f"Sync traces complete — {src} src, {test} test, {total_traces} traces",
    )
    return {"src": src, "test": test, "traces": total_traces}
