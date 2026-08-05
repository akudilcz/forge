"""Tests for DocumentParser — converts markdown to graph nodes."""

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType
from backend.graph.parsers.document import DocumentParser, ParseResult


@pytest.fixture
async def graph() -> AsyncIterator[ProjectGraph]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    g = ProjectGraph(db_path)
    await g.initialise()
    yield g
    os.unlink(db_path)


SAMPLE_DOC = """\
# System Requirements

## Overview

This section provides background.

## Functional Requirements

The system shall process inputs within 100ms.

The system shall provide a REST API.

## Notes

This is a note about implementation.
"""


@pytest.mark.asyncio
async def test_parse_creates_document_node(graph: ProjectGraph) -> None:
    parser = DocumentParser(llm_config=None)
    result = await parser.parse(
        document_slug="spec",
        content=SAMPLE_DOC,
        graph=graph,
        changed_by="test",
    )
    assert isinstance(result, ParseResult)
    assert len(result.created) > 0

    doc_node = await graph.find_node_by_slug("spec")
    assert doc_node is not None
    assert doc_node.node_type == NodeType.DOCUMENT.value


@pytest.mark.asyncio
async def test_parse_creates_para_nodes(graph: ProjectGraph) -> None:
    parser = DocumentParser(llm_config=None)
    result = await parser.parse(
        document_slug="spec",
        content=SAMPLE_DOC,
        graph=graph,
        changed_by="test",
    )
    para_count = sum(1 for n in result.created if n.node_type == NodeType.PARA.value)
    assert para_count > 0
    content_paras = [n for n in result.created if n.node_type == NodeType.PARA.value and n.para_type == "paragraph"]
    assert len(content_paras) > 0


@pytest.mark.asyncio
async def test_parse_idempotent_same_content(graph: ProjectGraph) -> None:
    parser = DocumentParser(llm_config=None)
    result1 = await parser.parse(document_slug="spec", content=SAMPLE_DOC, graph=graph, changed_by="test")
    result2 = await parser.parse(document_slug="spec", content=SAMPLE_DOC, graph=graph, changed_by="test")
    assert len(result2.created) <= len(result1.created)


@pytest.mark.asyncio
async def test_parse_with_empty_content(graph: ProjectGraph) -> None:
    parser = DocumentParser(llm_config=None)
    result = await parser.parse(document_slug="empty", content="", graph=graph, changed_by="test")
    assert isinstance(result, ParseResult)


@pytest.mark.asyncio
async def test_parse_sets_project_parent_id(graph: ProjectGraph) -> None:
    from backend.graph.models import GraphNode, LifecycleState, NodeType

    proj = GraphNode(
        node_id="proj.test",
        node_type=NodeType.PROJECT.value,
        layer=0,
        title="Test Project",
        content="",
        lifecycle=LifecycleState.ACTIVE,
        created_by="test",
    )
    await graph.add_node(proj)

    parser = DocumentParser(llm_config=None)
    await parser.parse(
        document_slug="spec",
        content=SAMPLE_DOC,
        graph=graph,
        changed_by="test",
        project_node_id="proj.test",
    )

    doc = await graph.find_node_by_slug("spec")
    assert doc is not None
    assert doc.parent_id == "proj.test"


@pytest.mark.asyncio
async def test_parse_purges_removed_sections(graph: ProjectGraph) -> None:
    parser = DocumentParser(llm_config=None)

    original = "# Section A\n\nContent about A.\n\n# Section B\n\nContent about B."
    result1 = await parser.parse("doc", original, graph, "test")
    assert len(result1.created) > 0

    shorter = "# Section A\n\nContent about A."
    result2 = await parser.parse("doc", shorter, graph, "test")
    assert len(result2.purged) > 0


@pytest.mark.asyncio
async def test_parse_setext_headings(graph: ProjectGraph) -> None:
    content = "Introduction\n============\n\nSome background text.\n"
    parser = DocumentParser(llm_config=None)
    result = await parser.parse("setext", content, graph, "test")
    headings = [n for n in result.created if n.node_type == NodeType.PARA.value and n.para_type == "heading"]
    assert len(headings) >= 1


# ── SemanticSegmenter naive mode ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_naive_segment_splits_on_blank_lines() -> None:
    from backend.graph.parsers.document import SemanticSegmenter

    seg = SemanticSegmenter(llm_config=None)
    chunks = await seg.segment("First paragraph.\n\nSecond paragraph.")
    assert len(chunks) == 2
    assert chunks[0].content == "First paragraph."
    assert chunks[1].content == "Second paragraph."


