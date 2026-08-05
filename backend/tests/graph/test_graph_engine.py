"""Unit tests for the Graph Engine."""

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from backend.graph.engine import ProjectGraph
from backend.graph.models import EdgeType, GraphEdge, GraphNode, LifecycleState, NodeType


@pytest.fixture
async def graph() -> AsyncIterator[ProjectGraph]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    g = ProjectGraph(db_path)
    await g.initialise()
    yield g

    os.unlink(db_path)


@pytest.mark.asyncio
async def test_add_node(graph: ProjectGraph) -> None:
    node = GraphNode(
        node_id="test.node.1",
        node_type=NodeType.DOCUMENT.value,
        title="Test Node",
        content="Test Content",
    )

    saved = await graph.add_node(node)
    assert saved.node_id == "test.node.1"
    assert saved.content_hash is not None

    fetched = await graph.node("test.node.1")
    assert fetched is not None
    assert fetched.content == "Test Content"


@pytest.mark.asyncio
async def test_update_node(graph: ProjectGraph) -> None:
    node = GraphNode(
        node_id="test.node.1",
        node_type=NodeType.DOCUMENT.value,
        title="Test Node",
        content="Original Content",
    )
    await graph.add_node(node)

    updated, impact = await graph.update_node(
        node_id="test.node.1",
        content="New Content",
        properties=None,
        changed_by="tester",
        change_reason="update test",
    )

    assert updated.content == "New Content"
    assert updated.version == 2
    assert impact.root_node_id == "test.node.1"

    fetched = await graph.node("test.node.1")
    assert fetched is not None
    assert fetched.content == "New Content"


@pytest.mark.asyncio
async def test_parent_child_relationship(graph: ProjectGraph) -> None:
    parent = GraphNode(node_id="parent", node_type=NodeType.DOCUMENT.value, title="Parent")
    await graph.add_node(parent)

    child = GraphNode(
        node_id="child", node_type=NodeType.PARA.value, title="Child", parent_id="parent"
    )
    await graph.add_node(child)

    children = await graph.children("parent")
    assert len(children) == 1
    assert children[0].node_id == "child"


@pytest.mark.asyncio
async def test_lifecycle_initial_state(graph: ProjectGraph) -> None:
    node = GraphNode(
        node_id="test.node",
        node_type=NodeType.DOCUMENT.value,
        title="Test Node",
        lifecycle=LifecycleState.DRAFT,
    )
    await graph.add_node(node)

    fetched = await graph.node("test.node")
    assert fetched is not None
    assert fetched.lifecycle == LifecycleState.DRAFT


@pytest.mark.asyncio
async def test_nodes_filter_by_type(graph: ProjectGraph) -> None:
    req = GraphNode(node_id="req.hlr.1", node_type=NodeType.HLR.value, title="Req 1")
    doc = GraphNode(node_id="doc.spec", node_type=NodeType.DOCUMENT.value, title="Doc")
    await graph.add_node(req)
    await graph.add_node(doc)

    results = await graph.nodes(type_prefix="HLR")
    assert len(results) == 1
    assert results[0].node_id == "req.hlr.1"


@pytest.mark.asyncio
async def test_nodes_filter_lifecycle(graph: ProjectGraph) -> None:
    doc = GraphNode(
        node_id="doc.a",
        node_type=NodeType.DOCUMENT.value,
        title="A",
        lifecycle=LifecycleState.ACTIVE,
    )
    doc2 = GraphNode(
        node_id="doc.b",
        node_type=NodeType.DOCUMENT.value,
        title="B",
        lifecycle=LifecycleState.DRAFT,
    )
    await graph.add_node(doc)
    await graph.add_node(doc2)

    active = await graph.nodes(lifecycle="active")
    assert all(n.lifecycle.value == "active" for n in active)


