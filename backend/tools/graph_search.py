"""graph_search — fuzzy (edit-distance) search across graph node titles/content."""

from __future__ import annotations

import difflib
import json
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool

if TYPE_CHECKING:
    from backend.graph.models import GraphNode


class _SearchableGraph(Protocol):
    """The slice of the Project Graph API that graph_search depends on."""

    def all_nodes(self) -> list[GraphNode]:
        """Return every node currently in the graph."""
        ...


class _Args(BaseModel):
    query: str = Field(description="Search term to match against node text.")
    field: str = Field(
        default="both",
        description="Which field to search: 'title', 'content', or 'both'.",
    )
    node_type: str = Field(default="", description="Optional node type filter (e.g. 'HLR').")
    max_results: int = Field(default=10, description="Maximum matches to return.")
    threshold: float = Field(
        default=0.3,
        description="Minimum similarity score (0.0–1.0) to include a result.",
    )


_NAME = "graph_search"
_DESCRIPTION = (
    "Fuzzy search across graph node titles and content using edit distance. "
    "Returns closest matches ranked by similarity score."
)


class GraphSearchTool(ForgeTool):
    """Fuzzy (edit-distance) search across graph node titles and/or content.

    Uses Python's SequenceMatcher; content matching uses the best per-line score
    and short-circuits to 0.95 on exact substring hits.
    """

    name: str = _NAME
    description: str = _DESCRIPTION
    args_schema: type[BaseModel] = _Args

    def __init__(self, graph: _SearchableGraph | None = None) -> None:
        """Args:
            graph: ProjectGraph instance to search (injected by the tool factory).
        """
        # name/description are also passed here because BaseTool declares them
        # as required fields; the class-level defaults alone do not satisfy it.
        super().__init__(name=_NAME, description=_DESCRIPTION)
        self._graph = graph

    def _execute(  # type: ignore[override]
        self,
        query: str,
        field: str = "both",
        node_type: str = "",
        max_results: int = 10,
        threshold: float = 0.3,
    ) -> str:
        """Score all nodes against query and return top matches as a JSON array.

        Returns a no-match message string when no node meets the threshold.
        """
        graph = self._graph
        if graph is None:
            return "ERROR: Graph not available"

        nodes = graph.all_nodes()
        if node_type:
            nodes = [n for n in nodes if n.node_type.upper() == node_type.upper()]

        scored: list[tuple[float, str, GraphNode]] = []
        query_lower = query.lower()

        for node in nodes:
            best_score = 0.0
            matched_field = ""
            if field in ("title", "both") and node.title:
                score = _similarity(query_lower, node.title.lower())
                if score > best_score:
                    best_score, matched_field = score, "title"
            if field in ("content", "both") and node.content:
                score = _content_similarity(query_lower, node.content.lower())
                if score > best_score:
                    best_score, matched_field = score, "content"
            if best_score >= threshold:
                scored.append((best_score, matched_field, node))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [
            {
                "node_id": n.node_id,
                "node_type": n.node_type,
                "title": n.title,
                "score": round(score, 3),
                "matched_field": mf,
            }
            for score, mf, n in scored[:max_results]
        ]
        if not results:
            return f"No matches found for '{query}' (threshold={threshold})"
        return json.dumps(results, indent=2)


def _similarity(query: str, text: str) -> float:
    """SequenceMatcher ratio between query and text."""
    return difflib.SequenceMatcher(None, query, text).ratio()


def _content_similarity(query: str, content: str) -> float:
    """Best line-level similarity within content."""
    best = 0.0
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Exact substring match → high score
        if query in stripped:
            return 0.95
        score = difflib.SequenceMatcher(None, query, stripped).ratio()
        if score > best:
            best = score
    return best
