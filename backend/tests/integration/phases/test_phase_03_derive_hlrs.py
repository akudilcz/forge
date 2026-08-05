"""Per-phase integration tests for Phase 3 — Derive HLRs.

Phase 3 batch-dispatches a real Requirements Engineer agent over every
UNCOVERED_PARA gap and must leave each functional paragraph covered by at
least one HLR child (design/13_phase_03_derive_hlrs.md).

These tests make real, paid LLM calls. Phases 0-2 are run for real against a
deliberately tiny two-section spec so that phase 3 operates on genuine parser
output rather than hand-seeded PARAs. The expensive run happens once per
module (cached), and several cheap tests assert against it; the idempotency
test then re-runs phase 3 on the same graph.

Run with::

    uv run pytest backend/tests/integration/phases/test_phase_03_derive_hlrs.py -m integration
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

# Tiny two-section spec: two unambiguous functional requirements, nothing else,
# so phases 1-3 stay fast and cheap while still exercising the real agents.
TINY_SPEC = """\
# Counter Service Specification

## 1. Increment Command

The system shall increment the stored counter value by exactly one each time
an increment command is received from the client.

## 2. Reset Command

The system shall set the stored counter value to zero each time a reset
command is received from the client.
"""

_LLM_BUDGET = 200  # calls per real pipeline run — a runaway loop fails loudly


@contextmanager
def _llm_budget(extra_calls: int) -> Iterator[None]:
    """Cap LLM calls for the enclosed block so a non-converging loop fails fast."""
    factory.llm_call_limit = factory.llm_call_count + extra_calls
    try:
        yield
    finally:
        factory.llm_call_limit = None


async def _build_flow(
    config_template: ForgeConfig, root: Path, project_name: str, spec: str | None
) -> ForgeFlow:
    """Wire a real ForgeFlow (real agents, real graph on SQLite) in *root*."""
    workspace = root / "workspace"
    workspace.mkdir()
    if spec is not None:
        (workspace / "forge.md").write_text(spec, encoding="utf-8")
    config = config_template.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = project_name
    builder = ForgeBuilder(config=config, workspace=workspace, db_path=root / "forge.db")
    return await builder.build()


def _nodes(flow: ForgeFlow, node_type: NodeType) -> list[GraphNode]:
    return [n for n in flow.graph.all_nodes() if n.node_type == node_type.value]


def _gap_types(flow: ForgeFlow) -> set[GapType]:
    return {g.type for g in GapAnalyser().analyse(flow.graph)}


def _functional_paras(flow: ForgeFlow) -> list[GraphNode]:
    """PARAs the gap analyser treats as requirement sources.

    Mirrors the UNCOVERED_PARA skip rules in
    ``GapAnalyser._check_uncovered_para``: heading PARAs, empty PARAs, and
    heading-only PARAs (< 20 chars of body after a ``#`` line) are not
    requirement sources.
    """
    functional: list[GraphNode] = []
    for para in _nodes(flow, NodeType.PARA):
        content = para.content.strip()
        if (para.para_type or "paragraph") == "heading" or not content:
            continue
        if content.startswith("#"):
            body = content.split("\n", 1)[1].strip() if "\n" in content else ""
            if len(body) < 20:
                continue
        functional.append(para)
    return functional


# ── The one expensive run, cached at module scope ────────────────────────────

_cache: dict[str, object] = {}


@pytest.fixture
async def p3_flow(
    integration_config: ForgeConfig, tmp_path_factory: pytest.TempPathFactory
) -> ForgeFlow:
    """Run phases 0-3 for real, once per module, and serve the cached flow."""
    if "error" in _cache:
        pytest.fail(f"upstream phase 0-3 run already failed: {_cache['error']}")
    if "flow" not in _cache:
        root = tmp_path_factory.mktemp("phase3_it")
        flow = await _build_flow(integration_config, root, "phase3-it", TINY_SPEC)
        try:
            with _llm_budget(_LLM_BUDGET):
                for phase in (0, 1, 2, 3):
                    await flow.run_phase(phase)
        except Exception as exc:
            _cache["error"] = f"{type(exc).__name__}: {exc}"
            raise
        _cache["flow"] = flow
    flow_obj = _cache["flow"]
    assert isinstance(flow_obj, ForgeFlow)
    return flow_obj


# ── Happy path ───────────────────────────────────────────────────────────────


async def test_every_functional_para_has_an_hlr_child(p3_flow: ForgeFlow) -> None:
    """The phase-3 postcondition: functional PARAs are covered by HLR children."""
    functional = _functional_paras(p3_flow)
    assert functional, "the tiny spec must yield at least one functional PARA"

    hlrs = _nodes(p3_flow, NodeType.HLR)
    assert hlrs, "phase 3 produced no HLR nodes"

    hlr_parents = {h.parent_id for h in hlrs}
    uncovered = [p.node_id for p in functional if p.node_id not in hlr_parents]
    assert uncovered == [], f"functional PARAs with no HLR child: {uncovered}"


async def test_hlrs_are_well_formed(p3_flow: ForgeFlow) -> None:
    """Each HLR hangs off a PARA at layer 2 with a shall-statement and a title."""
    para_ids = {p.node_id for p in _nodes(p3_flow, NodeType.PARA)}
    for hlr in _nodes(p3_flow, NodeType.HLR):
        assert hlr.parent_id in para_ids, (
            f"{hlr.node_id} parent {hlr.parent_id!r} is not a PARA"
        )
        assert hlr.layer == 2, f"{hlr.node_id} layer must be 2, got {hlr.layer}"
        content = hlr.content.strip()
        assert content.lower().startswith("the system shall "), (
            f"{hlr.node_id} is not a shall-statement: {content[:80]!r}"
        )
        assert hlr.title.strip(), f"{hlr.node_id} has no title"


async def test_no_uncovered_para_gaps_outstanding(p3_flow: ForgeFlow) -> None:
    """The gap analyser must report phase 3's own gap type fully closed."""
    assert GapType.UNCOVERED_PARA not in _gap_types(p3_flow)


# ── Robustness: re-run idempotency (real LLM) ────────────────────────────────


async def test_rerunning_phase_3_creates_no_duplicate_hlrs(p3_flow: ForgeFlow) -> None:
    """Phase re-runs are documented as idempotent: coverage holds, no dupes.

    With every PARA already covered the batch step must find zero gaps and
    dispatch no derivation work, so the HLR population is unchanged.
    """
    before_ids = {h.node_id for h in _nodes(p3_flow, NodeType.HLR)}
    assert before_ids, "precondition: the happy-path run derived HLRs"

    with _llm_budget(_LLM_BUDGET):
        await p3_flow.run_phase(3)

    after = _nodes(p3_flow, NodeType.HLR)
    assert {h.node_id for h in after} == before_ids, (
        "re-running phase 3 changed the HLR population: "
        f"before={sorted(before_ids)} after={sorted(h.node_id for h in after)}"
    )

    by_content: dict[str, list[str]] = {}
    for hlr in after:
        by_content.setdefault(hlr.content.strip().lower(), []).append(hlr.node_id)
    dupes = {k[:60]: v for k, v in by_content.items() if len(v) > 1}
    assert dupes == {}, f"duplicate HLR content after re-run: {dupes}"

    assert GapType.UNCOVERED_PARA not in _gap_types(p3_flow)


# ── Robustness: empty precondition ───────────────────────────────────────────


async def test_phase_3_with_no_paras_completes_without_creating_anything(
    integration_config: ForgeConfig, tmp_path: Path
) -> None:
    """A DOCUMENT with no PARAs offers phase 3 no work: it must neither loop
    nor invent nodes nor spend a single LLM call."""
    flow = await _build_flow(integration_config, tmp_path, "phase3-empty", None)
    await flow.run_phase(0)  # PROJECT node — deterministic, free

    project_id = _nodes(flow, NodeType.PROJECT)[0].node_id
    doc_id = await flow.graph.allocate_node_id(NodeType.DOCUMENT.value)
    await flow.graph.add_node(
        GraphNode(
            node_id=doc_id,
            node_type=NodeType.DOCUMENT.value,
            title="Empty document",
            content="# Spec with no parsed paragraphs\n",
            parent_id=project_id,
            lifecycle=LifecycleState.ACTIVE,
            created_by="integration-seed",
        )
    )

    calls_before = factory.llm_call_count
    with _llm_budget(_LLM_BUDGET):
        await flow.run_phase(3)

    assert _nodes(flow, NodeType.HLR) == [], "phase 3 invented HLRs with no PARAs"
    assert _nodes(flow, NodeType.PARA) == [], "phase 3 invented PARAs — not its job"
    assert factory.llm_call_count == calls_before, (
        f"phase 3 spent {factory.llm_call_count - calls_before} LLM call(s) "
        "despite having no gaps to resolve"
    )
