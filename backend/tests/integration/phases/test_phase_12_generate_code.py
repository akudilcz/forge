"""Phase 12 (code generation) integration tests — real LLM, tiny scope.

Seeds the smallest realistic precondition graph (one module, two trivial
LLRs: ``add`` and ``subtract``) so the mission agent converges in minutes,
then runs ONLY phase 12 (after the free, deterministic phase 11 doc render
that feeds the mission context) and asserts its postconditions:

  * generated ``src/*.py`` and ``tests/test_*.py`` exist and parse
  * the generated tests pass
  * every seeded LLR appears in a ``@traces`` decorator in source
  * the phase-12 coverage gate passed (``run_phase(12)`` did not raise
    ``CodeGenIncompleteError``)

One robustness case runs phase 12 against an empty graph and asserts it
completes without crashing and without writing junk files.

The expensive build runs once per session; the assertions below share it.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

import pytest

from backend.config.models import ForgeConfig
from backend.crew.trace_parser import analyse_traces
from backend.crew.workspace_scanner import WorkspaceState, scan_workspace
from backend.forge_builder import ForgeBuilder
from backend.graph.models import GraphNode, LifecycleState, NodeType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(3600)]

SEEDED_LLR_IDS = ("LLR-0001", "LLR-0002")


# ── Graph seeding ────────────────────────────────────────────────────────────


async def _seed_tiny_adder_graph(graph: Any) -> None:
    """Seed the full phase-12 precondition graph for a two-function module.

    Node shapes mirror ``scenarios/calculator/graph.py`` and respect the
    parent/layer constraints asserted in ``test_phase_contracts.py``.
    """
    nodes = [
        GraphNode(
            node_id="PROJECT-0001",
            node_type=NodeType.PROJECT,
            title="Adder",
            content="A tiny arithmetic module for per-phase integration testing.",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DOCUMENT-0001",
            node_type=NodeType.DOCUMENT,
            title="Adder Spec",
            content="Specification for two arithmetic functions.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="PARA-0001",
            node_type=NodeType.PARA,
            title="Requirements",
            content="Functional requirements for arithmetic operations.",
            parent_id="DOCUMENT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0001",
            node_type=NodeType.HLR,
            title="Basic Arithmetic",
            content=(
                "The module SHALL support addition and subtraction of two "
                "numbers."
            ),
            parent_id="PARA-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0001",
            node_type=NodeType.LLR,
            title="Addition",
            content="add(a: float, b: float) -> float: return a + b",
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0002",
            node_type=NodeType.LLR,
            title="Subtraction",
            content="subtract(a: float, b: float) -> float: return a - b",
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="ARCHITECTURE-0001",
            node_type=NodeType.ARCHITECTURE,
            title="Adder Architecture",
            content="Single-module architecture for the adder.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="MODULE-0001",
            node_type=NodeType.MODULE,
            title="Arithmetic",
            content="Module containing arithmetic operations.",
            parent_id="ARCHITECTURE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CONTRACT-0001",
            node_type=NodeType.CONTRACT,
            title="Arithmetic API",
            content=(
                "def add(a: float, b: float) -> float: ...\n"
                "def subtract(a: float, b: float) -> float: ..."
            ),
            parent_id="MODULE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DESIGN-0001",
            node_type=NodeType.DESIGN,
            title="Arithmetic Implementation",
            content=(
                "Implement module-level functions in src/arithmetic.py.\n"
                "- add(a, b) returns a + b\n"
                "- subtract(a, b) returns a - b\n"
                "No classes, no validation, no branches. Every function "
                "must carry a @traces decorator referencing its LLR ID."
            ),
            parent_id="MODULE-0001",
            trace_to=["LLR-0001", "LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="SUITE-0001",
            node_type=NodeType.SUITE,
            title="Arithmetic Tests",
            content="Test suite for the Arithmetic module.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0001",
            node_type=NodeType.CASE_LLR,
            title="Test Addition",
            content="Verify add(2, 3) == 5 and add(-1, 1) == 0. Traces to LLR-0001.",
            parent_id="SUITE-0001",
            trace_to=["LLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0002",
            node_type=NodeType.CASE_LLR,
            title="Test Subtraction",
            content="Verify subtract(5, 3) == 2 and subtract(1, 1) == 0. Traces to LLR-0002.",
            parent_id="SUITE-0001",
            trace_to=["LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
    ]
    for node in nodes:
        await graph.add_node(node)


# ── The one expensive fixture ────────────────────────────────────────────────


@dataclasses.dataclass
class Phase12Run:
    """Everything one phase-12 run produced, recorded for later assertion."""

    workspace: Path
    graph: Any
    error: str | None
    state: WorkspaceState | None
    phase_status: str


@pytest.fixture(scope="session")
def _p12_cache() -> dict[str, Phase12Run]:
    return {}


@pytest.fixture
async def p12(
    integration_config: ForgeConfig,
    tmp_path_factory: pytest.TempPathFactory,
    _p12_cache: dict[str, Phase12Run],
) -> Phase12Run:
    """Run phase 12 exactly once on the seeded tiny graph; serve cached result."""
    if "run" in _p12_cache:
        return _p12_cache["run"]

    import backend.agents.factory as factory

    root = tmp_path_factory.mktemp("phase12_adder")
    workspace = root / "workspace"
    workspace.mkdir()

    config = integration_config.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = "phase12-adder"

    builder = ForgeBuilder(config=config, workspace=workspace, db_path=root / "forge.db")
    flow = await builder.build()
    await _seed_tiny_adder_graph(flow.graph)

    factory.llm_call_count = 0
    factory.llm_call_limit = 400
    error: str | None = None
    try:
        # Phase 11 is deterministic (no LLM) and feeds the mission context;
        # phase 12 is the system under test.
        await flow.run_phase(11)
        await flow.run_phase(12)
    except Exception as exc:  # noqa: BLE001 — recorded, asserted in tests
        error = f"{type(exc).__name__}: {exc}"
    finally:
        factory.llm_call_limit = None

    state: WorkspaceState | None = None
    try:
        state = await scan_workspace(workspace)
    except Exception as exc:  # noqa: BLE001 — scanning is diagnostic
        if error is None:
            error = f"workspace scan failed: {exc}"

    row = flow.phase_store.get(12)
    status = str(row["status"]) if row is not None else "missing"
    run = Phase12Run(
        workspace=workspace, graph=flow.graph, error=error, state=state, phase_status=status
    )
    _p12_cache["run"] = run
    return run


def _require_success(run: Phase12Run) -> None:
    if run.error is not None:
        pytest.fail(f"phase 12 run failed: {run.error}")


# ── Postcondition assertions ─────────────────────────────────────────────────


async def test_phase_12_completes_and_reports_complete(p12: Phase12Run) -> None:
    """Phase 12 must finish without raising and mark itself complete.

    ``run_phase(12)`` raising ``CodeGenIncompleteError`` means the coverage
    gate failed — so a green run here is also proof the gate passed.
    """
    _require_success(p12)
    assert p12.phase_status == "complete", (
        f"phase 12 status is {p12.phase_status!r}, expected 'complete'"
    )


async def test_generated_source_files_exist_and_parse(p12: Phase12Run) -> None:
    _require_success(p12)
    sources = [
        p for p in (p12.workspace / "src").rglob("*.py") if p.name != "__init__.py"
    ]
    assert sources, "phase 12 produced no source files under src/"
    for path in sources:
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            pytest.fail(f"generated {path.name} is not valid Python: {exc}")


async def test_generated_test_files_exist_and_parse(p12: Phase12Run) -> None:
    _require_success(p12)
    tests = list((p12.workspace / "tests").rglob("test_*.py"))
    assert tests, "phase 12 produced no test files under tests/"
    for path in tests:
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            pytest.fail(f"generated {path.name} is not valid Python: {exc}")


async def test_generated_tests_pass(p12: Phase12Run) -> None:
    _require_success(p12)
    state = p12.state
    assert state is not None, "workspace scan did not run"
    assert state.test_run_error == "", f"test run errored: {state.test_run_error}"
    assert state.test_results, "no test results were collected at all"
    failed = [r for r in state.test_results if r.status not in ("passed", "skipped")]
    assert not failed, (
        f"{len(failed)} generated tests did not pass: "
        f"{[(r.test_id, r.status) for r in failed][:5]}"
    )


async def test_every_seeded_llr_is_traced_in_source(p12: Phase12Run) -> None:
    _require_success(p12)
    traced: set[str] = set()
    for path in (p12.workspace / "src").rglob("*.py"):
        analysis = analyse_traces(path.read_text(encoding="utf-8"))
        for trace in analysis.traces:
            traced.update(trace.llr_ids or [])
    missing = sorted(set(SEEDED_LLR_IDS) - traced)
    assert not missing, f"seeded LLRs never reached a @traces decorator: {missing}"


async def test_coverage_gate_passed(p12: Phase12Run) -> None:
    """Statement coverage must be measured and at 100% (the phase-12 gate)."""
    _require_success(p12)
    state = p12.state
    assert state is not None
    assert state.coverage_pct is not None, (
        "no coverage data — a build whose coverage cannot be measured has "
        "not been shown to meet the phase-12 gate"
    )
    assert state.coverage_pct >= 99.999, (
        f"statement coverage {state.coverage_pct:.1f}% — the phase-12 gate "
        f"requires 100%"
    )


# ── Robustness: phase 12 on an empty graph ───────────────────────────────────


async def test_phase_12_on_empty_graph_completes_without_junk(
    integration_config: ForgeConfig,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """With no DESIGNs (and no nodes at all), phase 12 must be a clean no-op.

    It may scaffold the workspace (bazel files, tracing/ package), but must
    not invent source or test files and must not crash.
    """
    import backend.agents.factory as factory

    root = tmp_path_factory.mktemp("phase12_empty")
    workspace = root / "workspace"
    workspace.mkdir()

    config = integration_config.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = "phase12-empty"

    builder = ForgeBuilder(config=config, workspace=workspace, db_path=root / "forge.db")
    flow = await builder.build()

    factory.llm_call_count = 0
    factory.llm_call_limit = 100
    try:
        await flow.run_phase(12)
    finally:
        factory.llm_call_limit = None

    row = flow.phase_store.get(12)
    assert row is not None and str(row["status"]) == "complete"

    junk_sources = [
        p.name for p in (workspace / "src").rglob("*.py") if p.name != "__init__.py"
    ]
    assert junk_sources == [], f"phase 12 wrote source files with nothing to build: {junk_sources}"
    junk_tests = [p.name for p in (workspace / "tests").rglob("test_*.py")]
    assert junk_tests == [], f"phase 12 wrote test files with nothing to build: {junk_tests}"
