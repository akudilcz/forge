"""End-to-end proof that FORGE reliably builds working software from a spec.

Each whitepaper in ``whitepapers/`` is driven through all 15 phases exactly once
per session, and the resulting build is then graded from several independent
angles. The design goal is to get **per-phase and per-quality-gate test IDs for
the cost of a single build**: a session-scoped fixture does the expensive work,
and many cheap function-scoped tests assert against its recorded result. A
regression in phase 6, or in traceability, or in behavioural correctness, shows
up as its own red test rather than as one aborted run.

The gates, in increasing order of strength:

1. **It ran** — every phase completed and closed its structural gaps.
2. **It produced code** — source and test files exist and parse.
3. **Its own tests pass** — the suite FORGE wrote for itself is green.
4. **It is traced** — every LLR reaches code through a ``@traces`` decorator.
5. **It is correct** — the behavioural oracle, authored from the whitepaper and
   never shown to any agent, accepts the generated module.

Gate 5 is the one that cannot be gamed. Gates 1-4 are all self-referential: the
same agent wrote both the code and the tests that grade it, so a spec
misreading produces a build that is wrong and green. See
``oracles/_base.py`` for the reasoning.

These tests make real, paid LLM calls and take hours. They are excluded from the
default run and from CI; invoke them deliberately with ``make test-integration``.
"""

from __future__ import annotations

import ast
import dataclasses
import time
from pathlib import Path
from typing import Any

import pytest

from backend.config.models import ForgeConfig
from backend.crew.workspace_scanner import WorkspaceState, scan_workspace
from backend.forge_builder import ForgeBuilder
from backend.graph.models import NodeType
from backend.tests.integration.oracles import (
    binary_search,
    circular_buffer,
    csv_parser,
    edit_distance,
    expression_evaluator,
    interval_tree,
    lru_cache,
    merge_sort,
    online_statistics,
    priority_queue,
    rational_arithmetic,
    semver,
    topological_sort,
    trie,
    union_find,
)
from backend.tests.integration.oracles._base import Oracle, OracleResult, run_oracle

WHITEPAPER_DIR = Path(__file__).parent / "whitepapers"

# ── Registry ─────────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class Build:
    """A whitepaper plus the budget and expectations for building it."""

    name: str
    whitepaper: str
    oracle: Oracle | None
    max_llm_calls: int = 2500
    min_source_files: int = 1
    min_test_files: int = 1
    min_statement_coverage: float = 90.0
    stresses: str = ""


BUILDS: list[Build] = [
    Build(
        name="merge_sort",
        whitepaper="01_stable_merge_sort.md",
        oracle=merge_sort.ORACLE,
        stresses="recursion plus a cross-cutting stability invariant",
    ),
    Build(
        name="lru_cache",
        whitepaper="02_lru_cache.md",
        oracle=lru_cache.ORACLE,
        stresses="mutable state and structural invariants across two data structures",
    ),
    Build(
        name="expression_evaluator",
        whitepaper="03_expression_evaluator.md",
        oracle=expression_evaluator.ORACLE,
        max_llm_calls=3000,
        stresses="parsing with a four-class positional error taxonomy",
    ),
    Build(
        name="topological_sort",
        whitepaper="04_topological_sort.md",
        oracle=topological_sort.ORACLE,
        stresses="graph traversal, determinism, iterative-only algorithms",
    ),
    Build(
        name="online_statistics",
        whitepaper="05_online_statistics.md",
        oracle=online_statistics.ORACLE,
        stresses="floating-point numerical stability",
    ),
    Build(
        name="edit_distance",
        whitepaper="06_edit_distance.md",
        oracle=edit_distance.ORACLE,
        stresses="dynamic programming plus alignment reconstruction",
    ),
    Build(
        name="binary_search",
        whitepaper="07_binary_search_family.md",
        oracle=binary_search.ORACLE,
        stresses="boundary conditions and loop termination",
    ),
    Build(
        name="priority_queue",
        whitepaper="08_priority_queue.md",
        oracle=priority_queue.ORACLE,
        stresses="array-encoded tree arithmetic with an index map kept in sync",
    ),
    Build(
        name="interval_tree",
        whitepaper="09_interval_tree.md",
        oracle=interval_tree.ORACLE,
        stresses="half-open boundary semantics — touching versus overlapping",
    ),
    Build(
        name="csv_parser",
        whitepaper="10_csv_parser.md",
        oracle=csv_parser.ORACLE,
        max_llm_calls=3000,
        stresses="character-level state machine over quoted fields and embedded newlines",
    ),
    Build(
        name="union_find",
        whitepaper="11_union_find.md",
        oracle=union_find.ORACLE,
        stresses="amortised complexity — the structure must genuinely compress",
    ),
    Build(
        name="rational_arithmetic",
        whitepaper="12_rational_arithmetic.md",
        oracle=rational_arithmetic.ORACLE,
        stresses="invariant maintenance: lowest terms, positive denominator, hash consistency",
    ),
    Build(
        name="trie",
        whitepaper="13_trie.md",
        oracle=trie.ORACLE,
        stresses="recursive structure whose deletion must not over-prune shared branches",
    ),
    Build(
        name="circular_buffer",
        whitepaper="14_circular_buffer.md",
        oracle=circular_buffer.ORACLE,
        stresses="wraparound arithmetic and the full-versus-empty ambiguity",
    ),
    Build(
        name="semver",
        whitepaper="15_semver.md",
        oracle=semver.ORACLE,
        stresses="pre-release precedence rules that most implementations get wrong",
    ),
]

