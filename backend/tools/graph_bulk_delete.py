"""graph_bulk_delete — delete multiple graph nodes matching filters."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.async_utils import run_async
from backend.tools.base import ForgeTool


class _Args(BaseModel):
    node_ids: str = Field(
        default="[]",
        description="JSON array of specific node IDs to delete.",
    )
    node_type: str = Field(default="", description="Delete nodes of this type (e.g. 'PARA').")
    pattern: str = Field(default="", description="Regex pattern — delete nodes whose content matches.")
    dry_run: str = Field(
        default="true",
        description="'true' to preview deletions. 'false' to actually delete.",
    )


class GraphBulkDeleteTool(ForgeTool):
    """Delete multiple graph nodes by ID list, type filter, and/or content regex."""

    name: str = "graph_bulk_delete"
    description: str = (
        "Delete multiple graph nodes in one call. Filter by node_ids, node_type, "
        "and/or content regex. Defaults to dry_run=true (preview only)."
    )
    args_schema: type[BaseModel] = _Args

    _graph: object = None

    def __init__(self, graph: object = None) -> None:
        super().__init__()
        object.__setattr__(self, "_graph", graph)

    def _execute(self, **kwargs: Any) -> str:
        if self._graph is None:
            return "ERROR: Graph not available"
        try:
            result: str = run_async(self._dispatch(self._graph, **kwargs), timeout=120)
            return result
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    async def _dispatch(self, graph: Any, **kw: Any) -> str:
        raw_ids = kw.get("node_ids", "[]")
        node_type = kw.get("node_type", "").strip()
        pattern = kw.get("pattern", "").strip()
        dry_run = str(kw.get("dry_run", "true")).lower() != "false"

        try:
            id_list = json.loads(raw_ids) if raw_ids.strip() else []
        except json.JSONDecodeError as exc:
            return f"ERROR: Invalid JSON in node_ids: {exc}"

        if not id_list and not node_type and not pattern:
            return "ERROR: At least one filter required (node_ids, node_type, or pattern)."

        candidates = _filter_nodes(graph.all_nodes(), id_list, node_type, pattern)
        if isinstance(candidates, str):
            return candidates  # error message from invalid regex

        if not candidates:
            return "No nodes match the given filters."

        if dry_run:
            return _format_preview(candidates)

        return await _delete_nodes(graph, candidates)


def _filter_nodes(
    nodes: list[Any], id_list: list[str], node_type: str, pattern: str,
) -> list[Any] | str:
    """Apply filters (AND logic) and return matching nodes or error string."""
    result = list(nodes)

    if id_list:
        id_set = set(id_list)
        result = [n for n in result if n.node_id in id_set]

    if node_type:
        result = [n for n in result if n.node_type.upper() == node_type.upper()]

    if pattern:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return f"ERROR: Invalid regex '{pattern}': {exc}"
        result = [n for n in result if n.content and regex.search(n.content)]

    return result


def _format_preview(candidates: list[Any]) -> str:
    """Format dry-run preview of nodes that would be deleted."""
    items = [
        {"node_id": n.node_id, "node_type": n.node_type, "title": n.title or ""}
        for n in candidates
    ]
    return f"DRY RUN — {len(items)} node(s) would be deleted:\n" + json.dumps(items, indent=2)


async def _delete_nodes(graph: Any, candidates: list[Any]) -> str:
    """Delete the candidate nodes from the graph."""
    deleted = 0
    errors: list[str] = []
    for n in candidates:
        try:
            await graph.delete_node(n.node_id)
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{n.node_id}: {exc}")
    parts = [f"OK: {deleted} node(s) deleted."]
    if errors:
        parts.append("Errors:\n" + "\n".join(errors))
    return "\n".join(parts)
