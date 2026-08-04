"""Workspace sync — deterministic Phase 13 step.

Reads the workspace filesystem and creates CODE/TEST nodes in the graph
by matching DESIGN and CASE nodes to their workspace files.  Pure code —
no LLM calls, no agents, no retries.

Phase 12 (code gen) stores ``file_path`` on every DESIGN and CASE node
it processes.  This module reads that property, reads the file from disk,
and batch-creates the corresponding CODE/TEST child nodes with full
source content and LLR traceability.

On missing files, emits a ``MISSING_CODE`` gap so the quality audit
surfaces the drift loudly rather than silently skipping. On re-runs,
refreshes existing CODE/TEST content from disk when it has changed.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.crew.trace_parser import analyse_traces
from backend.graph.models import GraphNode, NodeType
from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from backend.crew.phase_steps import StepResult

logger = logging.getLogger(__name__)


# ── Public API ───────────────────────────────────────────────────────────────

async def workspace_sync(flow: Any, phase: int) -> StepResult:
    """Deterministic workspace sync: create CODE and TEST nodes from files.

    Returns a ``StepResult``-compatible dict. Attaches any
    ``MISSING_CODE`` / ``STALE_CODE`` gaps detected during sync to
    ``flow._workspace_sync_gaps`` for the next audit cycle to pick up.
    """
    import time  # noqa: PLC0415
    t0 = time.monotonic()
    forge_logger.emit(
        "INFO", "PIPE ",
        f"Phase {phase} · step: workspace_sync",
        phase=phase,
    )

    graph = flow.graph
    workspace: Path = flow._workspace
    detected_gaps: list[Gap] = []

    code_count, code_refreshed = await _sync_code_nodes(graph, workspace, detected_gaps)
    test_count, test_refreshed = await _sync_test_nodes(graph, workspace, detected_gaps)

    duration_ms = int((time.monotonic() - t0) * 1000)
    forge_logger.emit(
        "INFO", "SYNC ",
        f"Workspace sync complete — {code_count} CODE created, "
        f"{code_refreshed} refreshed, {test_count} TEST created, "
        f"{test_refreshed} refreshed, {len(detected_gaps)} gap(s)",
        phase=phase,
        duration_ms=duration_ms,
        code_created=code_count,
        code_refreshed=code_refreshed,
        test_created=test_count,
        test_refreshed=test_refreshed,
        gaps_detected=len(detected_gaps),
    )
    flow._workspace_sync_gaps = detected_gaps
    return {"step_name": "workspace_sync", "deletions": 0}


# ── CODE node sync ───────────────────────────────────────────────────────────

async def _sync_code_nodes(
    graph: Any,
    workspace: Path,
    detected_gaps: list[Gap],
) -> tuple[int, int]:
    """Create or refresh CODE nodes for DESIGN nodes with workspace files.

    Returns (created_count, refreshed_count). Emits MISSING_CODE gaps when
    DESIGN.file_path is set but the file is missing.
    """
    designs = [
        n for n in graph.all_nodes()
        if n.node_type == NodeType.DESIGN.value
    ]
    created = 0
    refreshed = 0
    for design in designs:
        file_path = (design.properties or {}).get("file_path", "")
        if not file_path:
            continue
        abs_path = workspace / file_path
        content = _read_file(abs_path)

        existing = _find_child_of_type(graph, design.node_id, NodeType.CODE.value)

        if content is None:
            # File missing where one was declared: loud gap, not silent skip.
            detected_gaps.append(
                Gap(
                    type=GapType.MISSING_CODE,
                    priority=GapPriority.MAINTENANCE,
                    node_id=design.node_id,
                    description=(
                        f"DESIGN {design.node_id} declares file_path "
                        f"{file_path!r} but the file is missing or unreadable."
                    ),
                    context={"file_path": file_path},
                )
            )
            forge_logger.emit(
                "WARN", "SYNC ",
                f"  MISSING {design.node_id} → {file_path}",
            )
            continue

        if existing is not None:
            # Refresh file_content if it changed on disk.
            prev_content = (existing.properties or {}).get("file_content", "")
            if prev_content != content:
                new_props = dict(existing.properties or {})
                new_props["file_content"] = content
                new_props["file_hash"] = _hash(content)
                await graph.update_node(
                    existing.node_id,
                    content=None,
                    properties=new_props,
                    changed_by="workspace-sync",
                    change_reason="Workspace file content changed on disk",
                    trace_to=None,
                )
                refreshed += 1
                forge_logger.emit(
                    "INFO", "SYNC ",
                    f"  REFRESH {existing.node_id} ← {design.node_id} → {file_path}",
                )
            continue

        node_id = await graph.allocate_node_id("CODE")
        node = GraphNode(
            node_id=node_id,
            node_type=NodeType.CODE.value,
            parent_id=design.node_id,
            title=f"{design.title} implementation",
            content=f"Source implementation for {design.title} ({file_path}).",
            trace_to=list(design.trace_to or []),
            created_by="workspace-sync",
            properties={
                "file_path": file_path,
                "file_content": content,
                "file_hash": _hash(content),
            },
        )
        await graph.add_node(node)
        forge_logger.emit(
            "INFO", "SYNC ",
            f"  CODE {node_id} ← {design.node_id} → {file_path}",
        )
        created += 1
    return created, refreshed


# ── TEST node sync ───────────────────────────────────────────────────────────

async def _sync_test_nodes(
    graph: Any,
    workspace: Path,
    detected_gaps: list[Gap],
) -> tuple[int, int]:
    """Create or refresh TEST nodes for CASE nodes with workspace files."""
    cases = [
        n for n in graph.all_nodes()
        if n.node_type in (NodeType.CASE_HLR.value, NodeType.CASE_LLR.value)
    ]
    created = 0
    refreshed = 0
    for case in cases:
        file_path = (case.properties or {}).get("file_path", "")
        if not file_path:
            continue
        abs_path = workspace / file_path
        content = _read_file(abs_path)

        existing = _find_child_of_type(graph, case.node_id, NodeType.TEST.value)

        if content is None:
            detected_gaps.append(
                Gap(
                    type=GapType.MISSING_CODE,
                    priority=GapPriority.MAINTENANCE,
                    node_id=case.node_id,
                    description=(
                        f"CASE {case.node_id} declares file_path "
                        f"{file_path!r} but the test file is missing or unreadable."
                    ),
                    context={"file_path": file_path},
                )
            )
            forge_logger.emit(
                "WARN", "SYNC ",
                f"  MISSING {case.node_id} → {file_path}",
            )
            continue

        analysis = analyse_traces(content)
        test_functions = [t.symbol for t in analysis.traces]

        if existing is not None:
            prev_content = (existing.properties or {}).get("file_content", "")
            if prev_content != content:
                new_props = dict(existing.properties or {})
                new_props["file_content"] = content
                new_props["file_hash"] = _hash(content)
                new_props["test_functions"] = test_functions
                await graph.update_node(
                    existing.node_id,
                    content=None,
                    properties=new_props,
                    changed_by="workspace-sync",
                    change_reason="Workspace test file content changed on disk",
                    trace_to=None,
                )
                refreshed += 1
                forge_logger.emit(
                    "INFO", "SYNC ",
                    f"  REFRESH {existing.node_id} ← {case.node_id} → {file_path}",
                )
            continue

        node_id = await graph.allocate_node_id("TEST")
        node = GraphNode(
            node_id=node_id,
            node_type=NodeType.TEST.value,
            parent_id=case.node_id,
            title=f"{case.title} tests",
            content=f"Workspace tests for {case.title} ({file_path}).",
            # Mirrors _sync_code_nodes, which copies its DESIGN's traces. The
            # omission here was not cosmetic: `result_recorder._find_trace_targets`
            # locates a TEST by scanning `node.trace_to` for the case id, so with
            # an empty trace it never matched and every RESULT node was parented
            # on the CASE instead — leaving them permanently ORPHAN_NODE. A live
            # build produced 195 RESULT nodes and ~197 unresolved gaps.
            trace_to=[case.node_id, *(case.trace_to or [])],
            created_by="workspace-sync",
            properties={
                "file_path": file_path,
                "file_content": content,
                "file_hash": _hash(content),
                "test_functions": test_functions,
            },
        )
        await graph.add_node(node)
        forge_logger.emit(
            "INFO", "SYNC ",
            f"  TEST {node_id} ← {case.node_id} → {file_path} "
            f"({len(test_functions)} fn)",
        )
        created += 1
    return created, refreshed


# ── Helpers ──────────────────────────────────────────────────────────────────

def _find_child_of_type(graph: Any, node_id: str, node_type: str) -> Any:
    """Return the first child of the given type, or None."""
    for c in graph.children_sync(node_id):
        if c.node_type == node_type:
            return c
    return None


def _read_file(path: Path) -> str | None:
    """Read a file, returning None if it doesn't exist or can't be read."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