@pytest.mark.asyncio
async def test_all_edges_and_filters(graph: ProjectGraph) -> None:
    n1 = GraphNode(node_id="n1", node_type=NodeType.HLR.value, title="N1")
    n2 = GraphNode(node_id="n2", node_type=NodeType.CODE.value, title="N2")
    await graph.add_node(n1)
    await graph.add_node(n2)

    edge = GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="n2", target_id="n1", created_by="test"
    )
    await graph.add_edge(edge)

    all_edges = await graph.all_edges()
    assert len(all_edges) == 1

    from_n2 = await graph.edges_from("n2")
    assert len(from_n2) == 1
    assert from_n2[0].target_id == "n1"

    to_n1 = await graph.edges_to("n1")
    assert len(to_n1) == 1

    none = await graph.edges_from("n1")
    assert len(none) == 0


@pytest.mark.asyncio
async def test_traceability_gaps(graph: ProjectGraph) -> None:
    req = GraphNode(node_id="req.1", node_type=NodeType.HLR.value, title="Req")
    await graph.add_node(req)

    gaps = await graph.traceability_gaps()
    assert "req.1" in gaps.unimplemented_requirements
    assert "req.1" in gaps.uncovered_requirements


@pytest.mark.asyncio
async def test_create_baseline(graph: ProjectGraph) -> None:
    baseline = await graph.create_baseline("v1.0", "phase", "First release")
    assert baseline.node_id == "rec.baseline.v1.0"
    assert baseline.properties["record_type"] == "baseline"


@pytest.mark.asyncio
async def test_predecessors_sync_returns_source_nodes(graph: ProjectGraph) -> None:
    para = GraphNode(node_id="doc.spec.par.001", node_type=NodeType.PARA.value, title="P1")
    req = GraphNode(
        node_id="doc.spec.par.001.req.hlr.001",
        node_type=NodeType.HLR.value,
        title="HLR1",
        parent_id="doc.spec.par.001",
    )
    await graph.add_node(para)
    await graph.add_node(req)

    edge = GraphEdge(
        edge_type=EdgeType.DERIVES_FROM.value,
        source_id="doc.spec.par.001.req.hlr.001",
        target_id="doc.spec.par.001",
    )
    await graph.add_edge(edge)

    preds = graph.predecessors_sync("doc.spec.par.001", edge_type="DERIVES_FROM")
    assert len(preds) == 1
    assert preds[0].node_id == "doc.spec.par.001.req.hlr.001"

    # With wrong edge type filter, returns nothing
    preds_none = graph.predecessors_sync("doc.spec.par.001", edge_type="IMPLEMENTS")
    assert len(preds_none) == 0


@pytest.mark.asyncio
async def test_any_trace_to_returns_true_when_module_traces_llr(graph: ProjectGraph) -> None:
    llr = GraphNode(node_id="req.llr.1", node_type=NodeType.HLR.value, title="LLR")
    mod = GraphNode(
        node_id="mod.1",
        node_type=NodeType.MODULE.value,
        title="Module",
        trace_to=["req.llr.1"],
    )
    await graph.add_node(llr)
    await graph.add_node(mod)

    assert graph.any_trace_to("req.llr.1", "MODULE") is True


@pytest.mark.asyncio
async def test_any_trace_to_returns_false_when_no_trace(graph: ProjectGraph) -> None:
    llr = GraphNode(node_id="req.llr.2", node_type=NodeType.HLR.value, title="LLR2")
    mod = GraphNode(node_id="mod.2", node_type=NodeType.MODULE.value, title="Mod2")
    await graph.add_node(llr)
    await graph.add_node(mod)

    assert graph.any_trace_to("req.llr.2", "MODULE") is False


@pytest.mark.asyncio
async def test_siblings_sync_returns_correct_siblings(graph: ProjectGraph) -> None:
    parent = GraphNode(node_id="p", node_type=NodeType.HLR.value, title="Parent")
    child1 = GraphNode(node_id="p.c1", node_type=NodeType.CONTRACT.value, title="C1", parent_id="p")
    child2 = GraphNode(node_id="p.c2", node_type=NodeType.MODULE.value, title="C2", parent_id="p")
    await graph.add_node(parent)
    await graph.add_node(child1)
    await graph.add_node(child2)

    siblings = graph.siblings_sync("p.c1")
    sibling_ids = [s.node_id for s in siblings]
    assert "p.c2" in sibling_ids
    assert "p.c1" not in sibling_ids


