"""Router coverage tests — maximise coverage for all router modules.

Uses a lightweight FastAPI test app with dependency overrides and mocked
services so tests are fast, deterministic, and require no external I/O.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config.models import ForgeConfig
from backend.graph.models import (
    GraphEdge,
    GraphNode,
    ImpactSet,
    LifecycleState,
    NodeType,
    TraceabilityGaps,
)

# ---------------------------------------------------------------------------
# Shared test-app factory
# ---------------------------------------------------------------------------


def _make_app(
    graph: MagicMock | None = None,
    pool: MagicMock | None = None,
    config: ForgeConfig | MagicMock | None = None,
    session: MagicMock | None = None,
    broadcaster: MagicMock | None = None,
    phase_store: MagicMock | None = None,
    bus: MagicMock | None = None,
    workspace_root: str | None = None,
    db_path: str = "",
) -> FastAPI:
    """Build a minimal FastAPI app with all routers and mocked state."""
    # Ensure auth middleware is disabled
    os.environ.pop("FORGE_AUTH_USER", None)
    os.environ.pop("FORGE_AUTH_PASS", None)

    from backend.server.app import create_app

    app = create_app()

    ws_root = workspace_root or tempfile.mkdtemp()

    if config is None:
        config = MagicMock()
        config.compliance.enabled = True
        config.compliance.standard = "DO-178C"
        config.compliance.dal = "B"
        config.project.name = "Test Project"
        config.project.workspace_dir = ws_root
        config.project.forgemd = "forge.md"
        config.model_dump.return_value = {"project": {}, "llm": {}}
        config.llm.agents = {}
        config.llm.call_delay_ms = 400
        config.llm.context_window_for_model.return_value = 128000

    if session is None:
        session = MagicMock()
        session.session_id = "sess-test"
        session.workspace_root = ws_root
        session.model_dump.return_value = {"session_id": "sess-test"}

    if broadcaster is None:
        broadcaster = MagicMock()
        broadcaster.emit = MagicMock()
        broadcaster.agent_status_change = MagicMock()
        broadcaster.gap_list_update = MagicMock()

    if phase_store is None:
        phase_store = MagicMock()
        phase_store.get_all.return_value = [
            {"phase_number": i, "status": "pending"} for i in range(15)
        ]
        phase_store.get.return_value = {"phase_number": 0, "status": "pending"}
        phase_store.set_status = MagicMock()
        phase_store.reset_all = MagicMock()

    if pool is None:
        pool = MagicMock()
        pool.all_ids.return_value = ["doc"]
        pool.get.return_value = MagicMock()
        pool.rebuild = MagicMock()

    if bus is None:
        bus = MagicMock()
        bus.emit = AsyncMock()
        bus.recent_events.return_value = []

    app.state.graph = graph
    app.state.phase_store = phase_store
    app.state.agent_pool = pool
    app.state.bus = bus
    app.state.config = config
    app.state.session = session
    app.state.broadcaster = broadcaster
    app.state.contracts = MagicMock()
    app.state.db_path = db_path
    app.state.workspace = ws_root
    app.state.workspace_root = Path(ws_root)
    app.state.tool_registry = None

    # purge-derived delegates to OperatorService, so the router needs it wired.
    import asyncio as _asyncio

    from backend.services.operator import OperatorService

    app.state.operator_service = OperatorService(app.state, _asyncio.new_event_loop())

    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


def _mock_graph(**overrides: object) -> MagicMock:
    """Return a MagicMock pretending to be a ProjectGraph."""
    g = MagicMock()
    g.nodes = AsyncMock(return_value=[])
    g.node = AsyncMock(return_value=None)
    g.node_sync = MagicMock(return_value=None)
    g.add_node = AsyncMock()
    g.delete_node = AsyncMock()
    g.update_node = AsyncMock(
        return_value=(
            GraphNode(node_id="n1", node_type="HLR", title="T", content="c"),
            ImpactSet(root_node_id="n1"),
        )
    )
    g.ancestors = AsyncMock(return_value=[])
    g.descendants = AsyncMock(return_value=[])
    g.children = AsyncMock(return_value=[])
    g.children_sync = MagicMock(return_value=[])
    g.siblings_sync = MagicMock(return_value=[])
    g.impact_set = AsyncMock(return_value=ImpactSet(root_node_id="n1"))
    g.traceability_chain = AsyncMock(return_value=MagicMock(model_dump=MagicMock(return_value={})))
    g.traceability_gaps = AsyncMock(return_value=TraceabilityGaps())
    g.all_edges = AsyncMock(return_value=[])
    g.edges_from = AsyncMock(return_value=[])
    g.edges_to = AsyncMock(return_value=[])
    g.nodes_by_type = AsyncMock(return_value=[])
    g.all_nodes = MagicMock(return_value=[])
    g.find_node_by_slug = AsyncMock(return_value=None)
    g.reset = AsyncMock()
    g.reset_sequences = AsyncMock()
    g.allocate_node_id = AsyncMock(return_value="PROJECT-001")
    g.create_baseline = AsyncMock(
        return_value=GraphNode(node_id="bl-001", node_type="RECORD", title="Baseline")
    )
    g.context_bundle_sync = MagicMock(
        return_value={"node_id": "n1", "inner": [], "middle": [], "outer": []}
    )
    g.nodes_tracing_to = MagicMock(return_value=[])
    for k, v in overrides.items():
        setattr(g, k, v)
    return g


# ===================================================================
# PHASES ROUTER
# ===================================================================


class TestPhasesRouter:
    """Tests for /phases endpoints."""

    def test_get_phases(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/api/v1/phases")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_phases_503_no_store(self) -> None:
        app = _make_app(graph=_mock_graph(), phase_store=None)
        app.state.phase_store = None
        resp = _client(app).get("/api/v1/phases")
        assert resp.status_code == 503

    def test_start_build_503_no_infra(self) -> None:
        app = _make_app(graph=None, pool=None)
        app.state.agent_pool = None
        resp = _client(app).post("/api/v1/phases/start", json={})
        assert resp.status_code == 503

    @patch("backend.server.routers.phases._make_flow")
    def test_start_build_success(self, mock_make_flow: MagicMock) -> None:
        mock_flow = MagicMock()
        mock_flow.state = MagicMock()
        mock_flow.kickoff_async = AsyncMock()
        mock_make_flow.return_value = mock_flow

        app = _make_app(graph=_mock_graph())
        resp = _client(app).post(
            "/api/v1/phases/start",
            json={"start_phase": 2, "end_phase": 5, "single_step": True},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_stop_build_not_running(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/phases/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_running"

    def test_stop_build_cancels_task(self) -> None:
        app = _make_app(graph=_mock_graph())
        task = MagicMock()
        task.done.return_value = False
        app.state.flow_task = task
        resp = _client(app).post("/api/v1/phases/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"
        task.cancel.assert_called_once()

    def test_approve_phase_not_found(self) -> None:
        ps = MagicMock()
        ps.get.return_value = None
        app = _make_app(graph=_mock_graph(), phase_store=ps)
        resp = _client(app).post("/api/v1/phases/99/approve")
        assert resp.status_code == 404

    def test_approve_phase_success(self) -> None:
        ps = MagicMock()
        ps.get.return_value = {"phase_number": 3, "status": "awaiting_approval"}
        app = _make_app(graph=_mock_graph(), phase_store=ps)
        mock_flow = MagicMock()
        app.state.flow = mock_flow
        resp = _client(app).post("/api/v1/phases/3/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        mock_flow.approve_phase.assert_called_once_with(3)

    def test_approve_phase_503_no_store(self) -> None:
        app = _make_app(graph=_mock_graph(), phase_store=None)
        app.state.phase_store = None
        resp = _client(app).post("/api/v1/phases/3/approve")
        assert resp.status_code == 503

    def test_log_user_action(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post(
            "/api/v1/phases/log/user-action",
            json={"action": "test_action", "detail": "some detail"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "logged"

    @patch("backend.server.routers.phases._make_flow")
    def test_run_phase_success(self, mock_make_flow: MagicMock) -> None:
        mock_flow = MagicMock()
        mock_flow.state = MagicMock()
        mock_flow.run_phase = AsyncMock()
        mock_make_flow.return_value = mock_flow

        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/phases/3/run")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_run_phase_503_no_infra(self) -> None:
        app = _make_app(graph=None, pool=None)
        app.state.agent_pool = None
        resp = _client(app).post("/api/v1/phases/3/run")
        assert resp.status_code == 503

    def test_run_phase_400_out_of_range(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/phases/99/run")
        assert resp.status_code == 400

    def test_scan_phase_gaps(self) -> None:
        app = _make_app(graph=_mock_graph())
        with (
            patch("backend.analysis.gap_analyser.GapAnalyser") as mock_cls,
            patch("backend.crew.flow.GAP_TYPE_TO_PHASE", {}),
            patch("backend.crew.flow._QUALITY_GAP_TYPES", set()),
        ):
            mock_cls.return_value.analyse.return_value = []
            resp = _client(app).post("/api/v1/phases/3/scan")
        assert resp.status_code == 200
        assert resp.json()["gap_count"] == 0

    def test_scan_phase_gaps_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).post("/api/v1/phases/3/scan")
        assert resp.status_code == 503

    @patch("backend.server.routers.phases._make_flow")
    def test_scan_qual(self, mock_make_flow: MagicMock) -> None:
        mock_flow = MagicMock()
        mock_flow.scan_qual_detect = AsyncMock(return_value=[])
        mock_make_flow.return_value = mock_flow
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/phases/3/scan-qual")
        assert resp.status_code == 200
        assert resp.json()["qual_gap_count"] == 0

    def test_scan_qual_503(self) -> None:
        app = _make_app(graph=None, pool=None)
        app.state.agent_pool = None
        resp = _client(app).post("/api/v1/phases/3/scan-qual")
        assert resp.status_code == 503

    @patch("backend.server.routers.phases._make_flow")
    def test_qual_check_phase(self, mock_make_flow: MagicMock) -> None:
        mock_flow = MagicMock()
        mock_flow.run_qual_check = AsyncMock()
        mock_make_flow.return_value = mock_flow
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/phases/3/qual-check")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_qual_check_phase_503(self) -> None:
        app = _make_app(graph=None, pool=None)
        app.state.agent_pool = None
        resp = _client(app).post("/api/v1/phases/3/qual-check")
        assert resp.status_code == 503

    def test_qual_check_phase_400(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/phases/1/qual-check")
        assert resp.status_code == 400

    @patch("backend.server.routers.phases._make_flow")
    def test_semantic_check_phase(self, mock_make_flow: MagicMock) -> None:
        mock_flow = MagicMock()
        mock_flow.run_semantic_check = AsyncMock()
        mock_make_flow.return_value = mock_flow
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/phases/3/semantic-check")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_semantic_check_503(self) -> None:
        app = _make_app(graph=None, pool=None)
        app.state.agent_pool = None
        resp = _client(app).post("/api/v1/phases/3/semantic-check")
        assert resp.status_code == 503

    def test_semantic_check_400(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/phases/1/semantic-check")
        assert resp.status_code == 400

    def test_reset_build(self) -> None:
        app = _make_app(graph=_mock_graph())
        with patch(
            "backend.services.ingest._ensure_project_node",
            new_callable=AsyncMock,
            return_value="PROJECT-001",
        ):
            resp = _client(app).post("/api/v1/phases/reset")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reset"

    def test_reset_build_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).post("/api/v1/phases/reset")
        assert resp.status_code == 503

    def test_purge_derived(self) -> None:
        g = _mock_graph()
        g.nodes = AsyncMock(
            return_value=[
                GraphNode(node_id="P1", node_type="PROJECT", title="P"),
                GraphNode(node_id="D1", node_type="DOCUMENT", title="D"),
                GraphNode(node_id="H1", node_type="HLR", title="H"),
            ]
        )
        app = _make_app(graph=g)
        resp = _client(app).post("/api/v1/phases/purge-derived")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "purged"
        assert data["deleted_count"] == 1  # only HLR is deleted

    def test_purge_derived_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).post("/api/v1/phases/purge-derived")
        assert resp.status_code == 503

    def test_audit_lifecycle(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        with patch("backend.analysis.phase_auditor.PhaseAuditor") as mock_auditor:
            mock_auditor.return_value.audit_lifecycle.return_value = {}
            resp = _client(app).get("/api/v1/phases/audit")
        assert resp.status_code == 200

    def test_audit_lifecycle_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/phases/audit")
        assert resp.status_code == 503

    def test_audit_phase(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        with patch("backend.analysis.phase_auditor.PhaseAuditor") as mock_auditor:
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"phase": 3}
            mock_auditor.return_value.audit.return_value = mock_result
            resp = _client(app).get("/api/v1/phases/3/audit")
        assert resp.status_code == 200

    def test_audit_phase_404(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/api/v1/phases/99/audit")
        assert resp.status_code == 404

    def test_ingest_forgemd_success(self) -> None:
        with (
            patch(
                "backend.services.ingest.ingest_forgemd",
                new_callable=AsyncMock,
            ),
            patch(
                "backend.services.ingest.resolve_forgemd_path",
            ) as mock_resolve,
        ):
            tmp = tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w")
            tmp.write("# Forge\nContent")
            tmp.close()
            mock_resolve.return_value = Path(tmp.name)

            app = _make_app(graph=_mock_graph())
            resp = _client(app).post("/api/v1/phases/1/ingest")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ingested"
            os.unlink(tmp.name)

    def test_ingest_forgemd_not_found(self) -> None:
        with patch(
            "backend.services.ingest.resolve_forgemd_path",
        ) as mock_resolve:
            mock_resolve.return_value = Path("/nonexistent/forge.md")
            app = _make_app(graph=_mock_graph())
            resp = _client(app).post("/api/v1/phases/1/ingest")
            assert resp.status_code == 404

    def test_ingest_forgemd_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).post("/api/v1/phases/1/ingest")
        assert resp.status_code == 503

    def test_sync_traces(self) -> None:
        with patch(
            "backend.workspace.trace_manager.sync_traces",
            new_callable=AsyncMock,
            return_value={"synced": 5},
        ):
            app = _make_app(graph=_mock_graph())
            resp = _client(app).post("/api/v1/phases/12/sync-traces")
            assert resp.status_code == 200

    def test_sync_traces_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).post("/api/v1/phases/12/sync-traces")
        assert resp.status_code == 503

    def test_cancel_agent_work_done_tasks(self) -> None:
        """Reset succeeds when there are already-finished tasks in state."""
        g = _mock_graph()
        app = _make_app(graph=g)

        # Tasks that are already done — no cancel needed
        flow_task = MagicMock()
        flow_task.done.return_value = True
        console_task = MagicMock()
        console_task.done.return_value = True
        app.state.flow_task = flow_task
        app.state.console_task = console_task

        with patch(
            "backend.services.ingest._ensure_project_node",
            new_callable=AsyncMock,
            return_value="P1",
        ):
            resp = _client(app).post("/api/v1/phases/reset")
        assert resp.status_code == 200


# ===================================================================
# CONSOLE ROUTER
# ===================================================================


class TestConsoleRouter:
    """Tests for /console endpoints."""

    def test_run_console_empty_request(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/console/run", json={"request": "  "})
        assert resp.status_code == 422

    def test_run_console_503_no_pool(self) -> None:
        app = _make_app(graph=_mock_graph(), pool=None)
        app.state.agent_pool = None
        resp = _client(app).post("/api/v1/console/run", json={"request": "hello"})
        assert resp.status_code == 503

    def test_run_console_success(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/console/run", json={"request": "hello"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"
        assert "request_id" in resp.json()

    def test_clear_console(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/api/v1/console/clear")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"

    def test_get_history(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/api/v1/console/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "message_count" in data
        assert "context_window" in data


# ===================================================================
# WORKSPACE ROUTER
# ===================================================================


class TestWorkspaceRouter:
    """Tests for /workspace endpoints."""

    def test_get_file(self, tmp_path: Path) -> None:
        (tmp_path / "hello.txt").write_text("world")
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/file?path=hello.txt")
        assert resp.status_code == 200
        assert "world" in resp.text

    def test_get_file_not_found(self, tmp_path: Path) -> None:
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/file?path=missing.txt")
        assert resp.status_code == 404

    def test_get_file_no_path(self, tmp_path: Path) -> None:
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/file?path=")
        assert resp.status_code == 400

    def test_get_file_path_traversal(self, tmp_path: Path) -> None:
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/file?path=../../etc/passwd")
        assert resp.status_code == 400

    def test_get_file_binary_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "img.png").write_bytes(b"\x89PNG")
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/file?path=img.png")
        assert resp.status_code == 415

    def test_put_file(self, tmp_path: Path) -> None:
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).put(
            "/api/v1/workspace/file?path=new.txt",
            json={"content": "hello world"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"
        assert (tmp_path / "new.txt").read_text() == "hello world"

    def test_put_file_no_path(self, tmp_path: Path) -> None:
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).put(
            "/api/v1/workspace/file?path=",
            json={"content": "data"},
        )
        assert resp.status_code == 400

    def test_put_file_path_traversal(self, tmp_path: Path) -> None:
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).put(
            "/api/v1/workspace/file?path=../../etc/evil",
            json={"content": "bad"},
        )
        assert resp.status_code == 400

    def test_put_file_parent_missing(self, tmp_path: Path) -> None:
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).put(
            "/api/v1/workspace/file?path=deep/nested/file.txt",
            json={"content": "x"},
        )
        assert resp.status_code == 400

    def test_get_forgemd_empty(self) -> None:
        g = _mock_graph()
        g.find_node_by_slug = AsyncMock(return_value=None)
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/workspace/forgemd")
        assert resp.status_code == 200
        assert resp.text == ""

    def test_get_forgemd_with_content(self) -> None:
        g = _mock_graph()
        doc = MagicMock()
        doc.content = "# Forge\nMy spec"
        g.find_node_by_slug = AsyncMock(return_value=doc)
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/workspace/forgemd")
        assert resp.status_code == 200
        assert "My spec" in resp.text

    def test_get_forgemd_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/workspace/forgemd")
        assert resp.status_code == 503

    @patch("backend.server.routers.workspace._parse_forgemd", new_callable=AsyncMock)
    def test_put_forgemd(self, mock_parse: MagicMock) -> None:
        mock_parse.return_value = {"parsed": True, "created": 0, "updated": 0}
        g = _mock_graph()
        app = _make_app(graph=g)
        resp = _client(app).put(
            "/api/v1/workspace/forgemd",
            json={"content": "# New content"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

    def test_put_forgemd_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).put(
            "/api/v1/workspace/forgemd",
            json={"content": "x"},
        )
        assert resp.status_code == 503

    def test_upload_forgemd_empty(self, tmp_path: Path) -> None:
        from io import BytesIO

        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).post(
            "/api/v1/workspace/forgemd",
            files={"file": ("forge.md", BytesIO(b""), "text/markdown")},
        )
        assert resp.status_code == 400

    def test_upload_forgemd_success(self, tmp_path: Path) -> None:
        from io import BytesIO

        content = b"# Forge\nContent here"
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).post(
            "/api/v1/workspace/forgemd",
            files={"file": ("forge.md", BytesIO(content), "text/markdown")},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_tests_summary_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/workspace/tests/summary")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_tests_summary_with_results(self) -> None:
        from datetime import UTC, datetime

        g = _mock_graph()
        result_node = MagicMock()
        result_node.node_type = NodeType.RESULT.value
        result_node.content = "passed"
        result_node.updated_at = datetime.now(UTC)

        case_node = MagicMock()
        case_node.node_type = NodeType.CASE_HLR.value

        g.all_nodes = MagicMock(return_value=[case_node, result_node])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/workspace/tests/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["passed"] == 1

    def test_deliverables_manifest_empty(self, tmp_path: Path) -> None:
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/deliverables/manifest")
        assert resp.status_code == 200
        assert resp.json()["exists"] is False

    def test_deliverables_manifest_with_files(self, tmp_path: Path) -> None:
        deliv = tmp_path / "deliverables"
        deliv.mkdir()
        (deliv / "report.pdf").write_bytes(b"PDF")
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/deliverables/manifest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is True
        assert len(data["files"]) == 1

    def test_deliverables_download_missing(self, tmp_path: Path) -> None:
        app = _make_app(graph=_mock_graph(), workspace_root=str(tmp_path))
        resp = _client(app).get("/api/v1/workspace/deliverables/download")
        assert resp.status_code == 404


# ===================================================================
# AGENTS ROUTER
# ===================================================================


class TestAgentsRouter:
    """Tests for /agents endpoints."""

    def test_list_agents(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/api/v1/agents")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_agents_no_pool(self) -> None:
        app = _make_app(graph=_mock_graph(), pool=None)
        app.state.agent_pool = None
        resp = _client(app).get("/api/v1/agents")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_agent(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/api/v1/agents/doc")
        assert resp.status_code == 200

    def test_get_agent_not_found(self) -> None:
        pool = MagicMock()
        pool.all_ids.return_value = []
        app = _make_app(graph=_mock_graph(), pool=pool)
        resp = _client(app).get("/api/v1/agents/ghost")
        assert resp.status_code == 404

    def test_get_agent_503_no_pool(self) -> None:
        app = _make_app(graph=_mock_graph(), pool=None)
        app.state.agent_pool = None
        resp = _client(app).get("/api/v1/agents/doc")
        assert resp.status_code == 503

    def test_send_directive(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post(
            "/api/v1/agents/doc/directive",
            json={"content": "Focus on safety", "priority": "high"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"

    def test_send_directive_503_no_bus(self) -> None:
        app = _make_app(graph=_mock_graph(), bus=None)
        app.state.bus = None
        resp = _client(app).post(
            "/api/v1/agents/doc/directive",
            json={"content": "test"},
        )
        assert resp.status_code == 503

    def test_get_agent_messages_empty(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/api/v1/agents/doc/messages")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_agent_messages_no_bus(self) -> None:
        app = _make_app(graph=_mock_graph(), bus=None)
        app.state.bus = None
        resp = _client(app).get("/api/v1/agents/doc/messages")
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("backend.server.routers.agents.get_prompt")
    @patch("backend.server.routers.agents.is_default_prompt")
    def test_get_agent_prompt(self, mock_default: MagicMock, mock_prompt: MagicMock) -> None:
        mock_prompt.return_value = "You are a doc specialist."
        mock_default.return_value = True
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/api/v1/agents/doc/prompt")
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "doc"
        assert data["is_default"] is True

    @patch("backend.server.routers.agents.set_prompt")
    def test_set_agent_prompt(self, mock_set: MagicMock) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).put(
            "/api/v1/agents/doc/prompt",
            json={"prompt": "Custom prompt here"},
        )
        assert resp.status_code == 200
        assert resp.json()["is_default"] is False

    def test_set_agent_prompt_empty_rejected(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).put(
            "/api/v1/agents/doc/prompt",
            json={"prompt": "  "},
        )
        assert resp.status_code == 422

    @patch("backend.server.routers.agents.reset_prompt")
    @patch("backend.server.routers.agents.get_prompt")
    def test_delete_agent_prompt(self, mock_get: MagicMock, mock_reset: MagicMock) -> None:
        mock_get.return_value = "Default prompt"
        app = _make_app(graph=_mock_graph())
        resp = _client(app).delete("/api/v1/agents/doc/prompt")
        assert resp.status_code == 200
        assert resp.json()["is_default"] is True

    def test_list_definitions(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/api/v1/agents/definitions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @patch("backend.server.routers.agents.get_gap_prompt")
    @patch("backend.server.routers.agents.is_default_gap_prompt")
    @patch("backend.server.routers.agents.gap_inherits_from_role")
    def test_get_gap_prompt(self, mock_inherits: MagicMock, mock_default: MagicMock, mock_get: MagicMock) -> None:
        mock_get.return_value = "gap prompt"
        mock_default.return_value = True
        mock_inherits.return_value = True

        # Use a real gap type value from the enum
        from backend.analysis.gaps import GapType

        gap_type = list(GapType)[0].value
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get(f"/api/v1/agents/gaps/{gap_type}/prompt")
        assert resp.status_code == 200
        assert resp.json()["prompt"] == "gap prompt"

    @patch("backend.server.routers.agents.set_gap_prompt")
    def test_set_gap_prompt(self, mock_set: MagicMock) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).put(
            "/api/v1/agents/gaps/MISSING_HLR/prompt",
            json={"prompt": "Custom gap prompt"},
        )
        assert resp.status_code == 200
        assert resp.json()["is_default"] is False

    def test_set_gap_prompt_empty_rejected(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).put(
            "/api/v1/agents/gaps/MISSING_HLR/prompt",
            json={"prompt": "   "},
        )
        assert resp.status_code == 422

    @patch("backend.server.routers.agents.reset_gap_prompt")
    @patch("backend.server.routers.agents.get_gap_prompt")
    @patch("backend.server.routers.agents.gap_inherits_from_role")
    def test_delete_gap_prompt(self, mock_inherits: MagicMock, mock_get: MagicMock, mock_reset: MagicMock) -> None:
        mock_get.return_value = "default gap prompt"
        mock_inherits.return_value = False
        from backend.analysis.gaps import GapType

        gap_type = list(GapType)[0].value
        app = _make_app(graph=_mock_graph())
        resp = _client(app).delete(f"/api/v1/agents/gaps/{gap_type}/prompt")
        assert resp.status_code == 200


# ===================================================================
# GRAPH ROUTER
# ===================================================================


class TestGraphRouter:
    """Tests for /graph endpoints."""

    def test_list_nodes_empty(self) -> None:
        g = _mock_graph()
        g.nodes = AsyncMock(return_value=[])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_nodes_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/graph/nodes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_nodes_with_filter(self) -> None:
        node = GraphNode(
            node_id="hlr.1",
            node_type="HLR",
            title="Req 1",
            content="requirement",
            lifecycle=LifecycleState.ACTIVE,
        )
        g = _mock_graph()
        g.nodes = AsyncMock(return_value=[node])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes?type_prefix=HLR")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["node_id"] == "hlr.1"

    def test_get_node(self) -> None:
        node = GraphNode(
            node_id="hlr.1",
            node_type="HLR",
            title="Req 1",
            content="c",
        )
        g = _mock_graph()
        g.node = AsyncMock(return_value=node)
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes/hlr.1")
        assert resp.status_code == 200
        assert resp.json()["node_id"] == "hlr.1"

    def test_get_node_404(self) -> None:
        g = _mock_graph()
        g.node = AsyncMock(return_value=None)
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes/missing")
        assert resp.status_code == 404

    def test_get_node_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/graph/nodes/x")
        assert resp.status_code == 503

    def test_patch_node(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        resp = _client(app).patch(
            "/api/v1/graph/nodes/n1",
            json={"content": "updated", "title": "New Title", "change_reason": "test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "node" in data
        assert "stale_count" in data

    def test_patch_node_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).patch("/api/v1/graph/nodes/n1", json={"content": "x"})
        assert resp.status_code == 503

    def test_get_ancestors(self) -> None:
        node = GraphNode(node_id="anc1", node_type="HLR", title="Anc")
        g = _mock_graph()
        g.ancestors = AsyncMock(return_value=[node])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes/n1/ancestors")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_descendants(self) -> None:
        node = GraphNode(node_id="desc1", node_type="LLR", title="Desc")
        g = _mock_graph()
        g.descendants = AsyncMock(return_value=[node])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes/n1/descendants")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_impact(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes/n1/impact")
        assert resp.status_code == 200

    def test_get_impact_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/graph/nodes/n1/impact")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_get_traceability(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes/n1/traceability")
        assert resp.status_code == 200

    def test_get_node_context(self) -> None:
        g = _mock_graph()
        g.node_sync = MagicMock(return_value=GraphNode(node_id="n1", node_type="HLR", title="R"))
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes/n1/context")
        assert resp.status_code == 200
        data = resp.json()
        assert "inner" in data

    def test_get_node_context_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/graph/nodes/n1/context")
        assert resp.status_code == 200
        data = resp.json()
        assert data["inner"] == []

    def test_get_node_context_404(self) -> None:
        g = _mock_graph()
        g.node_sync = MagicMock(return_value=None)
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes/n1/context")
        assert resp.status_code == 404

    def test_get_siblings(self) -> None:
        g = _mock_graph()
        sib = GraphNode(node_id="sib1", node_type="MODULE", title="S")
        g.siblings_sync = MagicMock(return_value=[sib])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/nodes/n1/siblings")
        assert resp.status_code == 200
        assert len(resp.json()["nodes"]) == 1

    def test_put_trace(self) -> None:
        existing = GraphNode(
            node_id="mod1",
            node_type="MODULE",
            title="M",
            trace_to=["hlr1"],
        )
        g = _mock_graph()
        g.node = AsyncMock(return_value=existing)
        app = _make_app(graph=g)
        resp = _client(app).put(
            "/api/v1/graph/nodes/mod1/trace",
            json={"trace_to": ["hlr1", "hlr2"]},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_put_trace_404(self) -> None:
        g = _mock_graph()
        g.node = AsyncMock(return_value=None)
        app = _make_app(graph=g)
        resp = _client(app).put(
            "/api/v1/graph/nodes/missing/trace",
            json={"trace_to": []},
        )
        assert resp.status_code == 404

    def test_put_trace_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).put(
            "/api/v1/graph/nodes/n1/trace",
            json={"trace_to": []},
        )
        assert resp.status_code == 503

    def test_delete_trace(self) -> None:
        existing = GraphNode(
            node_id="mod1",
            node_type="MODULE",
            title="M",
            trace_to=["hlr1", "hlr2"],
        )
        g = _mock_graph()
        g.node = AsyncMock(return_value=existing)
        app = _make_app(graph=g)
        resp = _client(app).request(
            "DELETE",
            "/api/v1/graph/nodes/mod1/trace",
            json={"trace_refs": ["hlr1"]},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert "hlr1" in resp.json()["removed"]

    def test_delete_trace_404(self) -> None:
        g = _mock_graph()
        g.node = AsyncMock(return_value=None)
        app = _make_app(graph=g)
        resp = _client(app).request(
            "DELETE",
            "/api/v1/graph/nodes/missing/trace",
            json={"trace_refs": ["x"]},
        )
        assert resp.status_code == 404

    def test_delete_trace_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).request(
            "DELETE",
            "/api/v1/graph/nodes/n1/trace",
            json={"trace_refs": ["x"]},
        )
        assert resp.status_code == 503

    def test_delete_trace_no_match(self) -> None:
        existing = GraphNode(
            node_id="mod1",
            node_type="MODULE",
            title="M",
            trace_to=["hlr2"],
        )
        g = _mock_graph()
        g.node = AsyncMock(return_value=existing)
        app = _make_app(graph=g)
        resp = _client(app).request(
            "DELETE",
            "/api/v1/graph/nodes/mod1/trace",
            json={"trace_refs": ["hlr99"]},
        )
        assert resp.status_code == 200
        assert resp.json()["removed"] == []

    def test_list_edges(self) -> None:
        from datetime import UTC, datetime

        edge = GraphEdge(
            edge_type="DERIVES_FROM",
            source_id="llr1",
            target_id="hlr1",
            created_at=datetime.now(UTC),
        )
        g = _mock_graph()
        g.all_edges = AsyncMock(return_value=[edge])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/edges")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_edges_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/graph/edges")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_traceability_gaps(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/traceability/gaps")
        assert resp.status_code == 200

    def test_traceability_gaps_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/graph/traceability/gaps")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_compliance(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/compliance")
        assert resp.status_code == 200

    def test_compliance_disabled(self) -> None:
        g = _mock_graph()
        cfg = MagicMock()
        cfg.compliance.enabled = False
        app = _make_app(graph=g, config=cfg)
        resp = _client(app).get("/api/v1/graph/compliance")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_compliance_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/graph/compliance")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_list_baselines(self) -> None:
        g = _mock_graph()
        g.nodes_by_type = AsyncMock(return_value=[])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/graph/baselines")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_baselines_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/graph/baselines")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_baseline(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        resp = _client(app).post(
            "/api/v1/graph/baselines",
            json={"baseline_id": "bl-001", "baseline_type": "phase", "description": "Test"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

    def test_create_baseline_503(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).post(
            "/api/v1/graph/baselines",
            json={"baseline_id": "bl-001"},
        )
        assert resp.status_code == 503


# ===================================================================
# COMPLIANCE ROUTER
# ===================================================================


class TestComplianceRouter:
    """Tests for /compliance endpoints."""

    def test_report_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/compliance/report")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_started"

    def test_report_with_graph(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/compliance/report")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_requirements" in data

    def test_report_with_dal(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/compliance/report?dal=A")
        assert resp.status_code == 200
        assert resp.json()["dal"] == "A"

    def test_report_with_nodes(self) -> None:
        from datetime import UTC, datetime

        req = GraphNode(node_id="hlr.1", node_type="HLR", title="R", content="x")
        edge = GraphEdge(
            edge_type="IMPLEMENTS",
            source_id="code.1",
            target_id="hlr.1",
            created_at=datetime.now(UTC),
        )
        g = _mock_graph()

        async def _nodes_by_type(t: str) -> list[GraphNode]:
            if t == NodeType.HLR.value:
                return [req]
            return []

        g.nodes_by_type = AsyncMock(side_effect=_nodes_by_type)
        g.all_edges = AsyncMock(return_value=[edge])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/compliance/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requirements"] == 1
        assert data["compliance_percent"] == 100.0

    def test_objectives_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/compliance/objectives")
        assert resp.status_code == 200
        data = resp.json()
        assert all(not obj["satisfied"] for obj in data)

    def test_objectives_with_graph(self) -> None:
        hlr = GraphNode(node_id="hlr.1", node_type="HLR", title="R")
        g = _mock_graph()

        async def _nodes_by_type(t: str) -> list[GraphNode]:
            if t == NodeType.HLR.value:
                return [hlr]
            return []

        g.nodes_by_type = AsyncMock(side_effect=_nodes_by_type)
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/compliance/objectives?dal=A")
        assert resp.status_code == 200
        data = resp.json()
        assert any(obj["satisfied"] for obj in data)

    def test_objectives_dal_d(self) -> None:
        g = _mock_graph()
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/compliance/objectives?dal=D")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1  # DAL D only has SW-001


# ===================================================================
# SETTINGS ROUTER
# ===================================================================


class TestSettingsRouter:
    """Tests for /settings endpoints."""

    def test_get_settings(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/api/v1/settings")
        assert resp.status_code == 200

    def test_get_settings_no_config(self) -> None:
        app = _make_app(graph=_mock_graph(), config=None)
        app.state.config = None
        resp = _client(app).get("/api/v1/settings")
        assert resp.status_code == 200

    def test_patch_settings(self) -> None:
        from backend.config.models import ForgeConfig

        real_config = ForgeConfig()
        app = _make_app(graph=_mock_graph(), config=real_config)
        with patch("backend.server.routers.settings.save_config"):
            resp = _client(app).patch(
                "/api/v1/settings",
                json={"project": {"name": "Updated"}},
            )
        assert resp.status_code == 200
        assert resp.json()["project"]["name"] == "Updated"

    def test_patch_settings_invalid(self) -> None:
        from backend.config.models import ForgeConfig

        real_config = ForgeConfig()
        app = _make_app(graph=_mock_graph(), config=real_config)
        resp = _client(app).patch(
            "/api/v1/settings",
            json={"server": {"port": "not_a_number"}},
        )
        assert resp.status_code == 422

    def test_patch_settings_with_llm(self) -> None:
        from backend.config.models import ForgeConfig

        real_config = ForgeConfig()
        pool = MagicMock()
        pool.all_ids.return_value = []
        pool.rebuild = MagicMock()

        app = _make_app(graph=_mock_graph(), config=real_config, pool=pool)
        app.state.tool_registry = MagicMock()

        with (
            patch("backend.server.routers.settings.save_config"),
            patch("backend.agents.throttle.llm_throttle"),
        ):
            resp = _client(app).patch(
                "/api/v1/settings",
                json={"llm": {"call_delay_ms": 500}},
            )
        assert resp.status_code == 200
        pool.rebuild.assert_called_once()

    def test_deep_merge(self) -> None:
        from backend.server.routers.settings import _deep_merge

        base = {"a": {"b": 1, "c": 2}, "d": 3}
        patch_data = {"a": {"b": 10}, "e": 5}
        result = _deep_merge(base, patch_data)
        assert result["a"]["b"] == 10
        assert result["a"]["c"] == 2
        assert result["d"] == 3
        assert result["e"] == 5


# ===================================================================
# SECRETS ROUTER
# ===================================================================


class TestSecretsRouter:
    """Tests for /secrets endpoints."""

    def test_list_secrets(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/api/v1/secrets")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        names = [s["name"] for s in data]
        assert "POE_API_KEY" in names

    def test_set_secret(self, tmp_path: Path) -> None:
        import sqlite3

        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

        app = _make_app(graph=_mock_graph(), db_path=db)
        resp = _client(app).post(
            "/api/v1/secrets",
            json={"name": "TEST_KEY", "value": "secret_value"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # Verify it was set in env
        assert os.environ.get("TEST_KEY") == "secret_value"
        # Clean up
        os.environ.pop("TEST_KEY", None)

    def test_delete_secret(self, tmp_path: Path) -> None:
        import sqlite3

        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

        os.environ["DEL_KEY"] = "to_delete"
        app = _make_app(graph=_mock_graph(), db_path=db)
        resp = _client(app).delete("/api/v1/secrets/DEL_KEY")
        assert resp.status_code == 200
        assert "DEL_KEY" not in os.environ

    def test_set_and_clear_secret(self, tmp_path: Path) -> None:
        import sqlite3

        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

        app = _make_app(graph=_mock_graph(), db_path=db)
        # Set it
        _client(app).post(
            "/api/v1/secrets",
            json={"name": "CLEAR_KEY", "value": "val"},
        )
        assert os.environ.get("CLEAR_KEY") == "val"
        # Clear it by setting empty
        _client(app).post(
            "/api/v1/secrets",
            json={"name": "CLEAR_KEY", "value": ""},
        )
        assert "CLEAR_KEY" not in os.environ


# ===================================================================
# PATTERNS ROUTER
# ===================================================================


class TestPatternsRouter:
    """Tests for /patterns endpoints."""

    def test_health_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/patterns/health")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_health_with_modules(self) -> None:
        mod = GraphNode(node_id="mod.1", node_type="MODULE", title="Auth Module")
        code = GraphNode(node_id="code.1", node_type="CODE", title="auth.py", parent_id="mod.1")
        g = _mock_graph()
        g.nodes_by_type = AsyncMock(return_value=[mod])
        g.children = AsyncMock(return_value=[code])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/patterns/health")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["has_implementation"] is True
        assert data[0]["code_nodes"] == 1

    def test_interactions_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/patterns/interactions/mod.1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edges"] == []

    def test_interactions_with_edges(self) -> None:

        out_edge = MagicMock()
        out_edge.edge_type = "IMPLEMENTS"
        out_edge.target_id = "hlr.1"
        in_edge = MagicMock()
        in_edge.edge_type = "CONFORMS_TO"
        in_edge.source_id = "code.1"

        g = _mock_graph()
        g.edges_from = AsyncMock(return_value=[out_edge])
        g.edges_to = AsyncMock(return_value=[in_edge])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/patterns/interactions/mod.1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["edges"]) == 2
        assert data["diagram"]  # not empty


# ===================================================================
# AUTH ROUTER
# ===================================================================


class TestAuthRouter:
    """Tests for /auth endpoints."""

    def test_login_no_auth_configured(self) -> None:
        os.environ.pop("FORGE_AUTH_USER", None)
        os.environ.pop("FORGE_AUTH_PASS", None)
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post(
            "/auth/login",
            json={"username": "admin", "password": "pass"},
        )
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Auth not configured"

    def test_login_success(self) -> None:
        # Create app first (without auth middleware), then set env vars
        # so the login handler sees them at request time
        app = _make_app(graph=_mock_graph())
        os.environ["FORGE_AUTH_USER"] = "admin"
        os.environ["FORGE_AUTH_PASS"] = "secret"
        try:
            resp = _client(app).post(
                "/auth/login",
                json={"username": "admin", "password": "secret"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
        finally:
            os.environ.pop("FORGE_AUTH_USER", None)
            os.environ.pop("FORGE_AUTH_PASS", None)

    def test_login_invalid_credentials(self) -> None:
        app = _make_app(graph=_mock_graph())
        os.environ["FORGE_AUTH_USER"] = "admin"
        os.environ["FORGE_AUTH_PASS"] = "secret"
        try:
            resp = _client(app).post(
                "/auth/login",
                json={"username": "admin", "password": "wrong"},
            )
            # The handler sets status_code 401 on the response object
            assert resp.json()["status"] == "error"
        finally:
            os.environ.pop("FORGE_AUTH_USER", None)
            os.environ.pop("FORGE_AUTH_PASS", None)

    def test_check_auth_not_required(self) -> None:
        os.environ.pop("FORGE_AUTH_USER", None)
        os.environ.pop("FORGE_AUTH_PASS", None)
        app = _make_app(graph=_mock_graph())
        resp = _client(app).get("/auth/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["auth_required"] is False

    def test_check_auth_required_no_cookie(self) -> None:
        app = _make_app(graph=_mock_graph())
        os.environ["FORGE_AUTH_USER"] = "admin"
        os.environ["FORGE_AUTH_PASS"] = "secret"
        try:
            resp = _client(app).get("/auth/check")
            assert resp.status_code == 200
            data = resp.json()
            assert data["auth_required"] is True
            assert data["authenticated"] is False
        finally:
            os.environ.pop("FORGE_AUTH_USER", None)
            os.environ.pop("FORGE_AUTH_PASS", None)

    def test_check_auth_with_valid_cookie(self) -> None:
        from backend.server.middleware.auth import _make_secret, sign_token

        app = _make_app(graph=_mock_graph())
        os.environ["FORGE_AUTH_USER"] = "admin"
        os.environ["FORGE_AUTH_PASS"] = "secret"
        try:
            secret = _make_secret("secret")
            token = sign_token("admin", secret)
            resp = _client(app).get(
                "/auth/check",
                cookies={"forge_session": token},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["authenticated"] is True
        finally:
            os.environ.pop("FORGE_AUTH_USER", None)
            os.environ.pop("FORGE_AUTH_PASS", None)

    def test_logout(self) -> None:
        app = _make_app(graph=_mock_graph())
        resp = _client(app).post("/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ===================================================================
# REQUIREMENTS ROUTER
# ===================================================================


class TestRequirementsRouter:
    """Tests for /requirements endpoints."""

    def test_get_requirements_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/requirements")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_requirements_all(self) -> None:
        hlr = GraphNode(
            node_id="hlr.1",
            node_type="HLR",
            title="Req 1",
            content="c",
            lifecycle=LifecycleState.ACTIVE,
        )
        llr = GraphNode(
            node_id="llr.1",
            node_type="LLR",
            title="Req 2",
            content="c",
            lifecycle=LifecycleState.ACTIVE,
        )
        g = _mock_graph()

        async def _nodes_by_type(t: str) -> list[GraphNode]:
            if t == NodeType.HLR.value:
                return [hlr]
            if t == NodeType.LLR.value:
                return [llr]
            return []

        g.nodes_by_type = AsyncMock(side_effect=_nodes_by_type)
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/requirements")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_requirements_hlr_only(self) -> None:
        hlr = GraphNode(
            node_id="hlr.1",
            node_type="HLR",
            title="R",
            content="c",
            lifecycle=LifecycleState.ACTIVE,
        )
        g = _mock_graph()
        g.nodes_by_type = AsyncMock(return_value=[hlr])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/requirements?level=hlr")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_requirements_llr_only(self) -> None:
        llr = GraphNode(
            node_id="llr.1",
            node_type="LLR",
            title="R",
            content="c",
            lifecycle=LifecycleState.ACTIVE,
        )
        g = _mock_graph()
        g.nodes_by_type = AsyncMock(return_value=[llr])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/requirements?level=llr")
        assert resp.status_code == 200

    def test_get_requirements_lifecycle_filter(self) -> None:
        hlr_active = GraphNode(
            node_id="hlr.1",
            node_type="HLR",
            title="R",
            content="c",
            lifecycle=LifecycleState.ACTIVE,
        )
        hlr_draft = GraphNode(
            node_id="hlr.2",
            node_type="HLR",
            title="R2",
            content="c2",
            lifecycle=LifecycleState.DRAFT,
        )
        g = _mock_graph()

        async def _nodes_by_type(t: str) -> list[GraphNode]:
            if t == NodeType.HLR.value:
                return [hlr_active, hlr_draft]
            return []

        g.nodes_by_type = AsyncMock(side_effect=_nodes_by_type)
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/requirements?lifecycle=active")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_gaps_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/requirements/gaps")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unimplemented"] == []

    def test_get_gaps(self) -> None:
        g = _mock_graph()
        g.traceability_gaps = AsyncMock(
            return_value=TraceabilityGaps(
                unimplemented_requirements=["hlr.1"],
                uncovered_requirements=["hlr.2"],
                untested_code=["code.1"],
            )
        )
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/requirements/gaps")
        assert resp.status_code == 200
        data = resp.json()
        assert "hlr.1" in data["unimplemented"]


# ===================================================================
# TESTS ROUTER
# ===================================================================


class TestTestsRouter:
    """Tests for /tests endpoints."""

    def test_summary_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/tests/summary")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_summary_with_data(self) -> None:
        from datetime import UTC, datetime

        case = GraphNode(
            node_id="case.1",
            node_type="CASE_HLR",
            title="TC1",
        )
        result = GraphNode(
            node_id="res.1",
            node_type="RESULT",
            title="R1",
            properties={"status": "passed", "coverage_percent": 85.0},
            updated_at=datetime.now(UTC),
        )
        g = _mock_graph()

        async def _nodes_by_type(t: str) -> list[GraphNode]:
            if t == NodeType.SUITE.value:
                return []
            if t in (NodeType.CASE_HLR.value, NodeType.CASE_LLR.value):
                return [case] if t == NodeType.CASE_HLR.value else []
            if t == NodeType.RESULT.value:
                return [result]
            return []

        g.nodes_by_type = AsyncMock(side_effect=_nodes_by_type)
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/tests/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["passed"] == 1
        assert data["coverage_percent"] == 85.0

    def test_results_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/tests/results")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_results_with_data(self) -> None:
        from datetime import UTC, datetime

        result = GraphNode(
            node_id="res.1",
            node_type="RESULT",
            title="R1",
            content="Pass",
            lifecycle=LifecycleState.ACTIVE,
            updated_at=datetime.now(UTC),
        )
        g = _mock_graph()
        g.nodes_by_type = AsyncMock(return_value=[result])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/tests/results")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_suites_no_graph(self) -> None:
        app = _make_app(graph=None)
        resp = _client(app).get("/api/v1/tests/suites")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_suites_with_data(self) -> None:
        suite = GraphNode(
            node_id="suite.1",
            node_type="SUITE",
            title="Suite 1",
            lifecycle=LifecycleState.ACTIVE,
        )
        case = GraphNode(
            node_id="case.1",
            node_type="CASE_HLR",
            title="TC1",
        )
        g = _mock_graph()
        g.nodes_by_type = AsyncMock(return_value=[suite])
        g.children = AsyncMock(return_value=[case])
        app = _make_app(graph=g)
        resp = _client(app).get("/api/v1/tests/suites")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["case_count"] == 1
