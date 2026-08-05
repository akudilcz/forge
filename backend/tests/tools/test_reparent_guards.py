"""Regression tests: reparent guards must fire through BOTH tool entry points.

``graph_reparent_node`` and the composite ``graph_write`` share one mutation
coroutine, but they reach it differently: ``graph_ops.py`` calls the ``_op_*``
helpers as *unbound* methods, passing a ``self`` that is a single-operation tool
rather than a ``GraphWriteTool``.

While the guards were class attributes, that ``self`` lacked them entirely.
``self._VALID_PARENTS`` raised ``AttributeError`` — and the whole validation
block sat inside ``except Exception: pass``, so the parent-type check and the
orphan guard were silently skipped for ``graph_reparent_node``. The tool
happily performed cross-layer reparents that ``graph_write`` rejected, and no
test noticed because every existing test drove the composite tool.

These tests pin the invariant that matters: **both entry points enforce the same
rules.** Each guard is asserted twice, once per tool.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.analysis.gap_analyser import VALID_PARENT_TYPES
from backend.graph.models import GraphNode
from backend.tools.graph_ops import GraphReparentNodeTool
from backend.tools.graph_write import GraphWriteTool


def _graph(nodes: dict[str, GraphNode], children: dict[str, list[GraphNode]]) -> Any:
    """A graph stub answering the three calls the reparent path makes."""
    graph = MagicMock()
    graph.node = AsyncMock(side_effect=lambda nid: nodes.get(nid))
    graph.children_sync = MagicMock(side_effect=lambda pid: children.get(pid, []))
    graph.reparent_node = AsyncMock()
    return graph


def _node(node_id: str, node_type: str, parent_id: str | None = None) -> GraphNode:
    return GraphNode(node_id=node_id, node_type=node_type, title=node_id, parent_id=parent_id)


async def _reparent_via_write_tool(graph: Any, **kwargs: Any) -> str:
    return await GraphWriteTool(graph=graph)._op_reparent_node(graph, **kwargs)


async def _reparent_via_single_op_tool(graph: Any, **kwargs: Any) -> str:
    # _run_op takes the graph explicitly, so the instance need not hold one.
    # (Constructing with a graph is also not expressible to the type checker:
    # pydantic's dataclass_transform hides the explicit __init__.)
    return await GraphReparentNodeTool()._run_op(graph, **kwargs)


ENTRY_POINTS = [
    pytest.param(_reparent_via_write_tool, id="graph_write"),
    pytest.param(_reparent_via_single_op_tool, id="graph_reparent_node"),
]


@pytest.mark.parametrize("reparent", ENTRY_POINTS)
class TestParentTypeGuard:
    """A PARA may only live under a DOCUMENT (see VALID_PARENTS)."""

    async def test_rejects_incompatible_parent_type(self, reparent: Any) -> None:
        nodes = {
            "PARA-1": _node("PARA-1", "PARA", parent_id="DOCUMENT-1"),
            "HLR-1": _node("HLR-1", "HLR"),
            "DOCUMENT-1": _node("DOCUMENT-1", "DOCUMENT"),
        }
        graph = _graph(nodes, {"DOCUMENT-1": [nodes["PARA-1"], _node("PARA-2", "PARA")]})

        result = await reparent(graph, node_id="PARA-1", parent_id="HLR-1")

        assert result.startswith("ERROR"), f"cross-layer reparent was allowed: {result}"
        assert "PARA" in result
        assert "HLR" in result
        graph.reparent_node.assert_not_awaited()

    async def test_allows_compatible_parent_type(self, reparent: Any) -> None:
        nodes = {
            "PARA-1": _node("PARA-1", "PARA", parent_id="DOCUMENT-1"),
            "DOCUMENT-2": _node("DOCUMENT-2", "DOCUMENT"),
            "DOCUMENT-1": _node("DOCUMENT-1", "DOCUMENT"),
        }
        graph = _graph(nodes, {"DOCUMENT-1": [nodes["PARA-1"], _node("PARA-2", "PARA")]})

        result = await reparent(graph, node_id="PARA-1", parent_id="DOCUMENT-2")

        assert result.startswith("OK"), result
        graph.reparent_node.assert_awaited_once()

    async def test_unconstrained_child_type_is_permitted_anywhere(
        self, reparent: Any
    ) -> None:
        """Types absent from the table carry no constraint."""
        assert "RECORD" not in VALID_PARENT_TYPES
        nodes = {
            "RECORD-1": _node("RECORD-1", "RECORD", parent_id="PROJECT-1"),
            "PROJECT-2": _node("PROJECT-2", "PROJECT"),
            "PROJECT-1": _node("PROJECT-1", "PROJECT"),
        }
        graph = _graph(
            nodes,
            {"PROJECT-1": [nodes["RECORD-1"], _node("RECORD-2", "RECORD")]},
        )

        result = await reparent(graph, node_id="RECORD-1", parent_id="PROJECT-2")
        assert result.startswith("OK"), result


@pytest.mark.parametrize("reparent", ENTRY_POINTS)
class TestOrphanGuard:
    """Moving the last child of a type would leave its parent uncovered."""

    async def test_rejects_moving_the_only_child_of_its_type(self, reparent: Any) -> None:
        nodes = {
            "LLR-1": _node("LLR-1", "LLR", parent_id="HLR-1"),
            "HLR-1": _node("HLR-1", "HLR"),
            "HLR-2": _node("HLR-2", "HLR"),
        }
        graph = _graph(nodes, {"HLR-1": [nodes["LLR-1"]]})  # LLR-1 is the only LLR

        result = await reparent(graph, node_id="LLR-1", parent_id="HLR-2")

        assert result.startswith("ERROR"), f"orphaning move was allowed: {result}"
        assert "only" in result
        graph.reparent_node.assert_not_awaited()

    async def test_allows_the_move_when_a_sibling_remains(self, reparent: Any) -> None:
        nodes = {
            "LLR-1": _node("LLR-1", "LLR", parent_id="HLR-1"),
            "HLR-1": _node("HLR-1", "HLR"),
            "HLR-2": _node("HLR-2", "HLR"),
        }
        graph = _graph(nodes, {"HLR-1": [nodes["LLR-1"], _node("LLR-2", "LLR", "HLR-1")]})

        result = await reparent(graph, node_id="LLR-1", parent_id="HLR-2")

        assert result.startswith("OK"), result
        graph.reparent_node.assert_awaited_once()

    async def test_sibling_of_a_different_type_does_not_satisfy_the_guard(
        self, reparent: Any
    ) -> None:
        nodes = {
            "LLR-1": _node("LLR-1", "LLR", parent_id="HLR-1"),
            "HLR-1": _node("HLR-1", "HLR"),
            "HLR-2": _node("HLR-2", "HLR"),
        }
        # A CASE_HLR sibling does not keep the HLR's LLR coverage.
        graph = _graph(
            nodes, {"HLR-1": [nodes["LLR-1"], _node("CASE_HLR-1", "CASE_HLR", "HLR-1")]}
        )

        result = await reparent(graph, node_id="LLR-1", parent_id="HLR-2")
        assert result.startswith("ERROR"), result


@pytest.mark.parametrize("reparent", ENTRY_POINTS)
class TestDegradedGraph:
    """A graph that cannot answer node() still performs the move."""

    async def test_skips_validation_when_the_graph_lacks_node(self, reparent: Any) -> None:
        graph = MagicMock()
        del graph.node  # attribute access now raises AttributeError
        graph.reparent_node = AsyncMock()

        result = await reparent(graph, node_id="LLR-1", parent_id="HLR-2")

        assert result.startswith("OK"), result
        graph.reparent_node.assert_awaited_once()

    async def test_a_real_error_is_not_swallowed(self, reparent: Any) -> None:
        """The old bare ``except Exception`` hid genuine faults; this pins that.

        A graph whose ``node()`` raises a real error must surface it rather than
        silently proceeding with an unvalidated mutation — that behaviour is
        exactly what let the AttributeError go unnoticed for so long.
        """
        graph = MagicMock()
        graph.node = AsyncMock(side_effect=RuntimeError("db is down"))
        graph.reparent_node = AsyncMock()

        with pytest.raises(RuntimeError, match="db is down"):
            await reparent(graph, node_id="LLR-1", parent_id="HLR-2")

        graph.reparent_node.assert_not_awaited()

    async def test_non_awaitable_node_degrades_instead_of_raising(
        self, reparent: Any
    ) -> None:
        """A graph whose node() is not a coroutine cannot be validated.

        Real graphs are async; this shape only occurs in tests using a bare
        MagicMock. Treating it as "cannot validate" keeps those tests working
        without reinstating a catch-all handler.
        """
        graph = MagicMock()
        graph.reparent_node = AsyncMock()

        result = await reparent(graph, node_id="LLR-1", parent_id="HLR-2")

        assert result.startswith("OK"), result
        graph.reparent_node.assert_awaited_once()


async def test_there_is_exactly_one_parent_type_table() -> None:
    """The write tool and the analyser must not disagree about legal parents.

    graph_write.py kept its own copy that permitted CASE_HLR under HLR and HLR
    under PROJECT — parentages the analyser reports as ORPHAN_NODE the moment it
    next runs. An agent could perform a reparent the write tool accepted, watch
    the gap reappear, and repeat. The copy also lacked ARCHITECTURE and SUITE
    entries (so permitted anything) while forbidding the nested PARA the
    analyser allows.
    """
    from backend.tools import graph_write

    assert not hasattr(graph_write, "VALID_PARENTS"), (
        "graph_write has reintroduced its own parent-type table"
    )
    # graph_write must consult the analyser's table, not a copy of its own.
    import inspect

    src = inspect.getsource(graph_write.reparent_node_op)
    assert "VALID_PARENT_TYPES" in src
    assert "CASE_HLR" not in inspect.getsource(graph_write), (
        "graph_write appears to hard-code node types again"
    )
    assert VALID_PARENT_TYPES["CASE_HLR"] == frozenset({"SUITE"})


@pytest.mark.parametrize("reparent", ENTRY_POINTS)
async def test_case_nodes_must_hang_off_the_suite(reparent: Any) -> None:
    """The concrete divergence: CASE_HLR under HLR used to be accepted."""
    nodes = {
        "CASE_HLR-1": _node("CASE_HLR-1", "CASE_HLR", parent_id="SUITE-1"),
        "HLR-1": _node("HLR-1", "HLR"),
        "SUITE-1": _node("SUITE-1", "SUITE"),
    }
    graph = _graph(
        nodes, {"SUITE-1": [nodes["CASE_HLR-1"], _node("CASE_HLR-2", "CASE_HLR", "SUITE-1")]}
    )

    result = await reparent(graph, node_id="CASE_HLR-1", parent_id="HLR-1")

    assert result.startswith("ERROR"), (
        f"CASE_HLR was allowed under HLR, which the analyser flags as "
        f"ORPHAN_NODE: {result}"
    )
    graph.reparent_node.assert_not_awaited()


async def test_orphan_guard_shim_remains_callable_on_the_instance() -> None:
    """``GraphWriteTool._check_orphan_guard`` is still used by existing tests."""
    child = _node("LLR-1", "LLR", parent_id="HLR-1")
    graph = _graph({}, {"HLR-1": [child]})

    result = await GraphWriteTool(graph=graph)._check_orphan_guard(graph, "LLR-1", child)

    assert result is not None
    assert result.startswith("ERROR")