@pytest.mark.asyncio
async def test_siblings_sync_returns_empty_for_root_node(graph: ProjectGraph) -> None:
    root = GraphNode(node_id="root", node_type=NodeType.PROJECT.value, title="Root")
    await graph.add_node(root)

    siblings = graph.siblings_sync("root")
    assert siblings == []


@pytest.mark.asyncio
async def test_allocate_node_id_returns_sequential_ids(graph: ProjectGraph) -> None:
    id1 = await graph.allocate_node_id("HLR")
    id2 = await graph.allocate_node_id("HLR")
    assert id1 == "HLR-0001"
    assert id2 == "HLR-0002"


@pytest.mark.asyncio
async def test_allocate_node_id_separate_counters_per_type(graph: ProjectGraph) -> None:
    hlr1 = await graph.allocate_node_id("HLR")
    llr1 = await graph.allocate_node_id("LLR")
    hlr2 = await graph.allocate_node_id("HLR")
    assert hlr1 == "HLR-0001"
    assert llr1 == "LLR-0001"
    assert hlr2 == "HLR-0002"


@pytest.mark.asyncio
async def test_reset_sequences_resets_all_counters(graph: ProjectGraph) -> None:
    await graph.allocate_node_id("HLR")
    await graph.allocate_node_id("LLR")
    await graph.reset_sequences()
    assert await graph.allocate_node_id("HLR") == "HLR-0001"
    assert await graph.allocate_node_id("LLR") == "LLR-0001"


@pytest.mark.asyncio
async def test_reset_sequences_with_exclude_preserves_excluded(graph: ProjectGraph) -> None:
    await graph.allocate_node_id("HLR")
    await graph.allocate_node_id("HLR")
    await graph.allocate_node_id("LLR")
    await graph.reset_sequences(exclude=["HLR"])
    assert await graph.allocate_node_id("HLR") == "HLR-0003"
    assert await graph.allocate_node_id("LLR") == "LLR-0001"


@pytest.mark.asyncio
async def test_delete_children_recursive_removes_deep_subtree(graph: ProjectGraph) -> None:
    root = GraphNode(node_id="root", node_type=NodeType.PROJECT.value, title="Root")
    child = GraphNode(node_id="child", node_type=NodeType.DOCUMENT.value,
                      title="Child", parent_id="root")
    grandchild = GraphNode(node_id="grand", node_type=NodeType.PARA.value,
                           title="Grand", parent_id="child")
    for n in (root, child, grandchild):
        await graph.add_node(n)

    await graph.delete_children_recursive("root")

    assert await graph.node("root") is not None
    assert await graph.node("child") is None
    assert await graph.node("grand") is None


@pytest.mark.asyncio
async def test_find_node_by_slug_returns_matching_document(graph: ProjectGraph) -> None:
    doc = GraphNode(
        node_id="DOCUMENT-0001",
        node_type=NodeType.DOCUMENT.value,
        title="Whitepaper",
        properties={"slug": "whitepaper"},
    )
    await graph.add_node(doc)
    found = await graph.find_node_by_slug("whitepaper")
    assert found is not None
    assert found.node_id == "DOCUMENT-0001"


@pytest.mark.asyncio
async def test_find_node_by_slug_returns_none_when_not_found(graph: ProjectGraph) -> None:
    found = await graph.find_node_by_slug("nonexistent")
    assert found is None


