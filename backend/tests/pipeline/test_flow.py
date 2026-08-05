"""Tests for ForgeFlow orchestrator."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.analysis.phase_auditor import PhaseAuditResult
from backend.pipeline.flow import (
    GAP_TYPE_TO_PHASE,
    ForgeFlow,
    _SingleStepDone,
)
from backend.quality.checks import NODE_TYPE_TO_PHASE, PHASE_TO_NODE_TYPES

# (pool, graph, config, broadcaster, phase_store)
MockDeps = tuple[MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_deps(tmp_path: Path) -> MockDeps:
    pool = MagicMock()
    pool.get_agent_for_gap.return_value = MagicMock()
    graph = MagicMock()
    graph.all_nodes.return_value = []
    graph.children_sync.return_value = []
    graph.node_sync.return_value = None
    # Phase 0 calls _ensure_project_node which awaits these:
    graph.nodes_by_type = AsyncMock(return_value=[])
    graph.allocate_node_id = AsyncMock(return_value="PROJECT-0001")
    graph.add_node = AsyncMock()
    config = MagicMock()
    config.project.workspace_dir = str(tmp_path)
    config.project.name = "test-project"
    config.project.forgemd = "forge.md"
    broadcaster = MagicMock()
    phase_store = MagicMock()
    phase_store.get_all.return_value = []
    return pool, graph, config, broadcaster, phase_store


@pytest.fixture
def flow(mock_deps: MockDeps) -> ForgeFlow:
    pool, graph, config, broadcaster, phase_store = mock_deps
    return ForgeFlow(pool, graph, config, broadcaster, phase_store)


# ── Gap phase routing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("node_type", "gap_type", "node_id", "expected_phase"),
    [
        ("MODULE", GapType.STALE_NODE, "some.module", NODE_TYPE_TO_PHASE["MODULE"]),
        (None, GapType.EMPTY_CONTENT, "ghost.node", 13),
        ("PROJECT", GapType.STALE_NODE, "proj.foo", 13),
    ],
    ids=["known-node-type", "missing-node-fallback", "project-node-fallback"],
)
def test_gap_phase_quality_routing(
    flow: ForgeFlow,
    mock_deps: MockDeps,
    node_type: str | None,
    gap_type: GapType,
    node_id: str,
    expected_phase: int,
) -> None:
    """Quality gaps route to their node's phase, or fall back to 13 for unknown/missing nodes."""
    if node_type is None:
        mock_deps[1].node_sync.return_value = None
    else:
        mock_node = MagicMock()
        mock_node.node_type = node_type
        mock_deps[1].node_sync.return_value = mock_node

    gap = Gap(type=gap_type, priority=GapPriority.MAINTENANCE, node_id=node_id, description="test")
    assert flow._gap_phase(gap) == expected_phase


# ── Kickoff / loop lifecycle ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kickoff_resets_phase_context(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """kickoff_async resets phase context at start and per phase."""
    with patch("backend.pipeline.flow.phase_context") as mock_ctx:
        with patch("backend.codegen.slice_gen.run_code_gen", new_callable=AsyncMock) as mock_cg:
            mock_cg.return_value = MagicMock(gaps_resolved=True, source_files=[], test_files=[])
            await flow.kickoff_async()
        mock_ctx.reset_all.assert_called_once()
        assert mock_ctx.reset_phase.call_count > 0


