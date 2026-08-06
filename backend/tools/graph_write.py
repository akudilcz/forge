"""graph_write — add/update nodes and edges in the Project Graph."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from backend.analysis.gap_analyser import VALID_PARENT_TYPES
from backend.tools.async_utils import run_async
from backend.tools.base import ForgeTool
from backend.tools.graph_write_parsing import (
    _coerce_to_list,
    _parse_json_obj,
    _parse_trace_to,
    _TraceToCoerceError,
)
from backend.tools.graph_write_validation import (
    validate_add_node,
    validate_trace_update,
    validate_update_node,
)


async def check_orphan_guard(graph: object, node_id: str, child_node: Any) -> str | None:
    """Error string if moving ``node_id`` leaves its parent with no same-type child."""
    siblings = graph.children_sync(child_node.parent_id)  # type: ignore[attr-defined]
    same_type_siblings = [
        s for s in siblings
        if s.node_type == child_node.node_type and s.node_id != node_id
    ]
    if not same_type_siblings:
        return (
            f"ERROR: Cannot move {node_id} — it is the only "
            f"{child_node.node_type} under {child_node.parent_id}. "
            f"Moving it would leave the parent uncovered. "
            f"Create a new node instead."
        )
    return None


async def reparent_node_op(graph: object, **kwargs: Any) -> str:
    """Reparent a node after checking type compatibility and the orphan guard.

    Shared by ``GraphWriteTool`` and ``GraphReparentNodeTool`` so both enforce
    the same rules.
    """
    node_id = kwargs.get("node_id", "")
    new_parent_id = (kwargs.get("parent_id") or "").strip() or None

    # Validation is skipped only when this graph genuinely cannot answer the
    # query: no node() attribute (AttributeError) or a non-awaitable one
    # (TypeError, which is what a bare MagicMock in a test produces).
    #
    # The handler is deliberately narrow. Catching every exception is what hid
    # the AttributeError described above for so long, and it would equally hide
    # a real fault — a database error must surface, not silently downgrade the
    # call into an unvalidated mutation.
    try:
        child_node = await graph.node(node_id)  # type: ignore[attr-defined]
        parent_node = (
            await graph.node(new_parent_id)  # type: ignore[attr-defined]
            if new_parent_id
            else None
        )
        can_validate = True
    except (AttributeError, TypeError):
        child_node = parent_node = None
        can_validate = False

    if can_validate and child_node:
        if parent_node:
            child_type = child_node.node_type
            parent_type = parent_node.node_type
            allowed = VALID_PARENT_TYPES.get(child_type)
            if allowed and parent_type not in allowed:
                return (
                    f"ERROR: Cannot reparent {child_type} {node_id} under "
                    f"{parent_type} {new_parent_id}. "
                    f"{child_type} nodes can only be children of: "
                    f"{', '.join(sorted(allowed))}"
                )
        if child_node.parent_id:
            try:
                orphan_err = await check_orphan_guard(graph, node_id, child_node)
            except AttributeError:
                orphan_err = None
            if orphan_err:
                return orphan_err

    await graph.reparent_node(  # type: ignore[attr-defined]
        node_id, new_parent_id, "agent", kwargs.get("reason", "reparent"),
    )
    return f"OK: moved {node_id} to parent {new_parent_id}"


class _Args(BaseModel):
    operation: str = Field(
        description=(
            "Operation: add_node | update_node | update_trace | reparent_node | add_edge | remove_edge | delete_node"
        )
    )
    node_id: str = Field(default="", description="Node ID (required for most operations).")
    node_type: str = Field(default="", description="Node type (required for add_node).")
    parent_id: str = Field(
        default="", description="Parent node ID for structural containment (required for add_node)."
    )
    title: str = Field(
        default="", description="Short human-readable title: 3-5 words (add_node / update_node)."
    )
    content: str = Field(default="", description="Node content (add_node / update_node).")
    para_type: str = Field(default="", description="PARA sub-type discriminator ('heading' or 'paragraph').")
    properties: str = Field(default="{}", description="JSON object of extra properties.")
    trace_to: str = Field(
        default="[]",
        description="JSON array of node IDs to trace to (update_trace). Targets must be shallower.",
    )
    edge_type: str = Field(default="", description="Edge type (add_edge).")
    source_id: str = Field(default="", description="Source node ID (add_edge).")
    target_id: str = Field(default="", description="Target node ID (add_edge).")
    edge_id: str = Field(default="", description="Edge ID (remove_edge).")
    lifecycle: str = Field(
        default="",
        description="Initial lifecycle state for add_node (draft|stale|locked). Omit to use draft (default).",
    )
    reason: str = Field(default="", description="Justification for the change.")


class GraphWriteTool(ForgeTool):
    """Mutation tool for the Project Graph supporting node and edge CRUD plus trace management.

    All write operations are async internally; sync callers are handled via a
    thread-pool executor.  Phase-creation constraints are enforced before any
    node is persisted.
    """

    name: str = "graph_write"
    description: str = (
        "Add or update nodes and edges in the Project Graph. "
        "EVERY task that creates an artefact must call this to record traceability edges. "
        "Operations: add_node, update_node, update_trace, add_traces, remove_traces, "
        "reparent_node, add_edge, remove_edge, delete_node, refresh_provenance. "
        "refresh_provenance marks a STALE_NODE as reviewed-and-still-valid by "
        "re-stamping its provenance against the current parent content. "
        "Prefer add_traces / remove_traces over update_trace: "
        "add_traces appends IDs to the existing trace_to; "
        "remove_traces deletes specific IDs from it; "
        "update_trace replaces the whole list (use only when a full reset is intentional)."
    )
    args_schema: type[BaseModel] = _Args

    _DISPATCH: dict[str, str] = {
        "add_node": "_op_add_node",
        "update_node": "_op_update_node",
        "update_trace": "_op_update_trace",
        "add_traces": "_op_add_traces",
        "remove_traces": "_op_remove_traces",
        "reparent_node": "_op_reparent_node",
        "add_edge": "_op_add_edge",
        "remove_edge": "_op_remove_edge",
        "delete_node": "_op_delete_node",
        "refresh_provenance": "_op_refresh_provenance",
    }

    def __init__(self, graph: object = None) -> None:
        """Args:
            graph: ProjectGraph instance to mutate (injected by the tool factory).
        """
        super().__init__()
        self._graph = graph

    def _execute(self, **kwargs: Any) -> str:
        """Dispatch the write operation and return a status string.

        Handles async execution from both sync and async call contexts.
        """
        graph = self._graph
        if graph is None:
            return "ERROR: Graph not available"
        try:
            coro = self._dispatch(graph, **kwargs)
            return run_async(coro, timeout=30)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    async def _dispatch(self, graph: object, **kwargs: Any) -> str:
        """Route the operation kwarg to the matching graph mutation and return a status string."""
        op = kwargs.get("operation", "").strip().lower()

        method_name = self._DISPATCH.get(op)
        if method_name is None:
            return (
                f"Unknown operation '{op}'. "
                "Valid: add_node, update_node, update_trace, add_traces, remove_traces, "
                "reparent_node, add_edge, remove_edge, delete_node, refresh_provenance"
            )

        method = getattr(self, method_name)
        return await method(graph, **kwargs)

    # ------------------------------------------------------------------
    # Individual operation handlers
    # ------------------------------------------------------------------

    async def _op_add_node(self, graph: object, **kwargs: Any) -> str:
        from backend.graph.models import GraphNode, LifecycleState
        from backend.pipeline.phase_constraints import check_create_allowed
        from backend.quality.module_validators import check_design_count_allowed

        node_type_req = kwargs.get("node_type", "")
        constraint_err = check_create_allowed(node_type_req)
        if constraint_err:
            return constraint_err

        # Enforce #DESIGN children ≤ #classes in the owning MODULE's class plan.
        if node_type_req.upper() == "DESIGN":
            parent = (kwargs.get("parent_id") or "").strip()
            if parent:
                limit_err = check_design_count_allowed(graph, parent)
                if limit_err:
                    return limit_err

        props = _parse_json_obj(kwargs.get("properties", "{}"))

        lifecycle_raw = kwargs.get("lifecycle", "").strip().lower()
        lifecycle_val = LifecycleState(lifecycle_raw) if lifecycle_raw else LifecycleState.DRAFT

        parent_id_raw = (kwargs.get("parent_id") or "").strip() or None
        node_id = (kwargs.get("node_id") or "").strip()
        if not node_id:
            node_type_val = (kwargs.get("node_type") or "NODE").upper()
            node_id = await graph.allocate_node_id(node_type_val)  # type: ignore[attr-defined]
        para_type = (kwargs.get("para_type") or "").strip()

        trace_targets = _parse_trace_to(kwargs, props)

        # Write-time invariant enforcement (specs/12 §3.6): reject writes
        # the Gap Analyser would flag anyway, so the agent fixes them in
        # the same turn instead of a later paid repair dispatch.
        invariant_err = validate_add_node(
            graph,
            node_type_req.upper(),
            node_id,
            parent_id_raw or "",
            kwargs.get("title", ""),
            kwargs.get("content", ""),
            trace_targets,
            props,
        )
        if invariant_err:
            return invariant_err

        node = GraphNode(
            node_id=node_id,
            node_type=kwargs.get("node_type", ""),
            parent_id=parent_id_raw,
            title=kwargs.get("title", ""),
            content=kwargs.get("content", ""),
            para_type=para_type,
            trace_to=trace_targets,
            properties=props,
            lifecycle=lifecycle_val,
            created_by="agent",
        )
        node = await graph.add_node(node)  # type: ignore[attr-defined]
        return f"OK: added node {node.node_id}"

    async def _op_update_node(self, graph: object, **kwargs: Any) -> str:
        node_id = kwargs.get("node_id", "")
        props_raw = (kwargs.get("properties") or "").strip()
        props: dict | None = None
        if props_raw:
            try:
                props = json.loads(props_raw)
            except json.JSONDecodeError:
                props = None

        label_raw = (kwargs.get("title") or "").strip()
        title: str | None = label_raw if label_raw else None
        content_raw = kwargs.get("content")
        content: str | None = content_raw if content_raw else None

        para_type_raw = (kwargs.get("para_type") or "").strip()
        if para_type_raw:
            props = props or {}
            props["sub_type"] = para_type_raw

        # Write-time invariant enforcement (specs/12 §3.6) on the fields
        # this update actually changes.
        try:
            existing = graph.node_sync(node_id)  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            existing = None
        invariant_err = validate_update_node(graph, existing, title, content, props)
        if invariant_err:
            return invariant_err

        await graph.update_node(  # type: ignore[attr-defined]
            node_id, content, props, "agent", kwargs.get("reason", ""), title=title,
        )
        return f"OK: updated {node_id}"

    async def _op_update_trace(self, graph: object, **kwargs: Any) -> str:
        node_id = kwargs.get("node_id", "")
        try:
            trace_targets = _coerce_to_list(kwargs.get("trace_to"))
        except _TraceToCoerceError:
            return "ERROR: trace_to must be a JSON array of node ID strings"

        existing = graph.node_sync(node_id)  # type: ignore[attr-defined]
        if existing is None:
            return f"ERROR: Node not found: {node_id}"

        invariant_err = validate_trace_update(graph, existing, trace_targets)
        if invariant_err:
            return invariant_err

        await graph.update_node(  # type: ignore[attr-defined]
            node_id, None, None, "agent",
            kwargs.get("reason", "update_trace"),
            trace_to=trace_targets,
        )
        return f"OK: updated trace_to on {node_id}"

    async def _op_add_traces(self, graph: object, **kwargs: Any) -> str:
        node_id = kwargs.get("node_id", "")
        try:
            new_targets = _coerce_to_list(kwargs.get("trace_to"))
        except _TraceToCoerceError:
            return "ERROR: trace_to must be a JSON array of node ID strings"

        existing = graph.node_sync(node_id)  # type: ignore[attr-defined]
        if existing is None:
            return f"ERROR: Node not found: {node_id}"

        current: list[str] = list(existing.trace_to or [])
        added = [t for t in new_targets if t not in current]

        if not added:
            return f"OK: no new traces to add on {node_id} (already present)"

        invariant_err = validate_trace_update(graph, existing, current + added)
        if invariant_err:
            return invariant_err

        await graph.update_node(  # type: ignore[attr-defined]
            node_id, None, None, "agent",
            kwargs.get("reason", "add_traces"),
            trace_to=current + added,
        )
        return f"OK: added {added} to trace_to on {node_id}"

    async def _op_remove_traces(self, graph: object, **kwargs: Any) -> str:
        node_id = kwargs.get("node_id", "")
        try:
            to_remove = _coerce_to_list(kwargs.get("trace_to"))
        except _TraceToCoerceError:
            return "ERROR: trace_to must be a JSON array of node ID strings"

        existing = graph.node_sync(node_id)  # type: ignore[attr-defined]
        if existing is None:
            return f"ERROR: Node not found: {node_id}"

        remove_set = set(to_remove)
        current = list(existing.trace_to or [])
        existing_set = set(current)
        actually_removed = [t for t in to_remove if t in existing_set]

        if not actually_removed:
            not_found = [t for t in to_remove if t not in existing_set]
            if not_found:
                return f"OK: no matching traces to remove on {node_id} (requested {not_found} not present)"
            return f"OK: no matching traces to remove on {node_id}"

        new_trace = [t for t in current if t not in remove_set]
        await graph.update_node(  # type: ignore[attr-defined]
            node_id, None, None, "agent",
            kwargs.get("reason", "remove_traces"),
            trace_to=new_trace,
        )
        return f"OK: removed {actually_removed} from trace_to on {node_id}"

    async def _op_reparent_node(self, graph: object, **kwargs: Any) -> str:
        return await reparent_node_op(graph, **kwargs)

    async def _check_orphan_guard(
        self, graph: object, node_id: str, child_node: Any,
    ) -> str | None:
        """Backwards-compatible shim delegating to the module-level guard."""
        return await check_orphan_guard(graph, node_id, child_node)

    async def _op_add_edge(self, graph: object, **kwargs: Any) -> str:
        from backend.graph.models import GraphEdge

        edge = GraphEdge(
            edge_type=kwargs.get("edge_type", ""),
            source_id=kwargs.get("source_id", ""),
            target_id=kwargs.get("target_id", ""),
            created_by="agent",
            rationale=kwargs.get("reason", ""),
        )
        edge = await graph.add_edge(edge)  # type: ignore[attr-defined]
        return f"OK: added edge {edge.edge_id}"

    async def _op_remove_edge(self, graph: object, **kwargs: Any) -> str:
        edge_id = kwargs.get("edge_id", "")
        if not edge_id:
            return "ERROR: edge_id is required for remove_edge"
        await graph.remove_edge(edge_id, kwargs.get("reason", ""))  # type: ignore[attr-defined]
        return f"OK: removed edge {edge_id}"

    async def _op_delete_node(self, graph: object, **kwargs: Any) -> str:
        node_id = kwargs.get("node_id", "")
        await graph.delete_node(node_id)  # type: ignore[attr-defined]
        return f"OK: deleted {node_id}"

    async def _op_refresh_provenance(self, graph: object, **kwargs: Any) -> str:
        """Deterministic STALE_NODE closure for 'reviewed, no change needed'.

        Re-stamps ``properties.derived_from_hash`` from the LIVE parent
        content without touching the node's own content — a free
        alternative to a paid no-op content rewrite (specs/12 §2.6).
        """
        from backend.graph.provenance import (  # noqa: PLC0415
            DERIVED_FROM_HASH,
            provenance_hash,
        )

        node_id = kwargs.get("node_id", "")
        node = graph.node_sync(node_id)  # type: ignore[attr-defined]
        if node is None:
            return f"ERROR: Node not found: {node_id}"
        if not node.parent_id:
            return f"ERROR: {node_id} has no parent — nothing to re-stamp"
        parent = graph.node_sync(node.parent_id)  # type: ignore[attr-defined]
        if parent is None:
            return f"ERROR: parent {node.parent_id} of {node_id} not found"
        current = provenance_hash(parent.content or "")
        props = dict(node.properties or {})
        if DERIVED_FROM_HASH in props and props[DERIVED_FROM_HASH] == current:
            return f"OK: provenance on {node_id} already current"
        props[DERIVED_FROM_HASH] = current
        await graph.update_node(  # type: ignore[attr-defined]
            node_id, None, props, "agent",
            kwargs.get("reason", "refresh_provenance"),
        )
        return (
            f"OK: provenance re-stamped on {node_id} against parent "
            f"{node.parent_id}"
        )
