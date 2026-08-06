"""Tests for the deterministic markdown → PARA-tree splitter (phase 2 primary route).

Behavioural reference: specs/03-build-pipeline.md §Phase 2 — a markdown
document with at least two ATX headings is split deterministically (zero LLM
calls); headings become empty heading PARAs nested by level, and paragraphs,
bullet groups, and fenced code blocks become verbatim child PARAs with a
heuristic sub_type.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.graph.parsers.markdown_splitter import (
    MIN_QUALIFYING_HEADINGS,
    ParaBlock,
    count_markdown_headings,
    qualifies_for_deterministic_parse,
    split_markdown,
    write_para_tree,
)

WHITEPAPER = (
    Path(__file__).parents[2] / "integration" / "whitepapers" / "04_topological_sort.md"
)

NESTED_DOC = """\
# Title

Intro paragraph explaining why the system exists.

## Requirements

The system shall accept markdown input.

The system shall never truncate content.

### Limits

Input size is at most 10 MB.

## Notes

- first bullet
- second bullet

```python
def api(x: int) -> str: ...
```
"""


# ── Qualification detector ───────────────────────────────────────────────────


def test_markdown_with_two_headings_qualifies() -> None:
    assert qualifies_for_deterministic_parse("# A\n\ntext\n\n## B\n\nmore\n")


def test_plain_prose_does_not_qualify() -> None:
    prose = "This is a specification.\n\nIt has paragraphs but no headings at all.\n"
    assert not qualifies_for_deterministic_parse(prose)
    assert count_markdown_headings(prose) == 0


def test_single_heading_does_not_qualify() -> None:
    assert not qualifies_for_deterministic_parse("# Only One\n\nbody text\n")


def test_headings_inside_code_fences_do_not_count() -> None:
    doc = "# Real\n\n```\n# not a heading\n## also not\n```\n"
    assert count_markdown_headings(doc) == 1
    assert not qualifies_for_deterministic_parse(doc)


def test_min_qualifying_headings_is_two() -> None:
    assert MIN_QUALIFYING_HEADINGS == 2


# ── Splitter: structure ──────────────────────────────────────────────────────


def test_headings_become_empty_heading_blocks_nested_by_level() -> None:
    blocks = split_markdown(NESTED_DOC)
    headings = [b for b in blocks if b.kind == "heading"]
    assert [h.title for h in headings] == ["Title", "Requirements", "Limits", "Notes"]
    assert all(h.content == "" for h in headings), "heading PARAs are empty by design"
    assert all(h.sub_type == "heading" for h in headings)

    by_title = {h.title: h for h in headings}
    # Title (h1) sits at the document root.
    assert by_title["Title"].parent is None
    # Requirements (h2) nests under Title (h1).
    assert blocks[by_title["Requirements"].parent].title == "Title"  # type: ignore[index]
    # Limits (h3) nests under Requirements (h2).
    assert blocks[by_title["Limits"].parent].title == "Requirements"  # type: ignore[index]
    # Notes (h2) pops back up under Title (h1).
    assert blocks[by_title["Notes"].parent].title == "Title"  # type: ignore[index]


def test_body_blocks_attach_to_innermost_heading() -> None:
    blocks = split_markdown(NESTED_DOC)
    shall_accept = next(b for b in blocks if "shall accept markdown" in b.content)
    assert blocks[shall_accept.parent].title == "Requirements"  # type: ignore[index]
    limits_body = next(b for b in blocks if "at most 10 MB" in b.content)
    assert blocks[limits_body.parent].title == "Limits"  # type: ignore[index]


def test_body_before_any_heading_attaches_to_document_root() -> None:
    blocks = split_markdown("Preamble text.\n\n# A\n\nbody\n\n## B\n\nmore\n")
    preamble = next(b for b in blocks if b.content == "Preamble text.")
    assert preamble.parent is None


def test_source_order_is_preserved() -> None:
    blocks = split_markdown(NESTED_DOC)
    positions = {
        "Intro": next(i for i, b in enumerate(blocks) if "Intro paragraph" in b.content),
        "accept": next(i for i, b in enumerate(blocks) if "shall accept" in b.content),
        "truncate": next(i for i, b in enumerate(blocks) if "never truncate" in b.content),
        "bullets": next(i for i, b in enumerate(blocks) if "first bullet" in b.content),
    }
    assert positions["Intro"] < positions["accept"] < positions["truncate"] < positions["bullets"]


# ── Splitter: verbatim content ───────────────────────────────────────────────


def test_paragraphs_are_kept_verbatim() -> None:
    blocks = split_markdown(NESTED_DOC)
    assert any(b.content == "The system shall accept markdown input." for b in blocks)
    assert any(b.content == "The system shall never truncate content." for b in blocks)


def test_bullet_group_is_one_verbatim_block() -> None:
    blocks = split_markdown(NESTED_DOC)
    bullets = [b for b in blocks if "first bullet" in b.content]
    assert len(bullets) == 1
    assert bullets[0].content == "- first bullet\n- second bullet"


def test_bullet_group_with_blank_lines_between_items_stays_one_block() -> None:
    doc = "# A\n\n## B\n\n- one\n\n- two\n\n- three\n"
    blocks = split_markdown(doc)
    bullets = [b for b in blocks if "- one" in b.content]
    assert len(bullets) == 1
    assert "- two" in bullets[0].content
    assert "- three" in bullets[0].content


def test_code_block_kept_verbatim_including_fences() -> None:
    blocks = split_markdown(NESTED_DOC)
    code = [b for b in blocks if "def api" in b.content]
    assert len(code) == 1
    assert code[0].content == "```python\ndef api(x: int) -> str: ...\n```"


def test_no_content_is_lost_or_truncated() -> None:
    blocks = split_markdown(NESTED_DOC)
    for block in blocks:
        if block.kind == "body":
            assert block.content in NESTED_DOC, f"not verbatim: {block.content!r}"
    corpus = "\n".join(b.content for b in blocks)
    for fragment in (
        "Intro paragraph explaining why the system exists.",
        "at most 10 MB",
        "- second bullet",
        "def api(x: int) -> str: ...",
    ):
        assert fragment in corpus


# ── Splitter: sub_type heuristics ────────────────────────────────────────────


def test_normative_keywords_classify_as_functional() -> None:
    blocks = split_markdown(NESTED_DOC)
    shall = next(b for b in blocks if "shall accept" in b.content)
    assert shall.sub_type == "functional"


def test_limit_wording_classifies_as_constraint() -> None:
    blocks = split_markdown(NESTED_DOC)
    limit = next(b for b in blocks if "at most 10 MB" in b.content)
    assert limit.sub_type == "constraint"


def test_plain_prose_classifies_as_rationale() -> None:
    blocks = split_markdown(NESTED_DOC)
    intro = next(b for b in blocks if "Intro paragraph" in b.content)
    assert intro.sub_type == "rationale"


def test_code_blocks_classify_as_functional() -> None:
    """API-signature code blocks are normative requirement sources (specs/03)."""
    blocks = split_markdown(NESTED_DOC)
    code = next(b for b in blocks if "def api" in b.content)
    assert code.sub_type == "functional"


def test_empty_document_yields_no_blocks() -> None:
    assert split_markdown("") == []
    assert split_markdown("   \n\n  \n") == []


def test_para_block_is_immutable() -> None:
    block = ParaBlock(
        kind="body", level=0, title="t", content="c",
        sub_type="rationale", parent=None, section_path="",
    )
    with pytest.raises(AttributeError):
        block.content = "mutated"  # type: ignore[misc]


# ── Writer: PARA tree in a real graph ────────────────────────────────────────


@pytest.fixture
async def graph() -> AsyncIterator[ProjectGraph]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    g = ProjectGraph(db_path)
    await g.initialise()
    yield g
    os.unlink(db_path)


async def _make_document(graph: ProjectGraph, content: str) -> GraphNode:
    node_id = await graph.allocate_node_id("DOCUMENT")
    doc = GraphNode(
        node_id=node_id, node_type=NodeType.DOCUMENT.value, layer=1,
        title="Spec", content=content,
        properties={"doc_type": "reference", "doc_order": 99, "slug": "forgemd"},
        lifecycle=LifecycleState.DRAFT, created_by="test",
    )
    return await graph.add_node(doc)


@pytest.mark.asyncio
async def test_write_para_tree_builds_document_para_para_shape(graph: ProjectGraph) -> None:
    doc = await _make_document(graph, NESTED_DOC)
    created = await write_para_tree(graph, doc, "test")

    assert created, "qualifying markdown must produce PARA nodes"
    para_ids = {n.node_id for n in created}
    for node in created:
        assert node.node_type == NodeType.PARA.value
        assert node.layer == 1
        assert node.trace_to == []
        assert node.parent_id == doc.node_id or node.parent_id in para_ids
        assert node.node_id.startswith("PARA-"), "IDs come from the normal allocator"
        assert node.properties["document_slug"] == "forgemd"
        assert "seq" in node.properties

    # At least one PARA hangs directly under the DOCUMENT (closes UNCHUNKED_DOCUMENT).
    assert any(n.parent_id == doc.node_id for n in created)


@pytest.mark.asyncio
async def test_write_para_tree_stamps_provenance_via_engine(graph: ProjectGraph) -> None:
    from backend.graph.provenance import DERIVED_FROM_HASH

    doc = await _make_document(graph, NESTED_DOC)
    created = await write_para_tree(graph, doc, "test")
    for node in created:
        assert DERIVED_FROM_HASH in node.properties, (
            f"{node.node_id} missing engine provenance stamp"
        )


@pytest.mark.asyncio
async def test_write_para_tree_requires_document_slug(graph: ProjectGraph) -> None:
    node_id = await graph.allocate_node_id("DOCUMENT")
    doc = GraphNode(
        node_id=node_id, node_type=NodeType.DOCUMENT.value, layer=1,
        title="No Slug", content="# A\n\nx\n\n## B\n\ny\n",
        properties={}, lifecycle=LifecycleState.DRAFT, created_by="test",
    )
    doc = await graph.add_node(doc)
    with pytest.raises(ValueError, match="slug"):
        await write_para_tree(graph, doc, "test")


# ── Real whitepaper fixture: tree-shape contract for downstream phases ───────


@pytest.mark.asyncio
async def test_real_whitepaper_produces_downstream_compatible_tree(
    graph: ProjectGraph,
) -> None:
    content = WHITEPAPER.read_text(encoding="utf-8")
    assert qualifies_for_deterministic_parse(content)

    doc = await _make_document(graph, content)
    created = await write_para_tree(graph, doc, "test")

    headings = [n for n in created if n.para_type == "heading"]
    bodies = [n for n in created if n.para_type != "heading"]

    # Every section heading of the whitepaper is present, nested by level.
    titles = {h.title for h in headings}
    for expected in (
        "Topological Ordering with Kahn's Algorithm and Cycle Diagnosis",
        "Abstract",
        "3. Kahn's Algorithm",
        "3.1 Tie-Breaking and Determinism",
        "10. Public API",
    ):
        assert expected in titles, f"missing heading PARA {expected!r}"
    by_id = {n.node_id: n for n in created}
    sub = next(h for h in headings if h.title == "3.1 Tie-Breaking and Determinism")
    assert sub.parent_id is not None
    assert by_id[sub.parent_id].title == "3. Kahn's Algorithm"

    # Body PARAs: verbatim, non-empty, valid sub_type — the shape phase 3
    # batch prompts and the analyser's UNCOVERED_PARA check consume.
    valid = {"functional", "rationale", "constraint"}
    for node in bodies:
        assert node.content.strip(), f"body PARA {node.node_id} is empty"
        assert node.content in content, f"body PARA {node.node_id} not verbatim"
        assert node.para_type in valid, (
            f"{node.node_id} has sub_type {node.para_type!r}"
        )

    # Normative API-signature code block survives verbatim (specs/03).
    api_block = [n for n in bodies if "class CyclicGraphError(ValueError):" in n.content]
    assert len(api_block) == 1
    assert "def topological_sort(" in api_block[0].content
    assert api_block[0].para_type == "functional"

    # Heading PARAs are empty by design; the analyser exemptions cover them.
    assert all((h.content or "") == "" for h in headings)

    # The analyser sees a closed UNCHUNKED_DOCUMENT and requirement-source PARAs.
    from backend.analysis.gap_analyser import GapAnalyser
    from backend.analysis.gaps import GapType

    gaps = GapAnalyser().analyse(graph)
    assert [g for g in gaps if g.type == GapType.UNCHUNKED_DOCUMENT] == []
    assert [g for g in gaps if g.type == GapType.EMPTY_CONTENT] == []
    uncovered = [g for g in gaps if g.type == GapType.UNCOVERED_PARA]
    assert uncovered, "body PARAs must register as HLR sources for phase 3"
