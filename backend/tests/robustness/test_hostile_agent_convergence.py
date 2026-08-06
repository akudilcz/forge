"""Hostile-agent convergence — the pipeline must terminate and fail loudly.

The scripted agent on the dispatch seam misbehaves in four documented ways:
returns garbage and creates nothing, creates the WRONG node type, creates
duplicate nodes on every call, and deletes/mutates unrelated nodes. In every
case ``run_phase`` must terminate within its convergence guards (structural
circuit breaker ``_MAX_GAP_ATTEMPTS=3`` counting every dispatch of a
still-open gap, runner ``_max_cycles=12``), must NOT mark the phase
complete when its gap is still open, and must leave graph
invariants intact (no orphans, no ID collisions, untouched nodes unchanged).

``asyncio.wait_for`` is the belt on top of the global pytest timeout: a
non-converging loop fails the test rather than hanging it.
"""

from __future__ import annotations

import asyncio

from backend.analysis.gaps import Gap
from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType
from backend.pipeline.batch_steps import _MAX_BATCH_ATTEMPTS
from backend.pipeline.flow import ForgeFlow
from backend.pipeline.structural_loop import _MAX_GAP_ATTEMPTS
from backend.tests.robustness.harness import (
    ScriptedAgentBase,
    WellBehavedAgent,
    assert_no_orphans,
    assert_unique_node_ids,
    nodes_of,
    phase_status,
    run_phases_0_and_1,
    scripted_seams,
)

_WAIT = 60  # seconds — belt against a spinning pipeline


def _snapshot(graph: ProjectGraph) -> dict[str, tuple[str, int, str | None]]:
    """Record (content, version, parent) per node — the tamper-evidence seal."""
    return {
        n.node_id: (n.content or "", n.version, n.parent_id)
        for n in graph.all_nodes()
    }


async def _run_phases_0_to_2(flow: ForgeFlow, graph: ProjectGraph) -> None:
    await run_phases_0_and_1(flow)
    with scripted_seams(WellBehavedAgent(graph)):
        await asyncio.wait_for(flow.run_phase(2), timeout=_WAIT)
    assert phase_status(flow, 2) == "complete"


# ── Hostile behaviours ───────────────────────────────────────────────────────


class GarbageAgent(ScriptedAgentBase):
    """Returns confident garbage text; never touches the graph."""

    async def resolve(self, gap: Gap) -> str:
        return "OK: I have definitely chunked the document as requested."


class WrongTypeAgent(ScriptedAgentBase):
    """Answers UNCHUNKED_DOCUMENT by creating HLRs — the wrong node type."""

    async def resolve(self, gap: Gap) -> str:
        node = await self.add(
            NodeType.HLR,
            "Bogus requirement",
            "The system shall be excellent in every respect.",
            parent_id=gap.node_id,
            trace_to=[],
            properties={},
        )
        return f"OK: created {node.node_id}"


class DuplicateSpamAgent(ScriptedAgentBase):
    """Creates a byte-identical HLR for every gap it is handed."""

    async def resolve(self, gap: Gap) -> str:
        node = await self.add(
            NodeType.HLR,
            f"Requirement for {gap.node_id}",
            "The system shall do the thing.",
            parent_id=gap.node_id,
            trace_to=[gap.node_id],
            properties={},
        )
        return f"OK: created {node.node_id}"


class VandalMutatorAgent(ScriptedAgentBase):
    """Never resolves its gap; mutates an unrelated node (the DOCUMENT)."""

    def __init__(self, graph: ProjectGraph, victim_id: str) -> None:
        super().__init__(graph)
        self.victim_id = victim_id

    async def resolve(self, gap: Gap) -> str:
        await self.graph.update_node(
            self.victim_id,
            "VANDALISED CONTENT",
            None,
            "hostile-agent",
            "vandalism",
        )
        return "OK: improved an unrelated node instead"


class VandalDeleterAgent(ScriptedAgentBase):
    """Never resolves its gap; deletes a sibling PARA on every call."""

    async def resolve(self, gap: Gap) -> str:
        victims = [
            n for n in nodes_of(self.graph, NodeType.PARA) if n.node_id != gap.node_id
        ]
        if not victims:
            return "nothing left to destroy"
        await self.graph.delete_node(victims[-1].node_id)
        return f"OK: deleted {victims[-1].node_id}"


# ── 1. Garbage agent: gap never closes, circuit breaker abandons it ─────────


class TestGarbageAgent:
    async def test_terminates_fails_loudly_and_leaves_graph_untouched(
        self, flow: ForgeFlow, graph: ProjectGraph
    ) -> None:
        await run_phases_0_and_1(flow)
        seal = _snapshot(graph)

        with scripted_seams(GarbageAgent(graph)) as agent:
            await asyncio.wait_for(flow.run_phase(2), timeout=_WAIT)

        # Circuit breaker: exactly _MAX_GAP_ATTEMPTS dispatches, then abandoned.
        assert len(agent.seen) == _MAX_GAP_ATTEMPTS
        # Loud failure: the phase is NOT complete — the open gap fails the audit.
        assert phase_status(flow, 2) == "awaiting_approval"
        # Graph untouched: same nodes, same content, same versions.
        assert _snapshot(graph) == seal
        assert nodes_of(graph, NodeType.PARA) == []


# ── 2. Wrong node type: fake progress must still hit a hard stop ────────────


