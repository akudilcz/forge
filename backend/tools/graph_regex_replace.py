"""graph_regex_replace — find-and-replace via regex across graph node text."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.async_utils import run_async
from backend.tools.base import ForgeTool


class _Args(BaseModel):
    pattern: str = Field(description="Python regex pattern to match.")
    replacement: str = Field(
        description="Replacement string (supports \\1 backrefs).",
    )
    field: str = Field(
        default="content",
        description="Field to search: 'title', 'content', or 'both'.",
    )
    node_type: str = Field(default="", description="Optional node type filter (e.g. 'HLR').")
    dry_run: str = Field(
        default="true",
        description="'true' to preview changes without applying. 'false' to apply.",
    )


class GraphRegexReplaceTool(ForgeTool):
    """Regex find-and-replace across graph node titles and/or content."""

    name: str = "graph_regex_replace"
    description: str = (
        "Find-and-replace using regex across graph nodes. "
        "Defaults to dry_run=true (preview only). Set dry_run=false to apply."
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
            result: str = run_async(self._dispatch(self._graph, **kwargs), timeout=60)
            return result
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    async def _dispatch(self, graph: Any, **kw: Any) -> str:
        pattern = kw.get("pattern", "")
        replacement = kw.get("replacement", "")
        field = kw.get("field", "content")
        node_type = kw.get("node_type", "")
        dry_run = str(kw.get("dry_run", "true")).lower() != "false"

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return f"ERROR: Invalid regex '{pattern}': {exc}"

        nodes = graph.all_nodes()
        if node_type:
            nodes = [n for n in nodes if n.node_type.upper() == node_type.upper()]

        changes = _collect_changes(regex, replacement, nodes, field)
        if not changes:
            return f"No matches found for pattern '{pattern}'"

        if dry_run:
            return _format_preview(changes)

        return await _apply_changes(graph, changes)


def _collect_changes(
    regex: re.Pattern[str], replacement: str, nodes: list[Any], field: str,
) -> list[dict[str, Any]]:
    """Return list of change dicts for nodes where regex matches."""
    changes: list[dict[str, Any]] = []
    for node in nodes:
        entry: dict[str, Any] = {"node_id": node.node_id, "node_type": node.node_type}
        changed = False

        if field in ("title", "both") and node.title and regex.search(node.title):
            new_title = regex.sub(replacement, node.title)
            entry["old_title"] = node.title
            entry["new_title"] = new_title
            changed = True

        if field in ("content", "both") and node.content and regex.search(node.content):
            new_content = regex.sub(replacement, node.content)
            entry["old_content_preview"] = node.content[:200]
            entry["new_content_preview"] = new_content[:200]
            entry["new_content"] = new_content
            changed = True

        if changed:
            changes.append(entry)
    return changes


def _format_preview(changes: list[dict[str, Any]]) -> str:
    """Format dry-run preview, stripping full content from output."""
    preview = []
    for c in changes:
        p = {k: v for k, v in c.items() if k != "new_content"}
        preview.append(p)
    return f"DRY RUN — {len(preview)} node(s) would change:\n" + json.dumps(preview, indent=2)


async def _apply_changes(graph: Any, changes: list[dict[str, Any]]) -> str:
    """Apply the collected changes to the graph."""
    applied = 0
    for c in changes:
        new_content = c.get("new_content")
        new_title = c.get("new_title")
        await graph.update_node(
            node_id=c["node_id"],
            content=new_content,
            properties=None,
            changed_by="agent",
            change_reason="regex_replace",
            title=new_title,
        )
        applied += 1
    return f"OK: {applied} node(s) updated."
