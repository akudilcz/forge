"""ForgeFlow — the main phase-aware Observe-Act orchestration loop.

Coordinates the Gap Analyser, Agent Pool, and Phase Store to drive a
project from Phase 0 (workspace init) through Phase 14 (deliverables).

Design reference: design/01_architecture.md
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import Gap, GapType
from backend.analysis.phase_auditor import PhaseAuditor
from backend.crew.dispatch import (
    dispatch as _dispatch_impl,
)
from backend.crew.dispatch import (
    run_agent_task as _run_agent_task_impl,
)
from backend.crew.dispatch import (
    try_fast_trace as _try_fast_trace_impl,
)
from backend.crew.phase_context import phase_context
from backend.prompting.builder import (
    build_ancestor_context,
    build_task_description,
    find_suite_id,
)
from backend.quality.checks import NODE_TYPE_TO_PHASE as _NODE_TYPE_TO_PHASE  # noqa: F401
from backend.quality.checks import PHASE_TO_NODE_TYPES as _PHASE_TO_NODE_TYPES  # noqa: F401
from backend.quality.checks import QUALITY_GAP_TYPES as _QUALITY_GAP_TYPES  # noqa: F401
from backend.quality.checks import (
    quality_gaps_for_types,
    run_combined_quality_check,
    run_design_consolidation,
    run_semantic_check,
    scan_qual_detect,
    semantic_gaps_for_type,
)
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

# Canonical phase mapping for structural gaps.
GAP_TYPE_TO_PHASE: dict[GapType, int] = {
    GapType.UNCHUNKED_DOCUMENT: 2,
    GapType.UNCOVERED_PARA: 3,
    GapType.UNARCHITECTED: 4,
    GapType.UNMODULARISED: 5,
    GapType.UNCONTRACTED: 6,
    GapType.UNREFINED_HLR: 7,
    GapType.UNDESIGNED: 8,
    GapType.UNSUITED: 9,
    GapType.UNTESTED_HLR: 10,
    GapType.UNTESTED_LLR: 10,
    # UNSYNCED_DESIGN / UNSYNCED_TEST: handled by workspace_sync step (no agent)
}


class _SingleStepDone(Exception):  # noqa: N818 — control-flow signal, not an error
    """Raised when single_step=True and one gap has been resolved."""


class ForgeFlowState(BaseModel):
    """Mutable runtime state of a single ForgeFlow run."""

    session_id: str = ""
    active_agents: list[str] = Field(default_factory=list)
    start_phase: int = 0
    end_phase: int = 14
    current_phase: int = 0
    loop_status: str = "idle"
    iteration: int = 0
    single_step: bool = False
    error: str | None = None
    run_id: str | None = None


class ForgeFlow:
    """Phase-aware FORGE build loop."""

    def __init__(
        self,
        pool: Any,
        graph: Any,
        config: Any,
        broadcaster: Any,
        phase_store: Any,
        workspace: Path | None = None,
    ) -> None:
        self.pool = pool
        self.graph = graph
        self.config = config
        self.broadcaster = broadcaster
        self.phase_store = phase_store
        if workspace is not None:
            self._workspace = workspace
        elif config is not None:
            self._workspace = Path(config.project.workspace_dir)
        else:
            self._workspace = Path.cwd()
        self.state = ForgeFlowState()
        self._analyser = GapAnalyser()
        self._auditor = PhaseAuditor()
        self._approval_events: dict[int, asyncio.Event] = {}
        # Sticky semantic-dedup verdicts, keyed by (node_id, content-hash).
        # Flow-scoped so pipeline re-loops cannot re-litigate unchanged nodes.
        self._semantic_verdict_cache: dict[tuple[str, str], str] = {}

    # ── Public interface ─────────────────────────────────────────────────────

    async def kickoff_async(self) -> None:
        """Run phases from start_phase through end_phase (inclusive)."""
        from backend.observability import log_context, new_run_id  # noqa: PLC0415

        self.state.loop_status = "running"
        run_id = new_run_id()
        self.state.run_id = run_id
        logger.info("forge.flow.start phase=%d run=%s", self.state.start_phase, run_id)
        with log_context(run_id=run_id):
            forge_logger.loop_start()
            self._broadcast_loop_status("running")
            phase_context.reset_all()
            try:
                for phase in range(self.state.start_phase, self.state.end_phase + 1):
                    self.state.current_phase = phase
                    phase_context.reset_phase(phase)
                    with log_context(phase=phase):
                        await self._run_phase(phase)
                self.state.loop_status = "complete"
                logger.info("forge.flow.complete")
                forge_logger.loop_complete()
                self._broadcast_loop_status("complete")
            except _SingleStepDone:
                self.state.loop_status = "idle"
                logger.info(
                    "forge.flow.single_step_done phase=%d", self.state.current_phase
                )
                forge_logger.loop_stop()
                self._broadcast_loop_status("idle")
            except asyncio.CancelledError:
                self.state.loop_status = "idle"
                logger.info("forge.flow.cancelled")
                forge_logger.loop_cancelled()
                self._broadcast_loop_status("idle")
                self._reset_active_phase()
            except Exception as exc:  # noqa: BLE001
                self.state.loop_status = "error"
                self.state.error = str(exc)
                logger.exception("forge.flow.error: %s", exc)
                forge_logger.loop_error(str(exc))
                self._broadcast_loop_status("error")
                self._reset_active_phase()

    def approve_phase(self, phase_number: int) -> None:
        """Unblock a phase waiting for human approval."""
        event = self._approval_events.get(phase_number)
        if event:
            event.set()
            logger.info("forge.flow.phase_approved phase=%d", phase_number)

    # ── Quality checks (delegate to crew/quality.py) ─────────────────────────

    async def run_qual_check(self, phase: int, _broadcast_status: bool = True) -> int:
        """Run the quality-gap stability loop for a phase."""
        node_types = _PHASE_TO_NODE_TYPES.get(phase, [])
        if not node_types:
            logger.warning("forge.flow.qual_check.no_node_type phase=%d", phase)
            return 0
        if _broadcast_status:
            self._broadcast_loop_status("running")
        try:
            from backend.crew.qual_check_graph import create_qual_check_graph

            result = await create_qual_check_graph(self).ainvoke(
                {
                    "phase": phase,
                    "node_types": node_types,
                    "pass_num": 0,
                    "pending_gaps": [],
                    "count_before": 0,
                    "total_checked": 0,
                    "had_deletions": False,
                    "pass_had_deletions": False,
                }
            )
            return result.get("total_checked", 0)
        finally:
            if _broadcast_status:
                self._broadcast_loop_status("idle")

    async def run_semantic_check(
        self,
        phase: int,
        _broadcast_status: bool = True,
        only_node_ids: set[str] | None = None,
    ) -> int:
        """Check non-canonical siblings for semantic duplicates."""
        if _broadcast_status:
            self._broadcast_loop_status("running")
        try:
            return await run_semantic_check(self, phase, only_node_ids)
        finally:
            if _broadcast_status:
                self._broadcast_loop_status("idle")

    async def run_combined_quality_check(
        self,
        phase: int,
        _broadcast_status: bool = True,
    ) -> list:
        """Batched combined quality check (atomicity + EARS + title axes) —
        one LLM call for all applicable nodes in the current phase."""
        if _broadcast_status:
            self._broadcast_loop_status("running")
        try:
            return await run_combined_quality_check(self, phase)
        finally:
            if _broadcast_status:
                self._broadcast_loop_status("idle")

    async def run_design_consolidation(self, _broadcast_status: bool = True) -> int:
        """Consolidate DESIGN sprawl within each MODULE."""
        if _broadcast_status:
            self._broadcast_loop_status("running")
        try:
            return await run_design_consolidation(self)
        finally:
            if _broadcast_status:
                self._broadcast_loop_status("idle")

    async def scan_qual_detect(self, phase: int) -> list[dict]:
        """LLM-based detect-only quality scan. Does NOT modify the graph."""
        return await scan_qual_detect(self, phase)

    def _quality_gaps_for_types(self, node_types: list[str]) -> dict[str, list[Gap]]:
        return quality_gaps_for_types(self.graph, self._analyser, node_types)

    def _semantic_gaps_for_type(self, node_type: str) -> list[Gap]:
        return semantic_gaps_for_type(self.graph, node_type)

    # ── Phase lifecycle ──────────────────────────────────────────────────────

    async def run_phase(self, phase: int) -> dict:
        """Execute all steps of a phase via the phase pipeline runner.

        Wraps the whole body in ``log_context(phase=phase)`` so every log
        record emitted during this call carries the ``phase`` column —
        regardless of whether it was called via ``run_loop`` or directly
        (e.g. from integration tests).
        """
        from backend.observability import log_context  # noqa: PLC0415

        with log_context(phase=phase):
            self.state.current_phase = phase
            phase_context.reset_phase(phase)
            zero_result = {"structural_gaps": 0, "quality_gaps": 0, "semantic_gaps": 0}
            if phase == 0:
                await self._run_create_project_phase()
                return {"phase": 0, **zero_result}
            if phase == 1:
                await self._run_ingest_phase()
                return {"phase": 1, **zero_result}
            if phase == 11:
                await self._run_dashboard_phase()
                return {"phase": 11, **zero_result}
            if phase == 12:
                await self._run_code_gen_phase()
                return {"phase": 12, **zero_result}
            if phase == 14:
                await self._run_deliverables_phase()
                return {"phase": 14, **zero_result}

            from backend.crew.phase_pipeline import run_phase_pipeline  # noqa: PLC0415

            self._broadcast_loop_status("running")
            forge_logger.emit("INFO", "FLOW ", f"Phase {phase} run_phase: starting pipeline")
            try:
                result = await run_phase_pipeline(self, phase)
                forge_logger.emit("INFO", "FLOW ", f"Phase {phase} run_phase complete")
                return {
                    "phase": phase,
                    "structural_gaps": 0,
                    "quality_gaps": 0,
                    "semantic_gaps": 0,
                    "total_deletions": result.get("total_deletions", 0),
                }
            except Exception:
                self._reset_active_phase()
                raise
            finally:
                self._broadcast_loop_status("idle")

    # ── Phase loop (kickoff_async path) ──────────────────────────────────────

    async def _run_phase(self, phase: int) -> None:
        """Run one phase on the full-run (kickoff) path.

        Special phases have dedicated handlers; every other phase runs the
        full phase pipeline — the same batch authoring, quality, dedup, and
        coverage steps the per-phase route (``run_phase``) executes. There is
        no structural-only shortcut.
        """
        if phase == 0:
            await self._run_create_project_phase()
            return
        if phase == 1:
            return await self._run_ingest_phase()
        if phase == 11:
            return await self._run_dashboard_phase()
        if phase == 12:
            return await self._run_code_gen_phase()
        if phase == 14:
            return await self._run_deliverables_phase()
        from backend.crew.phase_pipeline import run_phase_pipeline  # noqa: PLC0415

        await run_phase_pipeline(self, phase)

    async def _run_structural_loop(self, phase: int, skip_approval: bool) -> None:
        """Run the structural gap-resolution loop for a phase.

        Invoked by the ``structural`` pipeline step. Raises ``_SingleStepDone``
        when single-step mode resolved one gap.
        """
        logger.info("forge.flow.phase_start phase=%d", phase)
        from backend.crew.structural_loop_graph import create_structural_loop_graph

        result = await create_structural_loop_graph(self).ainvoke(
            {
                "phase": phase,
                "skip_approval": skip_approval,
                "iteration": 0,
                "gap_fail_counts": {},
                "current_gaps": [],
                "single_step_done": False,
                "abandoned": set(),
            }
        )
        if result.get("single_step_done"):
            raise _SingleStepDone()

    def _collect_phase_gaps(self, phase: int, skipped: set[str]) -> list[Gap]:
        """Return sorted structural gaps belonging to this phase."""
        all_gaps = self._analyser.analyse(self.graph)
        return [
            g
            for g in all_gaps
            if g.type not in _QUALITY_GAP_TYPES
            and self._gap_phase(g) == phase
            and f"{g.type}:{g.node_id}" not in skipped
        ]

    def _gap_phase(self, gap: Gap) -> int:
        """Return the phase this gap belongs to."""
        if gap.type not in _QUALITY_GAP_TYPES:
            return GAP_TYPE_TO_PHASE.get(gap.type, 13)
        node = self.graph.node_sync(gap.node_id)
        if node:
            return _NODE_TYPE_TO_PHASE.get(node.node_type, 13)
        return 13

    async def _request_approval(self, phase: int) -> None:
        """Run phase audit, mark complete, and log the result."""
        audit = self._auditor.audit(phase, self.graph)
        if audit.is_complete:
            logger.info("forge.flow.phase_audit_pass phase=%d", phase)
            forge_logger.emit("INFO", "AUDIT", f"Phase {phase} audit PASS")
        else:
            gap_types = ", ".join(g.value for g in audit.blocking_gap_types)
            logger.warning(
                "forge.flow.phase_audit_fail phase=%d unresolved=%d gap_types=%s",
                phase,
                len(audit.unresolved_gaps),
                gap_types,
            )
            forge_logger.emit(
                "WARN",
                "AUDIT",
                f"Phase {phase} audit FAIL — {len(audit.unresolved_gaps)} gap(s): {gap_types}",
            )

        # A failed audit must not report completion. This previously set
        # "complete" unconditionally, so the auditor was decorative: a phase
        # whose own structural gaps were still open logged a warning and then
        # announced success, and the pipeline advanced into the next phase with
        # its precondition unmet. `awaiting_approval` is the honest state — the
        # phase did what it could and now needs a human — and both the phase
        # store and the Control Station already understand it.
        status = "complete" if audit.is_complete else "awaiting_approval"
        self._set_phase_status(phase, status, audit=audit.to_dict())
        if audit.is_complete:
            logger.info("forge.flow.phase_complete phase=%d", phase)
            forge_logger.phase_complete(phase)
        else:
            logger.warning(
                "forge.flow.phase_incomplete phase=%d status=%s", phase, status
            )

    # ── Special phases ───────────────────────────────────────────────────────

    async def _run_create_project_phase(self) -> None:
        """Phase 0: create the PROJECT node (root of the graph)."""
        self._set_phase_status(0, "active")
        logger.info("forge.flow.phase_start phase=0 (create project)")
        forge_logger.phase_start(0)
        from backend.services.ingest import _ensure_project_node  # noqa: PLC0415

        project_name = self.config.project.name
        node_id = await _ensure_project_node(self.graph, project_name)
        forge_logger.emit(
            "INFO",
            "PHASE",
            f"Phase 0: PROJECT node {node_id!r} ({project_name!r})",
        )
        self._set_phase_status(0, "complete")
        logger.info("forge.flow.phase_complete phase=0")
        forge_logger.phase_complete(0)

    async def _run_ingest_phase(self) -> None:
        """Phase 1: read Forge.md from workspace and create DOCUMENT node."""
        self._set_phase_status(1, "active")
        logger.info("forge.flow.phase_start phase=1 (ingest forge.md)")
        forge_logger.phase_start(1)
        from pathlib import Path

        from backend.services.ingest import ingest_forgemd, resolve_forgemd_path

        workspace = Path(self.config.project.workspace_dir)
        forgemd_setting = self.config.project.forgemd
        forge_logger.emit(
            "INFO", "PHASE", f"Phase 1: searching for {forgemd_setting!r} in {workspace}"
        )
        forgemd_path = resolve_forgemd_path(workspace, forgemd_setting)
        if not forgemd_path.exists():
            forge_logger.emit(
                "WARN", "PHASE", f"Phase 1: {forgemd_setting!r} not found at {forgemd_path}"
            )
            self._set_phase_status(1, "pending")
            return
        forge_logger.emit(
            "INFO",
            "PHASE",
            f"Phase 1: ingesting {forgemd_path} ({forgemd_path.stat().st_size:,} bytes)",
        )
        await ingest_forgemd(forgemd_path, self.graph, self.config)
        self._set_phase_status(1, "complete")
        logger.info("forge.flow.phase_complete phase=1")
        forge_logger.phase_complete(1)

    async def _run_dashboard_phase(self) -> None:
        """Phase 11: render Markdown docs into workspace/docs/."""
        self._set_phase_status(11, "active")
        logger.info("forge.flow.phase_start phase=11 (dashboard)")
        forge_logger.phase_start(11)
        from backend.rendering.dashboard import render_dashboard

        written = await render_dashboard(self.graph, self._workspace)
        forge_logger.emit(
            "INFO",
            "DASH",
            f"Dashboard rendered {len(written)} doc(s) to {self._workspace / 'docs'}",
        )
        self._set_phase_status(11, "complete")
        logger.info("forge.flow.phase_complete phase=11")
        forge_logger.phase_complete(11)

    async def _run_code_gen_phase(self) -> None:
        """Phase 12: generate source code and tests."""
        self._set_phase_status(12, "active")
        logger.info("forge.flow.phase_start phase=12 (code gen)")
        forge_logger.phase_start(12)
        from backend.codegen.slice_gen import run_code_gen

        tool_instances = self._get_tool_instances()
        result = await run_code_gen(
            self.graph,
            self._workspace,
            config=self.config,
            tool_instances=tool_instances,
        )
        total = len(result.source_files) + len(result.test_files)
        forge_logger.emit(
            "INFO",
            "CGEN ",
            f"Code Gen produced {total} file(s): "
            f"{len(result.source_files)} source, {len(result.test_files)} test",
        )
        self._set_phase_status(12, "complete")
        logger.info("forge.flow.phase_complete phase=12")
        forge_logger.phase_complete(12)
        if not result.gaps_resolved:
            forge_logger.emit(
                "WARN",
                "CGEN ",
                "Phase 12 complete with unresolved gaps — see final report",
            )

    async def _run_deliverables_phase(self) -> None:
        """Phase 14: build the deliverables pack ZIP."""
        self._set_phase_status(14, "active")
        logger.info("forge.flow.phase_start phase=14 (deliverables)")
        forge_logger.phase_start(14)
        from backend.rendering.deliverables import build_deliverables_pack

        zip_path = await build_deliverables_pack(self.graph, self._workspace)
        forge_logger.emit("INFO", "DLVR ", f"Deliverables pack built: {zip_path}")
        self._set_phase_status(14, "complete")
        logger.info("forge.flow.phase_complete phase=14")
        forge_logger.phase_complete(14)

    def _get_tool_instances(self) -> list[Any]:
        """Retrieve live tool instances from the agent pool's factory registry.

        Raises:
            RuntimeError: if the registry chain is unavailable — phase 12
                must never run with zero tools (design/22, Required tools).
        """
        try:
            registry = self.pool._factory._registry
            return registry._tools_instances
        except AttributeError as exc:
            raise RuntimeError(
                "Agent pool exposes no tool registry "
                "(pool._factory._registry._tools_instances) — cannot run "
                "phase 12 without tools"
            ) from exc

    # ── Agent dispatch (delegates to crew/dispatch.py) ─────────────────────

    async def _try_fast_trace(self, gap: Gap) -> bool:
        return await _try_fast_trace_impl(self, gap)

    async def _dispatch(self, gap: Gap, attempt: int = 1) -> str:
        return await _dispatch_impl(self, gap, attempt)

    async def _run_agent_task(
        self,
        agent: Any,
        gap: Gap,
        attempt: int = 1,
    ) -> str:
        return await _run_agent_task_impl(self, agent, gap, attempt)

    # ── Backward-compatible shims ────────────────────────────────────────────

    def _build_semantic_checker(self) -> Any:
        from backend.quality.checks import _build_semantic_checker

        return _build_semantic_checker(self)

    def _build_design_consolidator(self) -> Any:
        from backend.quality.checks import _build_design_consolidator

        return _build_design_consolidator(self)

    def _modules_needing_consolidation(self, modules: list[Any]) -> list[tuple[Any, list[Any]]]:
        from backend.quality.checks import modules_needing_consolidation

        return modules_needing_consolidation(self.graph, modules)

    def _find_contract(self, module_id: str) -> str:
        from backend.quality.checks import find_contract

        return find_contract(self.graph, module_id)

    def _build_ancestor_context(self, node_id: str) -> str:
        return build_ancestor_context(self.graph, node_id)

    def _build_task_description(
        self,
        gap: Gap,
        ancestor_context: str,
        attempt: int = 1,
    ) -> tuple[str, str]:
        suite_id = find_suite_id(self.graph) if self.graph else ""
        return build_task_description(gap, ancestor_context, attempt, suite_id=suite_id)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _set_phase_status(
        self,
        phase: int,
        status: str,
        audit: dict | None = None,
    ) -> None:
        if self.phase_store is not None:
            self.phase_store.set_status(phase, status)
        if self.broadcaster is not None:
            from backend.server.websocket.events import WSEventType

            payload: dict = {"to_phase": phase, "status": status}
            if audit is not None:
                payload["audit"] = audit
            self.broadcaster.emit(WSEventType.PHASE_TRANSITION, payload)

    def _broadcast_gap_list(self, gaps: list[Gap]) -> None:
        if self.broadcaster is not None:
            self.broadcaster.gap_list_update(gaps)

    def _reset_active_phase(self) -> None:
        """Demote any phase stuck in 'active' back to 'pending'.

        Called on error/cancellation so the frontend never shows a
        permanently-flashing amber badge.
        """
        if self.phase_store is None:
            return
        for phase in self.phase_store.get_all():
            if phase["status"] == "active":
                self._set_phase_status(phase["phase_number"], "pending")

    def _broadcast_loop_status(self, status: str) -> None:
        if self.broadcaster is not None:
            from backend.server.websocket.events import WSEventType

            self.broadcaster.emit(WSEventType.PHASE_TRANSITION, {"loop_status": status})

    def _graph_state_count(self) -> int:
        if self.graph is None:
            return 0
        try:
            return sum(n.version for n in self.graph.all_nodes())
        except Exception:  # noqa: BLE001
            return 0