BUILD_BY_NAME = {b.name: b for b in BUILDS}


# ── Build result ─────────────────────────────────────────────────────────────


@dataclasses.dataclass
class BuildResult:
    """Everything one full pipeline run produced, recorded for later assertion."""

    build: Build
    workspace: Path
    graph: Any
    phase_errors: dict[int, str] = dataclasses.field(default_factory=dict)
    phase_seconds: dict[int, float] = dataclasses.field(default_factory=dict)
    llm_calls: int = 0
    state: WorkspaceState | None = None

    def require_phase(self, phase: int) -> None:
        """Skip rather than fail when an *earlier* phase already broke.

        A phase-4 failure makes every later assertion meaningless; reporting ten
        more reds would bury the one real signal. The failing phase itself still
        fails loudly via ``test_phase_completed``.
        """
        broken = [p for p in sorted(self.phase_errors) if p < phase]
        if broken:
            pytest.skip(f"upstream phase {broken[0]} failed: {self.phase_errors[broken[0]]}")
        if phase in self.phase_errors:
            pytest.fail(f"phase {phase} failed: {self.phase_errors[phase]}")

    def nodes(self, node_type: NodeType) -> list[Any]:
        return [n for n in self.graph.all_nodes() if n.node_type == node_type.value]


# ── The one expensive fixture ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def _build_cache() -> dict[str, BuildResult]:
    return {}


@pytest.fixture
async def built(
    request: pytest.FixtureRequest,
    integration_config: ForgeConfig,
    tmp_path_factory: pytest.TempPathFactory,
    _build_cache: dict[str, BuildResult],
) -> BuildResult:
    """Run a whitepaper through phases 0-14 once, then serve the cached result.

    Keyed on build name so that the many assertions below share a single
    (expensive) pipeline run.
    """
    name: str = request.param
    if name in _build_cache:
        return _build_cache[name]

    import backend.agents.factory as factory

    build = BUILD_BY_NAME[name]
    spec = (WHITEPAPER_DIR / build.whitepaper).read_text(encoding="utf-8")

    root = tmp_path_factory.mktemp(f"build_{name}")
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "forge.md").write_text(spec, encoding="utf-8")

    config = integration_config.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = name

    factory.llm_call_count = 0
    factory.llm_call_limit = build.max_llm_calls

    # The graph DB lives outside the workspace so a misbehaving agent with file
    # tools cannot corrupt the record of its own run.
    builder = ForgeBuilder(config=config, workspace=workspace, db_path=root / "forge.db")
    flow = await builder.build()

    result = BuildResult(build=build, workspace=workspace, graph=flow.graph)
    try:
        for phase in range(15):
            started = time.monotonic()
            try:
                await flow.run_phase(phase)
            except Exception as exc:  # noqa: BLE001 — recorded, not raised
                result.phase_errors[phase] = f"{type(exc).__name__}: {exc}"
                break
            finally:
                result.phase_seconds[phase] = time.monotonic() - started
    finally:
        result.llm_calls = factory.llm_call_count
        factory.llm_call_limit = None

    try:
        result.state = await scan_workspace(workspace)
    except Exception as exc:  # noqa: BLE001 — scanning is diagnostic, not the SUT
        result.phase_errors.setdefault(12, f"workspace scan failed: {exc}")

    _build_cache[name] = result
    return result