async def _add_doc_para(g: ProjectGraph) -> tuple[GraphNode, GraphNode]:
    doc = GraphNode(node_id="doc.spec", node_type=NodeType.DOCUMENT.value, title="Spec", content="")
    para = GraphNode(
        node_id="doc.spec.p1",
        node_type=NodeType.PARA.value,
        title="Para 1",
        content="The system shall do X.",
        parent_id="doc.spec",
    )
    await g.add_node(doc)
    await g.add_node(para)
    return doc, para


@pytest.mark.asyncio
async def test_node_sync_returns_node(graph: ProjectGraph) -> None:
    doc, _ = await _add_doc_para(graph)
    node = graph.node_sync("doc.spec")
    assert node is not None
    assert node.node_id == "doc.spec"


@pytest.mark.asyncio
async def test_node_sync_unknown_returns_none(graph: ProjectGraph) -> None:
    assert graph.node_sync("nonexistent") is None


@pytest.mark.asyncio
async def test_children_sync_returns_children(graph: ProjectGraph) -> None:
    await _add_doc_para(graph)
    children = graph.children_sync("doc.spec")
    assert len(children) == 1
    assert children[0].node_id == "doc.spec.p1"


@pytest.mark.asyncio
async def test_reset_clears_all_nodes_and_edges(graph: ProjectGraph) -> None:
    await _add_doc_para(graph)
    edge = GraphEdge(
        edge_type=EdgeType.DERIVES_FROM.value, source_id="doc.spec.p1", target_id="doc.spec"
    )
    await graph.add_edge(edge)

    await graph.reset()
    assert graph.all_nodes() == []
    assert await graph.all_edges() == []


@pytest.mark.asyncio

@pytest.mark.asyncio

@pytest.mark.asyncio
async def test_impact_set_with_descendants(graph: ProjectGraph) -> None:
    await _add_doc_para(graph)
    impact = await graph.impact_set("doc.spec")
    assert impact.root_node_id == "doc.spec"
    assert isinstance(impact.stale_nodes, list)


@pytest.mark.asyncio
async def test_traceability_chain(graph: ProjectGraph) -> None:
    doc = GraphNode(node_id="doc.spec", node_type=NodeType.DOCUMENT.value, title="Spec")
    await graph.add_node(doc)
    chain = await graph.traceability_chain("doc.spec")
    assert chain.node_id == "doc.spec"
    assert isinstance(chain.ancestors, list)


# ── context_bundle_sync tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_bundle_unknown_node_returns_empty(graph: ProjectGraph) -> None:
    result = graph.context_bundle_sync("nonexistent")
    assert result == {"node_id": "nonexistent", "inner": [], "middle": [], "outer": []}


@pytest.mark.asyncio
async def test_context_bundle_inner_includes_parent_and_children(graph: ProjectGraph) -> None:
    parent = GraphNode(
        node_id="llr.auth",
        node_type=NodeType.HLR.value,
        title="Auth LLR",
        content="Auth requirements.",
        properties={"req_level": "llr"},
    )
    child = GraphNode(
        node_id="llr.auth.mod.auth",
        node_type=NodeType.MODULE.value,
        title="Auth module",
        content="Implements auth.",
        parent_id="llr.auth",
    )
    grandchild = GraphNode(
        node_id="llr.auth.mod.auth.cls.Validator",
        node_type=NodeType.DESIGN.value,
        title="Validator",
        content="Validates tokens.",
        parent_id="llr.auth.mod.auth",
    )
    await graph.add_node(parent)
    await graph.add_node(child)
    await graph.add_node(grandchild)

    bundle = graph.context_bundle_sync("llr.auth.mod.auth")
    inner_ids = {e["node_id"] for e in bundle["inner"]}
    assert "llr.auth" in inner_ids
    assert "llr.auth.mod.auth.cls.Validator" in inner_ids
    assert any(e["role"] == "parent" for e in bundle["inner"])


