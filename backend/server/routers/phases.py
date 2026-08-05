"""Phases router — phase lifecycle management endpoints.

Provides endpoints to list phases, approve/run/stop phases, trigger forge.md
ingestion, reset or purge the graph, and run audits and quality checks.
"""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.agents.pool import AgentPool
from backend.config.models import ForgeConfig
from backend.core.phase_store import PhaseStore
from backend.core.session import ForgeSession
from backend.graph.engine import ProjectGraph
from backend.server.dependencies import (
    get_agent_pool,
    get_broadcaster,
    get_forge_config,
    get_forge_session,
    get_phase_store,
    get_project_graph,
)
from backend.server.websocket.broadcaster import EventBroadcaster
from backend.server.websocket.events import WSEventType

router = APIRouter(prefix="/phases", tags=["phases"])


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _cancel_existing_flow(request: Request) -> None:
    """Cancel and await any currently-running flow task before starting a new one."""
    task: asyncio.Task[Any] | None = getattr(request.app.state, "flow_task", None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
        except (TimeoutError, asyncio.CancelledError):
            pass
    request.app.state.flow_task = None


def _make_flow(
    pool: AgentPool, graph: ProjectGraph, config: ForgeConfig,
    broadcaster: EventBroadcaster, phase_store: PhaseStore,
    workspace: Path | None = None,
) -> Any:
    """Create a new ForgeFlow instance."""
    from backend.crew.flow import ForgeFlow

    return ForgeFlow(
        pool=pool, graph=graph, config=config,
        broadcaster=broadcaster, phase_store=phase_store,
        workspace=workspace,
    )


# ── Phase listing ────────────────────────────────────────────────────────────


@router.get("", response_model=None)
async def get_phases(
    phase_store: PhaseStore = Depends(get_phase_store),
) -> list[dict[str, Any]]:
    """Return the list of all phase states from the DB."""
    if phase_store is None:
        raise HTTPException(status_code=503, detail="PhaseStore not ready")
    return phase_store.get_all()


# ── Phase approval ───────────────────────────────────────────────────────────


@router.post("/{phase_number}/approve")
async def approve_phase(
    phase_number: int,
    request: Request,
    phase_store: PhaseStore = Depends(get_phase_store),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
) -> dict[str, Any]:
    """Approve a phase that is awaiting human sign-off."""
    if phase_store is None:
        raise HTTPException(status_code=503, detail="PhaseStore not ready")

    phase = phase_store.get(phase_number)
    if phase is None:
        raise HTTPException(status_code=404, detail=f"Phase {phase_number} not found.")

    from backend.server.forge_logger import forge_logger

    forge_logger.user_action("approve phase", f"phase {phase_number}")
    phase_store.set_status(phase_number, "complete")

    flow = getattr(request.app.state, "flow", None)
    if flow is not None:
        flow.approve_phase(phase_number)

    if broadcaster is not None:
        broadcaster.emit(
            WSEventType.PHASE_TRANSITION,
            {"to_phase": phase_number, "status": "complete"},
        )
    return {"status": "approved", "phase": phase_number}


# ── Build start/stop ────────────────────────────────────────────────────────


class StartBuildRequest(BaseModel):
    """Request body for POST /phases/start to configure the build run."""

    start_phase: int = 0
    end_phase: int = 13
    single_step: bool = False
    active_agents: list[str] | None = None


@router.post("/start")
async def start_build(
    request: Request,
    body: StartBuildRequest | None = None,
    pool: AgentPool = Depends(get_agent_pool),
    graph: ProjectGraph = Depends(get_project_graph),
    config: ForgeConfig = Depends(get_forge_config),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
    phase_store: PhaseStore = Depends(get_phase_store),
    session: ForgeSession = Depends(get_forge_session),
) -> dict[str, str]:
    """Start the full FORGE build flow."""
    if body is None:
        body = StartBuildRequest()

    from backend.server.forge_logger import forge_logger

    forge_logger.user_action(
        "start build",
        f"phases {body.start_phase}–{body.end_phase}"
        + (" (single-step)" if body.single_step else ""),
    )
    if pool is None or graph is None or config is None:
        raise HTTPException(status_code=503, detail="Infrastructure not ready")

    await _cancel_existing_flow(request)

    flow = _make_flow(pool, graph, config, broadcaster, phase_store,
                      workspace=getattr(request.app.state, 'workspace', None))
    flow.state.session_id = str(session.session_id)
    flow.state.single_step = body.single_step
    flow.state.active_agents = body.active_agents or pool.all_ids()
    flow.state.start_phase = body.start_phase
    flow.state.end_phase = body.end_phase
    request.app.state.flow = flow

    task = asyncio.create_task(flow.kickoff_async())
    request.app.state.flow_task = task
    return {"status": "started", "session_id": session.session_id}


@router.post("/stop")
async def stop_build(
    request: Request,
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
) -> dict[str, str]:
    """Cancel the running FORGE build flow."""
    from backend.server.forge_logger import forge_logger

    task: asyncio.Task[Any] | None = getattr(request.app.state, "flow_task", None)
    if task is not None and not task.done():
        task.cancel()
        forge_logger.user_action("stop build")
        if broadcaster is not None:
            broadcaster.emit(WSEventType.PHASE_TRANSITION, {"loop_status": "idle"})
            pool = getattr(request.app.state, "agent_pool", None)
            if pool:
                for agent_id in pool.all_ids():
                    broadcaster.agent_status_change(agent_id, "idle")
        return {"status": "stopped"}
    forge_logger.user_action("stop build", "no active build")
    return {"status": "not_running"}


# ── Logging ──────────────────────────────────────────────────────────────────


class UserActionRequest(BaseModel):
    """Request body for logging a user-initiated action."""

    action: str
    detail: str | None = None


@router.post("/log/user-action")
async def log_user_action(body: UserActionRequest) -> dict[str, str]:
    """Record a user-initiated action in the diagnostic log."""
    from backend.server.forge_logger import forge_logger

    forge_logger.user_action(body.action, body.detail)
    return {"status": "logged"}


# ── Reset / Purge ────────────────────────────────────────────────────────────


async def _cancel_agent_work(
    request: Request,
    broadcaster: EventBroadcaster | None,
) -> None:
    """Cancel running flow and console tasks, reset all agents to idle."""
    for attr in ("flow_task", "console_task"):
        task: asyncio.Task[Any] | None = getattr(request.app.state, attr, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
        setattr(request.app.state, attr, None)

    if broadcaster is not None:
        broadcaster.emit(WSEventType.PHASE_TRANSITION, {"loop_status": "idle"})
        pool: AgentPool | None = getattr(request.app.state, "agent_pool", None)
        if pool:
            for agent_id in pool.all_ids():
                broadcaster.agent_status_change(agent_id, "idle")


def _reset_workspace(config: ForgeConfig) -> None:
    """Remove generated artifacts from workspace, preserving user files.

    Cleans: src/, tests/, docs/, tracing/, deliverables/, build artifacts.
    Preserves: FORGE.MD, requirements.txt, .forge/, and any other user files.
    """
    import shutil

    workspace = Path(config.project.workspace_dir)
    if not workspace.is_dir():
        return

    # Directories created by phase 12 and other phases
    for dirname in ("src", "tests", "docs", "tracing", "deliverables"):
        target = workspace / dirname
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)

    # Build artifacts and coverage files
    for pattern in (
        "BUILD.bazel", "MODULE.bazel", "MODULE.bazel.lock",
        ".bazelrc", ".coveragerc", ".coverage",
        "coverage.lcov", "coverage-test-results.xml",
        "deliverables.zip",
    ):
        for f in workspace.glob(pattern):
            f.unlink(missing_ok=True)

    # Bazel symlinks
    for link in ("bazel-bin", "bazel-out", "bazel-testlogs", "bazel-workspace"):
        p = workspace / link
        if p.is_symlink() or p.is_dir():
            if p.is_symlink():
                p.unlink()
            else:
                shutil.rmtree(p, ignore_errors=True)


@router.post("/reset")
async def reset_build(
    request: Request,
    graph: ProjectGraph = Depends(get_project_graph),
    config: ForgeConfig = Depends(get_forge_config),
    phase_store: PhaseStore = Depends(get_phase_store),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
) -> dict[str, Any]:
    """Wipe the project graph and reset all phase states to pending."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")

    from backend.server.forge_logger import forge_logger

    # Cancel any running agent work before wiping the graph
    await _cancel_agent_work(request, broadcaster)

    await graph.reset()
    await graph.reset_sequences()

    # Clean workspace — remove generated code, tests, docs from previous runs
    _reset_workspace(config)

    from backend.services.ingest import _ensure_project_node

    project_name = config.project.name if config else "my-project"
    await _ensure_project_node(graph, project_name)

    if phase_store is not None:
        phase_store.reset_all()
        phase_store.set_status(0, "complete")

    if broadcaster is not None and phase_store is not None:
        for phase in phase_store.get_all():
            broadcaster.emit(
                WSEventType.PHASE_TRANSITION,
                {"to_phase": phase["phase_number"], "status": phase["status"]},
            )
    forge_logger.user_action("project reset")
    return {"status": "reset"}


@router.post("/purge-derived")
async def purge_derived_nodes(
    request: Request,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Delete all derived nodes and reset phases 2-13.

    Delegates to ``OperatorService.purge_derived``. This handler previously
    carried a byte-identical second copy of that logic, so a fix to one silently
    left the other behind.
    """
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")

    service = getattr(request.app.state, "operator_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Operator service not available")

    result: dict[str, Any] = await service.purge_derived()
    return result


# ── Audit ────────────────────────────────────────────────────────────────────


@router.get("/audit")
async def audit_lifecycle(
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Run the PhaseAuditor across all 14 phases."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")

    from backend.analysis.phase_auditor import PhaseAuditor

    results = PhaseAuditor().audit_lifecycle(graph)
    return {str(phase): result.to_dict() for phase, result in results.items()}


@router.get("/{phase_number}/audit")
async def audit_phase(
    phase_number: int,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Run the PhaseAuditor for a single phase."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")
    if phase_number < 0 or phase_number > 14:
        raise HTTPException(status_code=404, detail=f"Phase {phase_number} out of range 0–14.")

    from backend.analysis.phase_auditor import PhaseAuditor

    result = PhaseAuditor().audit(phase_number, graph)
    return result.to_dict()


# ── Quality / Semantic checks ────────────────────────────────────────────────


@router.post("/{phase_number}/qual-check")
async def qual_check_phase(
    phase_number: int,
    request: Request,
    pool: AgentPool = Depends(get_agent_pool),
    graph: ProjectGraph = Depends(get_project_graph),
    config: ForgeConfig = Depends(get_forge_config),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
    phase_store: PhaseStore = Depends(get_phase_store),
) -> dict[str, Any]:
    """Dispatch quality consistency checks on all nodes produced by this phase."""
    if graph is None or pool is None:
        raise HTTPException(status_code=503, detail="Infrastructure not ready")
    if phase_number < 2 or phase_number > 13:
        raise HTTPException(status_code=400, detail=f"Phase {phase_number} has no qual-check target.")

    await _cancel_existing_flow(request)
    flow = _make_flow(pool, graph, config, broadcaster, phase_store,
                      workspace=getattr(request.app.state, 'workspace', None))
    task = asyncio.create_task(flow.run_qual_check(phase_number))
    request.app.state.flow = flow
    request.app.state.flow_task = task
    return {"status": "started", "phase": phase_number}


@router.post("/{phase_number}/semantic-check")
async def semantic_check_phase(
    phase_number: int,
    request: Request,
    pool: AgentPool = Depends(get_agent_pool),
    graph: ProjectGraph = Depends(get_project_graph),
    config: ForgeConfig = Depends(get_forge_config),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
    phase_store: PhaseStore = Depends(get_phase_store),
) -> dict[str, Any]:
    """Dispatch semantic duplicate checks for a phase."""
    if graph is None or pool is None:
        raise HTTPException(status_code=503, detail="Infrastructure not ready")
    if phase_number < 2 or phase_number > 13:
        raise HTTPException(status_code=400, detail=f"Phase {phase_number} has no semantic-check target.")

    await _cancel_existing_flow(request)
    flow = _make_flow(pool, graph, config, broadcaster, phase_store,
                      workspace=getattr(request.app.state, 'workspace', None))
    task = asyncio.create_task(flow.run_semantic_check(phase_number))
    request.app.state.flow = flow
    request.app.state.flow_task = task
    return {"status": "started", "phase": phase_number}


# ── Scan / Run ───────────────────────────────────────────────────────────────


@router.post("/{phase_number}/scan")
async def scan_phase_gaps(
    phase_number: int,
    graph: ProjectGraph = Depends(get_project_graph),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
) -> dict[str, Any]:
    """Run gap analyser for a phase and broadcast structural gaps."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")

    # _QUALITY_GAP_TYPES is a deliberate private re-export in flow, which tests
    # monkeypatch there — so it must be read from flow, not backend.crew.quality.
    # Strict mypy cannot treat an underscore-aliased import as re-exported.
    from backend.analysis.gap_analyser import GapAnalyser
    from backend.crew.flow import (  # type: ignore[attr-defined]
        _QUALITY_GAP_TYPES,
        GAP_TYPE_TO_PHASE,
    )
    from backend.server.forge_logger import forge_logger

    all_gaps = GapAnalyser().analyse(graph)
    phase_gaps = [
        g for g in all_gaps
        if g.type not in _QUALITY_GAP_TYPES and GAP_TYPE_TO_PHASE.get(g.type) == phase_number
    ]
    forge_logger.emit(
        "INFO", "SCAN ",
        f"Phase {phase_number} scan — {len(phase_gaps)} structural gap(s)",
        ", ".join(sorted({g.type.value for g in phase_gaps})) or "none",
    )
    if broadcaster is not None:
        broadcaster.gap_list_update(all_gaps)
    return {"phase": phase_number, "gap_count": len(phase_gaps)}


@router.post("/{phase_number}/scan-qual")
async def scan_phase_qual(
    phase_number: int,
    request: Request,
    pool: AgentPool = Depends(get_agent_pool),
    graph: ProjectGraph = Depends(get_project_graph),
    config: ForgeConfig = Depends(get_forge_config),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
    phase_store: PhaseStore = Depends(get_phase_store),
) -> dict[str, Any]:
    """Surface quality gaps for a phase using LLM detect-only mode."""
    if graph is None or pool is None:
        raise HTTPException(status_code=503, detail="Infrastructure not ready")
    flow = _make_flow(pool, graph, config, broadcaster, phase_store,
                      workspace=getattr(request.app.state, 'workspace', None))
    findings = await flow.scan_qual_detect(phase_number)
    return {"phase": phase_number, "qual_gap_count": len(findings)}


@router.post("/{phase_number}/run")
async def run_phase(
    phase_number: int,
    request: Request,
    pool: AgentPool = Depends(get_agent_pool),
    graph: ProjectGraph = Depends(get_project_graph),
    config: ForgeConfig = Depends(get_forge_config),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
    phase_store: PhaseStore = Depends(get_phase_store),
    session: ForgeSession = Depends(get_forge_session),
) -> dict[str, str]:
    """Run the full 4-step phase lifecycle."""
    if pool is None or graph is None or config is None:
        raise HTTPException(status_code=503, detail="Infrastructure not ready")
    if phase_number < 0 or phase_number > 14:
        raise HTTPException(status_code=400, detail=f"Phase {phase_number} is out of range (0–14).")

    await _cancel_existing_flow(request)
    flow = _make_flow(pool, graph, config, broadcaster, phase_store,
                      workspace=getattr(request.app.state, 'workspace', None))
    flow.state.session_id = str(session.session_id)
    request.app.state.flow = flow

    task = asyncio.create_task(flow.run_phase(phase_number))
    request.app.state.flow_task = task
    return {"status": "started", "phase": str(phase_number)}


# ── Trace management ─────────────────────────────────────────────────────────


@router.post("/12/sync-traces")
async def sync_traces(
    request: Request,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Sync trace properties with files on disk.

    Re-parses existing files and updates graph nodes. Clears trace
    properties from nodes whose files no longer exist.
    """
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")
    workspace = Path(getattr(request.app.state, "workspace", "."))
    from backend.workspace.trace_manager import sync_traces as _sync
    return await _sync(graph, workspace)


# ── Ingest ───────────────────────────────────────────────────────────────────


@router.post("/1/ingest")
async def ingest_forgemd(
    request: Request,
    graph: ProjectGraph = Depends(get_project_graph),
    config: ForgeConfig = Depends(get_forge_config),
    phase_store: PhaseStore = Depends(get_phase_store),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
) -> dict[str, Any]:
    """Read forge.md from the workspace and ingest it as the DOCUMENT node."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")

    if config and config.project.workspace_dir:
        workspace = Path(config.project.workspace_dir).resolve()
    else:
        workspace = Path(getattr(request.app.state, "workspace", Path(".")))

    from backend.server.forge_logger import forge_logger
    from backend.services.ingest import ingest_forgemd, resolve_forgemd_path

    forgemd_setting = config.project.forgemd if config else "forge.md"
    forge_logger.emit("INFO", "PHASE",
        f"Read Forge.md — workspace={workspace}, setting={forgemd_setting!r}")
    forgemd_path = resolve_forgemd_path(workspace, forgemd_setting)
    forge_logger.emit("INFO", "PHASE",
        f"Read Forge.md — resolved path: {forgemd_path} (exists={forgemd_path.exists()})")

    if not forgemd_path.exists():
        forge_logger.emit("WARN", "PHASE",
            f"Read Forge.md failed — file not found at {forgemd_path}",
            f"workspace={workspace}, setting={forgemd_setting!r}")
        raise HTTPException(
            status_code=404,
            detail=f"Could not find {forgemd_setting!r} in workspace {workspace}.",
        )

    forge_logger.user_action("read forge.md", str(forgemd_path))
    await ingest_forgemd(forgemd_path, graph, config)

    file_size = forgemd_path.stat().st_size
    forge_logger.emit("INFO", "PHASE",
        f"Forge.md ingested — {file_size:,} bytes", str(forgemd_path))

    if phase_store is not None:
        phase_store.set_status(1, "complete")
    if broadcaster is not None:
        broadcaster.emit(
            WSEventType.PHASE_TRANSITION,
            {"to_phase": 1, "status": "complete"},
        )
    return {"status": "ingested", "path": str(forgemd_path)}
