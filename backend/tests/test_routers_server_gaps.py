"""Gap-closing tests for phases, console, and workspace routers.

Complements test_routers_coverage.py — targets the error responses,
empty-state branches, and helper functions that file does not reach.
Self-contained: builds its own minimal app/mocks (no cross-test imports).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.console.history import console_history


def _make_app(
    graph: MagicMock | None = None,
    pool: MagicMock | None = None,
    config: MagicMock | None = None,
    broadcaster: MagicMock | None = None,
    phase_store: MagicMock | None = None,
    workspace_root: str | None = None,
) -> FastAPI:
    """Build the real app with mocked state (mirrors test_routers_coverage)."""
    os.environ.pop("FORGE_AUTH_USER", None)
    os.environ.pop("FORGE_AUTH_PASS", None)

    from backend.server.app import create_app

    app = create_app()
    ws_root = workspace_root or tempfile.mkdtemp()

    if config is None:
        config = MagicMock()
        config.project.name = "Test Project"
        config.project.workspace_dir = ws_root
        config.project.forgemd = "forge.md"
        config.llm.agents = {}
        config.llm.context_window_for_model.return_value = 128000

    session = MagicMock()
    session.session_id = "sess-test"
    session.workspace_root = ws_root
    session.model_dump.return_value = {"session_id": "sess-test"}

    app.state.graph = graph
    app.state.phase_store = phase_store
    app.state.agent_pool = pool
    app.state.config = config
    app.state.session = session
    app.state.broadcaster = broadcaster
    app.state.workspace = ws_root
    app.state.workspace_root = Path(ws_root)
    return app


def _mock_graph() -> MagicMock:
    g = MagicMock()
    g.reset = AsyncMock()
    g.reset_sequences = AsyncMock()
    g.find_node_by_slug = AsyncMock(return_value=None)
    g.all_nodes = MagicMock(return_value=[])
    return g


def _mock_store() -> MagicMock:
    ps = MagicMock()
    ps.get_all.return_value = [
        {"phase_number": i, "status": "pending"} for i in range(15)
    ]
    ps.get.return_value = {"phase_number": 3, "status": "awaiting_approval"}
    return ps


def _mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.all_ids.return_value = ["doc"]
    return pool


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ===================================================================
# PHASES ROUTER — helpers
# ===================================================================


class TestCancelExistingFlow:
    @pytest.mark.asyncio
    async def test_cancels_running_task(self) -> None:
        from backend.server.routers.phases import _cancel_existing_flow

        task = asyncio.create_task(asyncio.sleep(30))
        await asyncio.sleep(0)
        request = MagicMock()
        request.app.state = SimpleNamespace(flow_task=task)

        await _cancel_existing_flow(request)

        assert task.cancelled()
        assert request.app.state.flow_task is None

    @pytest.mark.asyncio
    async def test_no_task_is_noop(self) -> None:
        from backend.server.routers.phases import _cancel_existing_flow

        request = MagicMock()
        request.app.state = SimpleNamespace()
        await _cancel_existing_flow(request)
        assert request.app.state.flow_task is None


class TestCancelAgentWork:
    @pytest.mark.asyncio
    async def test_cancels_running_tasks_no_broadcaster(self) -> None:
        from backend.server.routers.phases import _cancel_agent_work

        flow_task = asyncio.create_task(asyncio.sleep(30))
        console_task = asyncio.create_task(asyncio.sleep(30))
        await asyncio.sleep(0)
        request = MagicMock()
        request.app.state = SimpleNamespace(
            flow_task=flow_task, console_task=console_task
        )

        await _cancel_agent_work(request, None)

        assert flow_task.cancelled()
        assert console_task.cancelled()
        assert request.app.state.flow_task is None
        assert request.app.state.console_task is None

    @pytest.mark.asyncio
    async def test_broadcaster_without_pool(self) -> None:
        from backend.server.routers.phases import _cancel_agent_work

        broadcaster = MagicMock()
        request = MagicMock()
        request.app.state = SimpleNamespace(agent_pool=None)

        await _cancel_agent_work(request, broadcaster)

        broadcaster.emit.assert_called_once()
        broadcaster.agent_status_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcaster_with_pool_resets_agents(self) -> None:
        from backend.server.routers.phases import _cancel_agent_work

        broadcaster = MagicMock()
        pool = _mock_pool()
        request = MagicMock()
        request.app.state = SimpleNamespace(agent_pool=pool)

        await _cancel_agent_work(request, broadcaster)

        broadcaster.agent_status_change.assert_called_once_with("doc", "idle")


class TestResetWorkspace:
    def test_missing_workspace_returns_early(self, tmp_path: Path) -> None:
        from backend.server.routers.phases import _reset_workspace

        config = MagicMock()
        config.project.workspace_dir = str(tmp_path / "does-not-exist")
        _reset_workspace(config)  # must not raise

    def test_removes_generated_artifacts(self, tmp_path: Path) -> None:
        from backend.server.routers.phases import _reset_workspace

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("x = 1")
        (tmp_path / "BUILD.bazel").write_text("# build")
        (tmp_path / "bazel-out").mkdir()
        target = tmp_path / "real-bin"
        target.mkdir()
        (tmp_path / "bazel-bin").symlink_to(target)
        (tmp_path / "FORGE.MD").write_text("# keep me")

        config = MagicMock()
        config.project.workspace_dir = str(tmp_path)
        _reset_workspace(config)

        assert not (tmp_path / "src").exists()
        assert not (tmp_path / "BUILD.bazel").exists()
        assert not (tmp_path / "bazel-out").exists()
        assert not (tmp_path / "bazel-bin").exists()
        assert (tmp_path / "FORGE.MD").exists()  # user files preserved


# ===================================================================
# PHASES ROUTER — endpoints
# ===================================================================


class TestPhasesEndpointGaps:
    def test_approve_without_flow_or_broadcaster(self) -> None:
        app = _make_app(
            graph=_mock_graph(), phase_store=_mock_store(), broadcaster=None
        )
        resp = _client(app).post("/api/v1/phases/3/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    @patch("backend.server.routers.phases._make_flow")
    def test_start_build_without_body_uses_defaults(
        self, mock_make_flow: MagicMock
    ) -> None:
        flow = MagicMock()
        flow.kickoff_async = AsyncMock()
        mock_make_flow.return_value = flow
        app = _make_app(
            graph=_mock_graph(), pool=_mock_pool(),
            phase_store=_mock_store(), broadcaster=MagicMock(),
        )
        resp = _client(app).post("/api/v1/phases/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"
        assert flow.state.start_phase == 0
        assert flow.state.end_phase == 13

    def test_stop_build_no_broadcaster(self) -> None:
        app = _make_app(graph=_mock_graph(), broadcaster=None)
        task = MagicMock()
        task.done.return_value = False
        app.state.flow_task = task
        resp = _client(app).post("/api/v1/phases/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"
        task.cancel.assert_called_once()

    def test_stop_build_broadcaster_without_pool(self) -> None:
        broadcaster = MagicMock()
        app = _make_app(graph=_mock_graph(), pool=None, broadcaster=broadcaster)
        app.state.agent_pool = None
        task = MagicMock()
        task.done.return_value = False
        app.state.flow_task = task
        resp = _client(app).post("/api/v1/phases/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"
        broadcaster.emit.assert_called_once()
        broadcaster.agent_status_change.assert_not_called()

    def test_reset_without_phase_store(self) -> None:
        app = _make_app(
            graph=_mock_graph(), phase_store=None, broadcaster=MagicMock()
        )
        with patch(
            "backend.services.ingest._ensure_project_node",
            new_callable=AsyncMock,
            return_value="P1",
        ):
            resp = _client(app).post("/api/v1/phases/reset")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reset"

    def test_reset_without_broadcaster(self) -> None:
        app = _make_app(
            graph=_mock_graph(), phase_store=_mock_store(), broadcaster=None
        )
        with patch(
            "backend.services.ingest._ensure_project_node",
            new_callable=AsyncMock,
            return_value="P1",
        ):
            resp = _client(app).post("/api/v1/phases/reset")
        assert resp.status_code == 200

    def test_purge_derived_503_no_operator_service(self) -> None:
        app = _make_app(graph=_mock_graph())
        app.state.operator_service = None
        resp = _client(app).post("/api/v1/phases/purge-derived")
        assert resp.status_code == 503

    def test_audit_phase_503_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/phases/5/audit")
        assert resp.status_code == 503

    def test_scan_without_broadcaster(self) -> None:
        app = _make_app(graph=_mock_graph(), broadcaster=None)
        with (
            patch("backend.analysis.gap_analyser.GapAnalyser") as mock_cls,
            patch("backend.crew.flow.GAP_TYPE_TO_PHASE", {}),
            patch("backend.crew.flow._QUALITY_GAP_TYPES", set()),
        ):
            mock_cls.return_value.analyse.return_value = []
            resp = _client(app).post("/api/v1/phases/3/scan")
        assert resp.status_code == 200
        assert resp.json()["gap_count"] == 0

    def test_ingest_workspace_from_app_state(self, tmp_path: Path) -> None:
        """When config has no workspace_dir, ingest falls back to app.state."""
        forgemd = tmp_path / "forge.md"
        forgemd.write_text("# Forge\nContent")
        config = MagicMock()
        config.project.workspace_dir = ""
        config.project.forgemd = "forge.md"

        with (
            patch(
                "backend.services.ingest.ingest_forgemd",
                new_callable=AsyncMock,
            ),
            patch(
                "backend.services.ingest.resolve_forgemd_path",
                return_value=forgemd,
            ),
        ):
            app = _make_app(
                graph=_mock_graph(), config=config,
                phase_store=None, broadcaster=None,
                workspace_root=str(tmp_path),
            )
            resp = _client(app).post("/api/v1/phases/1/ingest")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ingested"


# ===================================================================
# CONSOLE ROUTER — background agent internals
# ===================================================================


def _turn(text: str) -> MagicMock:
    turn = MagicMock()
    turn.tool_calls = []
    turn.text_content = text
    return turn


def _agen(*turns: MagicMock) -> Any:
    async def gen(*args: Any, **kwargs: Any) -> Any:
        for t in turns:
            yield t

    return gen


class TestConsoleInternals:
    def setup_method(self) -> None:
        console_history.clear()

    def teardown_method(self) -> None:
        console_history.clear()

    def test_run_console_no_config_uses_defaults(self) -> None:
        """With no config on app.state, context sync + model lookup no-op."""
        app = _make_app(graph=_mock_graph(), pool=_mock_pool())
        app.state.config = None
        resp = _client(app).post("/api/v1/console/run", json={"request": "hi"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    @pytest.mark.asyncio
    async def test_run_console_agent_records_response(self) -> None:
        from backend.server.routers.console import _run_console_agent

        pool = _mock_pool()
        with patch(
            "backend.server.routers.console._stream_agent",
            new_callable=AsyncMock,
            return_value="the answer",
        ):
            await _run_console_agent("question", "req-1234", pool, None, "m")
        assert console_history.message_count == 2  # user + ai

    @pytest.mark.asyncio
    async def test_run_console_agent_empty_response_not_recorded(self) -> None:
        from backend.server.routers.console import _run_console_agent

        pool = _mock_pool()
        with patch(
            "backend.server.routers.console._stream_agent",
            new_callable=AsyncMock,
            return_value="",
        ):
            await _run_console_agent("question", "req-1234", pool, None, "m")
        assert console_history.message_count == 1  # user only

    @pytest.mark.asyncio
    async def test_run_console_agent_swallows_errors(self) -> None:
        from backend.server.routers.console import _run_console_agent

        pool = MagicMock()
        pool.get.side_effect = RuntimeError("agent pool broken")
        await _run_console_agent("q", "req-1234", pool, None, "m")  # no raise

    def test_is_transient_classification(self) -> None:
        from backend.server.routers.console import _is_transient

        assert _is_transient(ConnectionError("reset")) is True
        assert _is_transient(TimeoutError()) is True
        assert _is_transient(ValueError("nope")) is False

    def test_is_transient_openai_errors(self) -> None:
        openai = pytest.importorskip("openai")
        from backend.server.routers.console import _is_transient

        server_err = openai.APIError.__new__(openai.APIError)
        server_err.status_code = 503
        assert _is_transient(server_err) is True

        client_err = openai.APIError.__new__(openai.APIError)
        client_err.status_code = 400
        assert _is_transient(client_err) is False

        no_code = openai.APIError.__new__(openai.APIError)
        assert _is_transient(no_code) is True

    @pytest.mark.asyncio
    async def test_stream_agent_returns_final_text(self) -> None:
        from backend.server.routers.console import _stream_agent

        with patch(
            "backend.agents.streaming.iter_agent_turns", _agen(_turn("hello"))
        ):
            result = await _stream_agent(MagicMock(), [], "model")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_stream_agent_recursion_limit(self) -> None:
        from langgraph.errors import GraphRecursionError

        from backend.server.routers.console import _stream_agent

        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise GraphRecursionError("too deep")
            yield  # pragma: no cover

        with patch("backend.agents.streaming.iter_agent_turns", boom):
            result = await _stream_agent(MagicMock(), [], "model")
        assert "Step limit reached" in result

    @pytest.mark.asyncio
    async def test_stream_agent_retries_transient_then_succeeds(self) -> None:
        from backend.server.routers.console import _stream_agent

        calls = {"n": 0}

        async def flaky(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("blip")
            yield _turn("recovered")

        with (
            patch("backend.agents.streaming.iter_agent_turns", flaky),
            patch(
                "backend.server.routers.console.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            result = await _stream_agent(MagicMock(), [], "model")
        assert result == "recovered"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_stream_agent_non_transient_raises(self) -> None:
        from backend.server.routers.console import _stream_agent

        async def bad(*args: Any, **kwargs: Any) -> Any:
            raise ValueError("bug")
            yield  # pragma: no cover

        with patch("backend.agents.streaming.iter_agent_turns", bad):
            with pytest.raises(ValueError, match="bug"):
                await _stream_agent(MagicMock(), [], "model")

    @pytest.mark.asyncio
    async def test_stream_agent_transient_exhausts_retries(self) -> None:
        from backend.server.routers.console import _stream_agent

        async def always_down(*args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("still down")
            yield  # pragma: no cover

        with (
            patch("backend.agents.streaming.iter_agent_turns", always_down),
            patch(
                "backend.server.routers.console.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            with pytest.raises(ConnectionError, match="still down"):
                await _stream_agent(MagicMock(), [], "model")


# ===================================================================
# WORKSPACE ROUTER
# ===================================================================


class TestWorkspaceGaps:
    def test_tree_skips_hidden_and_cache_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "visible.txt").write_text("x")
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/tree")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()["tree"]["children"]]
        assert names == ["visible.txt"]

    def test_build_tree_permission_error(self) -> None:
        from backend.server.routers.workspace import _build_tree

        root = MagicMock(spec=Path)
        root.exists.return_value = True
        root.is_dir.return_value = True
        root.iterdir.side_effect = PermissionError("denied")
        root.name = "locked"
        result = _build_tree(root, max_depth=4, current_depth=0)
        assert result == {"name": "locked", "type": "directory", "children": []}

    def test_functions_listing(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "__init__.py").write_text("")
        (src / "mod.py").write_text(
            "def foo():\n    return 1\n\n\ndef _hidden():\n    return 2\n"
        )
        (src / "broken.py").symlink_to(tmp_path / "missing-target.py")

        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/functions")
        assert resp.status_code == 200
        data = resp.json()
        assert "src/mod.py" in data
        assert "src/__init__.py" not in data
        assert "src/broken.py" not in data  # unreadable file skipped
        names = {f["name"] for f in data["src/mod.py"]["functions"]}
        assert "foo" in names

    def test_functions_no_src_dirs(self, tmp_path: Path) -> None:
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/functions")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_save_forgemd_without_config_skips_parse(self) -> None:
        app = _make_app(graph=_mock_graph())
        app.state.config = None
        resp = _client(app).put(
            "/api/v1/workspace/forgemd", json={"content": "# x"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "saved"}

    def test_deliverables_download_success(self, tmp_path: Path) -> None:
        (tmp_path / "deliverables.zip").write_bytes(b"PK\x03\x04zipdata")
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/deliverables/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

    def test_deliverables_manifest_skips_directories(self, tmp_path: Path) -> None:
        deliv = tmp_path / "deliverables"
        (deliv / "sub").mkdir(parents=True)
        (deliv / "sub" / "report.txt").write_text("r")
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/deliverables/manifest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is True
        assert [f["path"] for f in data["files"]] == ["sub/report.txt"]
