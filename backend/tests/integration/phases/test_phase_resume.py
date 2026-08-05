"""Resume-capability integration test — proving FORGE picks up mid-pipeline.

FORGE's persistence contract: the graph DB (SQLite at ``db_path``) plus the
workspace directory are the *entire* persistent state. A brand-new
ForgeBuilder/ForgeFlow constructed over the same pair must continue exactly
where a previous process stopped — no state loss, no duplicates, no phase
resets.

This module proves that with one expensive two-session run, cached at module
scope, followed by cheap assertions (the shared-run pattern of the phase-02
test):

* **Session A** — a first builder runs phases 0-3 for real against a tiny
  two-section spec; every node ID and phase status is recorded, then the
  builder/flow objects are dropped and garbage-collected to simulate process
  end. (There is no explicit teardown API: ``ProjectGraph`` and ``PhaseStore``
  both open a fresh connection per operation, so dropping references *is* the
  full shutdown.)
* **Session B** — a second builder over the SAME workspace and db_path. Before
  running anything, the restored state is snapshotted; then phase 3 is re-run
  (must be a no-op), then phases 4-5 continue the pipeline for real.

These tests make real, paid LLM calls. Run with::

    uv run pytest backend/tests/integration/phases/test_phase_resume.py -m integration
"""

from __future__ import annotations

import dataclasses
import gc
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import backend.agents.factory as factory
from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.config.models import ForgeConfig
from backend.crew.flow import GAP_TYPE_TO_PHASE, ForgeFlow
from backend.forge_builder import ForgeBuilder
from backend.graph.models import GraphNode, NodeType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(1200)]

# Tiny two-section spec (mirrors the phase-03 test): two unambiguous functional
# requirements so phases 0-5 stay fast and cheap on real agents.
TINY_SPEC = """\
# Counter Service Specification

## 1. Increment Command

The system shall increment the stored counter value by exactly one each time
an increment command is received from the client.

## 2. Reset Command

The system shall set the stored counter value to zero each time a reset
command is received from the client.
"""

_LLM_BUDGET = 300  # calls per real pipeline segment — a runaway loop fails loudly


@contextmanager
def _llm_budget(extra_calls: int) -> Iterator[None]:
    """Cap LLM calls for the enclosed block so a non-converging loop fails fast."""
    factory.llm_call_limit = factory.llm_call_count + extra_calls
    try:
        yield
    finally:
        factory.llm_call_limit = None


def _nodes(flow: ForgeFlow, node_type: NodeType) -> list[GraphNode]:
    return [n for n in flow.graph.all_nodes() if n.node_type == node_type.value]


def _node_ids_by_type(flow: ForgeFlow) -> dict[str, tuple[str, ...]]:
    """All node IDs grouped by node_type, sorted — the resume-integrity record."""
    grouped: dict[str, list[str]] = {}
    for node in flow.graph.all_nodes():
        grouped.setdefault(node.node_type, []).append(node.node_id)
    return {t: tuple(sorted(ids)) for t, ids in grouped.items()}


def _phase_statuses(flow: ForgeFlow) -> dict[int, str]:
    return {p["phase_number"]: p["status"] for p in flow.phase_store.get_all()}


async def _build_flow(
    config_template: ForgeConfig, workspace: Path, db_path: Path
) -> ForgeFlow:
    """Wire a real ForgeFlow (real agents, real graph on SQLite) over the pair."""
    config = config_template.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = "resume-it"
    builder = ForgeBuilder(config=config, workspace=workspace, db_path=db_path)
    return await builder.build()


# ── The recorded two-session run ─────────────────────────────────────────────


@dataclasses.dataclass
class ResumeRun:
    """Everything the two-session flow produced, recorded for cheap assertion."""

    # Session A, after phases 0-3:
    a_node_ids: dict[str, tuple[str, ...]]
    a_statuses: dict[int, str]
    a_hlr_ids: frozenset[str]
    # Session B, snapshotted BEFORE running anything on builder #2:
    b_pre_node_ids: dict[str, tuple[str, ...]]
    b_pre_statuses: dict[int, str]
    # Session B, after re-running phase 3 (must be a no-op):
    b_rerun_hlr_ids: frozenset[str]
    b_rerun_statuses: dict[int, str]
    # Session B, after continuing with phases 4-5:
    flow_b: ForgeFlow