def _parametrize(*, oracle_only: bool = False) -> pytest.MarkDecorator:
    """Parametrise `built` over the registry, one test ID per whitepaper."""
    builds = [b for b in BUILDS if not oracle_only or b.oracle is not None]
    return pytest.mark.parametrize(
        "built", [pytest.param(b.name, id=b.name) for b in builds], indirect=True
    )


pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(21600)]


# ── Gate 1: the pipeline ran ─────────────────────────────────────────────────


@_parametrize()
@pytest.mark.parametrize("phase", range(15))
async def test_phase_completed(built: BuildResult, phase: int) -> None:
    """Each of the 15 phases gets its own test ID for one build's cost."""
    built.require_phase(phase)


@_parametrize()
async def test_graph_has_no_structural_gaps(built: BuildResult) -> None:
    """The deterministic gap analyser must report nothing outstanding."""
    from backend.analysis.gap_analyser import GapAnalyser

    built.require_phase(14)
    gaps = GapAnalyser().analyse(built.graph)
    assert gaps == [], f"{len(gaps)} unresolved gaps: {sorted({g.type for g in gaps})}"


@_parametrize()
async def test_requirements_were_derived(built: BuildResult) -> None:
    built.require_phase(7)
    assert built.nodes(NodeType.HLR), "no HLR nodes — phase 3 produced nothing"
    assert built.nodes(NodeType.LLR), "no LLR nodes — phase 7 produced nothing"


# ── Gate 2: it produced code ─────────────────────────────────────────────────


@_parametrize()
async def test_generated_source_files_exist_and_parse(built: BuildResult) -> None:
    built.require_phase(12)
    sources = [
        p for p in (built.workspace / "src").rglob("*.py") if p.name != "__init__.py"
    ]
    assert len(sources) >= built.build.min_source_files, (
        f"expected >= {built.build.min_source_files} source files, got {len(sources)}"
    )
    for path in sources:
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            pytest.fail(f"generated {path.name} is not valid Python: {exc}")


@_parametrize()
async def test_generated_test_files_exist(built: BuildResult) -> None:
    built.require_phase(12)
    tests = list((built.workspace / "tests").rglob("test_*.py"))
    assert len(tests) >= built.build.min_test_files


# ── Gate 3: its own tests pass ───────────────────────────────────────────────


@_parametrize()
async def test_generated_tests_pass(built: BuildResult) -> None:
    built.require_phase(12)
    state = built.state
    assert state is not None, "workspace scan did not run"
    assert state.test_run_error == "", f"test run errored: {state.test_run_error}"
    assert state.test_results, "no test results were collected at all"

    failed = [r for r in state.test_results if r.status not in ("passed", "skipped")]
    assert not failed, (
        f"{len(failed)} generated tests did not pass: "
        f"{[(r.test_id, r.status, r.error_message[:60]) for r in failed][:5]}"
    )


@_parametrize()
async def test_statement_coverage_meets_threshold(built: BuildResult) -> None:
    built.require_phase(12)
    state = built.state
    assert state is not None
    assert state.coverage_pct is not None, (
        "no coverage data — this is a hard failure, not a skip: a build whose "
        "coverage cannot be measured has not been shown to work"
    )
    assert state.coverage_pct >= built.build.min_statement_coverage, (
        f"statement coverage {state.coverage_pct:.1f}% below "
        f"{built.build.min_statement_coverage}%"
    )


