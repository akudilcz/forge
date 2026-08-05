"""Special-phase handlers — the dedicated (non-pipeline) phases of ForgeFlow.

Phases 0, 1, 11, 12, and 14 do not run the generic phase pipeline; each has a
deterministic handler implemented here as a mixin that :class:`ForgeFlow`
inherits, so the methods stay reachable on the flow instance unchanged.

Design references: design/11_phase_01_ingest_document.md,
design/21_phase_11_render_documentation.md, design/22_phase_12_generate_code.md,
design/24_phase_14_build_deliverables.md
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)


class SpecialPhaseHandlers:
    """Dedicated handlers for phases 0, 1, 11, 12, and 14.

    Mixed into :class:`backend.pipeline.flow.ForgeFlow`, which provides the
    attributes and the ``_set_phase_status`` method declared below.
    """

    pool: Any
    graph: Any
    config: Any
    _workspace: Path
    _set_phase_status: Callable[..., None]

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
            tools: list[Any] = registry._tools_instances
            return tools
        except AttributeError as exc:
            raise RuntimeError(
                "Agent pool exposes no tool registry "
                "(pool._factory._registry._tools_instances) — cannot run "
                "phase 12 without tools"
            ) from exc
