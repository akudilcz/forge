"""Per-phase integration tests for Phase 4 — Create Architecture.

Phase 4 dispatches a real Design Architect agent against the single
UNARCHITECTED gap and must leave exactly one substantive ARCHITECTURE node
under the PROJECT (design/14_phase_04_create_architecture.md).

To isolate phase 4, the precondition graph (PROJECT, DOCUMENT, PARAs, HLRs)
is seeded deterministically via the graph engine rather than by running the
earlier LLM phases — the only real LLM work in these tests is phase 4's own.
The expensive run happens once per module (cached); the idempotency test then
re-runs phase 4 on the same graph.

Run with::

    uv run pytest backend/tests/integration/phases/test_phase_04_create_architecture.py -m integration
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
from backend.crew.flow import ForgeFlow
from backend.forge_builder import ForgeBuilder
from backend.graph.models import GraphNode, LifecycleState, NodeType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(1200)]

_LLM_BUDGET = 100  # phase 4 is a single-gap dispatch; a runaway loop fails loudly


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


async def _seed_requirements_graph(flow: ForgeFlow) -> None:
    """Seed the documented phase-4 precondition: PROJECT → DOCUMENT → PARAs → HLRs."""
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
    await _seed(
        flow,
        NodeType.HLR,
        "Counter Increment Behaviour",
        "The system shall increment the stored counter value by exactly one "
        "when an increment command is received.",
        para_cmd.node_id,
        [],
    )
    await _seed(
        flow,
        NodeType.HLR,
        "Counter Reset Behaviour",
        "The system shall set the stored counter value to zero when a reset "
        "command is received.",
        para_cmd.node_id,
        [],
    )
    await _seed(
        flow,
        NodeType.HLR,
        "Invalid Command Rejection",
        "The system shall reject any command other than increment or reset "
        "with an error response, leaving the counter value unchanged.",
        para_err.node_id,
        [],
    )


def _nodes(flow: ForgeFlow, node_type: NodeType) -> list[GraphNode]:
    return [n for n in flow.graph.all_nodes() if n.node_type == node_type.value]


def _gap_types(flow: ForgeFlow) -> set[GapType]:
    return {g.type for g in GapAnalyser().analyse(flow.graph)}


# ── The one expensive run, cached at module scope ────────────────────────────

_cache: dict[str, object] = {}


@pytest.fixture
async def p4_flow(
    integration_config: ForgeConfig, tmp_path_factory: pytest.TempPathFactory
) -> ForgeFlow:
    """Seed the precondition graph and run phase 4 for real, once per module."""
    if "error" in _cache:
        pytest.fail(f"upstream phase 4 run already failed: {_cache['error']}")
    if "flow" not in _cache:
        root = tmp_path_factory.mktemp("phase4_it")
        flow = await _build_flow(integration_config, root, "phase4-it")
        try:
            await _seed_requirements_graph(flow)
            with _llm_budget(_LLM_BUDGET):
                await flow.run_phase(4)
        except Exception as exc:
            _cache["error"] = f"{type(exc).__name__}: {exc}"
            raise
        _cache["flow"] = flow
    flow_obj = _cache["flow"]
    assert isinstance(flow_obj, ForgeFlow)
    return flow_obj


# ── Happy path ───────────────────────────────────────────────────────────────


async def test_creates_exactly_one_architecture_under_the_project(
    p4_flow: ForgeFlow,
) -> None:
    """Postcondition: one ARCHITECTURE node, child of PROJECT, layer 3."""
    architectures = _nodes(p4_flow, NodeType.ARCHITECTURE)
    assert len(architectures) == 1, (
        f"expected exactly 1 ARCHITECTURE node, got {len(architectures)}"
    )
    arch = architectures[0]
    project_id = _nodes(p4_flow, NodeType.PROJECT)[0].node_id
    assert arch.parent_id == project_id, (
        f"ARCHITECTURE must hang off PROJECT {project_id}, got {arch.parent_id!r}"
    )
    assert arch.layer == 3, f"ARCHITECTURE layer must be 3, got {arch.layer}"


async def test_architecture_content_is_substantive(p4_flow: ForgeFlow) -> None:
    """A few bullet points do not qualify as a system decomposition document."""
    arch = _nodes(p4_flow, NodeType.ARCHITECTURE)[0]
    content = arch.content.strip()
    assert content, "ARCHITECTURE node has empty content"
    assert len(content) >= 50, (
        f"ARCHITECTURE content is only {len(content)} chars — below the "
        "analyser's own INADEQUATE_CONTENT threshold"
    )


async def test_no_unarchitected_gap_outstanding(p4_flow: ForgeFlow) -> None:
    """The gap analyser must report phase 4's own gap type fully closed."""
    assert GapType.UNARCHITECTED not in _gap_types(p4_flow)


# ── Robustness: re-run idempotency (real LLM) ────────────────────────────────


async def test_rerunning_phase_4_does_not_create_a_second_architecture(
    p4_flow: ForgeFlow,
) -> None:
    """Phase re-runs are documented as idempotent: still exactly one ARCHITECTURE."""
    first_id = _nodes(p4_flow, NodeType.ARCHITECTURE)[0].node_id

    with _llm_budget(_LLM_BUDGET):
        await p4_flow.run_phase(4)

    architectures = _nodes(p4_flow, NodeType.ARCHITECTURE)
    assert len(architectures) == 1, (
        f"re-running phase 4 left {len(architectures)} ARCHITECTURE nodes"
    )
    assert architectures[0].node_id == first_id, "ARCHITECTURE node id must be stable"
    assert GapType.UNARCHITECTED not in _gap_types(p4_flow)


# ── Robustness: empty precondition ───────────────────────────────────────────


async def test_phase_4_on_an_empty_graph_is_a_silent_no_op(
    integration_config: ForgeConfig, tmp_path: Path
) -> None:
    """With no PROJECT there is no UNARCHITECTED gap: phase 4 must complete
    without inventing nodes or spending a single LLM call."""
    flow = await _build_flow(integration_config, tmp_path, "phase4-empty")

    calls_before = factory.llm_call_count
    with _llm_budget(_LLM_BUDGET):
        await flow.run_phase(4)

    assert flow.graph.all_nodes() == [], (
        f"phase 4 created nodes on an empty graph: "
        f"{[n.node_id for n in flow.graph.all_nodes()]}"
    )
    assert factory.llm_call_count == calls_before, (
        f"phase 4 spent {factory.llm_call_count - calls_before} LLM call(s) "
        "despite having no gaps to resolve"
    )
