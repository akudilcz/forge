"""Shared conversion helpers for the ProjectGraph subsystem."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from backend.graph.models import (
    GraphEdge,
    GraphNode,
    LifecycleState,
)


def row_to_node(row: dict[str, Any]) -> GraphNode:
    """Convert a pg_nodes database row to a GraphNode model."""
    raw_trace = row.get("trace_to", "[]")
    trace_to = json.loads(raw_trace) if isinstance(raw_trace, str) else (raw_trace or [])
    return GraphNode(
        node_id=row["node_id"],
        node_type=row["node_type"],
        layer=row.get("layer", 0),
        title=row["title"],
        content=row.get("content", ""),
        content_hash=row.get("content_hash", ""),
        version=row.get("version", 1),
        parent_id=row.get("parent_id"),
        trace_to=trace_to,
        lifecycle=LifecycleState(row.get("lifecycle", "draft")),
        created_by=row.get("created_by", "system"),
        created_at=datetime.fromisoformat(row["created_at"])
        if row.get("created_at")
        else datetime.now(UTC),
        updated_at=datetime.fromisoformat(row["updated_at"])
        if row.get("updated_at")
        else datetime.now(UTC),
        properties=json.loads(row.get("properties") or "{}") or {},
    )


def row_to_edge(row: dict[str, Any]) -> GraphEdge:
    """Convert a pg_edges database row to a GraphEdge model."""
    return GraphEdge(
        edge_id=row["edge_id"],
        edge_type=row["edge_type"],
        source_id=row["source_id"],
        target_id=row["target_id"],
        rationale=row.get("rationale"),
        confidence=row.get("confidence", 1.0),
        created_by=row.get("created_by", "system"),
        created_at=datetime.fromisoformat(row["created_at"])
        if row.get("created_at")
        else datetime.now(UTC),
    )


def nx_data_to_node(node_id: str, data: dict[str, Any]) -> GraphNode:
    """Convert a NetworkX node's data dict to a GraphNode model.

    Eliminates duplication between node_sync() and all_nodes().
    """
    updated_at_raw = data.get("updated_at", "")
    created_at_raw = data.get("created_at", "")
    return GraphNode(
        node_id=node_id,
        node_type=data["node_type"],
        layer=data["layer"],
        title=data["title"],
        lifecycle=LifecycleState(data["lifecycle"]),
        content=data.get("content", ""),
        content_hash=data.get("content_hash", ""),
        version=data.get("version", 1),
        parent_id=data.get("parent_id"),
        trace_to=data.get("trace_to", []),
        created_by=data.get("created_by", "system"),
        created_at=datetime.fromisoformat(created_at_raw) if created_at_raw else datetime.now(UTC),
        updated_at=datetime.fromisoformat(updated_at_raw) if updated_at_raw else datetime.now(UTC),
        properties=data.get("properties", {}),
    )
