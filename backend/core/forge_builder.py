"""ForgeBuilder — constructs a fully wired ForgeFlow from a config and workspace.

Used by the integration test-suite (the server wires its own dependencies in
``server/lifespan.py``). Encapsulates the dependency wiring so test fixtures
build a real pipeline in one call.

Usage::

    from backend.core.forge_builder import ForgeBuilder

    builder = ForgeBuilder(config=config, workspace=Path("/tmp/ws"))
    flow = await builder.build()
    await flow.run_phase(2)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from backend.agents.factory import AgentFactory
from backend.agents.pool import AgentPool
from backend.config.models import ForgeConfig
from backend.core.phase_store import PhaseStore
from backend.graph.engine import ProjectGraph
from backend.pipeline.flow import ForgeFlow

logger = logging.getLogger(__name__)


class ForgeBuilder:
    """Build a fully wired ForgeFlow with all production dependencies."""

    def __init__(
        self,
        config: ForgeConfig,
        workspace: Path,
        *,
        broadcaster: Any | None = None,
        db_path: Path | str | None = None,
    ) -> None:
        self._config = config
        self._workspace = workspace
        self._broadcaster = broadcaster or MagicMock()
        self._db_path = str(db_path) if db_path else str(workspace / ".forge" / "forge.db")
        self._graph: ProjectGraph | None = None
        self._pool: AgentPool | None = None

    async def build(self) -> ForgeFlow:
        """Construct and return a ready-to-use ForgeFlow."""
        self._ensure_dirs()
        graph = await self._init_graph()
        phase_store = PhaseStore(self._db_path)
        tools = self._build_tools(graph)
        pool = await self._init_agents(tools)

        flow = ForgeFlow(
            pool=pool,
            graph=graph,
            config=self._config,
            broadcaster=self._broadcaster,
            phase_store=phase_store,
            workspace=self._workspace,
        )
        return flow

    @property
    def graph(self) -> ProjectGraph | None:
        """The graph instance (available after build)."""
        return self._graph

    @property
    def pool(self) -> AgentPool | None:
        """The agent pool (available after build)."""
        return self._pool

    # ── Private init steps ───────────────────────────────────────────────

    def _ensure_dirs(self) -> None:
        """Create workspace subdirectories if they don't exist."""
        self._workspace.mkdir(parents=True, exist_ok=True)
        (self._workspace / "src").mkdir(exist_ok=True)
        (self._workspace / "tests").mkdir(exist_ok=True)
        forge_dir = Path(self._db_path).parent
        forge_dir.mkdir(parents=True, exist_ok=True)

    async def _init_graph(self) -> ProjectGraph:
        """Create and initialise the project graph."""
        graph = ProjectGraph(self._db_path)
        await graph.initialise()
        self._graph = graph
        return graph

    def _build_tools(self, graph: ProjectGraph) -> list[Any]:
        """Build all tool instances (graph + file + mission feedback tools)."""
        graph_tools = self._build_graph_tools(graph)
        file_tools = self._build_file_tools()
        mission_tools = self._build_mission_tools(graph)
        return graph_tools + file_tools + mission_tools

    def _build_graph_tools(self, graph: ProjectGraph) -> list[Any]:
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
            _GraphMutationTool,
        )
        from backend.tools.graph_read import GraphReadTool
        from backend.tools.graph_regex_replace import GraphRegexReplaceTool
        from backend.tools.graph_search import GraphSearchTool
        from backend.tools.graph_stats import GraphStatsTool
        from backend.tools.graph_trace import GraphTraceTool
        from backend.tools.multi_graph_write import MultiGraphWriteTool

        # Single-operation mutation tools all share _GraphMutationTool.__init__;
        # referencing them through the base type keeps that constructor visible
        # (pydantic's dataclass_transform otherwise hides it behind a synthesised
        # field-based __init__ that does not exist at runtime).
        mutation_tools: tuple[type[_GraphMutationTool], ...] = (
            GraphAddNodeTool,
            GraphUpdateNodeTool,
            GraphDeleteNodeTool,
            GraphReparentNodeTool,
            GraphUpdateTraceTool,
            GraphAddTracesTool,
            GraphRemoveTracesTool,
            GraphAddEdgeTool,
            GraphRemoveEdgeTool,
            GraphRefreshProvenanceTool,
        )

        llm = self._config.llm
        return [
            GraphReadTool(graph),
            *(tool_cls(graph) for tool_cls in mutation_tools),
            MultiGraphWriteTool(graph),
            GraphSearchTool(graph),
            GraphGrepTool(graph),
            GraphStatsTool(graph, analyser=GapAnalyser()),
            GraphTraceTool(graph),
            DeriveRequirementTool(llm),
            CheckConsistencyTool(llm),
            CheckAtomicityTool(llm),
            GraphRegexReplaceTool(graph),
            GraphBulkDeleteTool(graph),
        ]

    def _build_file_tools(self) -> list[Any]:
        """Instantiate file and shell tools."""
        from backend.tools.file_patch import FilePatchTool
        from backend.tools.file_read import FileReadTool
        from backend.tools.file_rename import FileRenameTool
        from backend.tools.file_write import FileWriteTool
        from backend.tools.insert_lines import InsertLinesTool
        from backend.tools.list_dir import ListDirTool
        from backend.tools.list_files import ListFilesTool
        from backend.tools.multi_file_write import MultiFileWriteTool
        from backend.tools.python_lint import PythonLintTool
        from backend.tools.read_docs import ReadDocsTool
        from backend.tools.shell_exec import ShellExecTool

        ws = str(self._workspace)
        return [
            FileReadTool(ws),
            FileWriteTool(ws),
            FilePatchTool(ws),
            FileRenameTool(ws),
            PythonLintTool(ws),
            ShellExecTool(ws, ["*"]),
            ListFilesTool(ws),
            ListDirTool(ws),
            ReadDocsTool(ws),
            InsertLinesTool(ws),
            MultiFileWriteTool(ws),
        ]

    def _build_mission_tools(self, graph: ProjectGraph) -> list[Any]:
        """Instantiate the phase-12 mission feedback tools.

        Must mirror the server path (lifespan._init_tools /
        _build_file_tools) — the mission agent refuses to start without
        evaluate_progress (specs/03, Required tools).
        """
        from backend.tools.check_trace_quality import CheckTraceQualityTool
        from backend.tools.evaluate_progress import EvaluateProgressTool
        from backend.tools.workspace_doctor import WorkspaceDoctorTool

        ws = str(self._workspace)
        return [
            WorkspaceDoctorTool(ws),
            EvaluateProgressTool(ws, graph),
            CheckTraceQualityTool(ws, graph, self._config.llm),
        ]

    async def _init_agents(self, tools: list[Any]) -> AgentPool:
        """Build the tool registry, agent factory, and agent pool."""
        from backend.tools.registry import ToolRegistry

        registry = ToolRegistry(tools=tools)
        factory = AgentFactory(registry, self._config)
        pool = AgentPool(self._broadcaster)
        await pool.initialise(factory, self._config)
        self._pool = pool
        return pool
