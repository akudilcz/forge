"""graph_stats — summary statistics for the project graph."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool

_NAME = "graph_stats"
_DESCRIPTION = (
    "Returns summary statistics for the project graph: node counts by type "
    "and lifecycle state, trace coverage, and optionally a gap summary."
)


class _Args(BaseModel):
    include_gaps: bool = Field(
        default=True,
        description="Include gap analysis summary (slightly slower).",
    )


class GraphStatsTool(ForgeTool):
    """Compute and return summary statistics for the project graph.

    Reports node counts by type and lifecycle, trace coverage for key pairs,
    and an optional gap summary from the GapAnalyser.
    """

    name: str = _NAME
    description: str = _DESCRIPTION
    args_schema: type[BaseModel] = _Args

    def __init__(self, graph: Any = None, analyser: Any = None) -> None:
        """Args:
            graph: ProjectGraph instance to inspect.
            analyser: Optional GapAnalyser instance; gap_summary is omitted when None.
        """
        super().__init__(name=_NAME, description=_DESCRIPTION)
        self._graph = graph
        self._analyser = analyser

    def _execute(self, *args: Any, **kwargs: Any) -> str:
        """Dispatch entry point — forwards schema-validated args to :meth:`_stats`."""
        return self._stats(*args, **kwargs)

    def _stats(self, include_gaps: bool = True) -> str:
        """Compute graph statistics and return them as a JSON object string."""
        graph = self._graph
        if graph is None:
            return "ERROR: Graph not available"

        nodes = graph.all_nodes()
        type_counts: Counter[str] = Counter()
        lifecycle_counts: Counter[str] = Counter()
        for n in nodes:
            type_counts[n.node_type] += 1
            lifecycle_counts[n.lifecycle.value if hasattr(n.lifecycle, "value") else str(n.lifecycle)] += 1

        result: dict[str, Any] = {
            "total_nodes": len(nodes),
            "by_type": dict(type_counts.most_common()),
            "by_lifecycle": dict(lifecycle_counts.most_common()),
            "trace_coverage": _trace_coverage(graph, nodes),
        }

        if include_gaps and self._analyser is not None:
            try:
                gaps = self._analyser.analyse(graph)
                gap_counts: Counter[str] = Counter()
                for g in gaps:
                    gap_counts[g.type.value] += 1
                result["gap_summary"] = dict(gap_counts.most_common())
                result["total_gaps"] = len(gaps)
            except Exception as exc:  # noqa: BLE001
                result["gap_summary"] = f"ERROR: {exc}"

        return json.dumps(result, indent=2)


def _trace_coverage(graph: Any, nodes: list[Any]) -> dict[str, str]:
    """Compute trace coverage for key trace pairs."""
    coverage: dict[str, str] = {}
    _trace_pairs = [
        ("HLR", "MODULE", "HLR traced by MODULE"),
        ("LLR", "DESIGN", "LLR traced by DESIGN"),
        ("HLR", "CASE_HLR", "HLR traced by CASE_HLR"),
    ]
    for target_type, source_type, label in _trace_pairs:
        targets = [n for n in nodes if n.node_type == target_type]
        if not targets:
            continue
        traced = sum(
            1 for t in targets
            if graph.any_trace_to(t.node_id, source_type)
        )
        coverage[label] = f"{traced}/{len(targets)}"
    return coverage
