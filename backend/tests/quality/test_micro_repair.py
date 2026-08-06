"""Behavioural tests for the batched micro-repair pre-pass (specs/12 §7.4).

N >= 3 same-family title/wording gaps must be repaired with ONE structured
LLM call, with per-node fixes applied through the graph engine under the
same write-time invariants the tools enforce. Every failure mode is loud
and falls back to the per-gap dispatch path: dropped/garbled node lines,
invariant-rejected fixes, and transport failures all leave the affected
gaps open. Resolution is certified per-gap by the analyser re-check.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.quality import micro_repair
from backend.quality.micro_repair import apply_micro_repair_batches


def _gap(gap_type: GapType, node_id: str) -> Gap:
    return Gap(
        type=gap_type,
        priority=GapPriority.MAINTENANCE,
        node_id=node_id,
        description=f"{gap_type.value} on {node_id}",
    )


def _node(node_id: str, node_type: str, title: str, content: str) -> MagicMock:
    node = MagicMock()
    node.node_id = node_id
    node.node_type = node_type
    node.title = title
    node.content = content
    node.parent_id = "MOD-0001"
    return node


def _flow(nodes: dict[str, MagicMock], open_after: list[Gap]) -> MagicMock:
    """Mock flow: graph lookups + an analyser whose fresh scan reports *open_after*."""
    flow = MagicMock()
    flow.graph.node_sync = MagicMock(side_effect=lambda nid: nodes.get(nid))
    flow.graph.children_sync = MagicMock(return_value=list(nodes.values()))
    flow.graph.update_node = AsyncMock()
    flow._analyser.analyse = MagicMock(return_value=list(open_after))
    return flow


def _llm(text: str) -> MagicMock:
    llm = MagicMock()
    response = MagicMock()
    response.content = text
    llm.ainvoke = AsyncMock(return_value=response)
    return llm


def _wording_fixture() -> tuple[list[Gap], dict[str, MagicMock]]:
    gaps = [_gap(GapType.MALFORMED_REQUIREMENT, f"HLR-000{i}") for i in (1, 2, 3)]
    nodes = {
        f"HLR-000{i}": _node(f"HLR-000{i}", "HLR", f"Title {i}", f"Bad wording {i}.")
        for i in (1, 2, 3)
    }
    return gaps, nodes


_WORDING_RESPONSE = (
    "HLR-0001: The system shall sort the input list ascending.\n"
    "HLR-0002: The system shall reject boolean values loudly.\n"
    "HLR-0003: The system shall return an empty list for empty input.\n"
)


@pytest.mark.asyncio
class TestBatchedRepairHappyPath:
    async def test_three_wording_gaps_use_exactly_one_llm_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gaps, nodes = _wording_fixture()
        flow = _flow(nodes, open_after=[])
        llm = _llm(_WORDING_RESPONSE)
        monkeypatch.setattr(micro_repair, "_build_repair_llm", lambda f: llm)

        remaining = await apply_micro_repair_batches(flow, gaps)

        assert llm.ainvoke.await_count == 1
        assert remaining == []
        assert flow.graph.update_node.await_count == 3

    async def test_wording_fix_is_applied_as_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gaps, nodes = _wording_fixture()
        flow = _flow(nodes, open_after=[])
        monkeypatch.setattr(
            micro_repair, "_build_repair_llm", lambda f: _llm(_WORDING_RESPONSE)
        )

        await apply_micro_repair_batches(flow, gaps)

        first_call = flow.graph.update_node.await_args_list[0]
        assert first_call.kwargs["content"] == (
            "The system shall sort the input list ascending."
        )
        assert first_call.kwargs["changed_by"] == "micro-repair-batch"

    async def test_three_title_gaps_apply_titles_not_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gaps = [_gap(GapType.VAGUE_TITLE, f"LLR-000{i}") for i in (1, 2, 3)]
        nodes = {
            f"LLR-000{i}": _node(
                f"LLR-000{i}", "LLR", "Handle Cases", "The system shall act."
            )
            for i in (1, 2, 3)
        }
        flow = _flow(nodes, open_after=[])
        llm = _llm(
            "LLR-0001: Sort Input List\n"
            "LLR-0002: Reject Boolean Values\n"
            "LLR-0003: Return Empty List\n"
        )
        monkeypatch.setattr(micro_repair, "_build_repair_llm", lambda f: llm)

        remaining = await apply_micro_repair_batches(flow, gaps)

        assert remaining == []
        assert llm.ainvoke.await_count == 1
        titles = [c.kwargs["title"] for c in flow.graph.update_node.await_args_list]
        assert titles == ["Sort Input List", "Reject Boolean Values", "Return Empty List"]
        assert all(c.kwargs["content"] is None for c in flow.graph.update_node.await_args_list)


@pytest.mark.asyncio
class TestParentCollisionBatching:
    """TITLE_COLLIDES_WITH_PARENT joins the title batch family."""

    def _fixture(self) -> tuple[list[Gap], MagicMock]:
        gaps = [_gap(GapType.TITLE_COLLIDES_WITH_PARENT, f"HLR-000{i}") for i in (1, 2, 3)]
        parent = _node("MOD-0001", "MODULE", "Sorting Module", "Module content.")
        parent.parent_id = None
        nodes = {
            f"HLR-000{i}": _node(
                f"HLR-000{i}", "HLR", "Sorting Module", "The system shall sort."
            )
            for i in (1, 2, 3)
        }
        children = list(nodes.values())
        nodes["MOD-0001"] = parent
        flow = MagicMock()
        flow.graph.node_sync = MagicMock(side_effect=lambda nid: nodes.get(nid))
        flow.graph.children_sync = MagicMock(return_value=children)
        flow.graph.update_node = AsyncMock()
        flow._analyser.analyse = MagicMock(return_value=[])
        return gaps, flow

    async def test_three_parent_collision_gaps_use_one_llm_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gaps, flow = self._fixture()
        llm = _llm(
            "HLR-0001: Sort Input Ascending\n"
            "HLR-0002: Sort Stability Guarantee\n"
            "HLR-0003: Sort Error Handling\n"
        )
        monkeypatch.setattr(micro_repair, "_build_repair_llm", lambda f: llm)

        remaining = await apply_micro_repair_batches(flow, gaps)

        assert remaining == []
        assert llm.ainvoke.await_count == 1
        assert flow.graph.update_node.await_count == 3

    async def test_batch_prompt_carries_parent_title(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The payload names the parent title so a rewrite cannot re-collide."""
        gaps, flow = self._fixture()
        llm = _llm(
            "HLR-0001: Sort Input Ascending\n"
            "HLR-0002: Sort Stability Guarantee\n"
            "HLR-0003: Sort Error Handling\n"
        )
        monkeypatch.setattr(micro_repair, "_build_repair_llm", lambda f: llm)

        await apply_micro_repair_batches(flow, gaps)

        messages = llm.ainvoke.await_args.args[0]
        payload = messages[1].content
        assert "Sorting Module" in payload
        assert "parent_title (must stay distinct from)" in payload

    async def test_fix_that_still_collides_with_parent_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A returned title equal to the parent's fails the write-time
        invariant — the gap stays open for per-gap dispatch."""
        gaps, flow = self._fixture()
        llm = _llm(
            "HLR-0001: Sorting Module\n"  # re-collides with parent title
            "HLR-0002: Sort Stability Guarantee\n"
            "HLR-0003: Sort Error Handling\n"
        )
        monkeypatch.setattr(micro_repair, "_build_repair_llm", lambda f: llm)

        remaining = await apply_micro_repair_batches(flow, gaps)

        assert remaining == [gaps[0]]
        titles = [c.kwargs["title"] for c in flow.graph.update_node.await_args_list]
        assert "Sorting Module" not in titles
        assert flow.graph.update_node.await_count == 2


@pytest.mark.asyncio
class TestThresholdAndScope:
    async def test_below_threshold_makes_no_llm_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gaps = [_gap(GapType.MALFORMED_REQUIREMENT, "HLR-0001"),
                _gap(GapType.MALFORMED_REQUIREMENT, "HLR-0002")]
        flow = _flow({}, open_after=list(gaps))
        built: list[Any] = []

        def track_build(f: Any) -> MagicMock:
            built.append(f)
            return _llm("")

        monkeypatch.setattr(micro_repair, "_build_repair_llm", track_build)

        remaining = await apply_micro_repair_batches(flow, gaps)

        assert remaining == gaps
        assert built == []
        flow.graph.update_node.assert_not_awaited()

    async def test_non_batchable_gap_types_pass_through_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gaps = [_gap(GapType.EMPTY_CONTENT, f"MOD-000{i}") for i in (1, 2, 3, 4)]
        flow = _flow({}, open_after=list(gaps))
        monkeypatch.setattr(
            micro_repair,
            "_build_repair_llm",
            lambda f: (_ for _ in ()).throw(AssertionError("must not build an LLM")),
        )

        remaining = await apply_micro_repair_batches(flow, gaps)

        assert remaining == gaps


@pytest.mark.asyncio
class TestLoudFallbacks:
    async def test_dropped_node_line_leaves_that_gap_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gaps, nodes = _wording_fixture()
        # HLR-0003 never gets a line; its analyser gap stays open.
        flow = _flow(nodes, open_after=[gaps[2]])
        llm = _llm(
            "HLR-0001: The system shall sort the input list ascending.\n"
            "HLR-0002: The system shall reject boolean values loudly.\n"
        )
        monkeypatch.setattr(micro_repair, "_build_repair_llm", lambda f: llm)

        remaining = await apply_micro_repair_batches(flow, gaps)

        assert remaining == [gaps[2]]
        assert flow.graph.update_node.await_count == 2

    async def test_invariant_rejected_fix_is_not_written_and_gap_stays(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gaps, nodes = _wording_fixture()
        flow = _flow(nodes, open_after=[gaps[1]])
        llm = _llm(
            "HLR-0001: The system shall sort the input list ascending.\n"
            "HLR-0002: Sorting should probably work.\n"  # violates wording invariant
            "HLR-0003: The system shall return an empty list for empty input.\n"
        )
        monkeypatch.setattr(micro_repair, "_build_repair_llm", lambda f: llm)

        remaining = await apply_micro_repair_batches(flow, gaps)

        assert remaining == [gaps[1]]
        written = [c.kwargs["content"] for c in flow.graph.update_node.await_args_list]
        assert "Sorting should probably work." not in written
        assert flow.graph.update_node.await_count == 2

    async def test_transport_failure_leaves_all_gaps_for_per_gap_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gaps, nodes = _wording_fixture()
        flow = _flow(nodes, open_after=list(gaps))
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=ConnectionError("boom"))
        monkeypatch.setattr(micro_repair, "_build_repair_llm", lambda f: llm)

        remaining = await apply_micro_repair_batches(flow, gaps)

        assert remaining == gaps
        flow.graph.update_node.assert_not_awaited()

    async def test_certificate_keeps_gap_the_analyser_still_reports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An applied write is not resolution — the analyser re-check is."""
        gaps, nodes = _wording_fixture()
        # All three fixes apply, but the analyser still reports HLR-0001.
        flow = _flow(nodes, open_after=[gaps[0]])
        monkeypatch.setattr(
            micro_repair, "_build_repair_llm", lambda f: _llm(_WORDING_RESPONSE)
        )

        remaining = await apply_micro_repair_batches(flow, gaps)

        assert remaining == [gaps[0]]
        assert flow.graph.update_node.await_count == 3
