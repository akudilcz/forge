"""Hostile quality-judge verdicts — no fail-open, no single-verdict deletion.

The combined quality checker and the semantic duplicate checker are the two
LLM judges that can fail a phase or destroy graph content. A hostile judge
must never be able to (a) sneak a phase to completion by returning garbage,
or (b) delete requirement text off the back of one nondeterministic
DUPLICATE verdict.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.pipeline.flow import ForgeFlow
from backend.quality.combined_check import (
    UnjudgedQualityError,
    create_combined_quality_checker,
)
from backend.quality.semantic_duplicate_check import create_semantic_checker
from backend.tests.robustness.harness import (
    WellBehavedAgent,
    nodes_of,
    phase_status,
    run_phases_0_and_1,
    scripted_seams,
)

_WAIT = 60


class ScriptedLLM:
    """An ``ainvoke``-shaped LLM that replays a fixed sequence of responses."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    async def ainvoke(self, messages: Any) -> Any:
        if self.calls >= len(self.responses):
            raise AssertionError(
                f"LLM called {self.calls + 1} times but only "
                f"{len(self.responses)} responses were scripted"
            )
        text = self.responses[self.calls]
        self.calls += 1

        class _Msg:
            content = text

        return _Msg()


_ITEMS = [
    ("HLR-0001", "HLR", "First requirement", "The system shall do the first thing."),
    ("HLR-0002", "HLR", "Second requirement", "The system shall do the second thing."),
]


# ── Combined quality checker: unparseable output is never a pass ─────────────


class TestUnparseableCombinedVerdicts:
    async def test_garbage_twice_raises_unjudged_quality_error(self) -> None:
        llm = ScriptedLLM(["I am a teapot.", "Still a teapot."])
        checker = create_combined_quality_checker(llm)

        with pytest.raises(UnjudgedQualityError) as excinfo:
            await checker(_ITEMS)

        # Exactly one retry, then loud failure naming the unjudged nodes.
        assert llm.calls == 2
        assert set(excinfo.value.missing) == {"HLR-0001", "HLR-0002"}

    async def test_partial_garbage_retries_only_the_unjudged_node(self) -> None:
        first = "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS\nblah blah"
        llm = ScriptedLLM([first, "nonsense again"])
        checker = create_combined_quality_checker(llm)

        with pytest.raises(UnjudgedQualityError) as excinfo:
            await checker(_ITEMS)

        assert llm.calls == 2
        assert set(excinfo.value.missing) == {"HLR-0002"}

    async def test_always_negative_verdicts_produce_gaps_not_deletions(self) -> None:
        """An all-FAIL judge is detect-only: it yields gaps for every axis and
        never touches the graph."""
        all_fail = "\n".join(
            f"{nid}: ATOMIC=FAIL(bad) EARS=FAIL(bad) MATCH=FAIL(bad) SPECIFIC=FAIL(bad)"
            for nid, _, _, _ in _ITEMS
        )
        llm = ScriptedLLM([all_fail])
        checker = create_combined_quality_checker(llm)

        gaps = await checker(_ITEMS)

        assert len(gaps) == 8  # 2 nodes x 4 axes
        assert {g.node_id for g in gaps} == {"HLR-0001", "HLR-0002"}


class TestPipelineNeverFailsOpenOnUnjudgedQuality:
    async def test_unjudged_quality_error_halts_run_phase_loudly(
        self, flow: ForgeFlow, graph: ProjectGraph
    ) -> None:
        """When the combined checker gives up, the phase must NOT complete —
        it goes to awaiting_approval and the error propagates."""
        await run_phases_0_and_1(flow)
        with scripted_seams(WellBehavedAgent(graph)):
            await asyncio.wait_for(flow.run_phase(2), timeout=_WAIT)

        async def _hostile_checker(items: Any) -> Any:
            raise UnjudgedQualityError({"HLR-9999": {"ATOMIC"}})

        agent = WellBehavedAgent(graph)
        flow.pool.get_agent_for_gap.return_value = agent
        with (
            scripted_seams(agent),
            patch(
                "backend.quality.combined_check.create_combined_quality_checker",
                return_value=_hostile_checker,
            ),
        ):
            with pytest.raises(UnjudgedQualityError):
                await asyncio.wait_for(flow.run_phase(3), timeout=_WAIT)

        assert phase_status(flow, 3) == "awaiting_approval"


