"""multi_graph_write — batch graph mutations in a single tool call.

Accepts a JSON array of operations and executes them sequentially by
delegating to GraphWriteTool's dispatch logic.  Useful for agents that
need to create many nodes and edges at once.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.async_utils import run_async
from backend.tools.base import ForgeTool


class _Args(BaseModel):
    operations: str = Field(
        description=(
            "JSON array of operation objects. Each must have an 'operation' field "
            "(add_node, update_node, update_trace, add_traces, remove_traces, "
            "reparent_node, add_edge, remove_edge, delete_node) plus the fields "
            "required by that operation."
        )
    )


_NAME = "multi_graph_write"
_DESCRIPTION = (
    "Execute multiple graph operations in a single call. "
    "Pass a JSON array of operation objects. "
    "Use this when creating many nodes and edges to avoid repeated tool calls."
)


class MultiGraphWriteTool(ForgeTool):
    """Batch graph mutation tool — executes multiple operations in a single call.

    Delegates each operation to GraphWriteTool so logic is never duplicated.
    """

    name: str = _NAME
    description: str = _DESCRIPTION
    args_schema: type[BaseModel] = _Args

    _graph: object = None

    def __init__(self, graph: object) -> None:
        # name/description are also passed here because BaseTool declares them
        # as required fields; the class-level defaults alone do not satisfy it.
        super().__init__(name=_NAME, description=_DESCRIPTION)
        object.__setattr__(self, "_graph", graph)

    def _execute(self, operations: str = "[]") -> str:  # type: ignore[override]
        graph = self._graph
        if graph is None:
            return "ERROR: Graph not available"
        try:
            ops = json.loads(operations)
            if not isinstance(ops, list):
                return "ERROR: 'operations' must be a JSON array"
        except json.JSONDecodeError as exc:
            return f"ERROR: Invalid JSON in 'operations': {exc}"

        try:
            # run_async is untyped (returns Any); _run_all's return type is str.
            summary: str = run_async(self._run_all(graph, ops), timeout=120)
            return summary
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    async def _run_all(self, graph: object, ops: list[dict[str, Any]]) -> str:
        from backend.tools.graph_write import GraphWriteTool

        # Whole-batch invariant pre-validation (multi_file_write precedent):
        # any violating operation rejects the batch atomically, so a bad op
        # can never leave the graph half-written (design/01 §3.6).
        rejections = _prevalidate_batch(graph, ops)
        if rejections:
            return "\n".join(
                [f"0/{len(ops)} operations succeeded.", "Errors:", *rejections]
            )

        delegate = GraphWriteTool(graph)
        results: list[str] = []
        errors: list[str] = []

        for i, op_dict in enumerate(ops):
            try:
                result = await delegate._dispatch(graph, **op_dict)
                if result.startswith("ERROR"):
                    errors.append(f"[{i}] {result}")
                else:
                    results.append(f"[{i}] {result}")
            except Exception as exc:  # noqa: BLE001
                op = op_dict.get("operation", "?")
                errors.append(f"[{i}] {op} failed: {exc}")

        summary_parts = [f"{len(results)}/{len(ops)} operations succeeded."]
        if results:
            summary_parts.append("Results:\n" + "\n".join(results))
        if errors:
            summary_parts.append("Errors:\n" + "\n".join(errors))
        return "\n".join(summary_parts)


def _prevalidate_batch(graph: object, ops: list[dict[str, Any]]) -> list[str]:
    """Dry-run authoring-invariant validation over a whole batch.

    Each op is checked against the live graph AND against nodes pending
    earlier in the same batch (two ops adding the same sibling title must
    fail here — the live graph alone cannot see the first one yet).
    Returns per-op error lines in the existing summary format; empty list
    means the batch may execute.
    """
    from backend.analysis.node_invariants import (
        check_sibling_content_unique,
        check_sibling_title_unique,
    )
    from backend.graph.models import GraphNode
    from backend.tools.graph_write_parsing import _parse_json_obj, _parse_trace_to
    from backend.tools.graph_write_validation import (
        validate_add_node,
        validate_trace_update,
        validate_update_node,
    )

    errors: list[str] = []
    pending: list[GraphNode] = []
    for i, op_dict in enumerate(ops):
        op = str(op_dict.get("operation", "")).strip().lower()
        err: str | None = None
        if op == "add_node":
            node_type = str(op_dict.get("node_type", "")).upper()
            node_id = str(op_dict.get("node_id", "")).strip() or f"PENDING-{i}"
            parent_id = str(op_dict.get("parent_id", "")).strip()
            title = str(op_dict.get("title", ""))
            content = str(op_dict.get("content", ""))
            props = _parse_json_obj(str(op_dict.get("properties", "{}")))
            trace_to = _parse_trace_to(dict(op_dict), props)
            err = validate_add_node(
                graph, node_type, node_id, parent_id, title, content, trace_to,
                props,
            )
            if err is None and parent_id:
                in_batch = [p for p in pending if p.parent_id == parent_id]
                for msg in (
                    check_sibling_title_unique(node_type, title, node_id, in_batch),
                    check_sibling_content_unique(node_type, content, node_id, in_batch),
                ):
                    if msg is not None:
                        err = f"ERROR: {msg} (conflicts within this batch)"
                        break
            if err is None:
                pending.append(
                    GraphNode(
                        node_id=node_id, node_type=node_type,
                        parent_id=parent_id or None, title=title, content=content,
                    )
                )
        elif op in ("update_node", "update_trace", "add_traces"):
            node_id = str(op_dict.get("node_id", ""))
            try:
                existing = graph.node_sync(node_id)  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                existing = None
            if op == "update_node":
                new_title = str(op_dict.get("title", "")).strip() or None
                new_content = str(op_dict.get("content", "")) or None
                err = validate_update_node(graph, existing, new_title, new_content)
            elif existing is not None and isinstance(existing, GraphNode):
                try:
                    from backend.tools.graph_write_parsing import _coerce_to_list
                    new_refs = _coerce_to_list(op_dict.get("trace_to"))
                except ValueError:
                    new_refs = []  # the op handler reports the parse error
                if op == "add_traces":
                    current = list(existing.trace_to or [])
                    new_refs = current + [r for r in new_refs if r not in current]
                err = validate_trace_update(graph, existing, new_refs)
        if err is not None:
            errors.append(f"[{i}] {err}")
    return errors
