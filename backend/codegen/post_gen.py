"""Post-generation recording — trace audit and coverage metrics.

Runs after the mission agent finishes: LLM trace audit over generated
files and persistence of statement/branch coverage onto the DESIGN node
for the web UI. RESULT nodes are NOT recorded here — a RESULT's only
valid parent is a TEST node, which phase 13 workspace sync creates, so
recording belongs to the phase 13 ``record_results_step``
(design/23_phase_13_workspace_sync.md).

Design reference: design/22_phase_12_generate_code.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from pathlib import Path

    from backend.codegen.slice_gen import CodeGenResult
    from backend.graph.engine import ProjectGraph


async def _run_trace_audit(
    result: CodeGenResult,
    workspace: Path,
    graph: ProjectGraph,
) -> None:
    """Run LLM trace audit on generated files and persist results."""
    from backend.quality.trace_auditor import audit_traces, persist_audit_results  # noqa: PLC0415

    all_files = result.source_files + result.test_files
    file_paths = [gf.file_path for gf in all_files]
    file_node_map = {gf.file_path: gf.node_id for gf in all_files}

    if not file_paths:
        forge_logger.emit("INFO", "CGEN ", "No files to audit")
        return

    audit_results = await audit_traces(workspace, file_paths, graph)
    await persist_audit_results(audit_results, graph, file_node_map)

    fully_traced = sum(1 for r in audit_results if r.fully_traced)
    total_suggested = sum(len(r.suggested_traces) for r in audit_results)
    forge_logger.emit(
        "INFO",
        "CGEN ",
        f"Trace audit complete — {fully_traced}/{len(audit_results)} fully traced, "
        f"{total_suggested} suggestion(s)",
    )


async def _persist_coverage_metrics(graph: Any, last_state: Any) -> None:
    """Store statement/branch coverage on the DESIGN node for the web UI."""
    if not last_state:
        return
    designs = [n for n in graph.all_nodes() if n.node_type == "DESIGN"]
    if not designs:
        return
    design = designs[0]
    props = dict(design.properties or {})
    if last_state.coverage_pct is not None:
        props["statement_coverage"] = round(last_state.coverage_pct, 1)
    if last_state.branch_coverage_pct is not None:
        props["branch_coverage"] = round(last_state.branch_coverage_pct, 1)
    await graph.update_node(
        design.node_id,
        None,
        props,
        "system",
        "persist coverage metrics",
    )
