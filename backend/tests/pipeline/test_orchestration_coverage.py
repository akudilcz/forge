"""Cross-package orchestration coverage: pipeline (dispatch, batch_steps)
and quality (design_consolidation, semantic_duplicate_check, checks).

Maximise coverage for previously-uncovered lines.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType

# ── Helpers ────────────────────────────────────────────────────────────────────


def _mock_node(
    node_id: str,
    node_type: str = "PARA",
    title: str = "",
    content: str = "",
    parent_id: str | None = None,
    trace_to: list[str] | None = None,
) -> MagicMock:
    n = MagicMock()
    n.node_id = node_id
    n.node_type = node_type
    n.title = title
    n.content = content
    n.parent_id = parent_id
    n.trace_to = trace_to or []
    return n


def _make_graph(nodes: list[MagicMock] | None = None) -> MagicMock:
    graph = MagicMock()
    nodes = nodes or []
    graph.all_nodes.return_value = nodes
    graph.node_sync.side_effect = lambda nid: next(
        (n for n in nodes if n.node_id == nid),
        None,
    )
    graph.children_sync.return_value = []
    graph.update_node = AsyncMock()
    graph.delete_node = AsyncMock()
    return graph


def _make_flow(
    nodes: list[MagicMock] | None = None, gaps: list[Gap] | None = None
) -> MagicMock:
    flow = MagicMock()
    nodes = nodes or []
    flow.graph = _make_graph(nodes)
    flow._collect_phase_gaps.return_value = gaps or []
    flow._graph_state_count.return_value = 0
    flow.state.current_phase = 3
    flow.config.llm.model_for_phase.return_value = "test-model"
    flow.config.llm.context_window_for_model.return_value = 128000
    flow.config.llm.batch_author_chunk_size = 20

    agent = AsyncMock()

    async def fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        if False:  # noqa: SIM223
            yield

    agent.astream_events = fake_stream
    flow.pool.get_agent_for_gap.return_value = agent
    return flow


# ═══════════════════════════════════════════════════════════════════════════════
# 1. design_consolidation
# ═══════════════════════════════════════════════════════════════════════════════


class TestDesignConsolidation:
    """Tests for create_design_consolidator and _execute_merges."""

    @pytest.mark.asyncio
    async def test_consolidate_single_design_returns_zero(self) -> None:
        """No merging when only one DESIGN exists."""
        from backend.quality.design_consolidation import create_design_consolidator

        llm = AsyncMock()
        graph = _make_graph()
        consolidate = create_design_consolidator(llm, graph)

        result = await consolidate(
            "MOD-1", "module text", "contract", [{"node_id": "D1", "trace_to": [], "content": "x"}]
        )
        assert result == 0
        llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_consolidate_no_merge_needed(self) -> None:
        """LLM responds NO_MERGE_NEEDED — no deletions."""
        from backend.quality.design_consolidation import create_design_consolidator

        llm = AsyncMock()
        response = MagicMock()
        response.content = "NO_MERGE_NEEDED"
        llm.ainvoke.return_value = response
        graph = _make_graph()

        consolidate = create_design_consolidator(llm, graph)
        designs = [
            {"node_id": "D1", "trace_to": ["LLR-1"], "content": "design 1"},
            {"node_id": "D2", "trace_to": ["LLR-2"], "content": "design 2"},
        ]
        result = await consolidate("MOD-1", "module text", "contract", designs)
        assert result == 0

    @pytest.mark.asyncio
    async def test_consolidate_llm_failure_returns_zero(self) -> None:
        """LLM exception is caught and returns 0."""
        from backend.quality.design_consolidation import create_design_consolidator

        llm = AsyncMock()
        llm.ainvoke.side_effect = RuntimeError("boom")
        graph = _make_graph()

        consolidate = create_design_consolidator(llm, graph)
        designs = [
            {"node_id": "D1", "trace_to": [], "content": "a"},
            {"node_id": "D2", "trace_to": [], "content": "b"},
        ]
        result = await consolidate("MOD-1", "module", "", designs)
        assert result == 0

    @pytest.mark.asyncio
    async def test_consolidate_merges_designs(self) -> None:
        """LLM responds with KEEP/MERGE directives — designs merged and deleted."""
        from backend.quality.design_consolidation import create_design_consolidator

        llm = AsyncMock()
        response = MagicMock()
        response.content = (
            "KEEP: D1\nMERGE: D2\nMERGED_CONTENT: combined design\nREASON: overlapping concerns"
        )
        llm.ainvoke.return_value = response

        d1 = _mock_node("D1", "DESIGN", content="design 1", trace_to=["LLR-1"])
        d2 = _mock_node("D2", "DESIGN", content="design 2", trace_to=["LLR-2"])
        graph = _make_graph([d1, d2])

        consolidate = create_design_consolidator(llm, graph)
        designs = [
            {"node_id": "D1", "trace_to": ["LLR-1"], "content": "design 1"},
            {"node_id": "D2", "trace_to": ["LLR-2"], "content": "design 2"},
        ]
        result = await consolidate("MOD-1", "module text", "contract", designs)
        assert result == 1
        graph.update_node.assert_awaited_once()
        graph.delete_node.assert_awaited_once_with("D2")

    @pytest.mark.asyncio
    async def test_execute_merges_skip_unknown_keep_id(self) -> None:
        """Skip merge block if keep_id is not in the designs list."""
        from backend.quality.design_consolidation import _execute_merges

        graph = _make_graph()
        designs = [
            {"node_id": "D1", "trace_to": [], "content": "a"},
            {"node_id": "D2", "trace_to": [], "content": "b"},
        ]
        text = "KEEP: UNKNOWN\nMERGE: D2\nMERGED_CONTENT: merged\nREASON: test"
        result = await _execute_merges(graph, "MOD-1", text, designs)
        assert result == 0
        graph.delete_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_merges_skip_missing_keep_merge(self) -> None:
        """Skip block when KEEP or MERGE is missing from block."""
        from backend.quality.design_consolidation import _execute_merges

        graph = _make_graph()
        designs = [{"node_id": "D1", "trace_to": [], "content": "a"}]
        # Block with only KEEP, no MERGE
        text = "KEEP: D1\nMERGED_CONTENT: merged\nREASON: test"
        result = await _execute_merges(graph, "MOD-1", text, designs)
        assert result == 0

    @pytest.mark.asyncio
    async def test_execute_merges_keep_node_is_none(self) -> None:
        """Skip if keep_id node no longer exists in graph."""
        from backend.quality.design_consolidation import _execute_merges

        graph = _make_graph()  # node_sync returns None for everything
        designs = [
            {"node_id": "D1", "trace_to": [], "content": "a"},
            {"node_id": "D2", "trace_to": [], "content": "b"},
        ]
        text = "KEEP: D1\nMERGE: D2\nMERGED_CONTENT: merged\nREASON: test"
        result = await _execute_merges(graph, "MOD-1", text, designs)
        assert result == 0

    @pytest.mark.asyncio
    async def test_execute_merges_no_merged_content_deletes_nothing(self) -> None:
        """No parsed content means nothing was merged, so nothing may be deleted.

        This previously deleted D2 while never calling update_node — the union
        of trace_to was inside `if merged_content:` but the delete loop was not,
        so D2's requirement links were destroyed rather than transferred.
        """
        from backend.quality.design_consolidation import _execute_merges

        d1 = _mock_node("D1", "DESIGN", trace_to=["LLR-1"])
        d2 = _mock_node("D2", "DESIGN", trace_to=["LLR-2"])
        graph = _make_graph([d1, d2])

        designs = [
            {"node_id": "D1", "trace_to": ["LLR-1"], "content": "a"},
            {"node_id": "D2", "trace_to": ["LLR-2"], "content": "b"},
        ]
        text = "KEEP: D1\nMERGE: D2\nREASON: test"
        result = await _execute_merges(graph, "MOD-1", text, designs)

        assert result == 0
        graph.update_node.assert_not_awaited()
        graph.delete_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_merges_keeps_multiline_content(self) -> None:
        """MERGED_CONTENT is a block; truncating it to one line loses the design."""
        from backend.quality.design_consolidation import _execute_merges

        d1 = _mock_node("D1", "DESIGN", trace_to=["LLR-1"])
        d2 = _mock_node("D2", "DESIGN", trace_to=["LLR-2"])
        graph = _make_graph([d1, d2])

        designs = [
            {"node_id": "D1", "trace_to": ["LLR-1"], "content": "a"},
            {"node_id": "D2", "trace_to": ["LLR-2"], "content": "b"},
        ]
        text = (
            "KEEP: D1\n"
            "MERGE: D2\n"
            "MERGED_CONTENT: class Combined:\n"
            "    def first(self) -> None: ...\n"
            "    def second(self) -> None: ...\n"
            "REASON: test"
        )
        result = await _execute_merges(graph, "MOD-1", text, designs)

        assert result == 1
        graph.update_node.assert_awaited_once()
        content = graph.update_node.await_args.kwargs["content"]
        assert "def first" in content
        assert "def second" in content, "multi-line design body was truncated"
        traces = graph.update_node.await_args.kwargs["trace_to"]
        assert set(traces) == {"LLR-1", "LLR-2"}, (
            f"merged node's requirement links were lost: {traces}"
        )

    @pytest.mark.asyncio
    async def test_execute_merges_merge_node_gone(self) -> None:
        """When the merge target no longer exists, skip deletion."""
        from backend.quality.design_consolidation import _execute_merges

        d1 = _mock_node("D1", "DESIGN", trace_to=["LLR-1"])
        # D2 exists for trace lookup but not for deletion check
        graph = _make_graph([d1])

        designs = [
            {"node_id": "D1", "trace_to": ["LLR-1"], "content": "a"},
            {"node_id": "D2", "trace_to": ["LLR-2"], "content": "b"},
        ]
        text = "KEEP: D1\nMERGE: D2\nMERGED_CONTENT: merged\nREASON: test"
        result = await _execute_merges(graph, "MOD-1", text, designs)
        assert result == 0  # D2 not found so not deleted
        # update_node is still called for the keep node
        graph.update_node.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_consolidate_response_without_content_attr(self) -> None:
        """When response lacks .content attribute, falls back to str()."""
        from backend.quality.design_consolidation import create_design_consolidator

        llm = AsyncMock()
        response = "NO_MERGE_NEEDED"
        llm.ainvoke.return_value = response
        graph = _make_graph()

        consolidate = create_design_consolidator(llm, graph)
        designs = [
            {"node_id": "D1", "trace_to": [], "content": "a"},
            {"node_id": "D2", "trace_to": [], "content": "b"},
        ]
        result = await consolidate("MOD-1", "module", "", designs)
        assert result == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. semantic_duplicate_check
# ═══════════════════════════════════════════════════════════════════════════════


class TestSemanticDuplicateCheck:
    """Tests for create_semantic_checker."""

    @pytest.mark.asyncio
    async def test_unique_response_does_not_delete(self) -> None:
        from backend.quality.semantic_duplicate_check import create_semantic_checker

        llm = AsyncMock()
        response = MagicMock()
        response.content = "UNIQUE - distinct obligation"
        llm.ainvoke.return_value = response

        graph = _make_graph()
        check = create_semantic_checker(llm, graph, {})

        result = await check("N1", "requirement text", "sibling text")
        assert result is False
        graph.delete_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_response_deletes_node(self) -> None:
        """Both the initial and the confirmation call return DUPLICATE."""
        from backend.quality.semantic_duplicate_check import create_semantic_checker

        llm = AsyncMock()
        response = MagicMock()
        response.content = "DUPLICATE - same as sibling S1"
        llm.ainvoke.return_value = response

        graph = _make_graph()
        check = create_semantic_checker(llm, graph, {})

        result = await check("N1", "requirement text", "sibling text")
        assert result is True
        assert llm.ainvoke.await_count == 2  # verdict + independent confirmation
        graph.delete_node.assert_awaited_once_with("N1")

    @pytest.mark.asyncio
    async def test_duplicate_case_insensitive(self) -> None:
        from backend.quality.semantic_duplicate_check import create_semantic_checker

        llm = AsyncMock()
        response = MagicMock()
        response.content = "duplicate - matched s2"
        llm.ainvoke.return_value = response

        graph = _make_graph()
        check = create_semantic_checker(llm, graph, {})

        result = await check("N2", "text", "siblings")
        assert result is True

    @pytest.mark.asyncio
    async def test_response_without_content_attr(self) -> None:
        """Falls back to str(response) when no .content attribute."""
        from backend.quality.semantic_duplicate_check import create_semantic_checker

        llm = AsyncMock()
        llm.ainvoke.return_value = "UNIQUE - plain string"
        graph = _make_graph()
        check = create_semantic_checker(llm, graph, {})

        result = await check("N1", "text", "siblings")
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# 3. dispatch
# ═══════════════════════════════════════════════════════════════════════════════


class TestTryFastTrace:
    """Tests for try_fast_trace."""

    @pytest.mark.asyncio
    async def test_non_undesigned_gap_returns_false(self) -> None:
        from backend.pipeline.dispatch import try_fast_trace

        flow = _make_flow()
        gap = Gap(
            type=GapType.UNCOVERED_PARA,
            priority=GapPriority.REQUIREMENTS_HLR,
            node_id="P1",
            description="test",
        )
        result = await try_fast_trace(flow, gap)
        assert result is False

    @pytest.mark.asyncio
    async def test_node_not_found_returns_false(self) -> None:
        from backend.pipeline.dispatch import try_fast_trace

        flow = _make_flow()
        gap = Gap(
            type=GapType.UNDESIGNED,
            priority=GapPriority.DESIGN,
            node_id="LLR-1",
            description="test",
        )
        result = await try_fast_trace(flow, gap)
        assert result is False

    @pytest.mark.asyncio
    async def test_node_without_parent_returns_false(self) -> None:
        from backend.pipeline.dispatch import try_fast_trace

        llr = _mock_node("LLR-1", "LLR", parent_id=None)
        flow = _make_flow(nodes=[llr])
        gap = Gap(
            type=GapType.UNDESIGNED,
            priority=GapPriority.DESIGN,
            node_id="LLR-1",
            description="test",
        )
        result = await try_fast_trace(flow, gap)
        # parent_id is empty string from mock default, treated as falsy
        assert result is False

    @pytest.mark.asyncio
    async def test_no_module_ids_returns_false(self) -> None:
        from backend.pipeline.dispatch import try_fast_trace

        llr = _mock_node("LLR-1", "LLR", parent_id="HLR-1")
        flow = _make_flow(nodes=[llr])
        flow.graph.nodes_tracing_to = MagicMock(return_value=[])
        gap = Gap(
            type=GapType.UNDESIGNED,
            priority=GapPriority.DESIGN,
            node_id="LLR-1",
            description="test",
        )
        result = await try_fast_trace(flow, gap)
        assert result is False

    @pytest.mark.asyncio
    async def test_no_designs_returns_false(self) -> None:
        from backend.pipeline.dispatch import try_fast_trace

        llr = _mock_node("LLR-1", "LLR", parent_id="HLR-1")
        mod = _mock_node("MOD-1", "MODULE")
        flow = _make_flow(nodes=[llr, mod])
        flow.graph.nodes_tracing_to = MagicMock(return_value=["MOD-1"])
        flow.graph.children_sync.return_value = []
        gap = Gap(
            type=GapType.UNDESIGNED,
            priority=GapPriority.DESIGN,
            node_id="LLR-1",
            description="test",
        )
        result = await try_fast_trace(flow, gap)
        assert result is False

    @pytest.mark.asyncio
    async def test_already_traced_returns_true(self) -> None:
        from backend.pipeline.dispatch import try_fast_trace

        llr = _mock_node("LLR-1", "LLR", parent_id="HLR-1")
        design = _mock_node("DES-1", "DESIGN", trace_to=["LLR-1"])
        mod = _mock_node("MOD-1", "MODULE")
        flow = _make_flow(nodes=[llr, mod, design])
        flow.graph.nodes_tracing_to = MagicMock(return_value=["MOD-1"])
        flow.graph.children_sync.return_value = [design]
        gap = Gap(
            type=GapType.UNDESIGNED,
            priority=GapPriority.DESIGN,
            node_id="LLR-1",
            description="test",
        )
        result = await try_fast_trace(flow, gap)
        assert result is True
        flow.graph.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_adds_trace_and_returns_true(self) -> None:
        from backend.pipeline.dispatch import try_fast_trace

        llr = _mock_node("LLR-1", "LLR", parent_id="HLR-1")
        design = _mock_node("DES-1", "DESIGN", trace_to=["LLR-99"])
        mod = _mock_node("MOD-1", "MODULE")
        flow = _make_flow(nodes=[llr, mod, design])
        flow.graph.nodes_tracing_to = MagicMock(return_value=["MOD-1"])
        flow.graph.children_sync.return_value = [design]
        gap = Gap(
            type=GapType.UNDESIGNED,
            priority=GapPriority.DESIGN,
            node_id="LLR-1",
            description="test",
        )
        result = await try_fast_trace(flow, gap)
        assert result is True
        flow.graph.update_node.assert_awaited_once()
        call_kwargs = flow.graph.update_node.call_args[1]
        assert "LLR-1" in call_kwargs["trace_to"]


class TestIsTransientError:
    """Tests for _is_transient_error."""

    def test_connection_error(self) -> None:
        from backend.pipeline.dispatch import _is_transient_error

        assert _is_transient_error(ConnectionError("refused")) is True

    def test_timeout_error(self) -> None:
        from backend.pipeline.dispatch import _is_transient_error

        assert _is_transient_error(TimeoutError("timed out")) is True

    def test_os_error(self) -> None:
        from backend.pipeline.dispatch import _is_transient_error

        assert _is_transient_error(OSError("network")) is True

    def test_value_error_not_transient(self) -> None:
        from backend.pipeline.dispatch import _is_transient_error

        assert _is_transient_error(ValueError("bad value")) is False

    def test_openai_server_error(self) -> None:
        from backend.pipeline.dispatch import _is_transient_error

        try:
            import openai

            exc = openai.APIError(
                message="server error",
                request=MagicMock(),
                body=None,
            )
            # ``status_code`` is declared by APIStatusError, not by the APIError
            # base; production reads it with getattr, so attach it the same way.
            exc.status_code = 500  # type: ignore[attr-defined]
            assert _is_transient_error(exc) is True
        except ImportError:
            pytest.skip("openai not installed")

    def test_openai_client_error_not_transient(self) -> None:
        from backend.pipeline.dispatch import _is_transient_error

        try:
            import openai

            exc = openai.APIError(
                message="bad request",
                request=MagicMock(),
                body=None,
            )
            # See test_openai_server_error — attribute is set dynamically.
            exc.status_code = 400  # type: ignore[attr-defined]
            assert _is_transient_error(exc) is False
        except ImportError:
            pytest.skip("openai not installed")


class TestIsQuotaError:
    """Tests for _is_quota_error."""

    def test_non_openai_error(self) -> None:
        from backend.pipeline.dispatch import _is_quota_error

        assert _is_quota_error(RuntimeError("nope")) is False

    def test_openai_401(self) -> None:
        from backend.pipeline.dispatch import _is_quota_error

        try:
            import openai

            exc = openai.APIStatusError(
                message="unauthorized",
                response=MagicMock(status_code=401, headers={}),
                body=None,
            )
            exc.status_code = 401
            assert _is_quota_error(exc) is True
        except ImportError:
            pytest.skip("openai not installed")

    def test_openai_403(self) -> None:
        from backend.pipeline.dispatch import _is_quota_error

        try:
            import openai

            exc = openai.APIStatusError(
                message="forbidden",
                response=MagicMock(status_code=403, headers={}),
                body=None,
            )
            exc.status_code = 403
            assert _is_quota_error(exc) is True
        except ImportError:
            pytest.skip("openai not installed")

    def test_openai_500_not_quota(self) -> None:
        from backend.pipeline.dispatch import _is_quota_error

        try:
            import openai

            exc = openai.APIStatusError(
                message="server error",
                response=MagicMock(status_code=500, headers={}),
                body=None,
            )
            exc.status_code = 500
            assert _is_quota_error(exc) is False
        except ImportError:
            pytest.skip("openai not installed")


class TestGetModel:
    """Tests for dispatch._get_model."""

    def test_get_model_success(self) -> None:
        from backend.pipeline.dispatch import _get_model

        flow = MagicMock()
        flow._current_phase = 5
        flow.config.llm.model_for_phase.return_value = "gpt-4"
        assert _get_model(flow) == "gpt-4"

    def test_get_model_exception_returns_empty(self) -> None:
        from backend.pipeline.dispatch import _get_model

        flow = MagicMock()
        flow._current_phase = 5
        flow.config.llm.model_for_phase.side_effect = RuntimeError
        assert _get_model(flow) == ""


class TestDispatch:
    """Tests for the dispatch function."""

    @pytest.mark.asyncio
    async def test_dispatch_fast_path(self) -> None:
        """dispatch returns 'fast-path trace' when try_fast_trace succeeds."""
        from backend.pipeline.dispatch import dispatch

        flow = _make_flow()
        gap = Gap(
            type=GapType.UNDESIGNED,
            priority=GapPriority.DESIGN,
            node_id="LLR-1",
            description="test",
        )

        with patch(
            "backend.pipeline.dispatch.try_fast_trace", new_callable=AsyncMock, return_value=True
        ):
            result = await dispatch(flow, gap)
        assert result == "fast-path trace"

    @pytest.mark.asyncio
    async def test_dispatch_no_agent(self) -> None:
        """dispatch returns empty string when no agent available."""
        from backend.pipeline.dispatch import dispatch

        flow = _make_flow()
        flow.pool.get_agent_for_gap.return_value = None
        gap = Gap(
            type=GapType.UNCOVERED_PARA,
            priority=GapPriority.REQUIREMENTS_HLR,
            node_id="P1",
            description="test",
        )

        with patch(
            "backend.pipeline.dispatch.try_fast_trace", new_callable=AsyncMock, return_value=False
        ):
            result = await dispatch(flow, gap)
        assert result == ""

    @pytest.mark.asyncio
    async def test_dispatch_quota_error_raises(self) -> None:
        """dispatch raises DispatchQuotaError on quota error."""
        from backend.pipeline.dispatch import DispatchQuotaError, dispatch

        flow = _make_flow()
        gap = Gap(
            type=GapType.UNCOVERED_PARA,
            priority=GapPriority.REQUIREMENTS_HLR,
            node_id="P1",
            description="test",
        )

        try:
            import openai

            exc = openai.APIStatusError(
                message="quota exceeded",
                response=MagicMock(status_code=402, headers={}),
                body=None,
            )
            exc.status_code = 402
        except ImportError:
            pytest.skip("openai not installed")

        with (
            patch(
                "backend.pipeline.dispatch.try_fast_trace", new_callable=AsyncMock, return_value=False
            ),
            patch("backend.pipeline.dispatch.run_agent_task", new_callable=AsyncMock, side_effect=exc),
        ):
            with pytest.raises(DispatchQuotaError):
                await dispatch(flow, gap)

    @pytest.mark.asyncio
    async def test_dispatch_transient_error_retries(self) -> None:
        """dispatch retries on transient error then succeeds."""
        from backend.pipeline.dispatch import dispatch

        flow = _make_flow()
        gap = Gap(
            type=GapType.UNCOVERED_PARA,
            priority=GapPriority.REQUIREMENTS_HLR,
            node_id="P1",
            description="test",
        )

        call_count = 0

        async def fake_run(*args: Any, **kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("transient")
            return "done"

        with (
            patch(
                "backend.pipeline.dispatch.try_fast_trace", new_callable=AsyncMock, return_value=False
            ),
            patch("backend.pipeline.dispatch.run_agent_task", side_effect=fake_run),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await dispatch(flow, gap)
        assert result == "done"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_dispatch_transient_error_graph_changed_skips_retry(self) -> None:
        """dispatch skips retry when graph already changed (partial work)."""
        from backend.pipeline.dispatch import dispatch

        flow = _make_flow()
        # Graph changed between calls
        flow._graph_state_count.side_effect = [0, 1]
        gap = Gap(
            type=GapType.UNCOVERED_PARA,
            priority=GapPriority.REQUIREMENTS_HLR,
            node_id="P1",
            description="test",
        )

        async def fake_run(*args: Any, **kwargs: Any) -> str:
            raise ConnectionError("transient")

        with (
            patch(
                "backend.pipeline.dispatch.try_fast_trace", new_callable=AsyncMock, return_value=False
            ),
            patch("backend.pipeline.dispatch.run_agent_task", side_effect=fake_run),
        ):
            result = await dispatch(flow, gap)
        assert result == ""

    @pytest.mark.asyncio
    async def test_dispatch_non_transient_error_breaks(self) -> None:
        """dispatch breaks on non-transient, non-quota error."""
        from backend.pipeline.dispatch import dispatch

        flow = _make_flow()
        gap = Gap(
            type=GapType.UNCOVERED_PARA,
            priority=GapPriority.REQUIREMENTS_HLR,
            node_id="P1",
            description="test",
        )

        async def fake_run(*args: Any, **kwargs: Any) -> str:
            raise ValueError("bad input")

        with (
            patch(
                "backend.pipeline.dispatch.try_fast_trace", new_callable=AsyncMock, return_value=False
            ),
            patch("backend.pipeline.dispatch.run_agent_task", side_effect=fake_run),
        ):
            result = await dispatch(flow, gap)
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════════
# 4. quality
# ═══════════════════════════════════════════════════════════════════════════════


class TestSemanticGapsForType:
    """Tests for semantic_gaps_for_type."""

    def test_no_nodes_returns_empty(self) -> None:
        from backend.quality.checks import semantic_gaps_for_type

        graph = _make_graph()
        gaps = semantic_gaps_for_type(graph, "HLR")
        assert gaps == []

    def test_single_node_no_gaps(self) -> None:
        from backend.quality.checks import semantic_gaps_for_type

        hlr = _mock_node("HLR-1", "HLR")
        graph = _make_graph([hlr])
        gaps = semantic_gaps_for_type(graph, "HLR")
        assert gaps == []

    def test_two_nodes_second_is_candidate(self) -> None:
        from backend.quality.checks import semantic_gaps_for_type

        hlr1 = _mock_node("HLR-1", "HLR")
        hlr2 = _mock_node("HLR-2", "HLR")
        graph = _make_graph([hlr1, hlr2])
        gaps = semantic_gaps_for_type(graph, "HLR")
        assert len(gaps) == 1
        assert gaps[0].node_id == "HLR-2"
        assert gaps[0].type == GapType.DUPLICATE_NODE

    def test_only_node_ids_filter(self) -> None:
        from backend.quality.checks import semantic_gaps_for_type

        hlr1 = _mock_node("HLR-1", "HLR")
        hlr2 = _mock_node("HLR-2", "HLR")
        hlr3 = _mock_node("HLR-3", "HLR")
        graph = _make_graph([hlr1, hlr2, hlr3])

        # Only HLR-3 is in the filter
        gaps = semantic_gaps_for_type(graph, "HLR", only_node_ids={"HLR-3"})
        assert len(gaps) == 1
        assert gaps[0].node_id == "HLR-3"

    def test_only_node_ids_excludes_all(self) -> None:
        from backend.quality.checks import semantic_gaps_for_type

        hlr1 = _mock_node("HLR-1", "HLR")
        hlr2 = _mock_node("HLR-2", "HLR")
        graph = _make_graph([hlr1, hlr2])

        gaps = semantic_gaps_for_type(graph, "HLR", only_node_ids={"HLR-99"})
        assert gaps == []

    def test_ignores_other_node_types(self) -> None:
        from backend.quality.checks import semantic_gaps_for_type

        hlr = _mock_node("HLR-1", "HLR")
        llr = _mock_node("LLR-1", "LLR")
        graph = _make_graph([hlr, llr])
        gaps = semantic_gaps_for_type(graph, "HLR")
        assert gaps == []



class TestModulesNeedingConsolidation:
    """Tests for modules_needing_consolidation."""

    def test_no_modules(self) -> None:
        from backend.quality.checks import modules_needing_consolidation

        graph = _make_graph()
        result = modules_needing_consolidation(graph, [])
        assert result == []

    def test_module_with_one_design(self) -> None:
        from backend.quality.checks import modules_needing_consolidation

        mod = _mock_node("MOD-1", "MODULE")
        des = _mock_node("DES-1", "DESIGN")
        graph = _make_graph([mod, des])
        graph.children_sync.return_value = [des]
        result = modules_needing_consolidation(graph, [mod])
        assert result == []

    def test_module_with_two_designs(self) -> None:
        from backend.quality.checks import modules_needing_consolidation

        mod = _mock_node("MOD-1", "MODULE")
        des1 = _mock_node("DES-1", "DESIGN")
        des2 = _mock_node("DES-2", "DESIGN")
        graph = _make_graph([mod, des1, des2])
        graph.children_sync.return_value = [des1, des2]
        result = modules_needing_consolidation(graph, [mod])
        assert len(result) == 1
        assert result[0][0] == mod
        assert len(result[0][1]) == 2


class TestFindContract:
    """Tests for find_contract."""

    def test_no_contract_returns_empty(self) -> None:
        from backend.quality.checks import find_contract

        graph = _make_graph()
        graph.children_sync.return_value = []
        assert find_contract(graph, "MOD-1") == ""

    def test_contract_found(self) -> None:
        from backend.quality.checks import find_contract

        con = _mock_node("CON-1", "CONTRACT", content="interface spec")
        graph = _make_graph([con])
        graph.children_sync.return_value = [con]
        assert find_contract(graph, "MOD-1") == "interface spec"

    def test_contract_without_content(self) -> None:
        from backend.quality.checks import find_contract

        con = _mock_node("CON-1", "CONTRACT", content="")
        graph = _make_graph([con])
        graph.children_sync.return_value = [con]
        assert find_contract(graph, "MOD-1") == ""

    def test_non_contract_children_ignored(self) -> None:
        from backend.quality.checks import find_contract

        des = _mock_node("DES-1", "DESIGN", content="design content")
        graph = _make_graph([des])
        graph.children_sync.return_value = [des]
        assert find_contract(graph, "MOD-1") == ""


class TestQualityGapsForTypes:
    """Tests for quality_gaps_for_types."""

    def test_no_gaps(self) -> None:
        from backend.quality.checks import quality_gaps_for_types

        graph = _make_graph()
        analyser = MagicMock()
        analyser.analyse.return_value = []
        result = quality_gaps_for_types(graph, analyser, ["HLR"])
        assert result == {}

    def test_filters_non_quality_gaps(self) -> None:
        from backend.quality.checks import quality_gaps_for_types

        hlr = _mock_node("HLR-1", "HLR")
        graph = _make_graph([hlr])
        analyser = MagicMock()
        # Structural gap — should be excluded
        analyser.analyse.return_value = [
            Gap(
                type=GapType.UNCOVERED_PARA,
                priority=GapPriority.REQUIREMENTS_HLR,
                node_id="HLR-1",
                description="test",
            ),
        ]
        result = quality_gaps_for_types(graph, analyser, ["HLR"])
        assert result == {}

    def test_filters_duplicate_node_gaps(self) -> None:
        from backend.quality.checks import quality_gaps_for_types

        hlr = _mock_node("HLR-1", "HLR")
        graph = _make_graph([hlr])
        analyser = MagicMock()
        analyser.analyse.return_value = [
            Gap(
                type=GapType.DUPLICATE_NODE,
                priority=GapPriority.MAINTENANCE,
                node_id="HLR-1",
                description="test",
            ),
        ]
        result = quality_gaps_for_types(graph, analyser, ["HLR"])
        assert result == {}

    def test_includes_quality_gap_for_matching_type(self) -> None:
        from backend.quality.checks import quality_gaps_for_types

        hlr = _mock_node("HLR-1", "HLR")
        graph = _make_graph([hlr])
        analyser = MagicMock()
        analyser.analyse.return_value = [
            Gap(
                type=GapType.EMPTY_CONTENT,
                priority=GapPriority.MAINTENANCE,
                node_id="HLR-1",
                description="empty",
            ),
        ]
        result = quality_gaps_for_types(graph, analyser, ["HLR"])
        assert "HLR-1" in result
        assert len(result["HLR-1"]) == 1

    def test_excludes_gap_for_wrong_node_type(self) -> None:
        from backend.quality.checks import quality_gaps_for_types

        llr = _mock_node("LLR-1", "LLR")
        graph = _make_graph([llr])
        analyser = MagicMock()
        analyser.analyse.return_value = [
            Gap(
                type=GapType.EMPTY_CONTENT,
                priority=GapPriority.MAINTENANCE,
                node_id="LLR-1",
                description="empty",
            ),
        ]
        # Only asking for HLR types
        result = quality_gaps_for_types(graph, analyser, ["HLR"])
        assert result == {}

    def test_excludes_gap_for_missing_node(self) -> None:
        from backend.quality.checks import quality_gaps_for_types

        graph = _make_graph()  # No nodes
        analyser = MagicMock()
        analyser.analyse.return_value = [
            Gap(
                type=GapType.EMPTY_CONTENT,
                priority=GapPriority.MAINTENANCE,
                node_id="GHOST-1",
                description="gone",
            ),
        ]
        result = quality_gaps_for_types(graph, analyser, ["HLR"])
        assert result == {}

    def test_multiple_gaps_per_node(self) -> None:
        from backend.quality.checks import quality_gaps_for_types

        hlr = _mock_node("HLR-1", "HLR")
        graph = _make_graph([hlr])
        analyser = MagicMock()
        analyser.analyse.return_value = [
            Gap(
                type=GapType.EMPTY_CONTENT,
                priority=GapPriority.MAINTENANCE,
                node_id="HLR-1",
                description="empty",
            ),
            Gap(
                type=GapType.UNTITLED_NODE,
                priority=GapPriority.MAINTENANCE,
                node_id="HLR-1",
                description="untitled",
            ),
        ]
        result = quality_gaps_for_types(graph, analyser, ["HLR"])
        assert len(result["HLR-1"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 5. batch_steps
# ═══════════════════════════════════════════════════════════════════════════════


class TestBatchGetModel:
    """Tests for batch_steps._get_model."""

    def test_success(self) -> None:
        from backend.pipeline.batch_steps import _get_model

        flow = MagicMock()
        flow.config.llm.model_for_phase.return_value = "gpt-4"
        assert _get_model(flow, 5) == "gpt-4"

    def test_exception_returns_empty(self) -> None:
        from backend.pipeline.batch_steps import _get_model

        flow = MagicMock()
        flow.config.llm.model_for_phase.side_effect = RuntimeError
        assert _get_model(flow, 5) == ""


class TestSnapshotAndTrackNodes:
    """Tests for _snapshot_node_ids and _track_new_nodes."""

    def test_snapshot_node_ids(self) -> None:
        from backend.pipeline.batch_steps import _snapshot_node_ids

        hlr1 = _mock_node("HLR-1", "HLR")
        hlr2 = _mock_node("HLR-2", "HLR")
        llr = _mock_node("LLR-1", "LLR")
        flow = _make_flow(nodes=[hlr1, hlr2, llr])
        result = _snapshot_node_ids(flow, "HLR")
        assert result == {"HLR-1", "HLR-2"}

    def test_snapshot_empty(self) -> None:
        from backend.pipeline.batch_steps import _snapshot_node_ids

        flow = _make_flow()
        result = _snapshot_node_ids(flow, "HLR")
        assert result == set()

    def test_track_new_nodes_finds_new(self) -> None:
        from backend.pipeline.batch_steps import _track_new_nodes

        hlr1 = _mock_node("HLR-1", "HLR")
        hlr2 = _mock_node("HLR-2", "HLR")
        flow = _make_flow(nodes=[hlr1, hlr2])
        # Ensure _batch_new_node_ids is not a MagicMock auto-attribute
        del flow._batch_new_node_ids
        before = {"HLR-1"}
        new = _track_new_nodes(flow, "HLR", before)
        assert new == {"HLR-2"}
        assert flow._batch_new_node_ids == {"HLR-2"}

    def test_track_new_nodes_no_new(self) -> None:
        from backend.pipeline.batch_steps import _track_new_nodes

        hlr1 = _mock_node("HLR-1", "HLR")
        flow = _make_flow(nodes=[hlr1])
        before = {"HLR-1"}
        new = _track_new_nodes(flow, "HLR", before)
        assert new == set()

    def test_track_new_nodes_accumulates(self) -> None:
        from backend.pipeline.batch_steps import _track_new_nodes

        hlr1 = _mock_node("HLR-1", "HLR")
        hlr2 = _mock_node("HLR-2", "HLR")
        flow = _make_flow(nodes=[hlr1, hlr2])
        flow._batch_new_node_ids = {"PREV-1"}
        before = {"HLR-1"}
        new = _track_new_nodes(flow, "HLR", before)
        assert new == {"HLR-2"}
        assert flow._batch_new_node_ids == {"PREV-1", "HLR-2"}


class TestFallbackStructural:
    """Tests for _fallback_structural."""

    @pytest.mark.asyncio
    async def test_calls_structural(self) -> None:
        from backend.pipeline.batch_steps import _fallback_structural

        flow = MagicMock()
        expected = {"step_name": "structural", "deletions": 0}
        with patch(
            "backend.pipeline.steps.structural", new_callable=AsyncMock, return_value=expected
        ) as mock_s:
            result = await _fallback_structural(flow, 5)
        mock_s.assert_awaited_once_with(flow, 5)
        assert result == expected


class TestGroupUnrefinedByModule:
    """Tests for _group_unrefined_by_module (U8 fused phase 7)."""

    def test_groups_by_owning_module(self) -> None:
        from backend.pipeline.batch_steps import _group_unrefined_by_module

        hlr1 = _mock_node("HLR-1", "HLR", content="shall parse")
        hlr2 = _mock_node("HLR-2", "HLR", content="shall report")
        mod = _mock_node("MOD-1", "MODULE", content="module",
                         trace_to=["HLR-1", "HLR-2"])
        con = _mock_node("CON-1", "CONTRACT", content="contract")
        des = _mock_node("DES-1", "DESIGN", content="design", trace_to=["LLR-99"])

        flow = _make_flow(nodes=[hlr1, hlr2, mod, con, des])
        flow.graph.nodes_tracing_to = MagicMock(return_value=["MOD-1"])
        flow.graph.children_sync.return_value = [con, des]

        gap1 = MagicMock()
        gap1.node_id = "HLR-1"
        gap2 = MagicMock()
        gap2.node_id = "HLR-2"

        groups, ungrouped = _group_unrefined_by_module(flow, [gap1, gap2])
        assert ungrouped == []
        assert len(groups) == 1
        mod_id, context = groups[0]
        assert mod_id == "MOD-1"
        assert context["hlr_ids"] == ["HLR-1", "HLR-2"]
        assert context["contract"] is not None
        assert context["contract"]["node_id"] == "CON-1"
        assert [d["node_id"] for d in context["designs"]] == ["DES-1"]

    def test_hlr_not_found_skipped(self) -> None:
        from backend.pipeline.batch_steps import _group_unrefined_by_module

        flow = _make_flow()
        gap = MagicMock()
        gap.node_id = "GHOST"
        groups, ungrouped = _group_unrefined_by_module(flow, [gap])
        assert groups == []
        assert ungrouped == []

    def test_no_owning_module_is_ungrouped(self) -> None:
        """An HLR no MODULE traces to cannot join a fused batch — it is
        returned as ungrouped so the step routes it to per-gap dispatch."""
        from backend.pipeline.batch_steps import _group_unrefined_by_module

        hlr = _mock_node("HLR-1", "HLR", content="shall parse")
        flow = _make_flow(nodes=[hlr])
        flow.graph.nodes_tracing_to = MagicMock(return_value=[])
        gap = MagicMock()
        gap.node_id = "HLR-1"
        groups, ungrouped = _group_unrefined_by_module(flow, [gap])
        assert groups == []
        assert ungrouped == ["HLR-1"]


class TestBatchPhaseNoGapsEarlyExit:
    """Test all batch_phase* functions exit early with no gaps."""

    @pytest.mark.asyncio
    async def test_batch_phase3_no_gaps(self) -> None:
        from backend.pipeline.batch_steps import batch_phase3

        flow = _make_flow(gaps=[])
        result = await batch_phase3(flow, 3)
        assert result["step_name"] == "batch_phase3"
        assert result["deletions"] == 0

    @pytest.mark.asyncio
    async def test_batch_phase7_no_gaps(self) -> None:
        from backend.pipeline.batch_steps import batch_phase7

        flow = _make_flow(gaps=[])
        result = await batch_phase7(flow, 7)
        assert result["step_name"] == "batch_phase7"
        assert result["deletions"] == 0


class TestBatchPhaseExceptionFallback:
    """Test batch_phase* functions fall back on agent exception."""

    @pytest.mark.asyncio
    async def test_batch_phase3_exception_falls_back(self) -> None:
        from backend.pipeline.batch_steps import batch_phase3

        para = _mock_node("PARA-1", "PARA", content="text")
        gap = Gap(
            type=GapType.UNCOVERED_PARA,
            priority=GapPriority.REQUIREMENTS_HLR,
            node_id="PARA-1",
            description="test",
        )
        flow = _make_flow(nodes=[para], gaps=[gap])

        with (
            patch(
                "backend.pipeline.batch_steps._run_batch_agent",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "backend.pipeline.batch_steps._fallback_structural",
                new_callable=AsyncMock,
                return_value={"step_name": "structural", "deletions": 0},
            ) as mock_fb,
        ):
            result = await batch_phase3(flow, 3)
        mock_fb.assert_awaited_once()
        assert result["step_name"] == "structural"

    @pytest.mark.asyncio
    async def test_batch_phase7_exception_falls_back(self) -> None:
        from backend.pipeline.batch_steps import batch_phase7

        hlr = _mock_node("HLR-1", "HLR", content="text")
        mod = _mock_node("MOD-1", "MODULE", content="module", trace_to=["HLR-1"])
        gap = Gap(
            type=GapType.UNREFINED_HLR,
            priority=GapPriority.REQUIREMENTS_LLR,
            node_id="HLR-1",
            description="test",
        )
        flow = _make_flow(nodes=[hlr, mod])
        flow.graph.nodes_tracing_to = MagicMock(return_value=["MOD-1"])
        flow._collect_phase_gaps.return_value = [gap]

        with (
            patch(
                "backend.pipeline.batch_steps._run_batch_agent",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "backend.pipeline.batch_steps._fallback_structural",
                new_callable=AsyncMock,
                return_value={"step_name": "structural", "deletions": 0},
            ) as mock_fb,
        ):
            await batch_phase7(flow, 7)
        mock_fb.assert_awaited_once()


class TestBatchPhaseAgentSuccess:
    """Test batch_phase functions with successful agent invocation."""

    @pytest.mark.asyncio
    async def test_batch_phase7_agent_resolves_gaps(self) -> None:
        from backend.pipeline.batch_steps import batch_phase7

        hlr = _mock_node("HLR-1", "HLR", content="text")
        mod = _mock_node("MOD-1", "MODULE", content="module", trace_to=["HLR-1"])
        flow = _make_flow(nodes=[hlr, mod])
        flow.graph.nodes_tracing_to = MagicMock(return_value=["MOD-1"])
        pending = {"HLR-1"}
        flow._collect_phase_gaps.side_effect = lambda phase, skipped: [
            Gap(type=GapType.UNREFINED_HLR, priority=GapPriority.REQUIREMENTS_LLR,
                node_id=hid, description="test")
            for hid in sorted(pending)
        ]

        async def fake_agent(*args: object, **kwargs: object) -> int:
            pending.clear()
            return 3

        with patch(
            "backend.pipeline.batch_steps._run_batch_agent",
            new_callable=AsyncMock, side_effect=fake_agent,
        ):
            result = await batch_phase7(flow, 7)
        assert result["step_name"] == "batch_phase7"
        assert result["deletions"] == 0
