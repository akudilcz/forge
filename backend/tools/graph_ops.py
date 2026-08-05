"""graph_ops — focused single-operation tools for Project Graph mutations.

Each tool exposes only the parameters relevant to its operation, making the
API self-documenting for LLM agents.  All tools delegate to shared async
helpers in graph_write for the actual graph mutations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field

from backend.tools.async_utils import run_async
from backend.tools.base import ForgeTool

if TYPE_CHECKING:
    from backend.tools.graph_write import GraphWriteTool

# ── Shared base ──────────────────────────────────────────────────────────────

class _GraphMutationTool(ForgeTool):
    """Base for single-operation graph tools; injects graph + error handling."""

    _graph: object = None

    def __init__(self, graph: object = None) -> None:
        super().__init__()
        self._graph = graph

    def _execute(self, **kwargs: Any) -> str:
        if self._graph is None:
            return "ERROR: Graph not available"
        try:
            result: str = run_async(self._run_op(self._graph, **kwargs), timeout=30)
            return result
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    async def _run_op(self, graph: object, **kwargs: Any) -> str:
        raise NotImplementedError

    def _as_write_tool(self) -> GraphWriteTool:
        """Return ``self`` typed as the tool whose ``_op_*`` helpers it borrows.

        The mutation logic lives on ``GraphWriteTool`` as unbound coroutines;
        these single-operation tools call them directly rather than duplicating
        it.  The cast records that deliberate duck-typing — no relationship
        between the two classes is implied or created.
        """
        return cast("GraphWriteTool", self)


# ── Add Node ─────────────────────────────────────────────────────────────────

class _AddNodeArgs(BaseModel):
    node_type: str = Field(description="Node type (e.g. HLR, LLR, MODULE, DESIGN).")
    parent_id: str = Field(default="", description="Parent node ID for structural containment.")
    node_id: str = Field(default="", description="Explicit node ID. Auto-allocated if omitted.")
    title: str = Field(default="", description="Short human-readable title (3-5 words).")
    content: str = Field(default="", description="Node body content.")
    para_type: str = Field(default="", description="PARA sub-type: 'heading' or 'paragraph'.")
    properties: str = Field(default="{}", description="JSON object of extra properties.")
    trace_to: str = Field(default="[]", description="JSON array of node IDs to trace to.")
    lifecycle: str = Field(default="", description="Initial lifecycle: draft|stale|locked (default: draft).")
    reason: str = Field(default="", description="Justification for creating this node.")


class GraphAddNodeTool(_GraphMutationTool):
    """Create a new node in the Project Graph."""

    name: str = "graph_add_node"
    description: str = (
        "Create a new node in the Project Graph. Specify node_type and parent_id "
        "at minimum. The node_id is auto-allocated if omitted."
    )
    args_schema: type[BaseModel] = _AddNodeArgs

    async def _run_op(self, graph: object, **kw: Any) -> str:
        from backend.tools.graph_write import GraphWriteTool
        return await GraphWriteTool._op_add_node(self._as_write_tool(), graph, operation="add_node", **kw)


# ── Update Node ──────────────────────────────────────────────────────────────

class _UpdateNodeArgs(BaseModel):
    node_id: str = Field(description="ID of the node to update.")
    title: str = Field(default="", description="New title (leave empty to keep current).")
    content: str = Field(default="", description="New content (leave empty to keep current).")
    para_type: str = Field(default="", description="PARA sub-type: 'heading' or 'paragraph'.")
    properties: str = Field(default="", description="JSON object of properties to merge.")
    reason: str = Field(default="", description="Justification for the change.")


class GraphUpdateNodeTool(_GraphMutationTool):
    """Update an existing node's title, content, or properties."""

    name: str = "graph_update_node"
    description: str = (
        "Update an existing node's title, content, or properties. "
        "Only the fields you provide will be changed."
    )
    args_schema: type[BaseModel] = _UpdateNodeArgs

    async def _run_op(self, graph: object, **kw: Any) -> str:
        from backend.tools.graph_write import GraphWriteTool
        return await GraphWriteTool._op_update_node(self._as_write_tool(), graph, **kw)


# ── Delete Node ──────────────────────────────────────────────────────────────

class _DeleteNodeArgs(BaseModel):
    node_id: str = Field(description="ID of the node to delete.")
    reason: str = Field(default="", description="Justification for deletion.")


class GraphDeleteNodeTool(_GraphMutationTool):
    """Delete a node and all its edges from the Project Graph."""

    name: str = "graph_delete_node"
    description: str = "Delete a node and all its edges from the Project Graph."
    args_schema: type[BaseModel] = _DeleteNodeArgs

    async def _run_op(self, graph: object, **kw: Any) -> str:
        from backend.tools.graph_write import GraphWriteTool
        return await GraphWriteTool._op_delete_node(self._as_write_tool(), graph, **kw)


# ── Reparent Node ────────────────────────────────────────────────────────────

class _ReparentNodeArgs(BaseModel):
    node_id: str = Field(description="ID of the node to move.")
    parent_id: str = Field(description="New parent node ID.")
    reason: str = Field(default="", description="Justification for the move.")


class GraphReparentNodeTool(_GraphMutationTool):
    """Move a node to a different parent in the Project Graph hierarchy."""

    name: str = "graph_reparent_node"
    description: str = "Move a node to a different parent in the containment hierarchy."
    args_schema: type[BaseModel] = _ReparentNodeArgs

    async def _run_op(self, graph: object, **kw: Any) -> str:
        from backend.tools.graph_write import GraphWriteTool
        return await GraphWriteTool._op_reparent_node(self._as_write_tool(), graph, **kw)


