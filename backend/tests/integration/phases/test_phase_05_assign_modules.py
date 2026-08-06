"""Per-phase integration tests for Phase 5 — Assign Modules.

Phase 5 batch-dispatches a real agent over the UNMODULARISED gaps and must
leave every HLR traced-to by a MODULE under the ARCHITECTURE
(specs/03-build-pipeline.md).

To isolate phase 5, the precondition graph (PROJECT, DOCUMENT, PARAs, HLRs,
ARCHITECTURE) is seeded deterministically via the graph engine — the only
real LLM work in these tests is phase 5's own batch assignment. The
expensive run happens once per module (cached); the idempotency test then
re-runs phase 5 on the same graph.

Run with::

    uv run pytest backend/tests/integration/phases/test_phase_05_assign_modules.py -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import backend.agents.factory as factory
from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.config.models import ForgeConfig
from backend.core.forge_builder import ForgeBuilder
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.pipeline.flow import ForgeFlow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(1200)]

_LLM_BUDGET = 150  # one batch prompt plus quality passes; a runaway loop fails loudly

ARCHITECTURE_CONTENT = """\
# Counter Service Architecture

The system decomposes into two modules.

## Module Inventory

- **counter_core** — owns the stored counter value and the state mutations on
  it: incrementing by one and resetting to zero. It exposes `increment()` and
  `reset()` and holds no parsing or transport logic.
- **command_gateway** — receives client commands, validates them, routes
  increment and reset to counter_core, and rejects any other command with an
  error response without touching the counter state.

## Key Interfaces

command_gateway calls counter_core through its two mutation operations; no
other module may mutate the counter.

## Cross-Cutting Concerns

Errors are reported by command_gateway only; counter_core raises on misuse.

## Rationale

State ownership (counter_core) is separated from protocol handling
(command_gateway) so each requirement cluster lands in exactly one module.
"""


@contextmanager
def _llm_budget(extra_calls: int) -> Iterator[None]:
    """Cap LLM calls for the enclosed block so a non-converging loop fails fast."""
    factory.llm_call_limit = factory.llm_call_count + extra_calls
    try:
        yield
    finally:
        factory.llm_call_limit = None


async def _build_flow(
    config_template: ForgeConfig, root: Path, project_name: str
) -> ForgeFlow:
    """Wire a real ForgeFlow (real agents, real graph on SQLite) in *root*."""
    workspace = root / "workspace"
    workspace.mkdir()
    config = config_template.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = project_name
    builder = ForgeBuilder(config=config, workspace=workspace, db_path=root / "forge.db")
    return await builder.build()


async def _seed(
    flow: ForgeFlow,
    node_type: NodeType,
    title: str,
    content: str,
    parent_id: str | None,
    trace_to: list[str],
) -> GraphNode:
    node_id = await flow.graph.allocate_node_id(node_type.value)
    node = GraphNode(
        node_id=node_id,
        node_type=node_type.value,
        title=title,
        content=content,
        parent_id=parent_id,
        trace_to=trace_to,
        lifecycle=LifecycleState.ACTIVE,
        created_by="integration-seed",
    )
    await flow.graph.add_node(node)
    return node


async def _seed_architecture_graph(flow: ForgeFlow) -> None:
    """Seed the documented phase-5 precondition: requirements plus ARCHITECTURE."""
    await flow.run_phase(0)  # PROJECT node — deterministic, free
    project_id = _nodes(flow, NodeType.PROJECT)[0].node_id

    doc = await _seed(
        flow,
        NodeType.DOCUMENT,
        "Counter service spec",
        "# Counter Service Specification\n\nCommands mutate a stored counter.\n",
        project_id,
        [],
    )
    para_cmd = await _seed(
        flow,
        NodeType.PARA,
        "Counter Command Handling",
        "The service keeps a single integer counter. Clients send increment "
        "commands to add one and reset commands to return it to zero.",
        doc.node_id,
        [],
    )
    para_err = await _seed(
        flow,
        NodeType.PARA,
        "Invalid Command Handling",
        "Any command other than increment or reset must be refused with an "
        "error response and must leave the counter value unchanged.",
        doc.node_id,
        [],
    )
    hlr_inc = await _seed(
        flow,
        NodeType.HLR,
        "Counter Increment Behaviour",
        "The system shall increment the stored counter value by exactly one "
        "when an increment command is received.",
        para_cmd.node_id,
        [],
    )
    hlr_reset = await _seed(
        flow,
        NodeType.HLR,
        "Counter Reset Behaviour",
        "The system shall set the stored counter value to zero when a reset "
        "command is received.",
        para_cmd.node_id,
        [],
    )
    hlr_reject = await _seed(
        flow,
        NodeType.HLR,
        "Invalid Command Rejection",
        "The system shall reject any command other than increment or reset "
        "with an error response, leaving the counter value unchanged.",
        para_err.node_id,
        [],
    )
    await _seed(
        flow,
        NodeType.ARCHITECTURE,
        "Counter Service Architecture",
        ARCHITECTURE_CONTENT,
        project_id,
        [hlr_inc.node_id, hlr_reset.node_id, hlr_reject.node_id],
    )


def _nodes(flow: ForgeFlow, node_type: NodeType) -> list[GraphNode]:
    return [n for n in flow.graph.all_nodes() if n.node_type == node_type.value]


def _gap_types(flow: ForgeFlow) -> set[GapType]:
    return {g.type for g in GapAnalyser().analyse(flow.graph)}


def _hlr_ids_covered_by_modules(flow: ForgeFlow) -> set[str]:
    return {ref for m in _nodes(flow, NodeType.MODULE) for ref in m.trace_to}


# ── The one expensive run, cached at module scope ────────────────────────────

_cache: dict[str, object] = {}


@pytest.fixture
async def p5_flow(
    integration_config: ForgeConfig, tmp_path_factory: pytest.TempPathFactory
) -> ForgeFlow:
    """Seed the precondition graph and run phase 5 for real, once per module."""
    if "error" in _cache:
        pytest.fail(f"upstream phase 5 run already failed: {_cache['error']}")
    if "flow" not in _cache:
        root = tmp_path_factory.mktemp("phase5_it")
        flow = await _build_flow(integration_config, root, "phase5-it")
        try:
            await _seed_architecture_graph(flow)
            with _llm_budget(_LLM_BUDGET):
                await flow.run_phase(5)
        except Exception as exc:
            _cache["error"] = f"{type(exc).__name__}: {exc}"
            raise
        _cache["flow"] = flow
    flow_obj = _cache["flow"]
    assert isinstance(flow_obj, ForgeFlow)
    return flow_obj


# ── Happy path ───────────────────────────────────────────────────────────────


async def test_every_hlr_is_assigned_to_a_module(p5_flow: ForgeFlow) -> None:
    """Postcondition: each seeded HLR is traced-to by at least one MODULE."""
    hlr_ids = {h.node_id for h in _nodes(p5_flow, NodeType.HLR)}
    assert hlr_ids, "precondition: the seed created HLRs"

    modules = _nodes(p5_flow, NodeType.MODULE)
    assert modules, "phase 5 produced no MODULE nodes"

    unassigned = sorted(hlr_ids - _hlr_ids_covered_by_modules(p5_flow))
    assert unassigned == [], f"HLRs not assigned to any MODULE: {unassigned}"


async def test_modules_are_well_formed(p5_flow: ForgeFlow) -> None:
    """MODULEs hang off the ARCHITECTURE at layer 4 with content and traces."""
    arch_id = _nodes(p5_flow, NodeType.ARCHITECTURE)[0].node_id
    hlr_ids = {h.node_id for h in _nodes(p5_flow, NodeType.HLR)}
    for module in _nodes(p5_flow, NodeType.MODULE):
        assert module.parent_id == arch_id, (
            f"{module.node_id} must hang off ARCHITECTURE {arch_id}, "
            f"got {module.parent_id!r}"
        )
        assert module.layer == 4, f"{module.node_id} layer must be 4, got {module.layer}"
        assert module.content.strip(), f"{module.node_id} has empty content"
        assert module.trace_to, f"{module.node_id} traces to no HLR — dead module"
        foreign = sorted(set(module.trace_to) - hlr_ids)
        assert foreign == [], f"{module.node_id} traces to non-HLR nodes: {foreign}"


async def test_no_unmodularised_or_empty_trace_gaps_outstanding(
    p5_flow: ForgeFlow,
) -> None:
    """The gap analyser must report phase 5's own gap types fully closed."""
    outstanding = _gap_types(p5_flow)
    assert GapType.UNMODULARISED not in outstanding
    assert GapType.EMPTY_TRACE not in outstanding, (
        "a MODULE with empty trace_to survived phase 5"
    )


