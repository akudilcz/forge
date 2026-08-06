"""Bounded resilience for the semantic dedup judge (specs/12 §7.4).

Live defect (online_statistics r4, trace .forge/llm_trace/trace.829201.jsonl,
call-e93cb8b792bf): the deepseek provider returned a body the HTTP client could
not parse as JSON. The raw ``json.JSONDecodeError`` —

    JSONDecodeError: Expecting value: line 157 column 1 (char 858)

— propagated out of ``llm.ainvoke`` inside ``_judge_once``, crashed
``run_semantic_check``, and halted the whole build at phase 8.

Required behaviour: on a parse failure the judge call is retried exactly once
(fresh LLM call). If the retry also fails to parse, the candidate is UNJUDGED —
the node is KEPT (never deleted on unparseable evidence), the failure is logged
at ERROR with the raw snippet, and the sweep CONTINUES to the next candidate.
At sweep end, unjudged candidates are reported loudly (count + node ids), but
the phase is not halted: an unjudged dedup verdict means "no deletion", the
safe outcome — unlike quality verdicts, which gate completion.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.quality.checks import run_combined_quality_check
from backend.quality.semantic_duplicate_check import (
    UnjudgedDedupError,
    create_semantic_checker,
)
from backend.quality.semantic_sweep import run_semantic_check


def _live_json_error() -> json.JSONDecodeError:
    """The exact error shape from the live trace: a multi-line garbage body
    whose parse fails at line 157 column 1 (char 858)."""
    doc = "abcd\n" * 155 + "x" * 82 + "\n" + "<html>truncated provider body"
    err = json.JSONDecodeError("Expecting value", doc, 858)
    # Pin the shape to the live failure before using it anywhere.
    assert str(err) == "Expecting value: line 157 column 1 (char 858)"
    return err


def _response(text: str) -> MagicMock:
    r = MagicMock()
    r.content = text
    return r


def _llm(*effects: Any) -> MagicMock:
    """LLM mock whose ainvoke yields each effect in order (exception instances
    are raised, message mocks are returned)."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(effects))
    return llm


def _graph() -> MagicMock:
    graph = MagicMock()
    graph.delete_node = AsyncMock()
    return graph


def _node(nid: str, ntype: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=nid,
        node_type=ntype,
        parent_id=kw.get("parent_id", ""),
        title=kw.get("title", ""),
        content=kw.get("content", ""),
        trace_to=kw.get("trace_to", []),
        properties=kw.get("properties", {}),
    )


def _flow(nodes: list[SimpleNamespace]) -> MagicMock:
    flow = MagicMock()
    flow.graph = MagicMock()
    flow.graph.all_nodes = MagicMock(return_value=nodes)
    flow.graph.node_sync = MagicMock(
        side_effect=lambda nid: next((n for n in nodes if n.node_id == nid), None)
    )
    flow.graph.children_sync = MagicMock(
        side_effect=lambda pid: [n for n in nodes if n.parent_id == pid]
    )
    flow._batch_new_node_ids = None
    flow.config = MagicMock()
    flow._set_phase_status = MagicMock()
    return flow


def _design_nodes() -> list[SimpleNamespace]:
    """Three phase-8 DESIGN nodes: one canonical + two dedup candidates whose
    content is lexically similar enough to pass the prescreen."""
    return [
        _node("DESIGN-0001", "DESIGN", content="The accumulator shall update the running mean."),
        _node("DESIGN-0002", "DESIGN", content="The accumulator shall update the running mean value."),
        _node("DESIGN-0003", "DESIGN", content="The accumulator shall update the running mean state."),
    ]


class TestJudgeParseRetry:
    """Checker-level behaviour: retry once, then UnjudgedDedupError."""

    async def test_malformed_response_is_retried_once_then_verdict_used(self) -> None:
        """First call raises the live JSONDecodeError; the fresh retry parses."""
        graph = _graph()
        llm = _llm(_live_json_error(), _response("UNIQUE - distinct obligation"))
        check = create_semantic_checker(llm, graph, {})

        deleted = await check(
            "DESIGN-0002",
            "The accumulator shall update the running mean.",
            "[DESIGN-0001] The accumulator shall update the running mean value.",
        )

        assert deleted is False
        assert llm.ainvoke.await_count == 2
        graph.delete_node.assert_not_awaited()

    async def test_retry_also_malformed_raises_unjudged_and_keeps_node(self) -> None:
        graph = _graph()
        llm = _llm(_live_json_error(), _live_json_error())
        check = create_semantic_checker(llm, graph, {})

        with pytest.raises(UnjudgedDedupError) as excinfo:
            await check(
                "DESIGN-0002",
                "The accumulator shall update the running mean.",
                "[DESIGN-0001] The accumulator shall update the running mean value.",
            )

        assert llm.ainvoke.await_count == 2
        graph.delete_node.assert_not_awaited()
        assert excinfo.value.node_id == "DESIGN-0002"
        # The raw body snippet is carried for the ERROR log at the sweep level.
        assert excinfo.value.raw_snippet

    async def test_parse_failure_on_confirmation_call_keeps_node(self) -> None:
        """DUPLICATE then two unparseable confirmation attempts → no deletion."""
        graph = _graph()
        llm = _llm(
            _response("DUPLICATE - same as DESIGN-0001"),
            _live_json_error(),
            _live_json_error(),
        )
        check = create_semantic_checker(llm, graph, {})

        with pytest.raises(UnjudgedDedupError):
            await check(
                "DESIGN-0002",
                "The accumulator shall update the running mean.",
                "[DESIGN-0001] The accumulator shall update the running mean value.",
            )

        graph.delete_node.assert_not_awaited()

    async def test_non_parse_errors_still_propagate_unretried(self) -> None:
        """Only JSON parse failures get the bounded retry — anything else is
        loud immediately (no silent fallback)."""
        graph = _graph()
        llm = _llm(RuntimeError("connection refused"))
        check = create_semantic_checker(llm, graph, {})

        with pytest.raises(RuntimeError, match="connection refused"):
            await check(
                "DESIGN-0002",
                "The accumulator shall update the running mean.",
                "[DESIGN-0001] The accumulator shall update the running mean value.",
            )

        assert llm.ainvoke.await_count == 1


