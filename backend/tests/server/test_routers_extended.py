"""Extended router tests — graph, workspace, control, agents, tests endpoints."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import State

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, NodeType
from backend.server.app import create_app

# ── Test client factory ───────────────────────────────────────────────────────


def _app_state(client: TestClient) -> State:
    """Return the FastAPI app state behind a TestClient.

    ``TestClient.app`` is declared as the bare ASGI callable, but every client
    here is built from :func:`create_app`, so the concrete type is ``FastAPI``.
    """
    return cast(FastAPI, client.app).state


def _make_client(
    graph_inst: ProjectGraph | None = None, workspace_root: str | None = None
) -> TestClient:
    import os

    os.environ.pop("FORGE_AUTH_USER", None)
    os.environ.pop("FORGE_AUTH_PASS", None)
    app = create_app()

    from backend.core.phase_store import PhaseStore
    from backend.server.websocket.broadcaster import EventBroadcaster
    from backend.server.websocket.manager import WebSocketManager

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = f.name

    phase_store = PhaseStore(db)
    broadcaster = EventBroadcaster(WebSocketManager())

    mock_pool = MagicMock()
    mock_pool.all_ids.return_value = ["doc"]

    mock_bus = MagicMock()
    mock_bus.emit = AsyncMock()
    mock_bus.recent_events.return_value = []


    ws_root = workspace_root or tempfile.mkdtemp()

    mock_config = MagicMock()
    mock_config.compliance.enabled = True
    mock_config.compliance.standard = "DO-178C"
    mock_config.compliance.dal = "B"
    mock_config.project.name = "Test Project"
    mock_config.project.workspace_dir = ws_root
    mock_config.project.forgemd = "forge.md"

    mock_session = MagicMock()
    mock_session.session_id = "sess-test"
    mock_session.workspace_root = ws_root
    mock_session.model_dump.return_value = {"session_id": "sess-test"}

    mock_contracts = MagicMock()

    app.state.graph = graph_inst
    app.state.phase_store = phase_store
    app.state.agent_pool = mock_pool
    app.state.bus = mock_bus
    app.state.config = mock_config
    app.state.session = mock_session
    app.state.broadcaster = broadcaster
    app.state.contracts = mock_contracts

    return TestClient(app, raise_server_exceptions=True)


async def _make_graph(db_path: str) -> ProjectGraph:
    g = ProjectGraph(db_path)
    await g.initialise()
    return g


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


@pytest.fixture
def client(db_path: str) -> TestClient:
    g = asyncio.run(_make_graph(db_path))
    return _make_client(g)


@pytest.fixture
def client_no_graph() -> TestClient:
    return _make_client(graph_inst=None)


# ── Graph router — nodes CRUD ─────────────────────────────────────────────────


def test_get_node_returns_node(db_path: str) -> None:
    g = asyncio.run(_make_graph(db_path))
    asyncio.run(
        g.add_node(
            GraphNode(
                node_id="doc.spec",
                node_type=NodeType.DOCUMENT.value,
                title="Spec",
                content="hello",
            )
        )
    )
    c = _make_client(g)
    resp = c.get("/api/v1/graph/nodes/doc.spec")
    assert resp.status_code == 200
    assert resp.json()["node_id"] == "doc.spec"


def test_get_node_503_when_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.get("/api/v1/graph/nodes/doc.x")
    assert resp.status_code == 503


def test_get_node_404_missing(client: TestClient) -> None:
    resp = client.get("/api/v1/graph/nodes/nonexistent.node")
    assert resp.status_code == 404


def test_list_nodes_with_type_filter(db_path: str) -> None:
    g = asyncio.run(_make_graph(db_path))
    asyncio.run(
        g.add_node(
            GraphNode(
                node_id="doc.spec",
                node_type=NodeType.DOCUMENT.value,
                title="Spec",
                content="",
            )
        )
    )
    c = _make_client(g)
    resp = c.get("/api/v1/graph/nodes?type_prefix=doc")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_nodes_lifecycle_filter(db_path: str) -> None:
    g = asyncio.run(_make_graph(db_path))
    asyncio.run(
        g.add_node(
            GraphNode(
                node_id="doc.spec",
                node_type=NodeType.DOCUMENT.value,
                title="Spec",
                content="",
            )
        )
    )
    c = _make_client(g)
    resp = c.get("/api/v1/graph/nodes?lifecycle=draft")
    assert resp.status_code == 200


def test_patch_node_updates_content(db_path: str) -> None:
    g = asyncio.run(_make_graph(db_path))
    asyncio.run(
        g.add_node(
            GraphNode(
                node_id="doc.spec",
                node_type=NodeType.DOCUMENT.value,
                title="Spec",
                content="original",
            )
        )
    )
    c = _make_client(g)
    resp = c.patch(
        "/api/v1/graph/nodes/doc.spec",
        json={"content": "updated", "change_reason": "test"},
    )
    assert resp.status_code == 200
    assert resp.json()["node"]["content"] == "updated"


def test_patch_node_503_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.patch(
        "/api/v1/graph/nodes/doc.x",
        json={"content": "x"},
    )
    assert resp.status_code == 503


def test_graph_nodes_no_graph_returns_empty(client_no_graph: TestClient) -> None:
    resp = client_no_graph.get("/api/v1/graph/nodes")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_edges_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/graph/edges")
    assert resp.status_code == 200
    assert resp.json() == []


def test_traceability_gaps(client: TestClient) -> None:
    resp = client.get("/api/v1/graph/traceability/gaps")
    assert resp.status_code == 200


def test_create_baseline(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/graph/baselines",
        json={"baseline_id": "bl-001", "baseline_type": "phase", "description": "Test"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "created"


def test_create_baseline_503_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.post(
        "/api/v1/graph/baselines",
        json={"baseline_id": "bl-001"},
    )
    assert resp.status_code == 503


# ── Workspace router ──────────────────────────────────────────────────────────


def test_workspace_tree(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hello')")
    c = _make_client(workspace_root=str(tmp_path))
    resp = c.get("/api/v1/workspace/tree")
    assert resp.status_code == 200
    data = resp.json()
    assert "root" in data
    assert "tree" in data


def test_workspace_file_read(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("Hello workspace")
    c = _make_client(workspace_root=str(tmp_path))
    resp = c.get("/api/v1/workspace/file?path=readme.txt")
    assert resp.status_code == 200
    assert "Hello workspace" in resp.text


def test_workspace_file_not_found(tmp_path: Path) -> None:
    c = _make_client(workspace_root=str(tmp_path))
    resp = c.get("/api/v1/workspace/file?path=missing.txt")
    assert resp.status_code == 404


def test_workspace_file_path_traversal_rejected(tmp_path: Path) -> None:
    c = _make_client(workspace_root=str(tmp_path))
    resp = c.get("/api/v1/workspace/file?path=../../../etc/passwd")
    assert resp.status_code == 400


def test_workspace_file_binary_extension_rejected(tmp_path: Path) -> None:
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")
    c = _make_client(workspace_root=str(tmp_path))
    resp = c.get("/api/v1/workspace/file?path=image.png")
    assert resp.status_code == 415


def test_workspace_tree_missing_root() -> None:
    from backend.server.routers.workspace import _build_tree

    result = _build_tree(Path("/nonexistent/path/xyz"))
    assert result["type"] == "missing"


def test_workspace_tree_file_node(tmp_path: Path) -> None:
    from backend.server.routers.workspace import _build_tree

    f = tmp_path / "test.txt"
    f.write_text("hello")
    result = _build_tree(f)
    assert result["type"] == "file"


def test_workspace_forgemd_put_saves(client: TestClient) -> None:
    resp = client.put(
        "/api/v1/workspace/forgemd",
        json={"content": "# My Forge.md\n\nContent here."},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "saved"


def test_workspace_forgemd_get_503_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.get("/api/v1/workspace/forgemd")
    assert resp.status_code == 503


def test_workspace_forgemd_upload_empty_file(client: TestClient) -> None:
    """Uploading an empty file returns 400."""
    from io import BytesIO

    resp = client.post(
        "/api/v1/workspace/forgemd",
        files={"file": ("forge.md", BytesIO(b""), "text/markdown")},
    )
    assert resp.status_code == 400


def test_workspace_forgemd_upload_succeeds(client: TestClient) -> None:
    from io import BytesIO

    content = b"# Forge.md\n\nThis is the project forge.md.\n"
    resp = client.post(
        "/api/v1/workspace/forgemd",
        files={"file": ("forge.md", BytesIO(content), "text/markdown")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["size"] == len(content)


def test_workspace_tests_summary_with_graph(client: TestClient) -> None:
    resp = client.get("/api/v1/workspace/tests/summary")
    assert resp.status_code == 200
    assert "total" in resp.json()


# ── Control router ────────────────────────────────────────────────────────────


def test_patch_session_updates_name(client: TestClient) -> None:
    resp = client.patch("/api/v1/session", json={"project_name": "My Project"})
    assert resp.status_code == 200


def test_patch_session_blank_name_rejected(client: TestClient) -> None:
    resp = client.patch("/api/v1/session", json={"project_name": "   "})
    assert resp.status_code == 422


def test_start_build_no_pool_503() -> None:
    """start_build returns 503 when pool/graph/config missing."""
    app = create_app()
    from backend.core.phase_store import PhaseStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = f.name

    app.state.graph = None
    app.state.phase_store = PhaseStore(db)
    app.state.agent_pool = None
    app.state.bus = None
    app.state.config = None
    app.state.session = MagicMock()
    app.state.session.session_id = "sess"
    app.state.broadcaster = None
    app.state.contracts = None
    c = TestClient(app, raise_server_exceptions=True)
    resp = c.post("/api/v1/phases/start", json={})
    assert resp.status_code == 503


def test_start_build_with_all_dependencies(db_path: str) -> None:
    """start_build returns 200 when pool/graph/config are all present."""
    from backend.core.phase_store import PhaseStore
    from backend.server.websocket.broadcaster import EventBroadcaster
    from backend.server.websocket.manager import WebSocketManager

    g = asyncio.run(_make_graph(db_path))

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = f.name

    app = create_app()
    phase_store = PhaseStore(db)
    broadcaster = EventBroadcaster(WebSocketManager())

    mock_pool = MagicMock()
    mock_pool.all_ids.return_value = ["doc"]
    mock_pool.get_agent_for_gap.return_value = None

    mock_config = MagicMock()
    mock_config.project.name = "Test"

    mock_session = MagicMock()
    mock_session.session_id = "s1"
    mock_session.model_dump.return_value = {}

    app.state.graph = g
    app.state.phase_store = phase_store
    app.state.agent_pool = mock_pool
    app.state.bus = MagicMock()
    app.state.config = mock_config
    app.state.session = mock_session
    app.state.broadcaster = broadcaster
    app.state.contracts = MagicMock()

    c = TestClient(app, raise_server_exceptions=True)
    resp = c.post("/api/v1/phases/start", json={"start_phase": 9, "single_step": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_stop_build_not_running(client: TestClient) -> None:
    resp = client.post("/api/v1/phases/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_running"


def test_stop_build_cancels_task(client: TestClient) -> None:
    mock_task = MagicMock()
    mock_task.done.return_value = False
    _app_state(client).flow_task = mock_task

    resp = client.post("/api/v1/phases/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"
    mock_task.cancel.assert_called_once()


def test_reset_build_succeeds(client: TestClient) -> None:
    resp = client.post("/api/v1/phases/reset")
    assert resp.status_code == 200
    assert resp.json()["status"] == "reset"


def test_reset_build_503_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.post("/api/v1/phases/reset")
    assert resp.status_code == 503


def test_approve_phase_not_found(client: TestClient) -> None:
    resp = client.post("/api/v1/phases/99/approve")
    assert resp.status_code == 404


def test_approve_phase_triggers_flow_approve(client: TestClient) -> None:
    mock_flow = MagicMock()
    _app_state(client).flow = mock_flow

    resp = client.post("/api/v1/phases/0/approve")
    assert resp.status_code == 200
    mock_flow.approve_phase.assert_called_once_with(0)


def test_get_phases_returns_list(client: TestClient) -> None:
    resp = client.get("/api/v1/phases")
    assert resp.status_code == 200
    phases = resp.json()
    assert isinstance(phases, list)
    assert len(phases) == 15


# ── Agents router ─────────────────────────────────────────────────────────────


def test_list_agents_returns_list(client: TestClient) -> None:
    resp = client.get("/api/v1/agents")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_agent_by_id(client: TestClient) -> None:
    resp = client.get("/api/v1/agents/doc")
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == "doc"


def test_get_agent_not_found_404(client: TestClient) -> None:
    app = create_app()
    from backend.core.phase_store import PhaseStore
    from backend.server.websocket.broadcaster import EventBroadcaster
    from backend.server.websocket.manager import WebSocketManager

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = f.name

    mock_pool = MagicMock()
    mock_pool.all_ids.return_value = []
    app.state.graph = None
    app.state.phase_store = PhaseStore(db)
    app.state.agent_pool = mock_pool
    app.state.bus = MagicMock()
    app.state.config = MagicMock()
    app.state.session = MagicMock()
    app.state.session.session_id = "s"
    app.state.session.model_dump.return_value = {}
    app.state.broadcaster = EventBroadcaster(WebSocketManager())
    app.state.contracts = MagicMock()
    c = TestClient(app, raise_server_exceptions=True)
    resp = c.get("/api/v1/agents/ghost")
    assert resp.status_code == 404


def test_send_directive_emits_event(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agents/doc/directive",
        json={"content": "Focus on safety reqs", "priority": "high"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


# ── Tests router ──────────────────────────────────────────────────────────────


def test_tests_summary_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.get("/api/v1/tests/summary")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_tests_summary_with_graph(client: TestClient) -> None:
    resp = client.get("/api/v1/tests/summary")
    assert resp.status_code == 200
    assert "total" in resp.json()


def test_tests_summary_counts_results(db_path: str) -> None:
    g = asyncio.run(_make_graph(db_path))

    async def _setup() -> None:
        from backend.graph.models import GraphNode, NodeType

        case = GraphNode(
            node_id="case.001",
            node_type=NodeType.CASE_HLR.value,
            title="Test Case 1",
            content="",
        )
        result = GraphNode(
            node_id="case.001.result.001",
            node_type=NodeType.RESULT.value,
            title="Result 1",
            content="passed",
            parent_id="case.001",
            properties={"status": "passed"},
        )
        await g.add_node(case)
        await g.add_node(result)

    asyncio.run(_setup())
    c = _make_client(g)
    resp = c.get("/api/v1/tests/summary")
    assert resp.status_code == 200


# ── Compliance router ─────────────────────────────────────────────────────────


def test_compliance_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.get("/api/v1/graph/compliance")
    assert resp.status_code == 200


def test_compliance_with_config_disabled(db_path: str) -> None:
    g = asyncio.run(_make_graph(db_path))
    c = _make_client(g)
    _app_state(c).config.compliance.enabled = False
    resp = c.get("/api/v1/graph/compliance")
    assert resp.status_code == 200
    assert resp.json().get("enabled") is False


# ── Contracts, quality, steering, patterns, infra ────────────────────────────


def test_list_contracts_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/contracts")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_contract_not_found(client: TestClient) -> None:
    resp = client.get("/api/v1/contracts/ctr.nonexistent")
    assert resp.status_code == 404


def test_quality_metrics(client: TestClient) -> None:
    resp = client.get("/api/v1/quality/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "total_nodes" in data


def test_add_steering(client: TestClient) -> None:
    resp = client.post("/api/v1/steering", json={"content": "Focus on safety first"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert "node_id" in data


def test_infra_build_status(client: TestClient) -> None:
    resp = client.get("/api/v1/infra/build-status")
    assert resp.status_code == 200
    data = resp.json()
    assert "build_status" in data


# ── Quality router — null-graph + populated paths ─────────────────────────────


def test_quality_findings_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.get("/api/v1/quality/findings")
    assert resp.status_code == 200
    assert resp.json() == []


def test_quality_metrics_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.get("/api/v1/quality/metrics")
    assert resp.status_code == 200
    assert resp.json() == {"status": "not_started"}


def test_quality_metrics_with_nodes(db_path: str) -> None:
    async def _setup(db: str) -> ProjectGraph:
        g = ProjectGraph(db)
        await g.initialise()
        await g.add_node(GraphNode(node_id="HLR-1", node_type=NodeType.HLR.value, title="h"))
        await g.add_node(GraphNode(node_id="HLR-2", node_type=NodeType.HLR.value, title="h2"))
        return g

    g = asyncio.run(_setup(db_path))
    c = _make_client(g)
    resp = c.get("/api/v1/quality/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["total_nodes"] == 2
    assert data["node_counts"][NodeType.HLR.value] == 2
    assert "gap_counts" in data


def test_quality_findings_shape(db_path: str) -> None:
    async def _setup(db: str) -> ProjectGraph:
        g = ProjectGraph(db)
        await g.initialise()
        # An HLR with no CASE → produces an UNTESTED_HLR gap
        await g.add_node(GraphNode(node_id="HLR-1", node_type=NodeType.HLR.value, title="h"))
        return g

    g = asyncio.run(_setup(db_path))
    c = _make_client(g)
    resp = c.get("/api/v1/quality/findings")
    assert resp.status_code == 200
    findings = resp.json()
    assert isinstance(findings, list)
    for f in findings:
        assert {"gap_id", "type", "priority", "node_id", "description", "context"} <= f.keys()


# ── Architecture router ───────────────────────────────────────────────────────


def test_architecture_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.get("/api/v1/architecture")
    assert resp.status_code == 200
    assert resp.json() == {"nodes": [], "edges": []}


def test_architecture_module_no_graph(client_no_graph: TestClient) -> None:
    resp = client_no_graph.get("/api/v1/architecture/modules/any")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_architecture_module_not_found(client: TestClient) -> None:
    resp = client.get("/api/v1/architecture/modules/does-not-exist")
    assert resp.status_code == 404


def test_architecture_lists_modules(db_path: str) -> None:
    async def _setup(db: str) -> ProjectGraph:
        g = ProjectGraph(db)
        await g.initialise()
        await g.add_node(
            GraphNode(node_id="ARCH-1", node_type=NodeType.ARCHITECTURE.value, title="Sys")
        )
        await g.add_node(
            GraphNode(node_id="MOD-1", node_type=NodeType.MODULE.value, title="Mod A")
        )
        return g

    g = asyncio.run(_setup(db_path))
    c = _make_client(g)

    resp = c.get("/api/v1/architecture")
    assert resp.status_code == 200
    ids = {n["node_id"] for n in resp.json()["nodes"]}
    assert {"ARCH-1", "MOD-1"} <= ids

    resp2 = c.get("/api/v1/architecture/modules/MOD-1")
    assert resp2.status_code == 200
    assert resp2.json()["node_id"] == "MOD-1"


# ── Siblings and trace ────────────────────────────────────────────────────────


def test_get_siblings_returns_sibling(db_path: str) -> None:
    async def _setup(db: str) -> ProjectGraph:
        g = ProjectGraph(db)
        await g.initialise()
        parent = GraphNode(node_id="par", node_type=NodeType.HLR.value, title="P")
        child1 = GraphNode(
            node_id="par.c1", node_type=NodeType.CONTRACT.value, title="C1", parent_id="par"
        )
        child2 = GraphNode(
            node_id="par.c2", node_type=NodeType.MODULE.value, title="C2", parent_id="par"
        )
        await g.add_node(parent)
        await g.add_node(child1)
        await g.add_node(child2)
        return g

    g = asyncio.run(_setup(db_path))
    c = _make_client(g)

    resp = c.get("/api/v1/graph/nodes/par.c1/siblings")
    assert resp.status_code == 200
    data = resp.json()
    ids = [n["node_id"] for n in data["nodes"]]
    assert "par.c2" in ids
    assert "par.c1" not in ids


def test_put_trace_updates_properties(db_path: str) -> None:
    async def _setup(db: str) -> ProjectGraph:
        g = ProjectGraph(db)
        await g.initialise()
        node = GraphNode(node_id="mod.1", node_type=NodeType.MODULE.value, title="Mod")
        target = GraphNode(node_id="req.1", node_type=NodeType.HLR.value, title="Req")
        await g.add_node(node)
        await g.add_node(target)
        return g

    g = asyncio.run(_setup(db_path))
    c = _make_client(g)

    resp = c.put("/api/v1/graph/nodes/mod.1/trace", json={"trace_to": ["req.1"]})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    node_resp = c.get("/api/v1/graph/nodes/mod.1")
    assert node_resp.status_code == 200
    assert node_resp.json()["trace_to"] == ["req.1"]


def test_put_trace_node_not_found(db_path: str) -> None:
    g = asyncio.run(_make_graph(db_path))
    c = _make_client(g)
    resp = c.put("/api/v1/graph/nodes/nonexistent/trace", json={"trace_to": []})
    assert resp.status_code == 404


# ── Architecture ──────────────────────────────────────────────────────────────


def test_architecture_includes_entity(db_path: str) -> None:
    async def _setup(db: str) -> ProjectGraph:
        g = ProjectGraph(db)
        await g.initialise()
        entity = GraphNode(node_id="ent.1", node_type=NodeType.ARCHITECTURE.value, title="Entity")
        comp = GraphNode(node_id="comp.1", node_type=NodeType.MODULE.value, title="Comp")
        await g.add_node(entity)
        await g.add_node(comp)
        return g

    g = asyncio.run(_setup(db_path))
    c = _make_client(g)

    resp = c.get("/api/v1/architecture")
    assert resp.status_code == 200
    data = resp.json()
    ids = [n["node_id"] for n in data["nodes"]]
    assert "ent.1" in ids
    assert "comp.1" in ids


# ── ForgeFlow helpers ─────────────────────────────────────────────────────────


def test_flow_graph_state_count_none_graph() -> None:
    from backend.pipeline.flow import ForgeFlow

    flow = ForgeFlow(None, None, None, None, None)
    assert flow._graph_state_count() == 0


def test_flow_graph_state_count_exception() -> None:
    from backend.pipeline.flow import ForgeFlow

    mock_graph = MagicMock()
    mock_graph.all_nodes.side_effect = RuntimeError("DB error")
    flow = ForgeFlow(mock_graph, mock_graph, None, None, None)
    assert flow._graph_state_count() == 0


# ── Ancestors / descendants traversal ────────────────────────────────────────


def test_get_ancestors_with_node(db_path: str) -> None:
    g = asyncio.run(_make_graph(db_path))

    async def _setup() -> None:
        parent = GraphNode(
            node_id="doc.p", node_type=NodeType.DOCUMENT.value, title="P", content=""
        )
        child = GraphNode(
            node_id="doc.p.c",
            node_type=NodeType.PARA.value,
            title="C",
            content="",
            parent_id="doc.p",
        )
        await g.add_node(parent)
        await g.add_node(child)

    asyncio.run(_setup())
    c = _make_client(g)
    resp = c.get("/api/v1/graph/nodes/doc.p.c/ancestors")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
