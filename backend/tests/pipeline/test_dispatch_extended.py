"""Coverage tests for crew/dispatch: error classifiers, fast-path, diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.pipeline.dispatch import (
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


# ── openai-absent fallbacks ──────────────────────────────────────────────────


def test_is_transient_error_without_openai_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When openai cannot be imported, classification falls back to builtins."""
    import sys

    monkeypatch.setitem(sys.modules, "openai", None)  # import openai → ImportError
    from backend.pipeline.dispatch import _is_transient_error as transient

    assert transient(ConnectionError("x")) is True
    assert transient(ValueError("x")) is False


def test_is_quota_error_without_openai_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "openai", None)
    from backend.pipeline.dispatch import _is_quota_error as quota

    assert quota(RuntimeError("x")) is False


# ── dispatch: transient retries exhausted ────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_exhausts_transient_retries_and_returns_empty() -> None:
    """Persistent transient errors are retried then abandoned with a log."""
    from backend.pipeline.dispatch import _MAX_API_RETRIES, dispatch

    flow = MagicMock()
    flow._graph_state_count.return_value = 0
    flow.pool.get_agent_for_gap.return_value = MagicMock()
    flow.config.llm.model_for_phase.return_value = "test-model"
    flow._current_phase = 1
    gap = _gap("HLR-1", GapType.UNCOVERED_PARA)

    with (
        patch(
            "backend.pipeline.dispatch.run_agent_task",
            new_callable=AsyncMock, side_effect=ConnectionError("flaky"),
        ) as run_task,
        patch("backend.pipeline.dispatch.asyncio.sleep", new_callable=AsyncMock),
        patch("backend.pipeline.dispatch.forge_logger") as logger_mock,
    ):
        result = await dispatch(flow, gap)

    assert result == ""
    assert run_task.await_count == _MAX_API_RETRIES
    exhausted = [
        c for c in logger_mock.emit.call_args_list
        if c.args[0] == "ERROR" and "retries exhausted" in c.args[2]
    ]
    assert exhausted


# ── run_agent_task: event stream handling ────────────────────────────────────


class _FakeAgent:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.captured_config: dict[str, Any] | None = None

    async def astream_events(self, _input: Any, version: str, config: dict[str, Any]) -> Any:
        self.captured_config = config
        for e in self._events:
            yield e


def _task_flow() -> MagicMock:
    flow = MagicMock()
    flow.graph = None  # skip graph diagnostics
    flow.state.current_phase = 0
    return flow


def _run_task_patches() -> list[Any]:
    return [
        patch("backend.pipeline.dispatch.build_context_for_gap", return_value=""),
        patch(
            "backend.pipeline.dispatch.build_task_description",
            return_value=("do it", "done"),
        ),
    ]


@pytest.mark.asyncio
async def test_run_agent_task_collects_tool_calls_and_final_text() -> None:
    """Tool calls are logged; the final text-only message becomes the output."""
    from types import SimpleNamespace

    from backend.pipeline.dispatch import run_agent_task

    tool_msg_dict = SimpleNamespace(tool_calls=[{"name": "file_write", "args": {"p": "x"}}], content="")
    tool_msg_obj = SimpleNamespace(tool_calls=[SimpleNamespace(name="shell_exec", args={"cmd": "ls"})], content="")
    wrapped = SimpleNamespace(message=SimpleNamespace(tool_calls=None, content="all done"))
    events: list[dict[str, Any]] = [
        {"event": "on_tool_start"},  # ignored
        {"event": "on_chat_model_end", "data": {"output": None}},  # no message
        {"event": "on_chat_model_end", "data": {"output": tool_msg_dict}},
        {"event": "on_chat_model_end", "data": {"output": tool_msg_obj}},
        {"event": "on_chat_model_end", "data": {"output": wrapped}},
    ]
    agent = _FakeAgent(events)
    gap = _gap("LLR-1")
    ctx_patch, desc_patch = _run_task_patches()
    with ctx_patch, desc_patch, patch("backend.pipeline.dispatch.forge_logger") as logger_mock:
        raw = await run_agent_task(_task_flow(), agent, gap, attempt=1, model="gpt-test")

    assert raw == "all done"
    tool_names = [c.args[0] for c in logger_mock.crew_tool_call.call_args_list]
    assert tool_names == ["file_write", "shell_exec"]
    logger_mock.crew_finish.assert_called_once_with("all done")


@pytest.mark.asyncio
async def test_run_agent_task_warns_on_text_only_response() -> None:
    """A response with zero tool calls triggers a hallucination warning."""
    from types import SimpleNamespace

    from backend.pipeline.dispatch import run_agent_task

    events = [
        {"event": "on_chat_model_end", "data": {"output": SimpleNamespace(tool_calls=None, content="just prose")}},
    ]
    gap = _gap("LLR-1")
    ctx_patch, desc_patch = _run_task_patches()
    with ctx_patch, desc_patch, patch("backend.pipeline.dispatch.forge_logger") as logger_mock:
        raw = await run_agent_task(
            _task_flow(), _FakeAgent(events), gap, attempt=1, model="",
        )

    assert raw == "just prose"
    warns = [
        c for c in logger_mock.emit.call_args_list
        if c.args[0] == "WARN" and "No tool calls" in c.args[2]
    ]
    assert warns


# ── per-gap thread scoping ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_agent_task_thread_scoped_per_gap() -> None:
    """Two different gaps of the same type get distinct conversation threads."""
    from backend.pipeline.dispatch import run_agent_task

    gap_a = _gap("LLR-0001", GapType.STALE_NODE)
    gap_b = _gap("LLR-0002", GapType.STALE_NODE)
    agent_a, agent_b = _FakeAgent([]), _FakeAgent([])
    ctx_patch, desc_patch = _run_task_patches()
    with ctx_patch, desc_patch, patch("backend.pipeline.dispatch.forge_logger"):
        await run_agent_task(_task_flow(), agent_a, gap_a, attempt=1, model="")
        await run_agent_task(_task_flow(), agent_b, gap_b, attempt=1, model="")

    assert agent_a.captured_config is not None
    assert agent_b.captured_config is not None
    tid_a = agent_a.captured_config["configurable"]["thread_id"]
    tid_b = agent_b.captured_config["configurable"]["thread_id"]
    assert tid_a != tid_b
    assert "LLR-0001" in tid_a
    assert "LLR-0002" in tid_b


@pytest.mark.asyncio
async def test_run_agent_task_retry_reuses_thread() -> None:
    """A retry of the same gap reuses the same conversation thread."""
    from backend.pipeline.dispatch import run_agent_task

    gap = _gap("LLR-0001", GapType.STALE_NODE)
    agent_1, agent_2 = _FakeAgent([]), _FakeAgent([])
    ctx_patch, desc_patch = _run_task_patches()
    with ctx_patch, desc_patch, patch("backend.pipeline.dispatch.forge_logger"):
        await run_agent_task(_task_flow(), agent_1, gap, attempt=1, model="")
        await run_agent_task(_task_flow(), agent_2, gap, attempt=2, model="")

    assert agent_1.captured_config is not None
    assert agent_2.captured_config is not None
    assert (
        agent_1.captured_config["configurable"]["thread_id"]
        == agent_2.captured_config["configurable"]["thread_id"]
    )
