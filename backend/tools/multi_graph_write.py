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
