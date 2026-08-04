"""Coverage tests for crew/dispatch: error classifiers, fast-path, diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.crew.dispatch import (
    _is_quota_error,
    _is_transient_error,
    _log_dispatch_diagnostics,
    try_fast_trace,
)

# ── _is_transient_error / _is_quota_error ────────────────────────────────────


def test_is_transient_error_connection_error() -> None:
    assert _is_transient_error(ConnectionError("dropped")) is True


def test_is_transient_error_timeout() -> None:
    assert _is_transient_error(TimeoutError("slow")) is True


def test_is_transient_error_oserror() -> None:
    assert _is_transient_error(OSError("socket")) is True


def test_is_transient_error_value_error_not_transient() -> None:
    assert _is_transient_error(ValueError("bad")) is False


def test_is_transient_error_openai_5xx_is_transient() -> None:
    try:
        import openai
    except ImportError:
        pytest.skip("openai not installed")
    # openai.APIError does not declare status_code (only APIStatusError does);
    # _is_transient_error reads it via getattr, so the test attaches it here.
    err: Any = openai.APIError(message="boom", request=MagicMock(), body=None)
    err.status_code = 503
    assert _is_transient_error(err) is True


def test_is_quota_error_openai_401_is_quota() -> None:
    try:
        import openai
    except ImportError:
        pytest.skip("openai not installed")
    err = openai.APIStatusError(
        message="no auth", response=MagicMock(status_code=401), body=None
    )
    err.status_code = 401
    assert _is_quota_error(err) is True


def test_is_quota_error_non_openai_false() -> None:
    assert _is_quota_error(RuntimeError("x")) is False


# ── try_fast_trace ───────────────────────────────────────────────────────────


def _llr(nid: str, parent: str) -> SimpleNamespace:
    return SimpleNamespace(node_id=nid, node_type="LLR", parent_id=parent, trace_to=[])


def _design(nid: str, parent: str, trace_to: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=nid, node_type="DESIGN", parent_id=parent,
        trace_to=trace_to, content="", title="",
    )


def _gap(node_id: str, gap_type: GapType = GapType.UNDESIGNED) -> Gap:
    return Gap(
        type=gap_type,
        priority=GapPriority.DESIGN,
        node_id=node_id,
        description="x",
    )


def _flow(
    nodes: dict[str, SimpleNamespace],
    tracing_to_map: dict[str, list[str]] | None = None,
) -> MagicMock:
    flow = MagicMock()
    flow.graph.node_sync = MagicMock(side_effect=lambda nid: nodes.get(nid))
    flow.graph.children_sync = MagicMock(
        side_effect=lambda pid: [n for n in nodes.values() if n.parent_id == pid]
    )
    flow.graph.nodes_tracing_to = MagicMock(
        side_effect=lambda tid, source_type="": (tracing_to_map or {}).get(tid, [])
    )
    flow.graph.update_node = AsyncMock()
    return flow


@pytest.mark.asyncio
async def test_fast_trace_wrong_gap_type_short_circuits() -> None:
    assert await try_fast_trace(MagicMock(), _gap("X", GapType.UNARCHITECTED)) is False


@pytest.mark.asyncio
async def test_fast_trace_no_llr_short_circuits() -> None:
    flow = _flow({})
    assert await try_fast_trace(flow, _gap("MISSING")) is False


@pytest.mark.asyncio
async def test_fast_trace_no_module_short_circuits() -> None:
    llr = _llr("L1", "H1")
    flow = _flow({"L1": llr}, tracing_to_map={})
    assert await try_fast_trace(flow, _gap("L1")) is False


@pytest.mark.asyncio
async def test_fast_trace_no_design_under_module_short_circuits() -> None:
    llr = _llr("L1", "H1")
    flow = _flow({"L1": llr}, tracing_to_map={"H1": ["M1"]})
    # No DESIGN nodes under M1 — children_sync returns empty list.
    assert await try_fast_trace(flow, _gap("L1")) is False


@pytest.mark.asyncio
async def test_fast_trace_already_linked_returns_true_without_update() -> None:
    llr = _llr("L1", "H1")
    design = _design("D1", "M1", trace_to=["L1"])
    nodes = {"L1": llr, "D1": design}
    flow = _flow(nodes, tracing_to_map={"H1": ["M1"]})
    # children_sync for M1 returns [D1]
    result = await try_fast_trace(flow, _gap("L1"))
    assert result is True
    flow.graph.update_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_fast_trace_links_and_updates() -> None:
    llr = _llr("L1", "H1")
    design = _design("D1", "M1", trace_to=[])
    nodes = {"L1": llr, "D1": design}
    flow = _flow(nodes, tracing_to_map={"H1": ["M1"]})
    result = await try_fast_trace(flow, _gap("L1"))
    assert result is True
    flow.graph.update_node.assert_awaited_once()
    # The trace_to kwarg should include the LLR id.
    assert "L1" in flow.graph.update_node.call_args.kwargs["trace_to"]


# ── _log_dispatch_diagnostics ────────────────────────────────────────────────


def test_log_dispatch_diagnostics_with_empty_context() -> None:
    flow = MagicMock()
    flow.graph.node_sync.return_value = None
    gap = _gap("NID")
    # Should not raise.
    _log_dispatch_diagnostics(flow, gap, "")


def test_log_dispatch_diagnostics_with_node_present() -> None:
    flow = MagicMock()
    node = SimpleNamespace(
        node_id="N1", parent_id="P1", content="a long content line\nwith newline"
    )
    flow.graph.node_sync.return_value = node
    gap = _gap("N1")
    _log_dispatch_diagnostics(flow, gap, "context line 1\ncontext line 2")