# ── Robustness: re-run idempotency (real LLM) ────────────────────────────────


async def test_rerunning_phase_5_creates_no_duplicate_modules(
    p5_flow: ForgeFlow,
) -> None:
    """Phase re-runs are documented as idempotent: with every HLR already
    assigned the batch step finds no gaps, so the MODULE set is unchanged."""
    before_ids = {m.node_id for m in _nodes(p5_flow, NodeType.MODULE)}
    assert before_ids, "precondition: the happy-path run created MODULEs"

    with _llm_budget(_LLM_BUDGET):
        await p5_flow.run_phase(5)

    after_ids = {m.node_id for m in _nodes(p5_flow, NodeType.MODULE)}
    assert after_ids == before_ids, (
        f"re-running phase 5 changed the MODULE set: "
        f"before={sorted(before_ids)} after={sorted(after_ids)}"
    )

    hlr_ids = {h.node_id for h in _nodes(p5_flow, NodeType.HLR)}
    unassigned = sorted(hlr_ids - _hlr_ids_covered_by_modules(p5_flow))
    assert unassigned == [], f"re-run dropped HLR coverage: {unassigned}"
    assert GapType.UNMODULARISED not in _gap_types(p5_flow)


# ── Robustness: empty precondition ───────────────────────────────────────────


async def test_phase_5_with_no_hlrs_completes_without_creating_modules(
    integration_config: ForgeConfig, tmp_path: Path
) -> None:
    """An ARCHITECTURE with no HLRs offers phase 5 no work: it must neither
    loop nor invent MODULEs nor spend a single LLM call."""
    flow = await _build_flow(integration_config, tmp_path, "phase5-empty")
    await flow.run_phase(0)  # PROJECT node — deterministic, free
    project_id = _nodes(flow, NodeType.PROJECT)[0].node_id
    await _seed(
        flow,
        NodeType.ARCHITECTURE,
        "Counter Service Architecture",
        ARCHITECTURE_CONTENT,
        project_id,
        [],
    )

    calls_before = factory.llm_call_count
    with _llm_budget(_LLM_BUDGET):
        await flow.run_phase(5)

    assert _nodes(flow, NodeType.MODULE) == [], (
        "phase 5 invented MODULEs with no HLRs to assign"
    )
    assert factory.llm_call_count == calls_before, (
        f"phase 5 spent {factory.llm_call_count - calls_before} LLM call(s) "
        "despite having no gaps to resolve"
    )
