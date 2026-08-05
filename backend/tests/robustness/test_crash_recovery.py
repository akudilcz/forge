"""Mid-phase crash recovery — the DB pair is the whole persistent state.

FORGE's persistence contract (see ``test_phase_resume.py``): the graph DB at
``db_path`` plus the workspace directory are everything. If the process dies
mid-dispatch, a FRESH ``ForgeBuilder`` over the same pair — after the server
startup ritual ``phase_store.reset_active_to_pending()`` — must resume the
phase and complete it with a well-behaved agent. Fully offline: the agent
seam is scripted and the crash is a ``BaseException`` that no pipeline layer
may swallow (mirroring a process kill, which no ``except Exception`` sees).
"""

from __future__ import annotations

import gc
from pathlib import Path

import pytest

from backend.analysis.gaps import Gap
from backend.config.models import ForgeConfig, ProjectConfig
from backend.core.forge_builder import ForgeBuilder
from backend.core.phase_store import PhaseStore
from backend.graph.models import NodeType
from backend.pipeline.flow import ForgeFlow
from backend.tests.robustness.harness import (
    ScriptedAgentBase,
    WellBehavedAgent,
    assert_no_orphans,
    assert_unique_node_ids,
    nodes_of,
    phase_status,
    scripted_seams,
    write_spec,
)


class SimulatedProcessKill(BaseException):
    """A crash no ``except Exception`` handler may swallow."""


class CrashingAgent(ScriptedAgentBase):
    """Dies mid-dispatch, optionally after doing partial work first."""

    def __init__(self, graph, paras_before_crash: int) -> None:  # type: ignore[no-untyped-def]
        super().__init__(graph)
        self.paras_before_crash = paras_before_crash

    async def resolve(self, gap: Gap) -> str:
        for i in range(self.paras_before_crash):
            await self.add(
                NodeType.PARA,
                f"Partial paragraph {i + 1}",
                "The system shall survive a mid-write crash without data loss.",
                parent_id=gap.node_id,
                trace_to=[],
                properties={"para_type": "requirement"},
            )
        raise SimulatedProcessKill("process killed mid-dispatch")


async def _build_flow(workspace: Path, db_path: Path) -> ForgeFlow:
    config = ForgeConfig(
        project=ProjectConfig(
            name="crash-recovery", forgemd="forge.md", workspace_dir=str(workspace)
        )
    )
    config.llm.keyless = True
    builder = ForgeBuilder(config=config, workspace=workspace, db_path=db_path)
    return await builder.build()


async def _crash_session_a(
    workspace: Path, db_path: Path, paras_before_crash: int
) -> None:
    """Session A: phases 0-1 succeed, then the agent dies inside phase 2."""
    flow_a = await _build_flow(workspace, db_path)
    await flow_a.run_phase(0)
    await flow_a.run_phase(1)
    with scripted_seams(CrashingAgent(flow_a.graph, paras_before_crash)):
        with pytest.raises(SimulatedProcessKill):
            await flow_a.run_phase(2)

    # The crash leaves the phase stranded 'active' — the honest crash residue.
    assert phase_status(flow_a, 2) == "active"

    # Simulate process end: no teardown API exists; every ProjectGraph and
    # PhaseStore operation opens its own connection, so dropping refs is the
    # full shutdown (same rationale as test_phase_resume.py).
    del flow_a
    gc.collect()


class TestCrashMidDispatchLeavesResumableState:
    async def test_fresh_builder_resumes_and_completes_the_phase(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "workspace"
        write_spec(workspace)
        db_path = tmp_path / "forge.db"

        await _crash_session_a(workspace, db_path, paras_before_crash=0)

        # ── Session B: a brand-new builder over the SAME workspace + db ──
        flow_b = await _build_flow(workspace, db_path)

        # Restored state: completed prefix intact, crash residue visible.
        assert phase_status(flow_b, 0) == "complete"
        assert phase_status(flow_b, 1) == "complete"
        assert phase_status(flow_b, 2) == "active"
        assert len(nodes_of(flow_b.graph, NodeType.PROJECT)) == 1
        assert len(nodes_of(flow_b.graph, NodeType.DOCUMENT)) == 1
        assert nodes_of(flow_b.graph, NodeType.PARA) == []

        # Server startup ritual (lifespan.py): stranded 'active' → 'pending'.
        flow_b.phase_store.reset_active_to_pending()
        assert phase_status(flow_b, 2) == "pending"
        assert phase_status(flow_b, 1) == "complete", (
            "reset_active_to_pending must not disturb completed phases"
        )

        with scripted_seams(WellBehavedAgent(flow_b.graph)) as agent:
            await flow_b.run_phase(2)

        assert phase_status(flow_b, 2) == "complete"
        assert len(agent.seen) == 1, "resume did not dispatch the reopened gap"
        assert len(nodes_of(flow_b.graph, NodeType.PARA)) == 3
        assert_unique_node_ids(flow_b.graph)
        assert_no_orphans(flow_b.graph)

    async def test_partial_work_survives_and_is_not_duplicated_on_resume(
        self, tmp_path: Path
    ) -> None:
        """The agent wrote one PARA before dying. The fresh builder must see
        it, must not lose it, and must not author a duplicate batch on top."""
        workspace = tmp_path / "workspace"
        write_spec(workspace)
        db_path = tmp_path / "forge.db"

        await _crash_session_a(workspace, db_path, paras_before_crash=1)

        # Partial work is durable: it is in SQLite before the crash surfaces.
        store = PhaseStore(str(db_path))
        record = store.get(2)
        assert record is not None
        assert record["status"] == "active"

        flow_b = await _build_flow(workspace, db_path)
        paras_restored = nodes_of(flow_b.graph, NodeType.PARA)
        assert len(paras_restored) == 1, "partial pre-crash work was lost"

        flow_b.phase_store.reset_active_to_pending()
        with scripted_seams(WellBehavedAgent(flow_b.graph)) as agent:
            await flow_b.run_phase(2)

        # The surviving PARA already satisfies UNCHUNKED_DOCUMENT, so the
        # resume is a no-op completion — no re-dispatch, no duplicates.
        assert phase_status(flow_b, 2) == "complete"
        assert agent.seen == []
        paras_after = nodes_of(flow_b.graph, NodeType.PARA)
        assert [p.node_id for p in paras_after] == [
            p.node_id for p in paras_restored
        ]
        assert_no_orphans(flow_b.graph)
