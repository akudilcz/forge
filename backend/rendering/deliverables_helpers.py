"""Helper functions for deliverables pack rendering."""

from __future__ import annotations

from typing import Any


def write_file(path: Any, content: str) -> None:
    """Write UTF-8 content to a file."""
    path.write_text(content, encoding="utf-8")


def nodes_by_type(graph: Any, *types: str) -> list[Any]:
    """Return nodes matching any of the given types, sorted by node_id."""
    type_set = set(types)
    return sorted(
        (n for n in graph.all_nodes() if n.node_type in type_set),
        key=lambda n: n.node_id,
    )


def node_lookup(graph: Any) -> dict[str, Any]:
    """Build a node_id → node dict for fast lookups."""
    return {n.node_id: n for n in graph.all_nodes()}


def build_trace_map(nodes: list[Any]) -> dict[str, list[str]]:
    """Build a map from traced target → list of source node_ids."""
    result: dict[str, list[str]] = {}
    for n in nodes:
        for ref in (n.trace_to or []):
            result.setdefault(ref, []).append(n.node_id)
    return result


def pct(num: int, denom: int) -> str:
    """Format a percentage, handling zero denominator."""
    if denom == 0:
        return "—"
    return f"{num * 100 // denom}%"


def req_section(
    node: Any, lookup: dict[str, Any], heading: str = "###",
) -> list[str]:
    """Render a single requirement node section."""
    lines = [f"{heading} {node.node_id}: {node.title or '(untitled)'}", ""]
    if node.content:
        lines += [node.content.strip(), ""]
    return lines