_cache: dict[str, object] = {}


async def _run_two_sessions(
    integration_config: ForgeConfig, tmp_path_factory: pytest.TempPathFactory
) -> ResumeRun:
    """The one expensive run: session A (phases 0-3), restart, session B (3-5)."""
    root = tmp_path_factory.mktemp("resume_it")
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "forge.md").write_text(TINY_SPEC, encoding="utf-8")
    db_path = root / "forge.db"

    # ── Session A: builder #1 runs phases 0-3 for real ──────────────────────
    flow_a = await _build_flow(integration_config, workspace, db_path)
    with _llm_budget(_LLM_BUDGET):
        for phase in (0, 1, 2, 3):
            await flow_a.run_phase(phase)

    a_node_ids = _node_ids_by_type(flow_a)
    a_statuses = _phase_statuses(flow_a)
    a_hlr_ids = frozenset(n.node_id for n in _nodes(flow_a, NodeType.HLR))

    # Simulate process end. There is no teardown API to call: ProjectGraph and
    # PhaseStore hold no persistent connections (each operation opens and
    # closes its own), so dropping every reference is a faithful shutdown.
    del flow_a
    gc.collect()

    # ── Session B: a brand-new builder over the SAME workspace and db_path ──
    flow_b = await _build_flow(integration_config, workspace, db_path)
    b_pre_node_ids = _node_ids_by_type(flow_b)
    b_pre_statuses = _phase_statuses(flow_b)

    # Re-run the already-complete phase 3: designed to be a no-op.
    with _llm_budget(_LLM_BUDGET):
        await flow_b.run_phase(3)
    b_rerun_hlr_ids = frozenset(n.node_id for n in _nodes(flow_b, NodeType.HLR))
    b_rerun_statuses = _phase_statuses(flow_b)

    # Continue the pipeline: phases 4 and 5 on the resumed state.
    with _llm_budget(_LLM_BUDGET):
        for phase in (4, 5):
            await flow_b.run_phase(phase)

    return ResumeRun(
        a_node_ids=a_node_ids,
        a_statuses=a_statuses,
        a_hlr_ids=a_hlr_ids,
        b_pre_node_ids=b_pre_node_ids,
        b_pre_statuses=b_pre_statuses,
        b_rerun_hlr_ids=b_rerun_hlr_ids,
        b_rerun_statuses=b_rerun_statuses,
        flow_b=flow_b,
    )


@pytest.fixture
async def resumed(
    integration_config: ForgeConfig, tmp_path_factory: pytest.TempPathFactory
) -> ResumeRun:
    """Run the two-session flow once per module and serve the cached record."""
    if "error" in _cache:
        pytest.fail(f"upstream two-session run already failed: {_cache['error']}")
    if "run" not in _cache:
        try:
            _cache["run"] = await _run_two_sessions(integration_config, tmp_path_factory)
        except Exception as exc:
            _cache["error"] = f"{type(exc).__name__}: {exc}"
            raise
    run = _cache["run"]
    assert isinstance(run, ResumeRun)
    return run


# ── 1. Resume preserves state ────────────────────────────────────────────────


async def test_phase_statuses_survive_restart(resumed: ResumeRun) -> None:
    """Before builder #2 runs anything, phases 0-3 still report complete."""
    for phase in (0, 1, 2, 3):
        assert resumed.a_statuses[phase] == "complete", (
            f"precondition: session A left phase {phase} "
            f"{resumed.a_statuses[phase]!r}, not complete"
        )
        assert resumed.b_pre_statuses[phase] == "complete", (
            f"phase {phase} was {resumed.b_pre_statuses[phase]!r} after restart — "
            "resume lost the phase record"
        )


