"""FastAPI lifespan — startup and shutdown for the full FORGE infrastructure stack.

Initialises the graph, config, session, phase store, agent pool, tool registry,
WebSocket manager, and diagnostic logger in the correct dependency order.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI

from backend.agents.factory import AgentFactory
from backend.agents.pool import AgentPool
from backend.comms.bus import EventBus
from backend.config.loader import load_config
from backend.core.phase_store import PhaseStore
from backend.core.session import ForgeSession
from backend.graph.engine import ProjectGraph
from backend.server.websocket.broadcaster import EventBroadcaster
from backend.server.websocket.manager import WebSocketManager
from backend.tools.registry import ToolRegistry

_log = structlog.get_logger(__name__)


def _configure_logging() -> None:
    """Configure structlog with human-readable output in dev mode, JSON in production."""
    dev_mode = os.environ.get("FORGE_DEV_MODE", "0") == "1"
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            (structlog.dev.ConsoleRenderer() if dev_mode else structlog.processors.JSONRenderer()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if dev_mode else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan context manager: run startup, yield, then shut down."""
    _configure_logging()
    background_tasks: list[asyncio.Task] = []
    await _startup(app, background_tasks)
    try:
        yield
    finally:
        await _shutdown(app, background_tasks)


# ── Startup orchestrator ────────────────────────────────────────────────────


async def _startup(app: FastAPI, background_tasks: list[asyncio.Task]) -> None:
    """Initialise all FORGE subsystems and attach them to ``app.state``."""
    forge_dir, db_path = _init_workspace_paths(app)
    config, workspace = _init_config(app, db_path)
    graph = await _init_graph(app, db_path)
    session_id = _init_session_and_phases(app, config, workspace, db_path)
    bus, ws_manager, broadcaster = _init_events(app, config, forge_dir)
    _wire_graph_events(graph, broadcaster)
    tool_registry = _init_tools(app, config, graph, workspace, bus)
    await _init_agents(app, config, tool_registry, broadcaster)
    _log.info("forge.startup.complete", session_id=session_id)


# ── Init helpers (each ≤30 lines) ──────────────────────────────────────────


def _init_workspace_paths(app: FastAPI) -> tuple[Path, str]:
    """Resolve app root, create .forge dirs, return (forge_dir, db_path)."""
    app_root = getattr(app.state, "workspace_root", None)
    if app_root is None:
        app_root = Path(os.environ.get("FORGE_WORKSPACE", "."))
    app_root = Path(app_root).resolve()
    forge_dir = app_root / ".forge"
    (forge_dir / "review").mkdir(parents=True, exist_ok=True)
    db_path = str(forge_dir / "forge.db")
    app.state.db_path = db_path
    return forge_dir, db_path


