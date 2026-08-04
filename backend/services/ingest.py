"""Forge.md resolution and ingestion — shared across server and service layers.

Extracted from ``backend.server.lifespan`` so that both the HTTP layer
and ``OperatorService`` / ``ForgeFlow`` can call these without reaching
into lifespan internals.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.config.models import ForgeConfig
    from backend.graph.engine import ProjectGraph

_log = logging.getLogger("forge")


def resolve_forgemd_path(workspace: Path, forgemd_setting: str) -> Path:
    """Resolve the forge.md path.

    If *forgemd_setting* is an absolute path, return it directly.
    Otherwise search the workspace root (then one level of subdirectories)
    for a file whose name matches *forgemd_setting* (case-insensitive),
    falling back to workspace/setting.
    """
    p = Path(forgemd_setting)
    if p.is_absolute():
        return p

    target_name = p.name.lower()

    # glob("*.md") is case-sensitive on Linux, so it would exclude FORGE.MD
    # before the lowercased comparison below ever ran — and the repo's own demo
    # whitepapers ship as FORGE-v1/v2/v3.MD. Match on the full filename instead,
    # extension included, so the case-insensitivity this function documents
    # actually holds. Sorted for deterministic resolution when a directory holds
    # several spellings.
    for candidate in sorted(workspace.glob("*")):
        if candidate.is_file() and candidate.name.lower() == target_name:
            return candidate

    # Search one level of subdirectories
    for candidate in sorted(workspace.glob("*/*")):
        if candidate.is_file() and candidate.name.lower() == target_name:
            return candidate

    return workspace / forgemd_setting


async def _ensure_project_node(graph: Any, project_name: str) -> str:
    """Return the PROJECT node_id, creating one if it does not exist."""
    from backend.graph.models import GraphNode, LifecycleState, NodeType

    existing: list[GraphNode] = await graph.nodes_by_type(NodeType.PROJECT.value)
    if existing:
        return existing[0].node_id
    project_node_id: str = await graph.allocate_node_id("PROJECT")
    proj_node = GraphNode(
        node_id=project_node_id,
        node_type=NodeType.PROJECT.value,
        layer=0,
        title=project_name,
        content="",
        lifecycle=LifecycleState.ACTIVE,
        created_by="system",
    )
    await graph.add_node(proj_node)
    _log.info("forge.project_node.created", extra={"node_id": project_node_id})
    return project_node_id


async def ingest_forgemd(
    forgemd_path: Path,
    graph: ProjectGraph,
    config: ForgeConfig,
) -> None:
    """Phase 1 ingestion — create/update the DOCUMENT node only.

    Chunking (PARA creation) is intentionally deferred to Phase 2
    (UNCHUNKED_DOCUMENT gap -> Document Specialist agent).
    """
    from backend.graph.models import GraphNode, LifecycleState, NodeType

    try:
        project_node_id = await _ensure_project_node(graph, config.project.name)
        content = forgemd_path.read_text(encoding="utf-8")

        doc_node = await graph.find_node_by_slug("forgemd")
        if doc_node is None:
            node_id = await graph.allocate_node_id("DOCUMENT")
            doc_node = GraphNode(
                node_id=node_id,
                node_type=NodeType.DOCUMENT.value,
                layer=1,
                title="Forge.md",
                content=content,
                parent_id=project_node_id,
                properties={"doc_type": "reference", "doc_order": 99, "slug": "forgemd"},
                lifecycle=LifecycleState.DRAFT,
                created_by="system",
            )
            await graph.add_node(doc_node)
            _log.info("forge.forgemd.created", extra={"node_id": node_id, "bytes": len(content)})
        elif doc_node.content != content:
            doc_node.content = content
            if project_node_id and doc_node.parent_id is None:
                doc_node.parent_id = project_node_id
            await graph.add_node(doc_node)
            _log.info(
                "forge.forgemd.updated",
                extra={"node_id": doc_node.node_id, "bytes": len(content)},
            )
    except Exception as exc:
        # Re-raise. Swallowing this meant phase 1 could create no DOCUMENT node
        # and still be marked complete by `_run_ingest_phase`, after which every
        # later phase ran against an empty graph and the build reported success.
        # The warning also went to stdlib logging, which is not wired to any
        # ForgeLogger sink, so nothing surfaced in the Control Station either.
        # Letting it propagate stops `_set_phase_status(1, "complete")` from
        # running, which is the loud failure the "no silent fallbacks" rule asks
        # for.
        from backend.server.forge_logger import forge_logger  # noqa: PLC0415

        forge_logger.emit(
            "ERROR",
            "PHASE",
            f"Phase 1 ingest failed for {forgemd_path.name}: {type(exc).__name__}: {exc}",
        )
        _log.error("forge.forgemd.ingest_failed: %s", str(exc))
        raise
