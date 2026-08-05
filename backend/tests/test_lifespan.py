"""Tests for backend.server.lifespan — startup/shutdown and init helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.server.lifespan import (
    _configure_logging,
    _init_config,
    _init_workspace_paths,
    _shutdown,
)

# ── _configure_logging ──────────────────────────────────────────────────────


class TestConfigureLogging:
    def test_dev_mode_sets_debug_level(self) -> None:
        with patch.dict("os.environ", {"FORGE_DEV_MODE": "1"}):
            _configure_logging()
            import structlog
            cfg = structlog.get_config()
            assert cfg["wrapper_class"] is not None

    def test_prod_mode_default(self) -> None:
        with patch.dict("os.environ", {"FORGE_DEV_MODE": "0"}):
            _configure_logging()
            import structlog
            cfg = structlog.get_config()
            assert cfg["wrapper_class"] is not None


# ── _init_workspace_paths ───────────────────────────────────────────────────


class TestInitWorkspacePaths:
    def test_creates_forge_dirs(self, tmp_path: Path) -> None:
        app = MagicMock()
        app.state.workspace_root = str(tmp_path)

        forge_dir, db_path = _init_workspace_paths(app)

        assert (forge_dir / "review").is_dir()
        assert db_path.endswith("forge.db")

    def test_falls_back_to_env(self, tmp_path: Path) -> None:
        app = MagicMock(spec=[])
        app.state = MagicMock(spec=[])  # no workspace_root attr

        with patch.dict("os.environ", {"FORGE_WORKSPACE": str(tmp_path)}):
            forge_dir, db_path = _init_workspace_paths(app)

        assert forge_dir == tmp_path / ".forge"


# ── _init_config ────────────────────────────────────────────────────────────


class TestInitConfig:
    def test_raises_without_api_key_in_prod(self, tmp_path: Path) -> None:
        app = MagicMock()
        mock_config = MagicMock()
        mock_config.project.workspace_dir = str(tmp_path / "ws")
        mock_config.project.name = "test"
        mock_config.server.host = "127.0.0.1"
        mock_config.server.port = 7340
        mock_config.llm.provider = "openrouter"
        mock_config.llm.api_key_env = "MISSING_KEY_XYZ"

        with (
            patch("backend.server.lifespan.load_config", return_value=mock_config),
            patch("backend.server.routers.secrets.inject_secrets_into_env"),
            patch.dict("os.environ", {"FORGE_DEV_MODE": "0"}, clear=False),
        ):
            import os
            os.environ.pop("MISSING_KEY_XYZ", None)
            with pytest.raises(RuntimeError, match="MISSING_KEY_XYZ"):
                _init_config(app, "/fake/db")

    def test_skips_api_key_check_for_ollama(self, tmp_path: Path) -> None:
        app = MagicMock()
        mock_config = MagicMock()
        mock_config.project.workspace_dir = str(tmp_path / "ws")
        mock_config.project.name = "test"
        mock_config.server.host = "127.0.0.1"
        mock_config.server.port = 7340
        mock_config.llm.provider = "ollama"

        with (
            patch("backend.server.lifespan.load_config", return_value=mock_config),
            patch("backend.server.routers.secrets.inject_secrets_into_env"),
            patch.dict("os.environ", {"FORGE_DEV_MODE": "0"}, clear=False),
        ):
            config, workspace = _init_config(app, "/fake/db")
            assert config is mock_config

    def test_skips_api_key_check_in_dev_mode(self, tmp_path: Path) -> None:
        app = MagicMock()
        mock_config = MagicMock()
        mock_config.project.workspace_dir = str(tmp_path / "ws")
        mock_config.project.name = "test"
        mock_config.server.host = "127.0.0.1"
        mock_config.server.port = 7340
        mock_config.llm.provider = "openrouter"
        mock_config.llm.api_key_env = "MISSING_KEY_XYZ"

        with (
            patch("backend.server.lifespan.load_config", return_value=mock_config),
            patch("backend.server.routers.secrets.inject_secrets_into_env"),
            patch.dict("os.environ", {"FORGE_DEV_MODE": "1"}, clear=False),
        ):
            config, workspace = _init_config(app, "/fake/db")
            assert config is mock_config


# ── _shutdown ───────────────────────────────────────────────────────────────


class TestShutdown:
    @pytest.mark.asyncio
    async def test_cancels_background_tasks(self) -> None:
        app = MagicMock()
        task1 = MagicMock(spec=asyncio.Task)
        task2 = MagicMock(spec=asyncio.Task)
        tasks: list[asyncio.Task[Any]] = [task1, task2]

        with patch("asyncio.gather", new_callable=AsyncMock):
            await _shutdown(app, tasks)

        task1.cancel.assert_called_once()
        task2.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_tasks_is_no_op(self) -> None:
        app = MagicMock()
        await _shutdown(app, [])


# ── Full _startup integration ──────────────────────────────────────────────


class TestStartup:
    @pytest.mark.asyncio
    async def test_startup_wires_all_subsystems(self, tmp_path: Path) -> None:
        """_startup creates dirs and wires subsystems to app.state."""
        app = MagicMock()
        app.state = MagicMock()
        app.state.workspace_root = str(tmp_path)

        mock_config = MagicMock()
        mock_config.project.name = "test"
        mock_config.project.workspace_dir = str(tmp_path / "ws")
        mock_config.project.forgemd = "forge.md"
        mock_config.server.host = "127.0.0.1"
        mock_config.server.port = 7340
        mock_config.llm.provider = "ollama"
        mock_config.llm.call_delay_ms = 0
        mock_config.tools.shell_exec_allowlist = []
        mock_config.tools.web_fetch_allowlist = []
        mock_config.git.commit_prefix = ""

        mock_graph = MagicMock()
        mock_graph.initialise = AsyncMock()
        mock_graph.all_nodes.return_value = []

        mock_pool = MagicMock()
        mock_pool.initialise = AsyncMock()

        with (
            patch("backend.server.lifespan.load_config", return_value=mock_config),
            patch("backend.server.lifespan.ProjectGraph", return_value=mock_graph),
            patch("backend.server.lifespan.AgentPool", return_value=mock_pool),
            patch("backend.server.lifespan.AgentFactory"),
            patch("backend.server.lifespan.EventBus"),
            patch("backend.server.lifespan.WebSocketManager") as mock_ws,
            patch("backend.server.lifespan.EventBroadcaster"),
            patch("backend.server.lifespan.ToolRegistry"),
            patch("backend.server.routers.secrets.inject_secrets_into_env"),
            patch("backend.server.forge_logger.forge_logger"),
            patch("backend.core.work_queue.work_queue"),
            patch("backend.server.lifespan.PhaseStore"),
            patch("backend.server.lifespan.ForgeSession"),
        ):
            mock_ws.return_value.set_loop = MagicMock()

            from backend.server.lifespan import _startup
            await _startup(app, [])

        forge_dir = tmp_path / ".forge"
        assert (forge_dir / "review").is_dir()


# ── lifespan context manager ────────────────────────────────────────────────


class TestLifespanContextManager:
    @pytest.mark.asyncio
    async def test_runs_startup_then_shutdown(self) -> None:
        from backend.server.lifespan import lifespan

        app = MagicMock()
        with (
            patch(
                "backend.server.lifespan._startup", new_callable=AsyncMock
            ) as mock_start,
            patch(
                "backend.server.lifespan._shutdown", new_callable=AsyncMock
            ) as mock_stop,
        ):
            async with lifespan(app):
                mock_start.assert_awaited_once()
                mock_stop.assert_not_awaited()
            mock_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_runs_even_when_body_raises(self) -> None:
        from backend.server.lifespan import lifespan

        app = MagicMock()
        with (
            patch("backend.server.lifespan._startup", new_callable=AsyncMock),
            patch(
                "backend.server.lifespan._shutdown", new_callable=AsyncMock
            ) as mock_stop,
        ):
            with pytest.raises(RuntimeError, match="app crashed"):
                async with lifespan(app):
                    raise RuntimeError("app crashed")
            mock_stop.assert_awaited_once()


# ── _init_config extra branches ─────────────────────────────────────────────


class TestInitConfigExtra:
    def _config(self, tmp_path: Path) -> MagicMock:
        mock_config = MagicMock()
        mock_config.project.workspace_dir = str(tmp_path / "ws")
        mock_config.project.name = "test"
        mock_config.server.host = "127.0.0.1"
        mock_config.server.port = 7340
        mock_config.llm.provider = "openrouter"
        mock_config.llm.api_key_env = "PRESENT_KEY_XYZ"
        return mock_config

    def test_passes_when_api_key_present(self, tmp_path: Path) -> None:
        app = MagicMock()
        mock_config = self._config(tmp_path)
        with (
            patch("backend.server.lifespan.load_config", return_value=mock_config),
            patch("backend.server.routers.secrets.inject_secrets_into_env"),
            patch.dict(
                "os.environ",
                {"FORGE_DEV_MODE": "0", "PRESENT_KEY_XYZ": "sk-123"},
                clear=False,
            ),
        ):
            config, workspace = _init_config(app, "/fake/db")
        assert config is mock_config

    def test_invalid_context_window_is_tolerated(self, tmp_path: Path) -> None:
        app = MagicMock()
        mock_config = self._config(tmp_path)
        mock_config.llm.provider = "ollama"
        mock_config.llm.context_window_default = "not-a-number"
        with (
            patch("backend.server.lifespan.load_config", return_value=mock_config),
            patch("backend.server.routers.secrets.inject_secrets_into_env"),
            patch.dict("os.environ", {"FORGE_DEV_MODE": "1"}, clear=False),
        ):
            config, workspace = _init_config(app, "/fake/db")
        assert config is mock_config


# ── _wire_graph_events ──────────────────────────────────────────────────────


class TestWireGraphEvents:
    def test_on_change_broadcasts_node_payload(self) -> None:
        from backend.server.lifespan import _wire_graph_events
        from backend.server.websocket.events import WSEventType

        graph = MagicMock()
        broadcaster = MagicMock()
        _wire_graph_events(graph, broadcaster)

        on_change = graph.set_on_change.call_args.args[0]
        on_change("update", {"node_id": "n1", "content": "x" * 500})

        event_type, payload = broadcaster.emit.call_args.args
        assert event_type == WSEventType.GRAPH_NODE_CHANGED
        assert payload["action"] == "update"
        assert payload["node"]["node_id"] == "n1"
        assert len(payload["node"]["content"]) == 200  # truncated


# ── _init_events ────────────────────────────────────────────────────────────


class TestInitEvents:
    @pytest.mark.asyncio
    async def test_wires_logger_and_prunes_logs(self, tmp_path: Path) -> None:
        from backend.server.lifespan import _init_events

        app = MagicMock()
        config = MagicMock()
        with (
            patch("backend.server.forge_logger.forge_logger") as mock_logger,
            patch(
                "backend.observability.log_retention.prune_old_logs",
                return_value=7,
            ) as mock_prune,
            patch("backend.core.work_queue.work_queue"),
            patch("backend.server.lifespan._register_litellm_callback"),
            patch("backend.server.lifespan._configure_throttle"),
        ):
            bus, ws_manager, broadcaster = _init_events(app, config, tmp_path)

        mock_logger.initialise.assert_called_once()
        mock_prune.assert_called_once()
        msgs = [c.args[2] for c in mock_logger.emit.call_args_list]
        assert any("pruned 7 rows" in m for m in msgs)
        assert app.state.ws_manager is ws_manager
        assert app.state.broadcaster is broadcaster

    @pytest.mark.asyncio
    async def test_prune_failure_is_tolerated(self, tmp_path: Path) -> None:
        from backend.server.lifespan import _init_events

        app = MagicMock()
        with (
            patch("backend.server.forge_logger.forge_logger") as mock_logger,
            patch(
                "backend.observability.log_retention.prune_old_logs",
                side_effect=RuntimeError("db locked"),
            ),
            patch("backend.core.work_queue.work_queue"),
            patch("backend.server.lifespan._register_litellm_callback"),
            patch("backend.server.lifespan._configure_throttle"),
        ):
            _init_events(app, MagicMock(), tmp_path)

        msgs = [c.args[2] for c in mock_logger.emit.call_args_list]
        assert any("prune failed" in m for m in msgs)


# ── _register_litellm_callback / _configure_throttle ────────────────────────


class TestBestEffortHelpers:
    def test_litellm_callback_registration_failure_tolerated(self) -> None:
        import sys

        from backend.server.lifespan import _register_litellm_callback

        with patch.dict(sys.modules, {"litellm": None}):
            _register_litellm_callback()  # ImportError swallowed

    def test_configure_throttle_applies_delay(self) -> None:
        from backend.server.lifespan import _configure_throttle

        config = MagicMock()
        config.llm.call_delay_ms = 250
        with patch("backend.agents.throttle.llm_throttle") as mock_throttle:
            _configure_throttle(config)
        assert mock_throttle.delay_ms == 250

    def test_configure_throttle_bad_value_keeps_default(self) -> None:
        from backend.server.lifespan import _configure_throttle

        config = MagicMock()
        config.llm.call_delay_ms = "not-a-number"
        with patch("backend.agents.throttle.llm_throttle") as mock_throttle:
            _configure_throttle(config)  # must not raise
        assert not isinstance(mock_throttle.delay_ms, str)


# ── _init_agents ────────────────────────────────────────────────────────────


class TestInitAgents:
    @pytest.mark.asyncio
    async def test_pool_init_failure_is_tolerated(self) -> None:
        from backend.server.lifespan import _init_agents

        app = MagicMock()
        mock_pool = MagicMock()
        mock_pool.initialise = AsyncMock(side_effect=RuntimeError("no models"))
        tool_registry = MagicMock()

        with (
            patch("backend.server.lifespan.AgentFactory"),
            patch("backend.server.lifespan.AgentPool", return_value=mock_pool),
        ):
            await _init_agents(app, MagicMock(), tool_registry, MagicMock())

        assert app.state.agent_pool is mock_pool
        tool_registry.add_tools.assert_called_once()
        mock_pool.rebuild.assert_called_once()