def _init_config(app: FastAPI, db_path: str) -> tuple[Any, Path]:
    """Load config, inject secrets, validate API key. Returns (config, workspace)."""
    from backend.server.routers.secrets import inject_secrets_into_env

    inject_secrets_into_env(db_path)
    config = load_config(db_path)
    app.state.config = config

    workspace = Path(config.project.workspace_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    app.state.workspace = workspace

    _log.info(
        "forge.startup",
        project=config.project.name,
        host=config.server.host,
        port=config.server.port,
    )

    dev_mode = os.environ.get("FORGE_DEV_MODE", "0") == "1"
    if not dev_mode and config.llm.provider != "ollama":
        if not os.environ.get(config.llm.api_key_env):
            raise RuntimeError(
                f"Environment variable {config.llm.api_key_env} is not set. "
                "Set it or run with FORGE_DEV_MODE=1."
            )

    # Sync context window to the LLM callback module
    try:
        from backend.agents.llm_callback import set_context_window
        set_context_window(int(config.llm.context_window_default))
    except (TypeError, ValueError):
        pass  # MagicMock in tests or invalid config

    return config, workspace


def _wire_graph_events(graph: ProjectGraph, broadcaster: EventBroadcaster) -> None:
    """Connect graph mutations to WebSocket broadcasts."""
    from backend.server.websocket.events import WSEventType

    def on_change(action: str, node_data: dict) -> None:
        broadcaster.emit(WSEventType.GRAPH_NODE_CHANGED, {
            "action": action,
            "node": {
                "node_id": node_data.get("node_id", ""),
                "node_type": node_data.get("node_type", ""),
                "title": node_data.get("title", ""),
                "parent_id": node_data.get("parent_id"),
                "trace_to": node_data.get("trace_to", []),
                "layer": node_data.get("layer", 0),
                "lifecycle": node_data.get("lifecycle", ""),
                "content": (node_data.get("content") or "")[:200],
            },
        })

    graph.set_on_change(on_change)


async def _init_graph(app: FastAPI, db_path: str) -> ProjectGraph:
    """Create ProjectGraph and apply schema."""
    graph = ProjectGraph(db_path)
    await graph.initialise()
    app.state.graph = graph
    _log.info("forge.graph.ready", db=db_path)
    return graph


def _init_session_and_phases(
    app: FastAPI, config: Any, workspace: Path, db_path: str,
) -> str:
    """Create session, phase store. Returns session_id."""
    session_id = "sess-" + hashlib.sha1(config.project.name.encode()).hexdigest()[:12]
    session = ForgeSession.create(
        project_name=config.project.name,
        forgemd_path=config.project.forgemd,
        workspace_root=str(workspace),
    )
    app.state.session = session
    app.state.session_id = session_id

    phase_store = PhaseStore(db_path)
    phase_store.reset_active_to_pending()
    app.state.phase_store = phase_store
    _log.info(
        "forge.phase_store.ready",
        phases={p["phase_number"]: p["status"] for p in phase_store.get_all()},
    )

    return session_id


def _init_events(
    app: FastAPI, config: Any, forge_dir: Path,
) -> tuple[EventBus, WebSocketManager, EventBroadcaster]:
    """Set up event bus, WebSocket, forge logger, work queue, LiteLLM callback."""
    bus = EventBus()
    ws_manager = WebSocketManager()
    broadcaster = EventBroadcaster(ws_manager)
    app.state.bus = bus
    app.state.ws_manager = ws_manager
    app.state.broadcaster = broadcaster

    loop = asyncio.get_event_loop()
    ws_manager.set_loop(loop)

    from backend.server.forge_logger import forge_logger
    logs_db_path = forge_dir / "forge.logs.db"
    forge_logger.initialise(
        forge_dir / "forge.log", ws_manager, sqlite_path=logs_db_path,
    )
    app.state.logs_db_path = str(logs_db_path)

    # Prune old logs on startup (retention = 3 days).
    from backend.observability.log_retention import (  # noqa: PLC0415
        DEFAULT_MAX_AGE_DAYS,
        prune_old_logs,
    )
    try:
        deleted = prune_old_logs(logs_db_path, max_age_days=DEFAULT_MAX_AGE_DAYS)
        forge_logger.emit(
            "INFO", "SYS  ",
            f"log retention pruned {deleted} rows (retention={DEFAULT_MAX_AGE_DAYS}d)",
            pruned_rows=deleted,
            retention_days=DEFAULT_MAX_AGE_DAYS,
        )
    except Exception as exc:  # noqa: BLE001
        forge_logger.emit(
            "WARN", "SYS  ", f"log retention prune failed: {exc}",
            error_type=type(exc).__name__,
        )

    forge_logger.emit(
        "INFO", "SYS  ", "FORGE server starting", startup_event=True,
    )

    from backend.core.work_queue import work_queue
    work_queue.initialise(ws_manager)

    _register_litellm_callback()
    _configure_throttle(config)

    return bus, ws_manager, broadcaster


def _register_litellm_callback() -> None:
    """Register LiteLLM diagnostic callback (best-effort)."""
    try:
        import litellm

        from backend.agents.llm_callback import ForgeLLMCallback
        litellm.callbacks = [ForgeLLMCallback()]
        _log.info("forge.litellm.callback_registered")
    except Exception as _cb_exc:  # noqa: BLE001
        _log.warning("forge.litellm.callback_failed", error=str(_cb_exc))


def _configure_throttle(config: Any) -> None:
    """Apply LLM call delay from config."""
    from backend.agents.throttle import llm_throttle
    try:
        llm_throttle.delay_ms = int(config.llm.call_delay_ms)
    except (AttributeError, TypeError, ValueError):
        pass  # keep default 400ms


def _init_tools(
    app: FastAPI, config: Any, graph: ProjectGraph,
    workspace: Path, bus: EventBus,
) -> ToolRegistry:
    """Build and register all LLM-callable tools."""
    from backend.tools.check_trace_quality import CheckTraceQualityTool
    from backend.tools.evaluate_progress import EvaluateProgressTool
    tools = _build_tool_list(config, graph, workspace, bus)
    tools.append(EvaluateProgressTool(str(workspace), graph))
    tools.append(CheckTraceQualityTool(str(workspace), graph, config.llm))
    tool_registry = ToolRegistry(tools=tools)
    app.state.tool_registry = tool_registry
    return tool_registry


def _build_tool_list(
    config: Any, graph: ProjectGraph, workspace: Path, bus: EventBus,
) -> list[Any]:
    """Instantiate all tools. Delegates to sub-builders by category."""
    return (
        _build_file_tools(config, workspace, bus)
        + _build_graph_tools(config, graph)
    )


def _build_file_tools(config: Any, workspace: Path, bus: EventBus) -> list[Any]:
    """Instantiate file, shell, and workspace tools."""
    from backend.tools.batch_patch import BatchPatchTool
    from backend.tools.code_search import CodeSearchTool
    from backend.tools.file_patch import FilePatchTool
    from backend.tools.file_read import FileReadTool
    from backend.tools.file_rename import FileRenameTool
    from backend.tools.file_write import FileWriteTool
    from backend.tools.git_ops import GitOpsTool
    from backend.tools.insert_lines import InsertLinesTool
    from backend.tools.list_dir import ListDirTool
    from backend.tools.list_files import ListFilesTool
    from backend.tools.multi_file_write import MultiFileWriteTool
    from backend.tools.python_lint import PythonLintTool
    from backend.tools.read_docs import ReadDocsTool
    from backend.tools.run_tests import RunTestsTool
    from backend.tools.send_message import SendMessageTool
    from backend.tools.shell_exec import ShellExecTool
    from backend.tools.web_fetch import WebFetchTool
    from backend.tools.work_queue_tools import QueueAddTool, QueuePromoteTool, QueueRemoveTool
    from backend.tools.workspace_doctor import WorkspaceDoctorTool

    ws = str(workspace)
    return [
        FileReadTool(ws), FileWriteTool(ws), FilePatchTool(ws),
        BatchPatchTool(ws), FileRenameTool(ws), PythonLintTool(ws),
        ShellExecTool(ws, config.tools.shell_exec_allowlist),
        CodeSearchTool(ws), RunTestsTool(ws),
        GitOpsTool(ws, config.git.commit_prefix),
        WebFetchTool(config.tools.web_fetch_allowlist),
        SendMessageTool().bind_bus(bus),
        ListFilesTool(ws), ListDirTool(ws), ReadDocsTool(ws),
        InsertLinesTool(ws), MultiFileWriteTool(ws),
        WorkspaceDoctorTool(ws),
        QueueAddTool(), QueueRemoveTool(), QueuePromoteTool(),
    ]


def _build_graph_tools(config: Any, graph: ProjectGraph) -> list[Any]:
    """Instantiate graph and analysis tools."""
    from backend.analysis.gap_analyser import GapAnalyser
    from backend.tools.analysis import (
        CheckAtomicityTool,
        CheckConsistencyTool,
        DeriveRequirementTool,
    )
    from backend.tools.graph_bulk_delete import GraphBulkDeleteTool
    from backend.tools.graph_grep import GraphGrepTool
    from backend.tools.graph_ops import (
        GraphAddEdgeTool,
        GraphAddNodeTool,
        GraphAddTracesTool,
        GraphDeleteNodeTool,
        GraphRefreshProvenanceTool,
        GraphRemoveEdgeTool,
        GraphRemoveTracesTool,
        GraphReparentNodeTool,
        GraphUpdateNodeTool,
        GraphUpdateTraceTool,
    )
    from backend.tools.graph_read import GraphReadTool
    from backend.tools.graph_regex_replace import GraphRegexReplaceTool
    from backend.tools.graph_search import GraphSearchTool
    from backend.tools.graph_stats import GraphStatsTool
    from backend.tools.graph_trace import GraphTraceTool
    from backend.tools.multi_graph_write import MultiGraphWriteTool

    return [
        GraphReadTool(graph), GraphAddNodeTool(graph),
        GraphUpdateNodeTool(graph), GraphDeleteNodeTool(graph),
        GraphReparentNodeTool(graph), GraphUpdateTraceTool(graph),
        GraphAddTracesTool(graph), GraphRemoveTracesTool(graph),
        GraphAddEdgeTool(graph), GraphRemoveEdgeTool(graph),
        GraphRefreshProvenanceTool(graph),
        MultiGraphWriteTool(graph), GraphSearchTool(graph),
        GraphGrepTool(graph), GraphStatsTool(graph, analyser=GapAnalyser()),
        GraphTraceTool(graph),
        DeriveRequirementTool(config.llm),
        CheckConsistencyTool(config.llm), CheckAtomicityTool(config.llm),
        GraphRegexReplaceTool(graph), GraphBulkDeleteTool(graph),
    ]


async def _init_agents(
    app: FastAPI, config: Any, tool_registry: ToolRegistry,
    broadcaster: EventBroadcaster,
) -> None:
    """Create agent pool + operator service, wire operator tools."""
    from backend.services.operator import OperatorService
    from backend.tools.operator import (
        IngestDocumentTool,
        PurgeDerivedTool,
        QualCheckTool,
        RunPhaseTool,
        ScanGapsTool,
        ScanQualityTool,
        StopAllAgentsTool,
        StopBuildTool,
    )

    agent_factory = AgentFactory(tool_registry, config)
    agent_pool = AgentPool(broadcaster)
    try:
        await agent_pool.initialise(agent_factory, config)
    except Exception as exc:
        _log.warning("forge.agents.init_failed", error=str(exc))
    app.state.agent_pool = agent_pool

    loop = asyncio.get_event_loop()
    operator_service = OperatorService(app.state, loop)
    app.state.operator_service = operator_service
    tool_registry.add_tools([
        RunPhaseTool(operator_service), StopBuildTool(operator_service),
        StopAllAgentsTool(operator_service), ScanGapsTool(operator_service),
        ScanQualityTool(operator_service), QualCheckTool(operator_service),
        PurgeDerivedTool(operator_service), IngestDocumentTool(operator_service),
    ])
    agent_pool.rebuild(config=config)


# ── Shutdown ────────────────────────────────────────────────────────────────


async def _shutdown(app: FastAPI, background_tasks: list[asyncio.Task]) -> None:
    """Cancel all background tasks and wait for them to finish."""
    _log.info("forge.shutdown")
    for task in background_tasks:
        task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)


# ── Forge.md helpers (delegated to services.ingest) ─────────────────────────
# Re-exported so existing imports from ``backend.server.lifespan`` keep working.
from backend.services.ingest import (  # noqa: E402
    resolve_forgemd_path as _resolve_forgemd_path,
)

# Legacy alias used by control.py
_find_forgemd_file = _resolve_forgemd_path