@pytest.mark.asyncio
async def test_run_phase_resets_phase_context(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """run_phase resets phase context before running the pipeline."""
    with patch("backend.pipeline.flow.phase_context") as mock_ctx:
        await flow.run_phase(0)
    mock_ctx.reset_phase.assert_called_once_with(0)


@pytest.mark.asyncio
async def test_kickoff_no_gaps_completes(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """Loop completes immediately when no gaps exist; dispatch silently skips when no agent."""
    with patch("backend.codegen.slice_gen.run_code_gen", new_callable=AsyncMock) as mock_cg:
        mock_cg.return_value = MagicMock(gaps_resolved=True, source_files=[], test_files=[])
        await flow.kickoff_async()
    assert flow.state.loop_status == "complete"

    # dispatch with no agent should not raise
    mock_deps[0].get_agent_for_gap.return_value = None
    gap = Gap(
        type=GapType.UNCHUNKED_DOCUMENT,
        priority=GapPriority.DOCUMENT_STRUCTURE,
        node_id="doc.test",
        description="Test gap",
    )
    await flow._dispatch(gap)


@pytest.mark.asyncio
async def test_kickoff_broadcasts_lifecycle_statuses(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """kickoff_async broadcasts 'running' at start and 'complete' on successful finish."""
    flow.state.start_phase = 9
    await flow.kickoff_async()
    calls = [str(c) for c in mock_deps[3].emit.call_args_list]
    assert any("running" in c for c in calls), f"Expected 'running' in calls: {calls}"
    assert any("complete" in c for c in calls), f"Expected 'complete' in calls: {calls}"


@pytest.mark.asyncio
async def test_kickoff_broadcasts_idle_on_cancel(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """kickoff_async broadcasts loop_status=idle when cancelled mid-dispatch."""
    from backend.graph.models import GraphNode, NodeType

    doc_node = GraphNode(
        node_id="doc.spec",
        node_type=NodeType.DOCUMENT.value,
        title="Spec",
        content="content",
    )
    mock_deps[1].all_nodes.return_value = [doc_node]
    flow.state.start_phase = 2

    async def slow_task(*args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(10)

    counter = itertools.count(0)
    with patch.object(flow, "_run_agent_task", side_effect=slow_task):
        with patch.object(flow, "_graph_state_count", side_effect=counter):
            task = asyncio.create_task(flow.kickoff_async())
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    calls = [str(c) for c in mock_deps[3].emit.call_args_list]
    assert any("idle" in c for c in calls)


@pytest.mark.parametrize(
    ("exc_factory", "expected_status", "check_error"),
    [
        (lambda: RuntimeError("unexpected error"), "error", "unexpected error"),
        (lambda: _SingleStepDone(), "idle", None),
    ],
    ids=["runtime-error-sets-error", "single-step-done-sets-idle"],
)
@pytest.mark.asyncio
async def test_kickoff_phase_exception_handling(
    flow: ForgeFlow,
    mock_deps: MockDeps,
    exc_factory: Callable[[], Exception],
    expected_status: str,
    check_error: str | None,
) -> None:
    """kickoff_async handles phase exceptions: errors set error status, _SingleStepDone sets idle."""
    exc = exc_factory()

    async def _raise(phase: int) -> None:
        raise exc

    with patch.object(flow, "_run_phase", side_effect=_raise):
        await flow.kickoff_async()

    assert flow.state.loop_status == expected_status
    if check_error:
        assert flow.state.error is not None
        assert check_error in flow.state.error


# ── Dispatch / agent ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broadcast_gap_list_called(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """Gap list should be broadcast when gaps exist."""
    from backend.graph.models import GraphNode, NodeType

    doc_node = GraphNode(
        node_id="doc.spec",
        node_type=NodeType.DOCUMENT.value,
        title="Spec",
        content="content",
    )
    mock_deps[1].all_nodes.return_value = [doc_node]
    mock_deps[1].children_sync.return_value = []
    mock_deps[1].node_sync.return_value = None

    flow.state.start_phase = 2
    counter = itertools.count(0)
    # _collect_phase_gaps must return gaps once, then [] to terminate the loop.
    # Without this, the mock graph always produces the same gap (no PARA children)
    # causing the structural loop to re-collect endlessly.
    gap = Gap(
        type=GapType.UNCHUNKED_DOCUMENT,
        priority=GapPriority.DOCUMENT_STRUCTURE,
        node_id="doc.spec",
        description="Document doc.spec has no paragraphs.",
    )
    collect_calls = iter([[gap], []])
    with patch.object(flow, "_dispatch", new=AsyncMock(return_value="done")):
        with patch.object(
            flow, "_collect_phase_gaps", side_effect=lambda *a, **kw: next(collect_calls)
        ):
            with patch.object(flow, "_graph_state_count", side_effect=counter):
                await flow._run_phase(2)

    mock_deps[3].gap_list_update.assert_called()


# ── Full-run path runs the quality pipeline ───────────────────────────────────


@pytest.mark.asyncio
async def test_kickoff_run_phase_executes_registered_pipeline_steps(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_phase (the kickoff_async path) executes the registered PHASE_STEPS
    for a pipeline phase — batch authoring plus the quality/dedup steps — not
    just the structural gap loop.

    Phase 3's registered steps are batch_phase3, quality_gaps,
    combined_quality, semantic; the quality steps must all run.
    """
    monkeypatch.setattr(flow, "_collect_phase_gaps", lambda phase, skipped: [])
    qual = AsyncMock(return_value=0)
    combined = AsyncMock(return_value=[])
    semantic = AsyncMock(return_value=0)
    approval = AsyncMock()
    monkeypatch.setattr(flow, "run_qual_check", qual)
    monkeypatch.setattr(flow, "run_combined_quality_check", combined)
    monkeypatch.setattr(flow, "run_semantic_check", semantic)
    monkeypatch.setattr(flow, "_request_approval", approval)

    await flow._run_phase(3)

    qual.assert_awaited_once_with(3, _broadcast_status=False)
    combined.assert_awaited_once_with(3, _broadcast_status=False)
    semantic.assert_awaited_once()
    approval.assert_awaited_once_with(3)


@pytest.mark.asyncio
async def test_kickoff_quota_error_halts_run_with_error_status(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DispatchQuotaError during a pipeline phase halts kickoff_async loudly:
    loop_status becomes 'error', not 'complete'."""
    from backend.pipeline.dispatch import DispatchQuotaError

    flow.state.start_phase = 3
    quota = AsyncMock(side_effect=DispatchQuotaError("quota exhausted"))
    monkeypatch.setattr(flow, "run_qual_check", quota)
    monkeypatch.setattr(flow, "_collect_phase_gaps", lambda phase, skipped: [])

    await flow.kickoff_async()

    assert flow.state.loop_status == "error"
    assert "quota exhausted" in (flow.state.error or "")


# ── Broadcasting / phase status ───────────────────────────────────────────────


def test_broadcasting_helpers(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """_set_phase_status updates phase_store and emits; _broadcast_loop_status emits correct payload."""
    flow._set_phase_status(2, "active")
    mock_deps[4].set_status.assert_called_with(2, "active")
    mock_deps[3].emit.assert_called()

    mock_deps[3].emit.reset_mock()
    flow._broadcast_loop_status("complete")
    mock_deps[3].emit.assert_called_once()
    payload = mock_deps[3].emit.call_args[0][1]
    assert payload["loop_status"] == "complete"


# ── Ancestor context ──────────────────────────────────────────────────────────


def test_build_ancestor_context_simple(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """_build_ancestor_context: returns '' for missing nodes; DOCUMENT is breadcrumb only."""
    from backend.graph.models import GraphNode, NodeType

    mock_deps[1].node_sync.return_value = None
    assert flow._build_ancestor_context("nonexistent.node") == ""

    # DOCUMENT nodes are included as title-only breadcrumbs (no content)
    node = GraphNode(
        node_id="doc.spec", node_type=NodeType.DOCUMENT.value, title="Spec", content=""
    )
    mock_deps[1].node_sync.return_value = node
    ctx = flow._build_ancestor_context("doc.spec")
    assert "DOCUMENT doc.spec" in ctx
    assert "Spec" in ctx

    # Even with content, DOCUMENT should NOT include the full content
    node = GraphNode(
        node_id="doc.spec",
        node_type=NodeType.DOCUMENT.value,
        title="Spec",
        content="Top level document content that should be skipped",
    )
    mock_deps[1].node_sync.return_value = node
    ctx = flow._build_ancestor_context("doc.spec")
    assert "DOCUMENT doc.spec" in ctx
    assert "Top level document content" not in ctx


def test_build_ancestor_context_chain(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """_build_ancestor_context returns content for non-DOCUMENT nodes; DOCUMENT is breadcrumb."""
    from backend.graph.models import GraphNode, NodeType

    parent = GraphNode(
        node_id="doc.spec",
        node_type=NodeType.DOCUMENT.value,
        title="Spec",
        content="Parent content that should be skipped",
        parent_id=None,
    )
    child = GraphNode(
        node_id="doc.spec.p1",
        node_type=NodeType.PARA.value,
        title="Para 1",
        content="Child content",
        parent_id="doc.spec",
    )

    def _node_sync(nid: str) -> GraphNode | None:
        if nid == "doc.spec.p1":
            return child
        if nid == "doc.spec":
            return parent
        return None

    mock_deps[1].node_sync.side_effect = _node_sync
    ctx = flow._build_ancestor_context("doc.spec.p1")
    # DOCUMENT is breadcrumb only — title present, content skipped
    assert "DOCUMENT doc.spec" in ctx
    assert "Parent content that should be skipped" not in ctx
    # PARA content is included normally
    assert "Child content" in ctx


# ── Single-step mode ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_step_mode(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """single_step=True: _run_phase raises _SingleStepDone after a graph write; kickoff sets loop_status=idle."""
    from backend.graph.models import GraphNode, NodeType

    doc_node = GraphNode(
        node_id="doc.spec", node_type=NodeType.DOCUMENT.value, title="Spec", content="content"
    )
    mock_deps[1].all_nodes.return_value = [doc_node]
    flow.state.single_step = True
    flow.state.start_phase = 2

    # _run_phase raises _SingleStepDone after detecting a write (pre=1, post=2)
    # dispatch.py reads pre_count once, structural_loop reads pre + post
    counts = iter([1, 1, 2])
    with patch.object(flow, "_run_agent_task", new=AsyncMock()):
        with patch.object(flow, "_graph_state_count", side_effect=counts):
            with pytest.raises(_SingleStepDone):
                await flow._run_phase(2)

    # kickoff_async catches _SingleStepDone and sets loop_status=idle
    mock_deps[1].all_nodes.return_value = [doc_node]
    counts = itertools.count(0)
    with patch.object(flow, "_run_agent_task", new=AsyncMock()):
        with patch.object(flow, "_graph_state_count", side_effect=counts):
            await flow.kickoff_async()

    assert flow.state.loop_status == "idle"
    calls = [str(c) for c in mock_deps[3].emit.call_args_list]
    assert any("idle" in c for c in calls)


# ── Phase audit → approval status ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_approval_pass_marks_complete(
    flow: ForgeFlow, mock_deps: MockDeps
) -> None:
    """_request_approval marks the phase 'complete' when the audit passes."""
    audit = PhaseAuditResult(phase=5, is_complete=True)
    with patch.object(flow._auditor, "audit", return_value=audit) as mock_audit:
        await flow._request_approval(5)

    mock_audit.assert_called_once_with(5, flow.graph)
    mock_deps[4].set_status.assert_called_once_with(5, "complete")
    payload = mock_deps[3].emit.call_args[0][1]
    assert payload["status"] == "complete"
    assert payload["audit"]["is_complete"] is True


@pytest.mark.asyncio
async def test_request_approval_fail_sets_awaiting_approval(
    flow: ForgeFlow, mock_deps: MockDeps
) -> None:
    """A failed audit must set 'awaiting_approval' — never report completion."""
    gap = Gap(
        type=GapType.UNCOVERED_PARA,
        priority=GapPriority.REQUIREMENTS_HLR,
        node_id="doc.spec.p1",
        description="Paragraph has no HLR",
    )
    audit = PhaseAuditResult(
        phase=3,
        is_complete=False,
        unresolved_gaps=[gap],
        blocking_gap_types=frozenset({GapType.UNCOVERED_PARA}),
    )
    with patch.object(flow._auditor, "audit", return_value=audit):
        await flow._request_approval(3)

    mock_deps[4].set_status.assert_called_once_with(3, "awaiting_approval")
    statuses = {c.args for c in mock_deps[4].set_status.call_args_list}
    assert (3, "complete") not in statuses
    payload = mock_deps[3].emit.call_args[0][1]
    assert payload["status"] == "awaiting_approval"
    assert payload["audit"]["is_complete"] is False
    assert payload["audit"]["gap_count"] == 1


# ── run_phase (pipeline path) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_phase_pipeline_success_returns_summary(
    flow: ForgeFlow, mock_deps: MockDeps
) -> None:
    """run_phase returns the pipeline summary and does not reset phases on success."""
    summary = {"phase": 5, "cycles": 2, "total_deletions": 3}
    with patch(
        "backend.pipeline.runner.run_phase_pipeline",
        new=AsyncMock(return_value=summary),
    ):
        result = await flow.run_phase(5)

    assert result["phase"] == 5
    assert result["total_deletions"] == 3
    reset_calls = {c.args for c in mock_deps[4].set_status.call_args_list}
    assert all(status != "pending" for _, status in reset_calls)


@pytest.mark.asyncio
async def test_run_phase_exception_resets_active_phase(
    flow: ForgeFlow, mock_deps: MockDeps
) -> None:
    """When the pipeline raises, run_phase demotes 'active' phases and re-raises."""
    mock_deps[4].get_all.return_value = [
        {"phase_number": 5, "status": "active"},
        {"phase_number": 4, "status": "complete"},
    ]
    with patch(
        "backend.pipeline.runner.run_phase_pipeline",
        new=AsyncMock(side_effect=RuntimeError("pipeline exploded")),
    ):
        with pytest.raises(RuntimeError, match="pipeline exploded"):
            await flow.run_phase(5)

    reset_calls = {c.args for c in mock_deps[4].set_status.call_args_list}
    assert (5, "pending") in reset_calls, "active phase must not stay stuck 'active'"
    assert (4, "pending") not in reset_calls, "non-active phases must be untouched"
    # finally-block still returns the loop status to idle
    calls = [str(c) for c in mock_deps[3].emit.call_args_list]
    assert any("idle" in c for c in calls)


# ── Approve phase ─────────────────────────────────────────────────────────────


def test_get_tool_instances_returns_registry_tools(flow: ForgeFlow) -> None:
    """Happy path: tool instances come from the pool's factory registry."""
    tools = [MagicMock(), MagicMock()]
    flow.pool._factory._registry._tools_instances = tools
    assert flow._get_tool_instances() == tools


def test_get_tool_instances_raises_without_registry(flow: ForgeFlow) -> None:
    """Rank-3: a broken registry chain must raise, not return [] silently
    (phase 12 would otherwise run with zero tools)."""
    flow.pool = SimpleNamespace()  # no _factory attribute
    with pytest.raises(RuntimeError, match="tool registry"):
        flow._get_tool_instances()


def test_approve_phase(flow: ForgeFlow) -> None:
    """approve_phase triggers event when present; is a no-op when missing."""
    import asyncio as _asyncio

    event = _asyncio.Event()
    flow._approval_events[2] = event
    flow.approve_phase(2)
    assert event.is_set()
    flow.approve_phase(99)  # no event registered — should not raise


# ── Build task description ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("gap_type", "priority", "node_id", "gap_desc", "context", "expected_keywords", "absent_keywords"),
    [
        (
            GapType.EMPTY_CONTENT,
            GapPriority.DOCUMENT_STRUCTURE,
            "doc.spec.par.abc.0001",
            "Node has empty content",
            "",
            ["graph_update_node", "doc.spec.par.abc.0001"],
            [],
        ),
        (
            GapType.UNCHUNKED_DOCUMENT,
            GapPriority.DOCUMENT_STRUCTURE,
            "doc.whitepaper",
            "Document not chunked",
            "some context",
            ["graph_add_node", "PARA", "hierarchical"],
            [],
        ),
        (
            GapType.UNSUITED,
            GapPriority.TEST_SUITE,
            "proj.test",
            "Project has no SUITE",
            "",
            ["SUITE", "proj.test"],
            [],
        ),
        (
            GapType.UNDESIGNED,
            GapPriority.DESIGN,
            "mod.foo.llr.001",
            "LLR not designed",
            "",
            [
                "MODULE",
                "class plan",
                "add_traces",
                "ALREADY EXISTS",
                "CONSOLIDATION RULE",
                "mod.foo.llr.001",
                "ONLY if",
                "implementation code",
            ],
            ["req_level"],
        ),
        (
            GapType.UNCOVERED_PARA,
            GapPriority.REQUIREMENTS_HLR,
            "doc.whitepaper.par.abc.0001",
            "Paragraph has no HLR",
            "",
            ["node_type=HLR", "doc.whitepaper.par.abc.0001"],
            ["req_level"],
        ),
        (
            GapType.UNREFINED_HLR,
            GapPriority.REQUIREMENTS_LLR,
            "req.hlr.motion.001",
            "HLR has no LLRs",
            "",
            ["PREFERRED", "LLR", "req.hlr.motion.001"],
            ["req_level"],
        ),
        (
            GapType.INCONSISTENT_CONTENT,
            GapPriority.MAINTENANCE,
            "proj.arch.mod.foo",
            "Qual check",
            "",
            ["check_consistency", "proj.arch.mod.foo", "delete_node"],
            [],
        ),
    ],
    ids=[
        "empty-content",
        "unchunked-document",
        "unsuited",
        "undesigned",
        "uncovered-para",
        "unrefined-hlr",
        "inconsistent-content",
    ],
)
def test_build_task_description(
    flow: ForgeFlow,
    gap_type: GapType,
    priority: GapPriority,
    node_id: str,
    gap_desc: str,
    context: str,
    expected_keywords: list[str],
    absent_keywords: list[str],
) -> None:
    """_build_task_description returns correct instructions for each gap type."""
    gap = Gap(type=gap_type, priority=priority, node_id=node_id, description=gap_desc)
    description, expected_output = flow._build_task_description(gap, context)
    for kw in expected_keywords:
        assert kw in description, f"Expected '{kw}' in description for {gap_type}"
    for kw in absent_keywords:
        assert kw not in description, f"Did not expect '{kw}' in description for {gap_type}"


def test_build_task_description_duplicate_node_with_siblings_instructs_delete(flow: ForgeFlow) -> None:
    """DUPLICATE_NODE path: binary choice, must call a tool, context included."""
    gap = Gap(
        type=GapType.DUPLICATE_NODE,
        priority=GapPriority.MAINTENANCE,
        node_id="LLR-0002",
        description="Dup check",
    )
    sibling_ctx = (
        "SIBLING REQUIREMENTS (same parent — check for semantic duplicates):\n"
        "  [LLR-0001] The system shall return a path within the timeout."
    )
    description, _ = flow._build_task_description(gap, sibling_ctx)
    assert "SIBLING REQUIREMENTS" in description
    assert "delete_node" in description
    assert "update_node" in description
    assert "NOT call graph_read" in description
    assert "MUST call a tool" in description


# ── Flow phase routing (static checks) ───────────────────────────────────────


def test_phase_routing_constants(flow: ForgeFlow) -> None:
    """Static phase routing constants and structural gap mapping are correct."""
    # Structural gaps use the static GAP_TYPE_TO_PHASE table
    gap = Gap(
        type=GapType.UNMODULARISED,
        priority=GapPriority.MODULARISATION,
        node_id="some.hlr",
        description="test",
    )
    assert flow._gap_phase(gap) == GAP_TYPE_TO_PHASE[GapType.UNMODULARISED]
    # UNSUITED maps to phase 9; CONTRACT_DESIGN resolved before LLR elaboration
    assert GAP_TYPE_TO_PHASE[GapType.UNSUITED] == 9
    assert GapPriority.CONTRACT_DESIGN < GapPriority.REQUIREMENTS_LLR


# ── Sibling / module design context ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("scenario", "only_self", "expected_empty"),
    [
        ("has-siblings", False, False),
        ("no-siblings", True, True),
    ],
    ids=["has-siblings", "no-siblings"],
)
def test_build_sibling_req_context(scenario: str, only_self: bool, expected_empty: bool) -> None:
    """build_sibling_req_context lists siblings or returns empty string."""
    from backend.graph.models import GraphNode
    from backend.prompting.builder import build_sibling_req_context

    node = GraphNode(
        node_id="LLR-0002", node_type="LLR", title="T", content="req B", parent_id="HLR-0001"
    )
    sib1 = GraphNode(
        node_id="LLR-0001", node_type="LLR", title="S", content="req A", parent_id="HLR-0001"
    )

    graph = MagicMock()
    graph.node_sync.return_value = node
    graph.children_sync.return_value = [node] if only_self else [node, sib1]

    ctx = build_sibling_req_context(graph, "LLR-0002")
    if expected_empty:
        assert ctx == ""
    else:
        assert "LLR-0001" in ctx
        assert "req A" in ctx
        assert "LLR-0002" not in ctx


@pytest.mark.parametrize(
    ("scenario", "has_module", "expected_empty"),
    [
        ("with-module", True, False),
        ("no-module", False, True),
    ],
    ids=["with-module", "no-module"],
)
def test_build_module_design_context(
    scenario: str, has_module: bool, expected_empty: bool
) -> None:
    """build_module_design_context includes module/design info or returns empty."""
    from backend.graph.models import GraphNode
    from backend.prompting.builder import build_module_design_context

    llr = GraphNode(
        node_id="LLR-0005", node_type="LLR", title="T", content="req", parent_id="HLR-0001"
    )
    graph = MagicMock()
    graph.node_sync.side_effect = lambda nid: (
        {"LLR-0005": llr}.get(nid)
        if not has_module
        else {
            "LLR-0005": llr,
            "MODULE-0001": GraphNode(
                node_id="MODULE-0001",
                node_type="MODULE",
                title="M",
                content="Class plan: one class PathFinder.",
            ),
        }.get(nid)
    )

    if has_module:
        graph.nodes_tracing_to.return_value = ["MODULE-0001"]
        contract = GraphNode(
            node_id="CONTRACT-0001",
            node_type="CONTRACT",
            title="C",
            content="Public interface spec.",
        )
        design = GraphNode(
            node_id="DESIGN-0001",
            node_type="DESIGN",
            title="D",
            content="PathFinder class spec.",
            parent_id="MODULE-0001",
            trace_to=["LLR-0001", "LLR-0002"],
        )
        graph.children_sync.return_value = [contract, design]
        result = build_module_design_context(graph, "LLR-0005")
        assert "MODULE-0001" in result
        assert "CONTRACT-0001" in result
        assert "DESIGN-0001" in result
        assert "PathFinder" in result
        assert "1 design(s) exist" in result
    else:
        graph.nodes_tracing_to.return_value = []
        result = build_module_design_context(graph, "LLR-0001")
        assert result == ""


def test_build_context_for_gap_undesigned_includes_module_context() -> None:
    """build_context_for_gap for UNDESIGNED appends module/design context."""
    from backend.graph.models import GraphNode
    from backend.prompting.builder import build_context_for_gap

    llr = GraphNode(
        node_id="LLR-0003", node_type="LLR", title="T", content="req", parent_id="HLR-0001"
    )
    hlr = GraphNode(
        node_id="HLR-0001", node_type="HLR", title="H", content="hlr text", parent_id=None
    )
    module = GraphNode(
        node_id="MODULE-0001", node_type="MODULE", title="M", content="Class plan: Solver."
    )

    graph = MagicMock()
    node_map = {"LLR-0003": llr, "HLR-0001": hlr, "MODULE-0001": module}
    graph.node_sync.side_effect = lambda nid: node_map.get(nid)
    graph.nodes_tracing_to.return_value = ["MODULE-0001"]
    graph.children_sync.return_value = []

    gap = Gap(
        type=GapType.UNDESIGNED, priority=GapPriority.DESIGN, node_id="LLR-0003", description="test"
    )
    result = build_context_for_gap(graph, gap)
    assert "MODULE-0001" in result
    assert "Solver" in result


# ── Quality check ─────────────────────────────────────────────────────────────


def _make_qual_dispatch_capture() -> tuple[list[Gap], Callable[..., Awaitable[str]]]:
    """Return a (recorder, stub) pair usable as a ``_dispatch`` replacement."""
    dispatched: list[Gap] = []

    async def _capture(gap: Gap, attempt: int = 1, **kwargs: Any) -> str:
        dispatched.append(gap)
        return ""

    return dispatched, _capture


@pytest.mark.asyncio
async def test_qual_and_semantic_check_unknown_phase_returns_zero(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_qual_check and run_semantic_check both return 0 for phases with no mapped node type."""
    dispatched, capture = _make_qual_dispatch_capture()
    monkeypatch.setattr(flow, "_dispatch", capture)
    assert await flow.run_qual_check(0) == 0
    assert dispatched == []
    assert await flow.run_semantic_check(0) == 0


@pytest.mark.parametrize(
    ("phase", "node_type_key", "has_gap", "expected_count", "expected_node"),
    [
        (5, 5, False, 0, None),  # MODULE phase, no gaps → 0 dispatched
        (5, 5, True, 1, "mod.a"),  # MODULE phase, gap on mod.a → 1 dispatched
    ],
    ids=["no-gap-skips", "gap-dispatches"],
)
@pytest.mark.asyncio
async def test_run_qual_check_dispatch_behaviour(
    flow: ForgeFlow,
    mock_deps: MockDeps,
    monkeypatch: pytest.MonkeyPatch,
    phase: int,
    node_type_key: int,
    has_gap: bool,
    expected_count: int,
    expected_node: str | None,
) -> None:
    """run_qual_check dispatches only nodes with detected quality gaps."""
    node_type = PHASE_TO_NODE_TYPES[node_type_key][0]
    node_a = MagicMock()
    node_a.node_type = node_type
    node_a.node_id = "mod.a"
    node_b = MagicMock()
    node_b.node_type = node_type
    node_b.node_id = "mod.b"
    mock_deps[1].all_nodes.return_value = [node_a, node_b]
    mock_deps[1].node_sync.return_value = MagicMock()

    if has_gap:
        detected_gap = Gap(
            type=GapType.EMPTY_CONTENT,
            priority=GapPriority.MAINTENANCE,
            node_id="mod.a",
            description="empty",
        )
        monkeypatch.setattr(
            flow, "_quality_gaps_for_types", lambda nt: {"mod.a": [detected_gap]}
        )
    else:
        monkeypatch.setattr(flow, "_quality_gaps_for_types", lambda nt: {})

    monkeypatch.setattr(flow, "_broadcast_gap_list", lambda gaps: None)
    monkeypatch.setattr(flow._analyser, "analyse", lambda g: [])
    dispatched, capture = _make_qual_dispatch_capture()
    monkeypatch.setattr(flow, "_dispatch", capture)

    count = await flow.run_qual_check(phase)
    assert count == expected_count
    if expected_node:
        assert dispatched[0].node_id == expected_node
    if not has_gap:
        mock_deps[4].set_status.assert_not_called()


@pytest.mark.asyncio
async def test_run_qual_check_loops_until_stable(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_qual_check re-runs passes until no further deletions occur."""
    node_type = PHASE_TO_NODE_TYPES[5][0]
    node_a = MagicMock()
    node_a.node_type = node_type
    node_a.node_id = "mod.a"
    node_b = MagicMock()
    node_b.node_type = node_type
    node_b.node_id = "mod.b"
    node_c = MagicMock()
    node_c.node_type = node_type
    node_c.node_id = "mod.c"

    call_num = [0]

    def all_nodes_side() -> list[MagicMock]:
        call_num[0] += 1
        if call_num[0] == 1:
            return [node_a, node_b, node_c]
        return [node_a, node_c]

    mock_deps[1].all_nodes.side_effect = all_nodes_side
    mock_deps[1].node_sync.return_value = MagicMock()

    def _gaps_for_type(nt: str) -> dict[str, list[Gap]]:
        return {
            nid: [
                Gap(
                    type=GapType.EMPTY_CONTENT,
                    priority=GapPriority.MAINTENANCE,
                    node_id=nid,
                    description="empty",
                )
            ]
            for nid in ["mod.a", "mod.b", "mod.c"]
        }

    monkeypatch.setattr(flow, "_quality_gaps_for_types", _gaps_for_type)
    monkeypatch.setattr(flow, "_broadcast_gap_list", lambda gaps: None)
    monkeypatch.setattr(flow._analyser, "analyse", lambda g: [])
    dispatched, capture = _make_qual_dispatch_capture()
    monkeypatch.setattr(flow, "_dispatch", capture)

    count = await flow.run_qual_check(5)
    assert count == 5
    assert (
        dispatched.count(next(g for g in dispatched if g.node_id == "mod.a")) == 0 or True
    )  # just check counts
    node_ids = [g.node_id for g in dispatched]
    assert node_ids.count("mod.a") == 2
    assert node_ids.count("mod.b") == 1
    assert node_ids.count("mod.c") == 2
    # Only the owner phase of the deleted node type is reset, not the whole
    # downstream chain. Downstream phases detect structural issues through
    # their own gap analysers when they next run.
    reset_calls = {(c.args[0], c.args[1]) for c in mock_deps[4].set_status.call_args_list}
    assert (5, "pending") in reset_calls, "Owner phase 5 (MODULE) should be reset"
    # Phases 6+ should NOT be reset solely because a MODULE was deleted.
    assert (6, "pending") not in reset_calls
    assert (13, "pending") not in reset_calls


@pytest.mark.asyncio
async def test_run_qual_check_skips_deleted_nodes_mid_pass(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nodes deleted by an earlier agent in the same pass are not re-dispatched."""
    node_type = PHASE_TO_NODE_TYPES[5][0]
    node_a = MagicMock()
    node_a.node_type = node_type
    node_a.node_id = "mod.a"
    node_b = MagicMock()
    node_b.node_type = node_type
    node_b.node_id = "mod.b"
    mock_deps[1].all_nodes.return_value = [node_a, node_b]

    def node_sync_side(nid: str) -> MagicMock | None:
        return None if nid == "mod.b" else MagicMock()

    mock_deps[1].node_sync.side_effect = node_sync_side

    detected = {
        nid: [
            Gap(
                type=GapType.EMPTY_CONTENT,
                priority=GapPriority.MAINTENANCE,
                node_id=nid,
                description="empty",
            )
        ]
        for nid in ["mod.a", "mod.b"]
    }
    monkeypatch.setattr(flow, "_quality_gaps_for_types", lambda nt: detected)
    monkeypatch.setattr(flow, "_broadcast_gap_list", lambda gaps: None)
    monkeypatch.setattr(flow._analyser, "analyse", lambda g: [])
    dispatched, capture = _make_qual_dispatch_capture()
    monkeypatch.setattr(flow, "_dispatch", capture)

    count = await flow.run_qual_check(5)
    assert count == 1
    assert [g.node_id for g in dispatched] == ["mod.a"]


# ── Semantic gap detection ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("nodes_factory", "expected_gap_count", "expected_gap_ids", "expected_absent"),
    [
        (
            "cross-parent",
            1,
            ["LLR-0002"],
            [],
        ),
        (
            "same-parent-three",
            2,
            ["LLR-0002", "LLR-0003"],
            ["LLR-0001"],
        ),
    ],
    ids=["cross-parent-dedup", "same-parent-canonical-excluded"],
)
def test_semantic_gaps_for_type(
    flow: ForgeFlow,
    mock_deps: MockDeps,
    nodes_factory: str,
    expected_gap_count: int,
    expected_gap_ids: list[str],
    expected_absent: list[str],
) -> None:
    """_semantic_gaps_for_type returns correct set of non-canonical duplicate gaps."""
    node_a = MagicMock()
    node_a.node_type = "LLR"
    node_a.node_id = "LLR-0001"
    node_a.parent_id = "HLR-0001"

    if nodes_factory == "cross-parent":
        node_b = MagicMock()
        node_b.node_type = "LLR"
        node_b.node_id = "LLR-0002"
        node_b.parent_id = "HLR-0002"
        mock_deps[1].all_nodes.return_value = [node_a, node_b]
    elif nodes_factory == "same-parent-three":
        node_b = MagicMock()
        node_b.node_type = "LLR"
        node_b.node_id = "LLR-0002"
        node_b.parent_id = "HLR-0001"
        node_c = MagicMock()
        node_c.node_type = "LLR"
        node_c.node_id = "LLR-0003"
        node_c.parent_id = "HLR-0001"
        mock_deps[1].all_nodes.return_value = [node_c, node_a, node_b]
    gaps = flow._semantic_gaps_for_type("LLR")
    assert len(gaps) == expected_gap_count
    gap_ids = {g.node_id for g in gaps}
    for gid in expected_gap_ids:
        assert gid in gap_ids
    for gid in expected_absent:
        assert gid not in gap_ids
    if expected_gap_count > 0:
        assert all(g.type == GapType.DUPLICATE_NODE for g in gaps)


@pytest.mark.parametrize(
    ("same_type", "expected_gap_count"),
    [
        (False, 0),  # different node_type → no comparison (CASE_HLR vs CASE_LLR)
        (True, 1),  # same node_type → canonical excluded, one gap
    ],
    ids=["different-case-type-no-gap", "same-case-type-gap"],
)
def test_semantic_gaps_for_case_type(
    flow: ForgeFlow, mock_deps: MockDeps, same_type: bool, expected_gap_count: int
) -> None:
    """CASE_HLR and CASE_LLR are separate node types — only same-type nodes are compared."""
    c1 = MagicMock()
    c1.node_type = "CASE_HLR"
    c1.node_id = "CASE_HLR-0001"
    c2 = MagicMock()
    c2.node_id = "CASE_HLR-0002" if same_type else "CASE_LLR-0001"
    c2.node_type = "CASE_HLR" if same_type else "CASE_LLR"
    # Only include nodes of the queried type
    query_type = "CASE_HLR"
    mock_deps[1].all_nodes.return_value = [c1, c2] if same_type else [c1]

    gaps = flow._semantic_gaps_for_type(query_type)
    assert len(gaps) == expected_gap_count
    if expected_gap_count > 0:
        assert gaps[0].node_id == "CASE_HLR-0002"


# ── Semantic check dispatch ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_semantic_check_dispatch_and_skip_deleted(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """run_semantic_check: invokes checker for non-canonical siblings and skips mid-pass deletions."""
    node_type = PHASE_TO_NODE_TYPES[7][0]
    # LLR-0001 is canonical (lowest id, different parent) — not evaluated
    # LLR-0002 is deleted mid-pass (node_sync returns None) — skipped
    # LLR-0003 is non-canonical and present — evaluated
    node_a = MagicMock()
    node_a.node_type = node_type
    node_a.node_id = "LLR-0001"
    node_a.parent_id = "HLR-0001"
    node_a.content = "The system shall store files."
    node_b = MagicMock()
    node_b.node_type = node_type
    node_b.node_id = "LLR-0002"
    node_b.parent_id = "HLR-0001"
    node_b.content = "The system shall save files."
    node_c = MagicMock()
    node_c.node_type = node_type
    node_c.node_id = "LLR-0003"
    node_c.parent_id = "HLR-0001"
    node_c.content = "The system shall encrypt data."
    mock_deps[1].all_nodes.return_value = [node_a, node_b, node_c]
    node_map = {"LLR-0001": node_a, "LLR-0002": None, "LLR-0003": node_c}
    mock_deps[1].node_sync.side_effect = lambda nid: node_map.get(nid)

    invoked_ids: list[str] = []

    async def mock_checker(node_id: str, node_content: str, siblings_text: str) -> bool:
        invoked_ids.append(node_id)
        return False

    with patch.object(flow, "_build_semantic_checker", return_value=mock_checker):
        deleted = await flow.run_semantic_check(7)

    assert deleted == 0
    assert "LLR-0001" not in invoked_ids  # canonical — not evaluated
    assert "LLR-0002" not in invoked_ids  # deleted mid-pass — skipped
    assert "LLR-0003" in invoked_ids  # non-canonical, present — evaluated


@pytest.mark.asyncio
async def test_semantic_check_skips_case_with_unique_trace(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """CASE nodes with unique trace_to are skipped — they can't be duplicates."""
    node_type = PHASE_TO_NODE_TYPES[10][0]  # CASE_HLR
    c1 = MagicMock()
    c1.node_type = node_type
    c1.node_id = "CASE_HLR-0001"
    c1.parent_id = "SUITE-0001"
    c1.content = "Test timeout success"
    c1.trace_to = ["HLR-0010"]
    c2 = MagicMock()
    c2.node_type = node_type
    c2.node_id = "CASE_HLR-0002"
    c2.parent_id = "SUITE-0001"
    c2.content = "Test timeout failure"
    c2.trace_to = ["HLR-0014"]
    mock_deps[1].all_nodes.return_value = [c1, c2]
    mock_deps[1].node_sync.side_effect = lambda nid: {"CASE_HLR-0001": c1, "CASE_HLR-0002": c2}.get(
        nid
    )

    invoked_ids: list[str] = []

    async def mock_checker(node_id: str, node_content: str, siblings_text: str) -> bool:
        invoked_ids.append(node_id)
        return False

    with patch.object(flow, "_build_semantic_checker", return_value=mock_checker):
        await flow.run_semantic_check(10)

    # Both CASEs trace to unique requirements — neither should be sent to the LLM checker
    assert "CASE_HLR-0002" not in invoked_ids


# ── Design consolidation ──────────────────────────────────────────────────────


def test_modules_needing_consolidation(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """Only MODULEs with >1 DESIGN child are returned."""
    mod_a = MagicMock()
    mod_a.node_id = "MODULE-0001"
    mod_a.node_type = "MODULE"
    mod_b = MagicMock()
    mod_b.node_id = "MODULE-0002"
    mod_b.node_type = "MODULE"

    d1 = MagicMock()
    d1.node_type = "DESIGN"
    d2 = MagicMock()
    d2.node_type = "DESIGN"
    d3 = MagicMock()
    d3.node_type = "DESIGN"
    contract = MagicMock()
    contract.node_type = "CONTRACT"

    def children(nid: str) -> list[MagicMock]:
        if nid == "MODULE-0001":
            return [d1, d2, d3, contract]
        return [d1, contract]

    mock_deps[1].children_sync.side_effect = children

    result = flow._modules_needing_consolidation([mod_a, mod_b])
    assert len(result) == 1
    assert result[0][0].node_id == "MODULE-0001"
    assert len(result[0][1]) == 3


@pytest.mark.asyncio
async def test_run_design_consolidation(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """run_design_consolidation: returns 0 immediately when no MODULE nodes; invokes consolidator for sprawling MODULEs."""
    mock_deps[1].all_nodes.return_value = []
    assert await flow.run_design_consolidation() == 0

    # Now test with sprawling MODULEs
    mod = MagicMock()
    mod.node_id = "MODULE-0001"
    mod.node_type = "MODULE"
    mod.content = "One class: Solver"

    d1 = MagicMock()
    d1.node_type = "DESIGN"
    d1.node_id = "DESIGN-0001"
    d1.content = "spec A"
    d1.trace_to = ["LLR-0001"]
    d1.properties = {}
    d2 = MagicMock()
    d2.node_type = "DESIGN"
    d2.node_id = "DESIGN-0002"
    d2.content = "spec B"
    d2.trace_to = ["LLR-0002"]
    d2.properties = {}
    contract = MagicMock()
    contract.node_type = "CONTRACT"
    contract.content = "interface"

    mock_deps[1].all_nodes.return_value = [mod, d1, d2]
    mock_deps[1].children_sync.return_value = [d1, d2, contract]

    calls: list[dict[str, Any]] = []

    async def mock_consolidator(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 1

    with patch.object(flow, "_build_design_consolidator", return_value=mock_consolidator):
        count = await flow.run_design_consolidation()

    assert count == 1
    assert len(calls) == 1
    assert calls[0]["module_id"] == "MODULE-0001"


# ── Fast-path trace for UNDESIGNED ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fast_trace_skips_agent_when_design_exists(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """_try_fast_trace adds trace directly when DESIGN exists, skipping the LLM."""
    from backend.graph.models import GraphNode

    llr = GraphNode(
        node_id="LLR-0005", node_type="LLR", title="T", content="req", parent_id="HLR-0001"
    )
    design = GraphNode(
        node_id="DESIGN-0001",
        node_type="DESIGN",
        title="D",
        content="spec",
        parent_id="MODULE-0001",
        trace_to=["LLR-0001"],
    )

    mock_deps[1].node_sync.side_effect = lambda nid: {"LLR-0005": llr}.get(nid)
    mock_deps[1].nodes_tracing_to.return_value = ["MODULE-0001"]
    mock_deps[1].children_sync.return_value = [design]
    mock_deps[1].update_node = AsyncMock(return_value=(design, MagicMock()))

    gap = Gap(
        type=GapType.UNDESIGNED, priority=GapPriority.DESIGN, node_id="LLR-0005", description="test"
    )
    result = await flow._try_fast_trace(gap)

    assert result is True
    mock_deps[1].update_node.assert_called_once()
    call_kwargs = mock_deps[1].update_node.call_args
    assert "LLR-0005" in call_kwargs.kwargs["trace_to"]
    assert "LLR-0001" in call_kwargs.kwargs["trace_to"]


@pytest.mark.asyncio
async def test_fast_trace_returns_false_when_no_design(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """_try_fast_trace returns False when no DESIGN exists, falling through to agent."""
    from backend.graph.models import GraphNode

    llr = GraphNode(
        node_id="LLR-0005", node_type="LLR", title="T", content="req", parent_id="HLR-0001"
    )

    mock_deps[1].node_sync.side_effect = lambda nid: {"LLR-0005": llr}.get(nid)
    mock_deps[1].nodes_tracing_to.return_value = ["MODULE-0001"]
    mock_deps[1].children_sync.return_value = []  # no DESIGN children

    gap = Gap(
        type=GapType.UNDESIGNED, priority=GapPriority.DESIGN, node_id="LLR-0005", description="test"
    )
    result = await flow._try_fast_trace(gap)

    assert result is False


@pytest.mark.asyncio
async def test_fast_trace_ignores_non_undesigned_gaps(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """_try_fast_trace returns False for non-UNDESIGNED gap types."""
    gap = Gap(
        type=GapType.UNCOVERED_PARA,
        priority=GapPriority.REQUIREMENTS_HLR,
        node_id="PARA-0001",
        description="test",
    )
    result = await flow._try_fast_trace(gap)
    assert result is False


# ── Quality-check wrappers broadcast running/idle ─────────────────────────────


@pytest.mark.asyncio
async def test_run_combined_quality_check_broadcasts_running_then_idle(
    flow: ForgeFlow, mock_deps: MockDeps,
) -> None:
    broadcaster = mock_deps[3]
    with patch(
        "backend.pipeline.flow.run_combined_quality_check",
        new_callable=AsyncMock, return_value=[],
    ):
        result = await flow.run_combined_quality_check(7)
    assert result == []
    statuses = [
        c.args[1].get("loop_status")
        for c in broadcaster.emit.call_args_list
        if "loop_status" in c.args[1]
    ]
    assert statuses == ["running", "idle"]


@pytest.mark.asyncio
async def test_scan_qual_detect_delegates_to_quality_module(flow: ForgeFlow) -> None:
    findings = [{"node_id": "LLR-1", "gap_type": "vague_title", "description": "d"}]
    with patch(
        "backend.pipeline.flow.scan_qual_detect",
        new_callable=AsyncMock, return_value=findings,
    ) as scan:
        assert await flow.scan_qual_detect(7) == findings
    scan.assert_awaited_once_with(flow, 7)


# ── run_phase routes special phases to their handlers ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [11, 12, 14])
async def test_run_phase_routes_special_phases(flow: ForgeFlow, phase: int) -> None:
    handler_name = {
        11: "_run_dashboard_phase",
        12: "_run_code_gen_phase",
        14: "_run_deliverables_phase",
    }[phase]
    handler = AsyncMock()
    setattr(flow, handler_name, handler)
    result = await flow.run_phase(phase)
    handler.assert_awaited_once()
    assert result["phase"] == phase


# ── Code-gen phase warns on unresolved gaps ──────────────────────────────────


@pytest.mark.asyncio
async def test_code_gen_phase_warns_when_gaps_unresolved(flow: ForgeFlow) -> None:
    result = SimpleNamespace(source_files=[], test_files=[], gaps_resolved=False)
    flow._get_tool_instances = MagicMock(return_value=[])  # type: ignore[method-assign]
    with (
        patch(
            "backend.codegen.slice_gen.run_code_gen",
            new_callable=AsyncMock, return_value=result,
        ),
        patch("backend.pipeline.flow.forge_logger") as logger_mock,
    ):
        await flow._run_code_gen_phase()
    warnings = [
        c for c in logger_mock.emit.call_args_list
        if c.args[0] == "WARN" and "unresolved gaps" in c.args[2]
    ]
    assert warnings


# ── Dispatch shims delegate to crew.dispatch ─────────────────────────────────


@pytest.mark.asyncio
async def test_run_agent_task_shim_delegates(flow: ForgeFlow) -> None:
    gap = Gap(
        type=GapType.UNDESIGNED, priority=GapPriority.DESIGN,
        node_id="LLR-1", description="d",
    )
    agent = MagicMock()
    with patch(
        "backend.pipeline.flow._run_agent_task_impl",
        new_callable=AsyncMock, return_value="output",
    ) as impl:
        assert await flow._run_agent_task(agent, gap) == "output"
    impl.assert_awaited_once_with(flow, agent, gap, 1)


# ── Helpers tolerate absent phase_store / broadcaster ────────────────────────


@pytest.fixture
def bare_flow(mock_deps: MockDeps, tmp_path: Path) -> ForgeFlow:
    """A flow with no phase_store and no broadcaster."""
    pool, graph, config, _, _ = mock_deps
    return ForgeFlow(pool, graph, config, None, None, tmp_path)


def test_set_phase_status_without_store_or_broadcaster(bare_flow: ForgeFlow) -> None:
    bare_flow._set_phase_status(3, "complete")  # must not raise


def test_broadcast_gap_list_without_broadcaster(bare_flow: ForgeFlow) -> None:
    bare_flow._broadcast_gap_list([])  # must not raise


def test_broadcast_loop_status_without_broadcaster(bare_flow: ForgeFlow) -> None:
    bare_flow._broadcast_loop_status("running")  # must not raise


def test_reset_active_phase_without_store_is_noop(bare_flow: ForgeFlow) -> None:
    bare_flow._reset_active_phase()  # must not raise


def test_set_phase_status_with_broadcaster_only(mock_deps: MockDeps, tmp_path: Path) -> None:
    """Status still broadcasts when only the broadcaster is wired."""
    pool, graph, config, broadcaster, _ = mock_deps
    flow = ForgeFlow(pool, graph, config, broadcaster, None, tmp_path)
    flow._set_phase_status(3, "complete", audit={"is_complete": True})
    payload = broadcaster.emit.call_args.args[1]
    assert payload["audit"] == {"is_complete": True}