async def test_graph_nodes_survive_restart_byte_identical(resumed: ResumeRun) -> None:
    """The reopened graph contains exactly the recorded nodes — no loss, no dupes."""
    assert resumed.a_hlr_ids, "precondition: session A derived no HLRs at all"
    assert resumed.b_pre_node_ids == resumed.a_node_ids, (
        "node population changed across restart:\n"
        f"  session A: {resumed.a_node_ids}\n"
        f"  session B: {resumed.b_pre_node_ids}"
    )


# ── 2. Continuation works across the restart ─────────────────────────────────


async def test_continuation_phases_4_and_5_complete(resumed: ResumeRun) -> None:
    """Builder #2 must carry the pipeline through architecture and modules."""
    statuses = _phase_statuses(resumed.flow_b)
    for phase in (4, 5):
        assert statuses[phase] == "complete", (
            f"phase {phase} ended {statuses[phase]!r} on the resumed builder"
        )
    # Continuation must not have disturbed the completed prefix either.
    for phase in (0, 1, 2, 3):
        assert statuses[phase] == "complete"


async def test_cross_session_trace_integrity(resumed: ResumeRun) -> None:
    """Session-B MODULEs trace to session-A HLRs — cross-session graph integrity."""
    flow = resumed.flow_b
    architectures = _nodes(flow, NodeType.ARCHITECTURE)
    modules = _nodes(flow, NodeType.MODULE)
    assert architectures, "phase 4 on the resumed builder created no ARCHITECTURE"
    assert modules, "phase 5 on the resumed builder created no MODULE"

    all_ids = {n.node_id for n in flow.graph.all_nodes()}
    referenced_hlrs: set[str] = set()
    for module in modules:
        dangling = [t for t in module.trace_to if t not in all_ids]
        assert dangling == [], (
            f"{module.node_id} traces to nonexistent nodes: {dangling}"
        )
        referenced_hlrs.update(t for t in module.trace_to if t in resumed.a_hlr_ids)

    assert referenced_hlrs, (
        "no MODULE trace_to target resolves to a session-A HLR — the modules "
        f"trace to {sorted(t for m in modules for t in m.trace_to)} but session A "
        f"recorded HLRs {sorted(resumed.a_hlr_ids)}"
    )


# ── 3. Re-running an already-complete phase is a no-op ───────────────────────


async def test_rerunning_complete_phase_3_is_a_noop(resumed: ResumeRun) -> None:
    """Phase 3 re-run on builder #2: HLR id set unchanged, phase stays complete."""
    assert resumed.b_rerun_hlr_ids == resumed.a_hlr_ids, (
        "re-running complete phase 3 after restart changed the HLR population: "
        f"before={sorted(resumed.a_hlr_ids)} after={sorted(resumed.b_rerun_hlr_ids)}"
    )
    assert resumed.b_rerun_statuses[3] == "complete", (
        f"phase 3 dropped to {resumed.b_rerun_statuses[3]!r} after its no-op re-run"
    )


async def test_no_duplicate_hlr_content_after_resume(resumed: ResumeRun) -> None:
    """Restart plus re-run must not smuggle in content-duplicate HLRs."""
    by_content: dict[str, list[str]] = {}
    for hlr in _nodes(resumed.flow_b, NodeType.HLR):
        by_content.setdefault(hlr.content.strip().lower(), []).append(hlr.node_id)
    dupes = {k[:60]: v for k, v in by_content.items() if len(v) > 1}
    assert dupes == {}, f"duplicate HLR content after resume: {dupes}"


# ── 4. No structural gaps for phases 0-5 after session B ─────────────────────


async def test_no_structural_gaps_through_phase_5(resumed: ResumeRun) -> None:
    """The gap analyser owes nothing for phases 0-5 on the resumed graph."""
    open_gaps: list[GapType] = [
        g.type
        for g in GapAnalyser().analyse(resumed.flow_b.graph)
        if g.type in GAP_TYPE_TO_PHASE and GAP_TYPE_TO_PHASE[g.type] <= 5
    ]
    assert open_gaps == [], (
        f"structural gaps for phases <= 5 still open after resume: {open_gaps}"
    )
