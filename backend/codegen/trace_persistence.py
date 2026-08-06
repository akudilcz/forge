"""Trace persistence — store parsed ``@traces`` data on graph nodes.

Persists each generated file's line traces, trace coverage, and a
codegen-hash fingerprint onto its DESIGN/CASE node, stamps codegen
errors so failures surface as STALE_CODE gaps, and clears stale trace
props from nodes whose files are now owned elsewhere.

Design reference: specs/03-build-pipeline.md
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from backend.codegen.slice_gen import CodeGenResult, GeneratedFile
    from backend.graph.engine import ProjectGraph


async def _persist_single_file(
    gf: GeneratedFile,
    graph: ProjectGraph,
) -> None:
    """Persist trace props for one generated file to the graph."""
    try:
        node = graph.node_sync(gf.node_id)
        if not node:
            return
        props = dict(node.properties or {})

        is_case = node.node_type in ("CASE_HLR", "CASE_LLR")
        trace_dicts = []
        for t in gf.line_traces:
            td = asdict(t)
            if is_case and gf.node_id not in td.get("case_ids", []):
                td.setdefault("case_ids", []).append(gf.node_id)
            trace_dicts.append(td)

        props["file_path"] = gf.file_path
        props["line_traces"] = trace_dicts
        props["traced_llrs"] = sorted({lid for t in gf.line_traces for lid in t.llr_ids})
        props["trace_coverage"] = {"total": gf.total_functions, "traced": gf.traced_functions}
        props["untraced_functions"] = [asdict(uf) for uf in gf.untraced_functions]
        props.pop("trace_audit", None)
        props.pop("codegen_error", None)  # successful gen clears prior error

        # Stamp a fingerprint of the inputs so future runs can detect when a
        # DESIGN/CASE has been regenerated against changed upstream content
        # (surfaces as STALE_CODE when inputs shift without a regeneration).
        contract_content = _owning_contract_content(graph, node)
        props["codegen_hash"] = codegen_hash(
            node.content or "",
            contract_content,
            "",  # model — left blank for now; wire in when provider config is exposed
        )

        await graph.update_node(
            gf.node_id,
            content=None,
            properties=props,
            changed_by="code_gen",
            change_reason="Persist line-level LLR traces",
        )
        forge_logger.emit(
            "INFO",
            "CGEN ",
            f"  {gf.node_id} -> {gf.file_path} "
            f"({len(gf.line_traces)} traces, {gf.traced_functions}/{gf.total_functions} funcs)",
        )
    except Exception as exc:  # noqa: BLE001
        forge_logger.emit("ERROR", "CGEN", f"Failed to persist traces for {gf.node_id}: {exc}")
        # Stamp the node so the next Gap Analysis pass detects it as STALE_CODE
        # rather than silently skipping.
        await _stamp_codegen_error(graph, gf.node_id, str(exc))


async def _stamp_codegen_error(graph: ProjectGraph, node_id: str, error: str) -> None:
    """Record a codegen failure on a DESIGN or CASE node so it surfaces as
    a STALE_CODE gap. Never raises — failure-path code must not escalate.
    """
    try:
        node = graph.node_sync(node_id)
        if node is None:
            return
        props = dict(node.properties or {})
        props["codegen_error"] = error
        await graph.update_node(
            node_id,
            content=None,
            properties=props,
            changed_by="code_gen",
            change_reason=f"Codegen failed: {error[:80]}",
        )
    except Exception as exc:  # noqa: BLE001
        forge_logger.emit("ERROR", "CGEN", f"Could not stamp codegen_error on {node_id}: {exc}")


def _owning_contract_content(graph: ProjectGraph, node: Any) -> str:
    """Return the content of the CONTRACT sibling of a DESIGN (empty for CASEs).

    Used by ``_persist_single_file`` to include the contract in the
    codegen-hash fingerprint — a CONTRACT change should invalidate caches
    on all DESIGNs that refer to it.
    """
    if node.node_type != "DESIGN" or not node.parent_id:
        return ""
    for child in graph.children_sync(node.parent_id):
        if child.node_type == "CONTRACT" and child.content:
            return child.content
    return ""


def codegen_hash(design_content: str, contract_content: str, model: str) -> str:
    """Return a stable hash identifying the inputs to a codegen call.

    Callers can compare a node's stored ``properties["codegen_hash"]`` with
    this value and skip regeneration when unchanged.
    """
    import hashlib  # noqa: PLC0415
    h = hashlib.sha256()
    h.update((design_content or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((contract_content or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((model or "").encode("utf-8"))
    return h.hexdigest()


async def _persist_traces(result: CodeGenResult, graph: ProjectGraph) -> None:
    """Store line_traces in each node's properties for frontend access.

    Also clears stale trace props from CASE/DESIGN nodes not in the
    current result but still carrying file_path from a previous run.
    """
    all_files = result.source_files + result.test_files
    current_ids = {gf.node_id for gf in all_files}
    forge_logger.emit("INFO", "CGEN ", f"Persisting traces for {len(all_files)} node(s)...")

    for gf in all_files:
        await _persist_single_file(gf, graph)

    stale_keys = (
        "file_path",
        "line_traces",
        "trace_coverage",
        "untraced_functions",
        "traced_llrs",
        "trace_audit",
    )
    for node in graph.all_nodes():
        if node.node_id in current_ids:
            continue
        if node.node_type not in ("DESIGN", "CASE_HLR", "CASE_LLR"):
            continue
        props = node.properties or {}
        if not props.get("file_path"):
            continue
        cleaned = {k: v for k, v in props.items() if k not in stale_keys}
        if len(cleaned) < len(props):
            await graph.update_node(
                node.node_id,
                content=None,
                properties=cleaned,
                changed_by="code_gen",
                change_reason="Clear stale trace props (file owned by another node)",
            )
            forge_logger.emit("INFO", "CGEN ", f"  cleared stale traces from {node.node_id}")