class TestWrongNodeType:
    async def test_fake_progress_is_bounded_by_the_circuit_breaker(
        self, flow: ForgeFlow, graph: ProjectGraph
    ) -> None:
        """Creating wrong-typed nodes looks like 'progress' (the graph grows),
        but the gap never closes. The circuit breaker must count every
        dispatch of a still-open gap — this exact scenario used to spin the
        structural loop unboundedly (LangGraph enforces no default recursion
        limit) until API quota exhaustion.

        The resolution certificate makes this provable, not just bounded:
        resolution requires the dispatched gap's own analyser check to clear,
        so a wrong-typed write is recorded as no_change on every single
        dispatch — never once as an improvement."""
        from backend.core.work_queue import work_queue

        history_before = len(work_queue.all_history)
        await run_phases_0_and_1(flow)

        with scripted_seams(WrongTypeAgent(graph)) as agent:
            await asyncio.wait_for(flow.run_phase(2), timeout=_WAIT)

        # Bounded: abandoned at the per-gap dispatch cap, not at the quota.
        assert len(agent.seen) == _MAX_GAP_ATTEMPTS
        assert phase_status(flow, 2) == "awaiting_approval"
        # The gap is still open and honestly reported: no PARA was created.
        assert nodes_of(graph, NodeType.PARA) == []
        assert len(nodes_of(graph, NodeType.HLR)) == _MAX_GAP_ATTEMPTS
        assert_no_orphans(graph)
        assert_unique_node_ids(graph)
        # Certificate: every dispatch that grew the graph without closing the
        # gap is recorded as no_change — fake progress is never certified.
        chunk_actions = [
            e
            for e in work_queue.all_history[history_before:]
            if e["category"] == "UNCHUNKED_DOCUMENT"
        ]
        assert len(chunk_actions) == _MAX_GAP_ATTEMPTS
        assert all(e["outcome"] == "no_change" for e in chunk_actions), (
            "a wrong-node-type write was recorded as improving the gap — "
            "the version-sum fake-progress signal is back"
        )


# ── 3. Duplicate spam: converges, IDs stay collision-free ───────────────────


class TestDuplicateSpam:
    async def test_duplicates_do_not_break_termination_or_id_allocation(
        self, flow: ForgeFlow, graph: ProjectGraph
    ) -> None:
        await _run_phases_0_to_2(flow, graph)

        with scripted_seams(DuplicateSpamAgent(graph)) as agent:
            flow.pool.get_agent_for_gap.return_value = agent
            await asyncio.wait_for(flow.run_phase(3), timeout=_WAIT)

        # One resolution per PARA gap; every HLR is byte-identical spam.
        assert len(agent.seen) == 3
        hlrs = nodes_of(graph, NodeType.HLR)
        assert len(hlrs) == 3
        assert len({h.content for h in hlrs}) == 1
        assert_unique_node_ids(graph)
        assert_no_orphans(graph)


# ── 4. Vandals: mutating/deleting unrelated nodes never corrupts the graph ──


class TestVandalMutator:
    async def test_mutation_spin_is_bounded_and_spares_bystanders(
        self, flow: ForgeFlow, graph: ProjectGraph
    ) -> None:
        """Version bumps look like progress, but no gap ever closes — the
        per-gap dispatch cap must bound the spin, and PARAs and PROJECT must
        come out untouched."""
        await _run_phases_0_to_2(flow, graph)
        document = nodes_of(graph, NodeType.DOCUMENT)[0]
        bystanders = {
            nid: rec
            for nid, rec in _snapshot(graph).items()
            if nid != document.node_id
        }

        with scripted_seams(VandalMutatorAgent(graph, document.node_id)) as agent:
            flow.pool.get_agent_for_gap.return_value = agent
            await asyncio.wait_for(flow.run_phase(3), timeout=_WAIT)

        # Three PARA gaps per batch attempt, bounded by _MAX_BATCH_ATTEMPTS;
        # exhausted chunks then fall back to per-gap structural dispatch
        # (specs/13 §Batch prompts), itself bounded by _MAX_GAP_ATTEMPTS.
        assert len(agent.seen) == 3 * _MAX_BATCH_ATTEMPTS + 3 * _MAX_GAP_ATTEMPTS
        assert phase_status(flow, 3) == "awaiting_approval"
        # Only the scripted victim changed — every bystander is untouched.
        after = _snapshot(graph)
        assert {nid: after[nid] for nid in bystanders} == bystanders
        assert nodes_of(graph, NodeType.HLR) == []
        assert_no_orphans(graph)


class TestVandalDeleter:
    async def test_deletions_terminate_fail_the_audit_and_leave_no_orphans(
        self, flow: ForgeFlow, graph: ProjectGraph
    ) -> None:
        await _run_phases_0_to_2(flow, graph)
        assert len(nodes_of(graph, NodeType.PARA)) == 3

        with scripted_seams(VandalDeleterAgent(graph)) as agent:
            flow.pool.get_agent_for_gap.return_value = agent
            await asyncio.wait_for(flow.run_phase(3), timeout=_WAIT)

        # Loud failure: destroying the PARAs reopens upstream criteria, so the
        # cumulative audit blocks completion.
        assert phase_status(flow, 3) == "awaiting_approval"
        # Roots survive and nothing is stranded.
        assert len(nodes_of(graph, NodeType.PROJECT)) == 1
        assert len(nodes_of(graph, NodeType.DOCUMENT)) == 1
        assert_no_orphans(graph)
        assert_unique_node_ids(graph)