class TestSweepContinuesPastUnjudged:
    """Sweep-level behaviour: the live crash, and its required replacement."""

    async def test_malformed_judge_response_mid_sweep_does_not_crash_the_phase(
        self,
    ) -> None:
        """The live failure: one candidate's judge calls both return unparseable
        bodies. The sweep must keep the node, continue to the next candidate,
        and finish without raising."""
        nodes = _design_nodes()
        graph = _graph()
        # Candidate 1 (DESIGN-0002): both attempts unparseable → UNJUDGED.
        # Candidate 2 (DESIGN-0003): judged UNIQUE normally.
        llm = _llm(
            _live_json_error(),
            _live_json_error(),
            _response("UNIQUE - distinct obligation"),
        )
        flow = _flow(nodes)
        flow._build_semantic_checker.return_value = create_semantic_checker(
            llm, graph, {}
        )

        deleted = await run_semantic_check(flow, 8)

        assert deleted == 0
        assert llm.ainvoke.await_count == 3
        graph.delete_node.assert_not_awaited()

    async def test_unjudged_candidates_are_reported_loudly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ERROR log with the raw snippet per candidate, plus a loud end-of-sweep
        report carrying the count and node ids."""
        records: list[tuple[str, str, str]] = []

        from backend.server import forge_logger as fl_module

        real_emit = fl_module.forge_logger.emit

        def capture(
            level: str, cat: Any, msg: str, detail: str | None = None, **meta: Any
        ) -> None:
            records.append((level, str(cat), msg))
            real_emit(level, cat, msg, detail, **meta)

        monkeypatch.setattr(fl_module.forge_logger, "emit", capture)

        nodes = _design_nodes()
        llm = _llm(
            _live_json_error(),
            _live_json_error(),
            _response("UNIQUE - distinct obligation"),
        )
        flow = _flow(nodes)
        flow._build_semantic_checker.return_value = create_semantic_checker(
            llm, _graph(), {}
        )

        await run_semantic_check(flow, 8)

        errors = [r for r in records if r[0] == "ERROR"]
        assert errors, "no ERROR record for the unjudged candidate"
        assert any("DESIGN-0002" in msg for _, _, msg in errors)
        # End-of-sweep report: count + node ids.
        assert any(
            "1" in msg and "DESIGN-0002" in msg and "unjudged" in msg.lower()
            for _, _, msg in errors
        )

    async def test_sweep_with_retry_success_deletes_nothing_extra(self) -> None:
        """A transient parse failure healed by the single retry leaves the sweep
        outcome identical to a clean run."""
        nodes = _design_nodes()
        graph = _graph()
        llm = _llm(
            _live_json_error(),
            _response("UNIQUE - distinct obligation"),
            _response("UNIQUE - distinct obligation"),
        )
        flow = _flow(nodes)
        flow._build_semantic_checker.return_value = create_semantic_checker(
            llm, graph, {}
        )

        deleted = await run_semantic_check(flow, 8)

        assert deleted == 0
        assert llm.ainvoke.await_count == 3
        graph.delete_node.assert_not_awaited()
        flow._set_phase_status.assert_not_called()


class TestCombinedQualityPathUnaffected:
    """The combined quality check gates completion — a double parse failure
    there must still propagate loudly (retry-once already lives in
    ``run_combined_quality_check``)."""

    async def test_json_decode_error_is_retried_once_then_propagates(self) -> None:
        flow = _flow(
            [_node("LLR-1", "LLR", title="Store files", content="The system shall store files.")]
        )
        flow._quality_verdict_cache = {}
        checker = AsyncMock(side_effect=[_live_json_error(), _live_json_error()])
        with (
            patch("backend.agents.factory.build_llm", return_value=MagicMock()),
            patch(
                "backend.quality.combined_check.create_combined_quality_checker",
                return_value=checker,
            ),
        ):
            with pytest.raises(json.JSONDecodeError, match="line 157 column 1"):
                await run_combined_quality_check(flow, phase=7)
        assert checker.await_count == 2
