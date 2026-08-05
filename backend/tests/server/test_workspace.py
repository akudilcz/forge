"""Tests for the workspace router — specifically the tests summary endpoint."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.graph.models import GraphNode, NodeType


def _make_node(node_id: str, node_type: str, content: str = "") -> GraphNode:
    return GraphNode(
        node_id=node_id,
        node_type=node_type,
        title=node_id,
        content=content,
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_graph() -> MagicMock:
    graph = MagicMock()
    graph.all_nodes.return_value = []
    return graph


def _build_app(mock_graph: MagicMock) -> FastAPI:
    """Build a minimal FastAPI app with the workspace router injected."""
    from backend.server.dependencies import get_project_graph
    from backend.server.routers.workspace import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_project_graph] = lambda: mock_graph
    return app


def test_tests_summary_no_graph() -> None:
    """Returns not_started when graph is None."""
    from backend.server.dependencies import get_project_graph
    from backend.server.routers.workspace import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_project_graph] = lambda: None

    client = TestClient(app)
    resp = client.get("/workspace/tests/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_started"
    assert data["total"] == 0


def test_tests_summary_empty_graph(mock_graph: MagicMock) -> None:
    """Returns not_started when graph has no CASE nodes."""
    app = _build_app(mock_graph)
    client = TestClient(app)
    resp = client.get("/workspace/tests/summary")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_started"


def test_tests_summary_all_passed(mock_graph: MagicMock) -> None:
    """Returns passed when all cases have passing results."""
    case = _make_node("tst.case.001", NodeType.CASE_HLR.value, "Login test")
    result = _make_node("tst.result.001", NodeType.RESULT.value, "Test passed successfully")
    mock_graph.all_nodes.return_value = [case, result]

    app = _build_app(mock_graph)
    client = TestClient(app)
    data = client.get("/workspace/tests/summary").json()

    assert data["total"] == 1
    assert data["passed"] == 1
    assert data["failed"] == 0
    assert data["status"] == "passed"
    assert data["last_run"] is not None


def test_tests_summary_some_failed(mock_graph: MagicMock) -> None:
    """Returns failed when any result contains 'fail'."""
    case1 = _make_node("tst.case.001", NodeType.CASE_HLR.value)
    case2 = _make_node("tst.case.002", NodeType.CASE_HLR.value)
    result1 = _make_node("tst.result.001", NodeType.RESULT.value, "passed")
    result2 = _make_node("tst.result.002", NodeType.RESULT.value, "failed: assertion error")
    mock_graph.all_nodes.return_value = [case1, case2, result1, result2]

    app = _build_app(mock_graph)
    client = TestClient(app)
    data = client.get("/workspace/tests/summary").json()

    assert data["total"] == 2
    assert data["passed"] == 1
    assert data["failed"] == 1
    assert data["status"] == "failed"


# ── _read_coverage_from_graph ─────────────────────────────────────────────────


def test_read_coverage_both_present() -> None:
    """DESIGN node with statement_coverage and branch_coverage returns both."""
    from backend.server.routers.workspace import _read_coverage_from_graph

    design = _make_node("D-1", NodeType.DESIGN.value)
    design.properties = {"statement_coverage": 92.0, "branch_coverage": 85.0}
    result = _read_coverage_from_graph([design])
    assert result == (92.0, 85.0)


def test_read_coverage_only_statement() -> None:
    """DESIGN node with only statement_coverage returns (value, None)."""
    from backend.server.routers.workspace import _read_coverage_from_graph

    design = _make_node("D-1", NodeType.DESIGN.value)
    design.properties = {"statement_coverage": 75.0}
    result = _read_coverage_from_graph([design])
    assert result == (75.0, None)


def test_read_coverage_no_design_nodes() -> None:
    """No DESIGN nodes in list returns (None, None)."""
    from backend.server.routers.workspace import _read_coverage_from_graph

    hlr = _make_node("H-1", NodeType.HLR.value)
    result = _read_coverage_from_graph([hlr])
    assert result == (None, None)


def test_read_coverage_empty_list() -> None:
    """Empty node list returns (None, None)."""
    from backend.server.routers.workspace import _read_coverage_from_graph

    result = _read_coverage_from_graph([])
    assert result == (None, None)


def test_read_coverage_design_empty_properties() -> None:
    """DESIGN node with empty properties returns (None, None)."""
    from backend.server.routers.workspace import _read_coverage_from_graph

    design = _make_node("D-1", NodeType.DESIGN.value)
    design.properties = {}
    result = _read_coverage_from_graph([design])
    assert result == (None, None)


def test_read_coverage_multiple_designs_returns_first() -> None:
    """Multiple DESIGN nodes — first one with coverage is returned."""
    from backend.server.routers.workspace import _read_coverage_from_graph

    d1 = _make_node("D-1", NodeType.DESIGN.value)
    d1.properties = {"statement_coverage": 80.0, "branch_coverage": 70.0}
    d2 = _make_node("D-2", NodeType.DESIGN.value)
    d2.properties = {"statement_coverage": 90.0, "branch_coverage": 85.0}
    result = _read_coverage_from_graph([d1, d2])
    assert result == (80.0, 70.0)


# ── get_tests_summary coverage fields ─────────────────────────────────────────


def test_tests_summary_includes_coverage_from_design(mock_graph: MagicMock) -> None:
    """Response includes coverage_percent from DESIGN node properties."""
    case = _make_node("tst.case.001", NodeType.CASE_HLR.value, "Login test")
    result_node = _make_node("tst.result.001", NodeType.RESULT.value, "Test passed")
    design = _make_node("D-1", NodeType.DESIGN.value)
    design.properties = {"statement_coverage": 92.5, "branch_coverage": 88.0}
    mock_graph.all_nodes.return_value = [case, result_node, design]

    app = _build_app(mock_graph)
    client = TestClient(app)
    data = client.get("/workspace/tests/summary").json()

    assert data["coverage_percent"] == 92.5
    assert data["branch_coverage_percent"] == 88.0


def test_tests_summary_coverage_none_without_design(mock_graph: MagicMock) -> None:
    """coverage_percent and branch_coverage_percent are None when no DESIGN."""
    case = _make_node("tst.case.001", NodeType.CASE_HLR.value, "Login test")
    mock_graph.all_nodes.return_value = [case]

    app = _build_app(mock_graph)
    client = TestClient(app)
    data = client.get("/workspace/tests/summary").json()

    assert data["coverage_percent"] is None
    assert data["branch_coverage_percent"] is None