# ── Update Trace (full replace) ──────────────────────────────────────────────

class _UpdateTraceArgs(BaseModel):
    node_id: str = Field(description="ID of the node whose trace_to list to replace.")
    trace_to: str = Field(description="JSON array of node IDs — replaces the entire trace_to list.")
    reason: str = Field(default="", description="Justification for the change.")


class GraphUpdateTraceTool(_GraphMutationTool):
    """Replace a node's entire trace_to list. Use add_traces/remove_traces for incremental changes."""

    name: str = "graph_update_trace"
    description: str = (
        "Replace a node's entire trace_to list with the provided IDs. "
        "Prefer graph_add_traces or graph_remove_traces for incremental changes."
    )
    args_schema: type[BaseModel] = _UpdateTraceArgs

    async def _run_op(self, graph: object, **kw: Any) -> str:
        from backend.tools.graph_write import GraphWriteTool
        return await GraphWriteTool._op_update_trace(self._as_write_tool(), graph, **kw)


# ── Add Traces ───────────────────────────────────────────────────────────────

class _AddTracesArgs(BaseModel):
    node_id: str = Field(description="ID of the node to add traces to.")
    trace_to: str = Field(description="JSON array of node IDs to append to trace_to.")
    reason: str = Field(default="", description="Justification for the change.")


class GraphAddTracesTool(_GraphMutationTool):
    """Append traceability links to a node's trace_to list (idempotent — skips duplicates)."""

    name: str = "graph_add_traces"
    description: str = (
        "Append node IDs to a node's trace_to list. "
        "Duplicates are silently skipped."
    )
    args_schema: type[BaseModel] = _AddTracesArgs

    async def _run_op(self, graph: object, **kw: Any) -> str:
        from backend.tools.graph_write import GraphWriteTool
        return await GraphWriteTool._op_add_traces(self._as_write_tool(), graph, **kw)


# ── Remove Traces ────────────────────────────────────────────────────────────

class _RemoveTracesArgs(BaseModel):
    node_id: str = Field(description="ID of the node to remove traces from.")
    trace_to: str = Field(description="JSON array of node IDs to remove from trace_to.")
    reason: str = Field(default="", description="Justification for the change.")


class GraphRemoveTracesTool(_GraphMutationTool):
    """Remove specific traceability links from a node's trace_to list."""

    name: str = "graph_remove_traces"
    description: str = "Remove specific node IDs from a node's trace_to list."
    args_schema: type[BaseModel] = _RemoveTracesArgs

    async def _run_op(self, graph: object, **kw: Any) -> str:
        from backend.tools.graph_write import GraphWriteTool
        return await GraphWriteTool._op_remove_traces(self._as_write_tool(), graph, **kw)


# ── Refresh Provenance ───────────────────────────────────────────────────────

class _RefreshProvenanceArgs(BaseModel):
    node_id: str = Field(description="ID of the reviewed node to re-stamp.")
    reason: str = Field(default="", description="Justification for the re-stamp.")


class GraphRefreshProvenanceTool(_GraphMutationTool):
    """Mark a STALE_NODE as reviewed-and-still-valid by re-stamping its provenance."""

    name: str = "graph_refresh_provenance"
    description: str = (
        "Resolve a STALE_NODE verdict of 'reviewed, no change needed': "
        "re-stamps the node's derived_from_hash against the parent's current "
        "content WITHOUT touching the node's own content. Use graph_update_node "
        "instead when the content actually needs re-deriving."
    )
    args_schema: type[BaseModel] = _RefreshProvenanceArgs

    async def _run_op(self, graph: object, **kw: Any) -> str:
        from backend.tools.graph_write import GraphWriteTool
        return await GraphWriteTool._op_refresh_provenance(
            self._as_write_tool(), graph, **kw
        )


# ── Add Edge ─────────────────────────────────────────────────────────────────

class _AddEdgeArgs(BaseModel):
    edge_type: str = Field(description="Edge type (e.g. DERIVES_FROM, IMPLEMENTS).")
    source_id: str = Field(description="Source node ID.")
    target_id: str = Field(description="Target node ID.")
    reason: str = Field(default="", description="Rationale for the edge.")


class GraphAddEdgeTool(_GraphMutationTool):
    """Create a typed edge between two nodes in the Project Graph."""

    name: str = "graph_add_edge"
    description: str = "Create a typed edge between two nodes in the Project Graph."
    args_schema: type[BaseModel] = _AddEdgeArgs

    async def _run_op(self, graph: object, **kw: Any) -> str:
        from backend.tools.graph_write import GraphWriteTool
        return await GraphWriteTool._op_add_edge(self._as_write_tool(), graph, **kw)


# ── Remove Edge ──────────────────────────────────────────────────────────────

class _RemoveEdgeArgs(BaseModel):
    edge_id: str = Field(description="ID of the edge to remove.")
    reason: str = Field(default="", description="Justification for removal.")


class GraphRemoveEdgeTool(_GraphMutationTool):
    """Remove an edge from the Project Graph by its edge ID."""

    name: str = "graph_remove_edge"
    description: str = "Remove an edge from the Project Graph by its edge ID."
    args_schema: type[BaseModel] = _RemoveEdgeArgs

    async def _run_op(self, graph: object, **kw: Any) -> str:
        from backend.tools.graph_write import GraphWriteTool
        return await GraphWriteTool._op_remove_edge(self._as_write_tool(), graph, **kw)