# ── Gate 4: it is traced ─────────────────────────────────────────────────────


@_parametrize()
async def test_every_llr_is_traced_into_code(built: BuildResult) -> None:
    """Traceability is FORGE's headline claim, so assert it end to end.

    Every LLR the pipeline invented must appear in a ``@traces`` decorator in the
    generated source. An LLR that reaches phase 12 and never lands in code is a
    silently dropped requirement.
    """
    from backend.crew.trace_parser import analyse_traces

    built.require_phase(13)
    llr_ids = {n.node_id for n in built.nodes(NodeType.LLR)}
    assert llr_ids, "no LLRs to trace"

    traced: set[str] = set()
    for path in (built.workspace / "src").rglob("*.py"):
        analysis = analyse_traces(path.read_text(encoding="utf-8"))
        for trace in analysis.traces:
            traced.update(getattr(trace, "llr_ids", []) or [])

    missing = sorted(llr_ids - traced)
    assert not missing, f"{len(missing)} LLRs never reached code: {missing[:10]}"


@_parametrize()
async def test_no_untraced_functions(built: BuildResult) -> None:
    """A function with no ``@traces`` implements no requirement — dead weight."""
    from backend.crew.trace_parser import analyse_traces

    built.require_phase(13)
    orphans: list[str] = []
    for path in (built.workspace / "src").rglob("*.py"):
        if path.name == "__init__.py":
            continue
        analysis = analyse_traces(path.read_text(encoding="utf-8"))
        orphans.extend(f"{path.name}::{fn.name}" for fn in analysis.untraced)

    assert not orphans, f"{len(orphans)} untraced functions: {orphans[:10]}"


# ── Gate 5: it is correct (the independent oracle) ───────────────────────────


@_parametrize(oracle_only=True)
async def test_behavioural_oracle_accepts_generated_code(built: BuildResult) -> None:
    """The only gate FORGE cannot grade itself on.

    Cases come from the whitepaper's Correctness Properties and Failure Modes
    sections and are never written into the workspace, so passing this means the
    generated module genuinely implements the specification rather than merely
    agreeing with the tests written alongside it.
    """
    built.require_phase(12)
    oracle = built.build.oracle
    assert oracle is not None

    result: OracleResult = run_oracle(oracle, built.workspace)
    assert result.ok, f"\n{result.summary()}"


# ── Deliverables ─────────────────────────────────────────────────────────────


@_parametrize()
async def test_deliverables_bundle_is_produced(built: BuildResult) -> None:
    built.require_phase(14)
    bundles = list(built.workspace.rglob("*.zip"))
    assert bundles, "phase 14 produced no deliverables archive"

    import zipfile

    with zipfile.ZipFile(bundles[0]) as zf:
        names = zf.namelist()
    assert names, "deliverables archive is empty"
    assert any(n.endswith(".py") for n in names), (
        f"deliverables contain no source files: {names[:10]}"
    )


# ── Telemetry ────────────────────────────────────────────────────────────────


@_parametrize()
async def test_stayed_within_llm_budget(built: BuildResult) -> None:
    """A build that silently doubles in cost is a regression worth catching."""
    budget = built.build.max_llm_calls
    assert built.llm_calls < budget, (
        f"hit the {budget}-call ceiling — the pipeline did not converge"
    )


@_parametrize()
async def test_report_build_telemetry(built: BuildResult, record_property: Any) -> None:
    """Not an assertion — records per-phase cost into the JUnit report."""
    record_property("llm_calls", built.llm_calls)
    record_property("total_seconds", round(sum(built.phase_seconds.values()), 1))
    for phase, seconds in sorted(built.phase_seconds.items()):
        record_property(f"phase_{phase:02d}_seconds", round(seconds, 1))
    slowest = sorted(built.phase_seconds.items(), key=lambda kv: -kv[1])[:3]
    print(
        f"\n[{built.build.name}] {built.llm_calls} LLM calls, "
        f"{sum(built.phase_seconds.values()):.0f}s total; "
        f"slowest phases: {[(p, round(s)) for p, s in slowest]}"
    )