# ── Semantic dedup: one hostile DUPLICATE verdict never deletes ──────────────


async def _seed_two_paras(graph: ProjectGraph) -> tuple[GraphNode, GraphNode]:
    doc = GraphNode(
        node_id="DOCUMENT-0001",
        node_type=NodeType.DOCUMENT.value,
        title="Forge.md",
        content="spec",
        lifecycle=LifecycleState.ACTIVE,
    )
    await graph.add_node(doc)
    paras = []
    for i in (1, 2):
        node = GraphNode(
            node_id=f"PARA-000{i}",
            node_type=NodeType.PARA.value,
            title=f"Paragraph {i}",
            content=f"The system shall do thing number {i}.",
            parent_id=doc.node_id,
            lifecycle=LifecycleState.ACTIVE,
        )
        await graph.add_node(node)
        paras.append(node)
    return paras[0], paras[1]


class TestSemanticDedupDoubleConfirmation:
    async def test_single_duplicate_verdict_never_deletes(
        self, graph: ProjectGraph
    ) -> None:
        """First call says DUPLICATE, confirmation says UNIQUE — the node is
        kept, and the disagreement sticks so it is never re-litigated."""
        _, target = await _seed_two_paras(graph)
        llm = ScriptedLLM(["DUPLICATE - PARA-0001 same thing", "UNIQUE - distinct"])
        cache: dict[tuple[str, str], str] = {}
        check = create_semantic_checker(llm, graph, cache)

        deleted = await check(target.node_id, target.content, "[PARA-0001] thing one")

        assert deleted is False
        assert llm.calls == 2, "deletion path must consult a second verdict"
        assert graph.node_sync(target.node_id) is not None
        # Sticky: a later hostile re-run cannot get a fresh coin-flip.
        deleted_again = await check(
            target.node_id, target.content, "[PARA-0001] thing one"
        )
        assert deleted_again is False
        assert llm.calls == 2, "sticky verdict was re-litigated"

    async def test_deletion_requires_two_independent_duplicate_verdicts(
        self, graph: ProjectGraph
    ) -> None:
        """The documented destructive path: only 2/2 DUPLICATE deletes."""
        _, target = await _seed_two_paras(graph)
        llm = ScriptedLLM(["DUPLICATE - PARA-0001", "DUPLICATE - PARA-0001"])
        check = create_semantic_checker(llm, graph, {})

        deleted = await check(target.node_id, target.content, "[PARA-0001] thing one")

        assert deleted is True
        assert llm.calls == 2
        assert graph.node_sync(target.node_id) is None
        # The sibling named as the duplicate's counterpart survives.
        assert graph.node_sync("PARA-0001") is not None

    async def test_unparseable_verdict_keeps_the_node(
        self, graph: ProjectGraph
    ) -> None:
        """An empty/garbled judgment is not a verdict — the node is kept and
        no confirmation call is wasted on it."""
        _, target = await _seed_two_paras(graph)
        llm = ScriptedLLM([""])
        check = create_semantic_checker(llm, graph, {})

        deleted = await check(target.node_id, target.content, "[PARA-0001] thing one")

        assert deleted is False
        assert llm.calls == 1
        assert graph.node_sync(target.node_id) is not None

    async def test_hostile_always_duplicate_judge_cannot_empty_a_phase(
        self, flow: ForgeFlow, graph: ProjectGraph
    ) -> None:
        """Pipeline-level: even a judge that screams DUPLICATE at everything
        can only remove non-canonical siblings — the canonical node of each
        group survives and the run terminates."""
        await run_phases_0_and_1(flow)

        async def _always_duplicate(
            node_id: str, node_content: str, peers_text: str
        ) -> bool:
            await flow.graph.delete_node(node_id)
            return True

        agent = WellBehavedAgent(graph)
        flow.pool.get_agent_for_gap.return_value = agent
        with (
            scripted_seams(agent),
            patch(
                "backend.quality.semantic_duplicate_check.create_semantic_checker",
                return_value=_always_duplicate,
            ),
        ):
            await asyncio.wait_for(flow.run_phase(2), timeout=_WAIT)

        paras = nodes_of(graph, NodeType.PARA)
        assert paras, "the hostile dedup judge deleted every PARA"
        assert len(nodes_of(graph, NodeType.DOCUMENT)) == 1