@pytest.mark.asyncio
async def test_naive_segment_empty_text_returns_empty() -> None:
    from backend.graph.parsers.document import SemanticSegmenter

    seg = SemanticSegmenter(llm_config=None)
    chunks = await seg.segment("")
    assert chunks == []

    chunks_ws = await seg.segment("   \n\n   ")
    assert chunks_ws == []


@pytest.mark.asyncio
async def test_naive_segment_label_truncated() -> None:
    from backend.graph.parsers.document import SemanticSegmenter

    seg = SemanticSegmenter(llm_config=None)
    long_text = "A" * 200
    chunks = await seg.segment(long_text)
    assert len(chunks) == 1
    assert len(chunks[0].label) <= 82


# ── LLM paths ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_segment_falls_back_on_llm_error() -> None:
    from backend.graph.parsers.document import SemanticSegmenter

    mock_cfg = MagicMock()
    mock_cfg.provider = "ollama"
    mock_cfg.model_for_phase.return_value = "llama3"
    mock_cfg.api_key_env = "OLLAMA_KEY"
    mock_cfg.base_url = "http://localhost:11434"
    mock_cfg.request_timeout = 30

    seg = SemanticSegmenter(llm_config=mock_cfg)

    with patch.object(seg, "_llm_segment", side_effect=RuntimeError("LLM down")):
        chunks = await seg.segment("Hello world.\n\nSecond line.")

    assert len(chunks) >= 1


@pytest.mark.asyncio
async def test_llm_segment_parses_json_response() -> None:
    from backend.graph.parsers.document import SemanticSegmenter

    mock_cfg = MagicMock()
    mock_cfg.provider = "ollama"
    mock_cfg.model_for_phase.return_value = "llama3"
    mock_cfg.api_key_env = "OLLAMA_KEY"
    mock_cfg.base_url = "http://localhost:11434"
    mock_cfg.request_timeout = 30

    seg = SemanticSegmenter(llm_config=mock_cfg)

    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '[{"label": "Requirement", "content": "The system must do X.", "para_type": "paragraph"}]'
    )

    with patch("litellm.acompletion", new=AsyncMock(return_value=mock_response)):
        chunks = await seg._llm_segment("The system must do X.")

    assert len(chunks) == 1
    assert chunks[0].para_type == "paragraph"


@pytest.mark.asyncio
async def test_llm_segment_handles_markdown_fenced_json() -> None:
    from backend.graph.parsers.document import SemanticSegmenter

    mock_cfg = MagicMock()
    mock_cfg.provider = "ollama"
    mock_cfg.model_for_phase.return_value = "llama3"
    mock_cfg.api_key_env = "OLLAMA_KEY"
    mock_cfg.base_url = "http://localhost:11434"
    mock_cfg.request_timeout = 30

    seg = SemanticSegmenter(llm_config=mock_cfg)

    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '```json\n[{"label": "Header", "content": "Overview", "para_type": "heading"}]\n```'
    )

    with patch("litellm.acompletion", new=AsyncMock(return_value=mock_response)):
        chunks = await seg._llm_segment("Overview")

    assert len(chunks) == 1
    assert chunks[0].label == "Header"


@pytest.mark.asyncio
async def test_llm_segment_raises_when_no_json_array() -> None:
    from backend.graph.parsers.document import SemanticSegmenter

    mock_cfg = MagicMock()
    mock_cfg.provider = "ollama"
    mock_cfg.model_for_phase.return_value = "llama3"
    mock_cfg.api_key_env = "OLLAMA_KEY"
    mock_cfg.base_url = "http://localhost:11434"
    mock_cfg.request_timeout = 30

    seg = SemanticSegmenter(llm_config=mock_cfg)

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Sorry, I cannot help with that."

    with patch("litellm.acompletion", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(RuntimeError, match="No JSON array"):
            await seg._llm_segment("Some text")


@pytest.mark.asyncio
async def test_llm_segment_raises_without_api_key_for_openai() -> None:
    import os

    from backend.graph.parsers.document import SemanticSegmenter

    mock_cfg = MagicMock()
    mock_cfg.provider = "openai"
    mock_cfg.model_for_phase.return_value = "gpt-4o"
    mock_cfg.api_key_env = "OPENAI_API_KEY"
    mock_cfg.base_url = "https://api.openai.com/v1"
    mock_cfg.request_timeout = 30

    seg = SemanticSegmenter(llm_config=mock_cfg)

    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeError, match="LLM API key not set"):
            await seg._llm_segment("Some text")
