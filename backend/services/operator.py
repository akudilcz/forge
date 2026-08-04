"""OperatorService — shared logic for phase lifecycle actions.

Encapsulates operator actions so they can be invoked from both
HTTP router endpoints and console agent tools without duplication.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class OperatorService:
    """Phase lifecycle actions backed by live app state.

    Holds a reference to ``app.state`` so every call reads the latest
    config, pool, broadcaster, etc.  The main event loop reference is
    stored so synchronous tool code can schedule coroutines via
    ``run_on_main_loop``.
    """

    def __init__(self, app_state: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._state = app_state
        self._loop = loop

    # ── Live-state accessors ──────────────────────────────────────────

    @property
    def _graph(self) -> Any:
        return self._state.graph

    @property
    def _config(self) -> Any:
        return self._state.config

    @property
    def _pool(self) -> Any:
        return self._state.agent_pool

    @property
    def _broadcaster(self) -> Any:
        return self._state.broadcaster

    @property
    def _phase_store(self) -> Any:
        return self._state.phase_store

    @property
    def _workspace(self) -> Path:
        return Path(self._state.workspace)

    # ── Helpers ───────────────────────────────────────────────────────

    async def _cancel_existing_flow(self) -> None:
        """Cancel and await any running flow task."""
        task: asyncio.Task[Any] | None = getattr(self._state, "flow_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
        self._state.flow_task = None

    def _make_flow(self) -> Any:
        from backend.crew.flow import ForgeFlow

        return ForgeFlow(
            pool=self._pool,
            graph=self._graph,
            config=self._config,
            broadcaster=self._broadcaster,
            phase_store=self._phase_store,
            workspace=self._workspace,
        )

    def run_on_main_loop(self, coro: Any, *, timeout: int = 120) -> Any:
        """Run *coro* on the main event loop from a synchronous context."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Operator actions ──────────────────────────────────────────────

    async def run_phase(self, phase: int, end_phase: int | None = None) -> dict[str, str]:
        """Run a single phase or a contiguous range."""
        from backend.server.forge_logger import forge_logger

        await self._cancel_existing_flow()
        flow = self._make_flow()

        session = getattr(self._state, "session", None)
        if session:
            flow.state.session_id = str(session.session_id)

        self._state.flow = flow

        if end_phase is not None and end_phase != phase:
            flow.state.start_phase = phase
            flow.state.end_phase = end_phase
            flow.state.active_agents = self._pool.all_ids()
            task = asyncio.create_task(flow.kickoff_async())
            forge_logger.user_action("run phases", f"{phase}–{end_phase}")
        else:
            task = asyncio.create_task(flow.run_phase(phase))
            forge_logger.user_action("run phase", str(phase))

        self._state.flow_task = task
        end = end_phase if end_phase is not None else phase
        return {"status": "started", "phases": f"{phase}-{end}"}

    async def stop_build(self) -> dict[str, str]:
        """Cancel the running build flow."""
        from backend.server.forge_logger import forge_logger
        from backend.server.websocket.events import WSEventType

        task: asyncio.Task[Any] | None = getattr(self._state, "flow_task", None)
        if task is not None and not task.done():
            task.cancel()
            forge_logger.user_action("stop build")
            if self._broadcaster:
                self._broadcaster.emit(
                    WSEventType.PHASE_TRANSITION, {"loop_status": "idle"},
                )
                for agent_id in self._pool.all_ids():
                    self._broadcaster.agent_status_change(agent_id, "idle")
            return {"status": "stopped"}

        forge_logger.user_action("stop build", "no active build")
        return {"status": "not_running"}

    async def stop_all(self) -> dict[str, Any]:
        """Cancel every running agent task (build flow + console)."""
        from backend.server.forge_logger import forge_logger
        from backend.server.websocket.events import WSEventType

        cancelled: list[str] = []

        # Cancel build flow task
        flow_task: asyncio.Task[Any] | None = getattr(self._state, "flow_task", None)
        if flow_task is not None and not flow_task.done():
            flow_task.cancel()
            cancelled.append("build")
        self._state.flow_task = None

        # Cancel console agent task
        console_task: asyncio.Task[Any] | None = getattr(self._state, "console_task", None)
        if console_task is not None and not console_task.done():
            console_task.cancel()
            cancelled.append("console")
        self._state.console_task = None

        # Reset all agents to idle
        if self._broadcaster:
            self._broadcaster.emit(
                WSEventType.PHASE_TRANSITION, {"loop_status": "idle"},
            )
            for agent_id in self._pool.all_ids():
                self._broadcaster.agent_status_change(agent_id, "idle")

        forge_logger.user_action("stop all", ", ".join(cancelled) or "none running")
        return {"status": "stopped", "cancelled": cancelled}

    async def scan_gaps(self, phase: int) -> dict[str, Any]:
        """Run the gap analyser for *phase* and broadcast results."""
        from backend.analysis.gap_analyser import GapAnalyser
        from backend.crew.flow import GAP_TYPE_TO_PHASE
        from backend.crew.quality import QUALITY_GAP_TYPES
        from backend.server.forge_logger import forge_logger

        all_gaps = GapAnalyser().analyse(self._graph)
        phase_gaps = [
            g for g in all_gaps
            if g.type not in QUALITY_GAP_TYPES
            and GAP_TYPE_TO_PHASE.get(g.type) == phase
        ]

        types_str = ", ".join(sorted({g.type.value for g in phase_gaps})) or "none"
        forge_logger.emit(
            "INFO", "SCAN ",
            f"Phase {phase} scan — {len(phase_gaps)} structural gap(s)",
            types_str,
        )
        if self._broadcaster:
            self._broadcaster.gap_list_update(all_gaps)

        return {
            "phase": phase,
            "gap_count": len(phase_gaps),
            "gap_types": sorted({g.type.value for g in phase_gaps}),
        }

    async def scan_quality(self, phase: int) -> dict[str, int]:
        """Surface quality gaps for *phase* using LLM detect-only mode."""
        flow = self._make_flow()
        findings = await flow.scan_qual_detect(phase)
        return {"phase": phase, "qual_gap_count": len(findings)}

    async def qual_check(self, phase: int) -> dict[str, Any]:
        """Dispatch quality auditor consistency checks for *phase*."""
        from backend.server.forge_logger import forge_logger

        await self._cancel_existing_flow()
        flow = self._make_flow()
        self._state.flow = flow

        task = asyncio.create_task(flow.run_qual_check(phase))
        self._state.flow_task = task
        forge_logger.user_action("qual check", f"phase {phase}")
        return {"status": "started", "phase": phase}

    async def purge_derived(self) -> dict[str, Any]:
        """Delete all derived nodes and reset phases 2-13."""
        from backend.server.forge_logger import forge_logger
        from backend.server.websocket.events import WSEventType

        all_nodes = await self._graph.nodes()
        preserved = {"PROJECT", "DOCUMENT"}
        to_delete = [n.node_id for n in all_nodes if n.node_type not in preserved]
        forge_logger.user_action(
            "purge derived", f"{len(to_delete)} nodes queued",
        )

        # A delete that fails leaves a derived node behind, so the purge did not
        # do what it reports. Swallowing it made `deleted_count` under-report
        # with nothing in the logs to explain the difference.
        deleted = 0
        failures: list[str] = []
        for node_id in to_delete:
            try:
                await self._graph.delete_node(node_id)
                deleted += 1
            except Exception as exc:  # noqa: BLE001 — reported, not hidden
                failures.append(f"{node_id}: {type(exc).__name__}: {exc}")

        if failures:
            forge_logger.emit(
                "WARN",
                "USER ",
                f"purge derived: {len(failures)}/{len(to_delete)} node(s) could not be deleted",
                "; ".join(failures[:5]),
            )

        await self._graph.reset_sequences(exclude=["PROJECT", "DOCUMENT"])

        if self._phase_store:
            for phase_num in range(2, 14):
                self._phase_store.set_status(phase_num, "pending")

        if self._broadcaster and self._phase_store:
            for phase in self._phase_store.get_all():
                if phase["phase_number"] >= 2:
                    self._broadcaster.emit(
                        WSEventType.PHASE_TRANSITION,
                        {"to_phase": phase["phase_number"], "status": phase["status"]},
                    )

        return {
            "status": "purged",
            "deleted_count": deleted,
            "failed_count": len(failures),
        }

    async def ingest_document(self) -> dict[str, str]:
        """Read forge.md from workspace and ingest as the DOCUMENT node."""
        from backend.server.forge_logger import forge_logger
        from backend.server.websocket.events import WSEventType
        from backend.services.ingest import ingest_forgemd, resolve_forgemd_path

        config = self._config
        workspace = self._workspace
        forgemd_setting = config.project.forgemd if config else "forge.md"
        forgemd_path = resolve_forgemd_path(workspace, forgemd_setting)

        if not forgemd_path.exists():
            return {
                "status": "error",
                "detail": f"forge.md not found at {forgemd_path}",
            }

        forge_logger.user_action("ingest forge.md", str(forgemd_path))
        await ingest_forgemd(forgemd_path, self._graph, config)

        if self._phase_store:
            self._phase_store.set_status(1, "complete")
        if self._broadcaster:
            self._broadcaster.emit(
                WSEventType.PHASE_TRANSITION,
                {"to_phase": 1, "status": "complete"},
            )

        return {"status": "ingested", "path": str(forgemd_path)}
