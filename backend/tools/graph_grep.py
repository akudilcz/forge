"""graph_grep — regex search across graph node titles and content."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _Args(BaseModel):
    pattern: str = Field(description="Python regex pattern to match.")
    field: str = Field(
        default="content",
        description="Which field to search: 'title', 'content', or 'both'.",
    )
    node_type: str = Field(default="", description="Optional node type filter (e.g. 'HLR').")
    max_results: int = Field(default=50, description="Maximum matches to return.")


class GraphGrepTool(ForgeTool):
    """Regex search across graph node titles and/or content (case-insensitive by default)."""

    name: str = "graph_grep"
    description: str = (
        "Regex search across graph node content and/or titles. "
        "Returns nodes whose text matches the given regular expression."
    )
    args_schema: type[BaseModel] = _Args

    def __init__(self, graph: object = None) -> None:
        """Args:
            graph: ProjectGraph instance to search (injected by the tool factory).
        """
        super().__init__()
        self._graph: Any = graph

    def _execute(self, **kwargs: Any) -> str:
        """Forward the LLM's validated ``_Args`` fields to :meth:`_search`."""
        return self._search(**kwargs)

    def _search(
        self,
        pattern: str,
        field: str = "content",
        node_type: str = "",
        max_results: int = 50,
    ) -> str:
        """Compile pattern as a regex and return matching nodes as a JSON array.

        Each result includes node_id, node_type, title, and up to 5 matching lines.
        Returns an error string for invalid regex patterns.
        """
        graph = self._graph
        if graph is None:
            return "ERROR: Graph not available"

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return f"ERROR: Invalid regex '{pattern}': {exc}"

        nodes = graph.all_nodes()
        if node_type:
            nodes = [n for n in nodes if n.node_type.upper() == node_type.upper()]

        results: list[dict[str, Any]] = []
        for node in nodes:
            matches = _find_matches(regex, node, field)
            if matches:
                results.append({
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "title": node.title,
                    "matches": matches[:5],  # cap context lines per node
                })
                if len(results) >= max_results:
                    break

        if not results:
            return f"No matches found for pattern '{pattern}'"
        return json.dumps(results, indent=2)


def _find_matches(regex: re.Pattern[str], node: Any, field: str) -> list[str]:
    """Return matching lines from the requested field(s).

    ``node`` is duck-typed: anything exposing ``title`` and ``content``.
    """
    lines: list[str] = []
    if field in ("title", "both") and node.title and regex.search(node.title):
        lines.append(f"[title] {node.title}")
    if field in ("content", "both") and node.content:
        for line in node.content.splitlines():
            if regex.search(line):
                lines.append(line.strip())
    return lines