@pytest.mark.asyncio
async def test_context_bundle_middle_contains_siblings(graph: ProjectGraph) -> None:
    parent = GraphNode(node_id="llr.1", node_type=NodeType.HLR.value, title="LLR 1", content="")
    mod_a = GraphNode(node_id="llr.1.mod.a", node_type=NodeType.MODULE.value, title="Module A", content="", parent_id="llr.1")
    mod_b = GraphNode(node_id="llr.1.mod.b", node_type=NodeType.MODULE.value, title="Module B", content="", parent_id="llr.1")
    await graph.add_node(parent)
    await graph.add_node(mod_a)
    await graph.add_node(mod_b)

    bundle = graph.context_bundle_sync("llr.1.mod.a")
    middle_ids = {e["node_id"] for e in bundle["middle"]}
    assert "llr.1.mod.b" in middle_ids
    assert "llr.1.mod.a" not in middle_ids


@pytest.mark.asyncio
async def test_context_bundle_contract_elevated_to_inner(graph: ProjectGraph) -> None:
    llr = GraphNode(node_id="llr.auth", node_type=NodeType.HLR.value, title="Auth LLR", content="")
    module = GraphNode(node_id="llr.auth.mod.auth", node_type=NodeType.MODULE.value, title="Auth Module", content="", parent_id="llr.auth")
    contract = GraphNode(node_id="llr.auth.ctr.db", node_type=NodeType.CONTRACT.value, title="DB contract", content="DB interface spec.", parent_id="llr.auth")
    await graph.add_node(llr)
    await graph.add_node(module)
    await graph.add_node(contract)

    bundle = graph.context_bundle_sync("llr.auth.mod.auth")
    inner_ids = {e["node_id"] for e in bundle["inner"]}
    assert "llr.auth.ctr.db" in inner_ids
    assert any(e["role"] == "contract" for e in bundle["inner"])


@pytest.mark.asyncio
async def test_context_bundle_inner_includes_trace_to_targets(graph: ProjectGraph) -> None:
    hlr = GraphNode(
        node_id="proj.x.hlr.1",
        node_type=NodeType.HLR.value,
        title="HLR 1",
        content="The system shall do X.",
        properties={"req_level": "hlr"},
    )
    proj = GraphNode(node_id="proj.x", node_type=NodeType.PROJECT.value, title="Project X", content="")
    arch = GraphNode(
        node_id="proj.x.arch.main",
        node_type=NodeType.ARCHITECTURE.value,
        title="Architecture",
        content="Module breakdown.",
        parent_id="proj.x",
        trace_to=["proj.x.hlr.1"],
    )
    await graph.add_node(proj)
    await graph.add_node(hlr)
    await graph.add_node(arch)

    bundle = graph.context_bundle_sync("proj.x.arch.main")
    inner_ids = {e["node_id"] for e in bundle["inner"]}
    assert "proj.x.hlr.1" in inner_ids
    assert any(e["role"] == "trace_to" for e in bundle["inner"])


@pytest.mark.asyncio
async def test_context_bundle_no_duplication_across_tiers(graph: ProjectGraph) -> None:
    proj3 = GraphNode(node_id="proj.q", node_type=NodeType.PROJECT.value, title="Project Q", content="")
    hlr = GraphNode(node_id="proj.q.hlr.1", node_type=NodeType.HLR.value, title="HLR 1", content="The system shall Q.", properties={"req_level": "hlr"})
    arch = GraphNode(node_id="proj.q.arch.main", node_type=NodeType.ARCHITECTURE.value, title="Arch Q", content="Modules.", parent_id="proj.q", trace_to=["proj.q.hlr.1"])
    await graph.add_node(proj3)
    await graph.add_node(hlr)
    await graph.add_node(arch)

    bundle = graph.context_bundle_sync("proj.q.arch.main")
    inner_ids = {e["node_id"] for e in bundle["inner"]}
    middle_ids = {e["node_id"] for e in bundle["middle"]}
    outer_ids = {e["node_id"] for e in bundle["outer"]}

    assert "proj.q.hlr.1" in inner_ids
    assert "proj.q.hlr.1" not in middle_ids
    assert "proj.q.hlr.1" not in outer_ids
