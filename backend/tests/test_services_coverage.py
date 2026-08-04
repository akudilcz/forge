"""Service and utility coverage tests — operator, ingest, context, main.

Covers OperatorService, ingest helpers, context bundle assembly, and the
CLI entry point. All external I/O is mocked for speed and determinism.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from backend.graph.models import GraphNode, NodeType

if TYPE_CHECKING:
    from backend.services.operator import OperatorService

# ===================================================================
# INGEST SERVICE
# ===================================================================


class TestResolveForgemdPath:
    """Tests for resolve_forgemd_path()."""

    def test_absolute_path_returned_directly(self) -> None:
        from backend.services.ingest import resolve_forgemd_path

        result = resolve_forgemd_path(Path("/workspace"), "/absolute/forge.md")
        assert result == Path("/absolute/forge.md")

    def test_finds_in_workspace_root(self, tmp_path: Path) -> None:
        from backend.services.ingest import resolve_forgemd_path

        (tmp_path / "forge.md").write_text("# Forge")
        result = resolve_forgemd_path(tmp_path, "forge.md")
        assert result == tmp_path / "forge.md"

    def test_case_insensitive_match(self, tmp_path: Path) -> None:
        from backend.services.ingest import resolve_forgemd_path

        # An uppercase extension must still resolve: the demo whitepapers ship
        # as FORGE-v1/v2/v3.MD, so a case-sensitive match would break the
        # documented demo flow on Linux.
        (tmp_path / "FORGE.MD").write_text("# Forge")
        result = resolve_forgemd_path(tmp_path, "forge.md")
        assert result == tmp_path / "FORGE.MD"

    def test_case_insensitive_match_in_subdirectory(self, tmp_path: Path) -> None:
        from backend.services.ingest import resolve_forgemd_path

        subdir = tmp_path / "docs"
        subdir.mkdir()
        (subdir / "Forge.MD").write_text("# Forge")
        result = resolve_forgemd_path(tmp_path, "forge.md")
        assert result == subdir / "Forge.MD"

    def test_root_match_beats_subdirectory_match(self, tmp_path: Path) -> None:
        from backend.services.ingest import resolve_forgemd_path

        (tmp_path / "forge.md").write_text("# root")
        subdir = tmp_path / "docs"
        subdir.mkdir()
        (subdir / "forge.md").write_text("# nested")
        assert resolve_forgemd_path(tmp_path, "forge.md") == tmp_path / "forge.md"

    def test_directory_named_like_the_target_is_ignored(self, tmp_path: Path) -> None:
        from backend.services.ingest import resolve_forgemd_path

        # Broadening the glob from "*.md" to "*" means directories are now
        # candidates too; only files may match.
        (tmp_path / "forge.md").mkdir()
        result = resolve_forgemd_path(tmp_path, "forge.md")
        assert result == tmp_path / "forge.md"

    def test_finds_in_subdirectory(self, tmp_path: Path) -> None:
        from backend.services.ingest import resolve_forgemd_path

        subdir = tmp_path / "docs"
        subdir.mkdir()
        (subdir / "forge.md").write_text("# Forge")
        result = resolve_forgemd_path(tmp_path, "forge.md")
        assert result == subdir / "forge.md"

    def test_fallback_to_workspace_setting(self, tmp_path: Path) -> None:
        from backend.services.ingest import resolve_forgemd_path

        result = resolve_forgemd_path(tmp_path, "custom.md")
        assert result == tmp_path / "custom.md"


class TestEnsureProjectNode:
    """Tests for _ensure_project_node()."""

    @pytest.mark.asyncio
    async def test_returns_existing(self) -> None:
        from backend.services.ingest import _ensure_project_node

        existing = GraphNode(
            node_id="PROJECT-001",
            node_type=NodeType.PROJECT.value,
            title="My Project",
        )
        graph = MagicMock()
        graph.nodes_by_type = AsyncMock(return_value=[existing])

        result = await _ensure_project_node(graph, "My Project")
        assert result == "PROJECT-001"
        graph.add_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_new(self) -> None:
        from backend.services.ingest import _ensure_project_node

        graph = MagicMock()
        graph.nodes_by_type = AsyncMock(return_value=[])
        graph.allocate_node_id = AsyncMock(return_value="PROJECT-001")
        graph.add_node = AsyncMock()

        result = await _ensure_project_node(graph, "New Project")
        assert result == "PROJECT-001"
        graph.add_node.assert_called_once()


class TestIngestForgemd:
    """Tests for ingest_forgemd()."""

    @pytest.mark.asyncio
    async def test_creates_new_document(self, tmp_path: Path) -> None:
        from backend.services.ingest import ingest_forgemd

        forge_path = tmp_path / "forge.md"
        forge_path.write_text("# My Spec\nContent here", encoding="utf-8")

        graph = MagicMock()
        graph.nodes_by_type = AsyncMock(
            return_value=[
                GraphNode(node_id="PROJECT-001", node_type="PROJECT", title="P"),
            ]
        )
        graph.find_node_by_slug = AsyncMock(return_value=None)
        graph.allocate_node_id = AsyncMock(return_value="DOCUMENT-001")
        graph.add_node = AsyncMock()

        config = MagicMock()
        config.project.name = "Test"

        await ingest_forgemd(forge_path, graph, config)
        # Should have been called twice: once for project check, once for doc
        assert graph.add_node.call_count == 1

    @pytest.mark.asyncio
    async def test_updates_existing_document(self, tmp_path: Path) -> None:
        from backend.services.ingest import ingest_forgemd

        forge_path = tmp_path / "forge.md"
        forge_path.write_text("# Updated content", encoding="utf-8")

        existing_doc = GraphNode(
            node_id="DOCUMENT-001",
            node_type="DOCUMENT",
            title="Forge.md",
            content="# Old content",
            properties={"slug": "forgemd"},
        )
        graph = MagicMock()
        graph.nodes_by_type = AsyncMock(
            return_value=[
                GraphNode(node_id="PROJECT-001", node_type="PROJECT", title="P"),
            ]
        )
        graph.find_node_by_slug = AsyncMock(return_value=existing_doc)
        graph.add_node = AsyncMock()

        config = MagicMock()
        config.project.name = "Test"

        await ingest_forgemd(forge_path, graph, config)
        graph.add_node.assert_called_once()
        # Content should have been updated on the doc object
        assert existing_doc.content == "# Updated content"

    @pytest.mark.asyncio
    async def test_no_update_when_content_same(self, tmp_path: Path) -> None:
        from backend.services.ingest import ingest_forgemd

        content = "# Same content"
        forge_path = tmp_path / "forge.md"
        forge_path.write_text(content, encoding="utf-8")

        existing_doc = GraphNode(
            node_id="DOCUMENT-001",
            node_type="DOCUMENT",
            title="Forge.md",
            content=content,
            properties={"slug": "forgemd"},
        )
        graph = MagicMock()
        graph.nodes_by_type = AsyncMock(
            return_value=[
                GraphNode(node_id="PROJECT-001", node_type="PROJECT", title="P"),
            ]
        )
        graph.find_node_by_slug = AsyncMock(return_value=existing_doc)
        graph.add_node = AsyncMock()

        config = MagicMock()
        config.project.name = "Test"

        await ingest_forgemd(forge_path, graph, config)
        graph.add_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_propagates_rather_than_being_swallowed(
        self, tmp_path: Path
    ) -> None:
        """A failed ingest must not let phase 1 report complete.

        This previously asserted the opposite — that the error was swallowed and
        logged. That behaviour meant phase 1 could create no DOCUMENT node and
        still be marked complete by ``_run_ingest_phase``, after which every
        later phase ran against an empty graph and the build reported success.
        The exception has to reach the caller so the phase-status write is
        skipped.
        """
        from backend.services.ingest import ingest_forgemd

        forge_path = tmp_path / "forge.md"
        forge_path.write_text("content", encoding="utf-8")

        graph = MagicMock()
        graph.nodes_by_type = AsyncMock(side_effect=RuntimeError("DB error"))

        config = MagicMock()
        config.project.name = "Test"

        with pytest.raises(RuntimeError, match="DB error"):
            await ingest_forgemd(forge_path, graph, config)


# ===================================================================
# CONTEXT BUNDLE ASSEMBLY
# ===================================================================


class TestContextBundle:
    """Tests for build_context_bundle() and tier builders."""

    def _make_node(
        self,
        node_id: str,
        node_type: str = "HLR",
        parent_id: str | None = None,
        trace_to: list[str] | None = None,
    ) -> GraphNode:
        return GraphNode(
            node_id=node_id,
            node_type=node_type,
            title=f"Title of {node_id}",
            content=f"Content of {node_id}",
            parent_id=parent_id,
            trace_to=trace_to or [],
        )

    def test_bundle_for_missing_node(self) -> None:
        from backend.graph.context import build_context_bundle

        graph = MagicMock()
        graph.node_sync.return_value = None
        result = build_context_bundle(graph, "missing")
        assert result["inner"] == []
        assert result["middle"] == []
        assert result["outer"] == []

    def test_bundle_inner_parent(self) -> None:
        from backend.graph.context import build_context_bundle

        parent = self._make_node("parent")
        child = self._make_node("child", parent_id="parent")

        graph = MagicMock()
        graph.node_sync.side_effect = lambda nid: {
            "child": child,
            "parent": parent,
        }.get(nid)
        graph.children_sync.return_value = []
        graph.siblings_sync.return_value = []
        graph.nodes_tracing_to.return_value = []

        result = build_context_bundle(graph, "child")
        inner_roles = [e["role"] for e in result["inner"]]
        assert "parent" in inner_roles

    def test_bundle_inner_children(self) -> None:
        from backend.graph.context import build_context_bundle

        node = self._make_node("node")
        child = self._make_node("child", parent_id="node")

        graph = MagicMock()
        graph.node_sync.side_effect = lambda nid: {
            "node": node,
            "child": child,
        }.get(nid)
        graph.children_sync.return_value = [child]
        graph.siblings_sync.return_value = []
        graph.nodes_tracing_to.return_value = []

        result = build_context_bundle(graph, "node")
        inner_roles = [e["role"] for e in result["inner"]]
        assert "child" in inner_roles

    def test_bundle_inner_contracts(self) -> None:
        from backend.graph.context import build_context_bundle

        node = self._make_node("mod.1", node_type="MODULE", parent_id="par")
        contract = self._make_node("ctr.1", node_type="CONTRACT", parent_id="par")
        parent = self._make_node("par")

        graph = MagicMock()
        graph.node_sync.side_effect = lambda nid: {
            "mod.1": node,
            "ctr.1": contract,
            "par": parent,
        }.get(nid)
        graph.children_sync.return_value = []
        graph.siblings_sync.return_value = [contract]
        graph.nodes_tracing_to.return_value = []

        result = build_context_bundle(graph, "mod.1")
        inner_roles = [e["role"] for e in result["inner"]]
        assert "contract" in inner_roles

    def test_bundle_inner_trace_to(self) -> None:
        from backend.graph.context import build_context_bundle

        target = self._make_node("hlr.1")
        node = self._make_node("mod.1", trace_to=["hlr.1"])

        graph = MagicMock()
        graph.node_sync.side_effect = lambda nid: {
            "mod.1": node,
            "hlr.1": target,
        }.get(nid)
        graph.children_sync.return_value = []
        graph.siblings_sync.return_value = []
        graph.nodes_tracing_to.return_value = []

        result = build_context_bundle(graph, "mod.1")
        inner_roles = [e["role"] for e in result["inner"]]
        assert "trace_to" in inner_roles

    def test_bundle_middle_siblings(self) -> None:
        from backend.graph.context import build_context_bundle

        node = self._make_node("mod.1", parent_id="par")
        sibling = self._make_node("mod.2", node_type="MODULE", parent_id="par")

        graph = MagicMock()
        graph.node_sync.side_effect = lambda nid: {
            "mod.1": node,
            "mod.2": sibling,
        }.get(nid)
        graph.children_sync.return_value = []
        graph.siblings_sync.return_value = [sibling]
        graph.nodes_tracing_to.return_value = []

        result = build_context_bundle(graph, "mod.1")
        middle_roles = [e["role"] for e in result["middle"]]
        assert "sibling" in middle_roles

    def test_bundle_middle_trace_from(self) -> None:
        from backend.graph.context import build_context_bundle

        node = self._make_node("hlr.1")
        tracer = self._make_node("mod.1", trace_to=["hlr.1"])

        graph = MagicMock()
        graph.node_sync.side_effect = lambda nid: {
            "hlr.1": node,
            "mod.1": tracer,
        }.get(nid)
        graph.children_sync.return_value = []
        graph.siblings_sync.return_value = []
        graph.nodes_tracing_to.return_value = ["mod.1"]

        result = build_context_bundle(graph, "hlr.1")
        middle_roles = [e["role"] for e in result["middle"]]
        assert "trace_from" in middle_roles

    def test_bundle_outer_ancestors(self) -> None:
        from backend.graph.context import build_context_bundle

        grandparent = self._make_node("gp", node_type="PROJECT")
        grandparent.parent_id = None
        parent = self._make_node("par", parent_id="gp")
        node = self._make_node("child", parent_id="par")

        graph = MagicMock()
        graph.node_sync.side_effect = lambda nid: {
            "child": node,
            "par": parent,
            "gp": grandparent,
        }.get(nid)
        graph.children_sync.return_value = []
        graph.siblings_sync.return_value = []
        graph.nodes_tracing_to.return_value = []

        result = build_context_bundle(graph, "child")
        outer_roles = [e["role"] for e in result["outer"]]
        assert "ancestor" in outer_roles

    def test_bundle_outer_trace_ancestors(self) -> None:
        from backend.graph.context import build_context_bundle

        trace_parent = self._make_node("tp", node_type="PROJECT")
        trace_parent.parent_id = None
        trace_target = self._make_node("hlr.1", parent_id="tp")
        node = self._make_node("mod.1", trace_to=["hlr.1"], parent_id="par")
        parent = self._make_node("par")
        parent.parent_id = None

        graph = MagicMock()
        graph.node_sync.side_effect = lambda nid: {
            "mod.1": node,
            "hlr.1": trace_target,
            "tp": trace_parent,
            "par": parent,
        }.get(nid)
        graph.children_sync.return_value = []
        graph.siblings_sync.return_value = []
        graph.nodes_tracing_to.return_value = []

        result = build_context_bundle(graph, "mod.1")
        outer_roles = [e["role"] for e in result["outer"]]
        assert "trace_ancestor" in outer_roles

    def test_no_duplicates_across_tiers(self) -> None:
        """A node appearing in inner should not also appear in middle or outer."""
        from backend.graph.context import build_context_bundle

        parent = self._make_node("par")
        parent.parent_id = None
        child = self._make_node("child", parent_id="par")
        sibling = self._make_node("sib", node_type="MODULE", parent_id="par")

        graph = MagicMock()
        graph.node_sync.side_effect = lambda nid: {
            "child": child,
            "par": parent,
            "sib": sibling,
        }.get(nid)
        graph.children_sync.return_value = []
        graph.siblings_sync.return_value = [sibling]
        graph.nodes_tracing_to.return_value = []

        result = build_context_bundle(graph, "child")
        all_ids = (
            [e["node_id"] for e in result["inner"]]
            + [e["node_id"] for e in result["middle"]]
            + [e["node_id"] for e in result["outer"]]
        )
        assert len(all_ids) == len(set(all_ids))


# ===================================================================
# OPERATOR SERVICE
# ===================================================================


class TestOperatorService:
    """Tests for OperatorService methods."""

    def _make_service(
        self, **overrides: object
    ) -> tuple[OperatorService, MagicMock, asyncio.AbstractEventLoop]:
        from backend.services.operator import OperatorService

        state = MagicMock()
        state.graph = MagicMock()
        state.config = MagicMock()
        state.config.project.name = "Test"
        state.config.project.forgemd = "forge.md"
        state.agent_pool = MagicMock()
        state.agent_pool.all_ids.return_value = ["doc"]
        state.broadcaster = MagicMock()
        state.phase_store = MagicMock()
        state.phase_store.get_all.return_value = [
            {"phase_number": i, "status": "pending"} for i in range(15)
        ]
        state.workspace = "/tmp/test_workspace"
        state.session = MagicMock()
        state.session.session_id = "s1"
        state.flow_task = None
        state.console_task = None

        for k, v in overrides.items():
            setattr(state, k, v)

        loop = asyncio.new_event_loop()
        service = OperatorService(state, loop)
        return service, state, loop

    @pytest.mark.asyncio
    async def test_cancel_existing_flow_no_task(self) -> None:
        svc, state, _ = self._make_service()
        await svc._cancel_existing_flow()
        assert state.flow_task is None

    @pytest.mark.asyncio
    async def test_cancel_existing_flow_with_task(self) -> None:
        svc, state, _ = self._make_service()
        task = MagicMock()
        task.done.return_value = False

        async def _wait() -> None:
            pass

        task.cancel = MagicMock()
        state.flow_task = task

        # Patch asyncio.wait_for to not actually wait
        with patch("asyncio.wait_for", new_callable=AsyncMock):
            await svc._cancel_existing_flow()
        task.cancel.assert_called_once()
        assert state.flow_task is None

    @pytest.mark.asyncio
    async def test_stop_build_running(self) -> None:
        svc, state, _ = self._make_service()
        task = MagicMock()
        task.done.return_value = False
        task.cancel = MagicMock()
        state.flow_task = task

        result = await svc.stop_build()
        assert result["status"] == "stopped"
        task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_build_not_running(self) -> None:
        svc, state, _ = self._make_service()
        result = await svc.stop_build()
        assert result["status"] == "not_running"

    @pytest.mark.asyncio
    async def test_stop_all(self) -> None:
        svc, state, _ = self._make_service()
        flow_task = MagicMock()
        flow_task.done.return_value = False
        flow_task.cancel = MagicMock()
        console_task = MagicMock()
        console_task.done.return_value = False
        console_task.cancel = MagicMock()
        state.flow_task = flow_task
        state.console_task = console_task

        result = await svc.stop_all()
        assert result["status"] == "stopped"
        assert "build" in result["cancelled"]
        assert "console" in result["cancelled"]
        flow_task.cancel.assert_called_once()
        console_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_all_nothing_running(self) -> None:
        svc, state, _ = self._make_service()
        result = await svc.stop_all()
        assert result["status"] == "stopped"
        assert result["cancelled"] == []

    @pytest.mark.asyncio
    @patch("backend.crew.flow.ForgeFlow")
    async def test_scan_quality(self, mock_flow_cls: MagicMock) -> None:
        mock_flow = MagicMock()
        mock_flow.scan_qual_detect = AsyncMock(return_value=["finding1"])
        mock_flow_cls.return_value = mock_flow

        svc, state, _ = self._make_service()
        result = await svc.scan_quality(3)
        assert result["qual_gap_count"] == 1

    @pytest.mark.asyncio
    @patch("backend.crew.flow.ForgeFlow")
    async def test_qual_check(self, mock_flow_cls: MagicMock) -> None:
        mock_flow = MagicMock()
        mock_flow.run_qual_check = AsyncMock()
        mock_flow_cls.return_value = mock_flow

        svc, state, _ = self._make_service()
        with patch("asyncio.wait_for", new_callable=AsyncMock):
            result = await svc.qual_check(5)
        assert result["status"] == "started"

    @pytest.mark.asyncio
    @patch("backend.crew.flow.ForgeFlow")
    async def test_run_phase_single(self, mock_flow_cls: MagicMock) -> None:
        mock_flow = MagicMock()
        mock_flow.state = MagicMock()
        mock_flow.run_phase = AsyncMock()
        mock_flow_cls.return_value = mock_flow

        svc, state, _ = self._make_service()
        with patch("asyncio.wait_for", new_callable=AsyncMock):
            result = await svc.run_phase(5)
        assert result["status"] == "started"

    @pytest.mark.asyncio
    @patch("backend.crew.flow.ForgeFlow")
    async def test_run_phase_range(self, mock_flow_cls: MagicMock) -> None:
        mock_flow = MagicMock()
        mock_flow.state = MagicMock()
        mock_flow.kickoff_async = AsyncMock()
        mock_flow_cls.return_value = mock_flow

        svc, state, _ = self._make_service()
        with patch("asyncio.wait_for", new_callable=AsyncMock):
            result = await svc.run_phase(2, end_phase=5)
        assert result["status"] == "started"
        assert result["phases"] == "2-5"

    @pytest.mark.asyncio
    async def test_purge_derived(self) -> None:
        svc, state, _ = self._make_service()
        state.graph.nodes = AsyncMock(
            return_value=[
                GraphNode(node_id="P1", node_type="PROJECT", title="P"),
                GraphNode(node_id="H1", node_type="HLR", title="H"),
            ]
        )
        state.graph.delete_node = AsyncMock()
        state.graph.reset_sequences = AsyncMock()

        result = await svc.purge_derived()
        assert result["status"] == "purged"
        assert result["deleted_count"] == 1

    @pytest.mark.asyncio
    async def test_ingest_document_not_found(self, tmp_path: Path) -> None:
        svc, state, _ = self._make_service()
        state.workspace = str(tmp_path)
        state.config.project.forgemd = "forge.md"

        result = await svc.ingest_document()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    @patch("backend.services.ingest.ingest_forgemd", new_callable=AsyncMock)
    async def test_ingest_document_success(
        self, mock_ingest: AsyncMock, tmp_path: Path
    ) -> None:
        forge_path = tmp_path / "forge.md"
        forge_path.write_text("# Forge", encoding="utf-8")

        svc, state, _ = self._make_service()
        state.workspace = str(tmp_path)
        state.config.project.forgemd = "forge.md"

        result = await svc.ingest_document()
        assert result["status"] == "ingested"
        mock_ingest.assert_called_once()

    def test_run_on_main_loop(self) -> None:
        svc, state, loop = self._make_service()

        async def _coro() -> int:
            return 42

        try:
            # Start the loop in a thread so we can call run_on_main_loop
            import threading

            loop_started = threading.Event()

            async def _run() -> None:
                loop_started.set()
                await asyncio.sleep(2)

            def _run_loop() -> None:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(_run())

            t = threading.Thread(target=_run_loop, daemon=True)
            t.start()
            loop_started.wait(timeout=2)

            result = svc.run_on_main_loop(_coro(), timeout=5)
            assert result == 42
        finally:
            loop.call_soon_threadsafe(loop.stop)


# ===================================================================
# MAIN / CLI ENTRY POINT
# ===================================================================


class TestMainCLI:
    """Tests for the CLI entry point."""

    @patch("backend.main.uvicorn.run")
    @patch("backend.main.load_config")
    def test_serve_command(
        self, mock_load: MagicMock, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        from click.testing import CliRunner

        from backend.main import cli

        mock_config = MagicMock()
        mock_config.server.host = "localhost"
        mock_config.server.port = 7340
        mock_load.return_value = mock_config

        runner = CliRunner()
        runner.invoke(cli, ["serve", "--workspace", str(tmp_path)])
        # uvicorn.run should have been called
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert (
            call_kwargs[1]["host"] == "localhost"
            or call_kwargs[0][0] == "backend.server.app:create_app"
        )

    @patch("backend.main.uvicorn.run")
    @patch("backend.main.load_config")
    def test_serve_with_custom_host_port(
        self, mock_load: MagicMock, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        from click.testing import CliRunner

        from backend.main import cli

        mock_config = MagicMock()
        mock_config.server.host = "localhost"
        mock_config.server.port = 7340
        mock_load.return_value = mock_config

        runner = CliRunner()
        runner.invoke(
            cli, ["serve", "--workspace", str(tmp_path), "--host", "0.0.0.0", "--port", "8888"]
        )
        mock_run.assert_called_once()
        kwargs = mock_run.call_args[1]
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 8888

    @patch("backend.main.uvicorn.run")
    @patch("backend.main.load_config")
    def test_serve_with_reload(
        self, mock_load: MagicMock, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        from click.testing import CliRunner

        from backend.main import cli

        mock_config = MagicMock()
        mock_config.server.host = "localhost"
        mock_config.server.port = 7340
        mock_load.return_value = mock_config

        runner = CliRunner()
        runner.invoke(cli, ["serve", "--workspace", str(tmp_path), "--reload"])
        mock_run.assert_called_once()
        kwargs = mock_run.call_args[1]
        assert kwargs["reload"] is True


# ===================================================================
# APP FACTORY
# ===================================================================


class TestAppFactory:
    """Tests for create_app() factory function."""

    def test_create_app_returns_fastapi(self) -> None:
        os.environ.pop("FORGE_AUTH_USER", None)
        os.environ.pop("FORGE_AUTH_PASS", None)
        from backend.server.app import create_app

        app = create_app()
        assert app.title == "FORGE Control Station"

    def test_create_app_has_health_endpoint(self) -> None:
        os.environ.pop("FORGE_AUTH_USER", None)
        os.environ.pop("FORGE_AUTH_PASS", None)
        from backend.server.app import create_app

        app = create_app()
        # Attach minimal state so routes don't crash
        app.state.session = MagicMock()
        app.state.session.model_dump.return_value = {}
        c = TestClient(app)
        resp = c.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_create_app_includes_routers(self) -> None:
        os.environ.pop("FORGE_AUTH_USER", None)
        os.environ.pop("FORGE_AUTH_PASS", None)
        from backend.server.app import create_app

        app = create_app()
        # FastAPI >=0.129 wraps included routers in _IncludedRouter objects that
        # expose neither .path nor .routes, so app.routes no longer lists the
        # mounted API paths. The OpenAPI schema is the stable public surface.
        routes = list(app.openapi()["paths"])
        # Check that key API routes exist under /api/v1
        assert any("/api/v1/phases" in r for r in routes)
        assert any("/api/v1/console" in r for r in routes)
        assert any("/api/v1/graph" in r for r in routes)
        assert any("/api/v1/agents" in r for r in routes)

    def test_create_app_with_workspace(self, tmp_path: Path) -> None:
        os.environ.pop("FORGE_AUTH_USER", None)
        os.environ.pop("FORGE_AUTH_PASS", None)
        from backend.server.app import create_app

        app = create_app(workspace_path=tmp_path)
        assert app.state.workspace_root == tmp_path


# ===================================================================
# SECRETS HELPERS
# ===================================================================


class TestSecretsHelpers:
    """Tests for secrets module helper functions."""

    def test_load_secrets_empty_path(self) -> None:
        from backend.server.routers.secrets import _load_secrets

        result = _load_secrets("")
        assert result == {}

    def test_load_secrets_missing_db(self) -> None:
        from backend.server.routers.secrets import _load_secrets

        result = _load_secrets("/nonexistent/path/db.sqlite")
        assert result == {}

    def test_load_and_save_secrets(self, tmp_path: Path) -> None:
        import sqlite3

        from backend.server.routers.secrets import _load_secrets, _save_secrets

        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

        _save_secrets(db, {"MY_KEY": "my_val"})
        loaded = _load_secrets(db)
        assert loaded["MY_KEY"] == "my_val"

    def test_save_secrets_empty_path(self, tmp_path: Path) -> None:
        from backend.server.routers.secrets import _save_secrets

        # Should not raise
        _save_secrets("", {"key": "val"})

    def test_inject_secrets_into_env(self, tmp_path: Path) -> None:
        import json
        import sqlite3

        from backend.server.routers.secrets import inject_secrets_into_env

        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO settings VALUES ('secrets', ?)",
            (json.dumps({"INJECT_TEST": "injected"}),),
        )
        conn.commit()
        conn.close()

        inject_secrets_into_env(db)
        assert os.environ.get("INJECT_TEST") == "injected"
        os.environ.pop("INJECT_TEST", None)


# ===================================================================
# AUTH MIDDLEWARE HELPERS
# ===================================================================


class TestAuthMiddleware:
    """Tests for auth middleware helper functions."""

    def test_sign_and_verify_token(self) -> None:
        from backend.server.middleware.auth import _make_secret, sign_token, verify_token

        secret = _make_secret("password")
        token = sign_token("admin", secret)
        user = verify_token(token, secret)
        assert user == "admin"

    def test_verify_invalid_token(self) -> None:
        from backend.server.middleware.auth import _make_secret, verify_token

        secret = _make_secret("password")
        assert verify_token("garbage", secret) is None
        assert verify_token("1:2:3", secret) is None

    def test_is_auth_enabled(self) -> None:
        from backend.server.middleware.auth import is_auth_enabled

        os.environ.pop("FORGE_AUTH_USER", None)
        os.environ.pop("FORGE_AUTH_PASS", None)
        assert is_auth_enabled() is False

        os.environ["FORGE_AUTH_USER"] = "admin"
        os.environ["FORGE_AUTH_PASS"] = "pass"
        try:
            assert is_auth_enabled() is True
        finally:
            os.environ.pop("FORGE_AUTH_USER", None)
            os.environ.pop("FORGE_AUTH_PASS", None)
