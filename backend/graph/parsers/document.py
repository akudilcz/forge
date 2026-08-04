"""DocumentParser — converts markdown documents into DOCUMENT/PARA graph nodes.

Produces nodes aligned with the FORGE Graph Engine node model:

  DOCUMENT  — the document itself (sequential ID from pg_node_sequences)
  PARA      — a paragraph within the document (sequential ID per PARA)

Node IDs follow the global TYPE-NNNN scheme (e.g. DOCUMENT-0001,
PARA-0003).  The slug is stored in DOCUMENT.properties["slug"].

Para properties include:
  para_type: heading | paragraph | note | list_item | table_row | figure
  document_slug: the owning document slug
  section_path: the full heading path to this paragraph's section

Structural containment uses parent_id:
  - DOCUMENT node is the root (parent_id = project node or None).
  - Top-level PARA(heading) nodes: parent_id = doc_node_id
  - Nested PARA(heading) nodes: parent_id = parent section node_id
  - Body PARA nodes: parent_id = current section node_id

Semantic paragraph boundary detection uses an LLM call when available;
falls back to blank-line splitting for tests and offline use.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.graph.models import GraphNode, LifecycleState, NodeType

if TYPE_CHECKING:
    from backend.config.models import LLMConfig
    from backend.graph.engine import ProjectGraph

_log = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Outcome of a single DocumentParser.parse() call.

    Attributes:
        created: Nodes added to the graph for the first time.
        updated: Nodes whose content changed (tuples of node + impact set).
        purged: Node IDs deleted because they no longer exist in the document.
    """

    created: list[GraphNode] = field(default_factory=list)
    updated: list[tuple[GraphNode, Any]] = field(default_factory=list)
    purged: list[str] = field(default_factory=list)


# ── Semantic segmenter ────────────────────────────────────────────────────────

_SEGMENT_PROMPT = """\
You are a requirements-engineering expert analysing a specification document.

Your task: split the TEXT below into semantic paragraphs.

A semantic paragraph is a coherent, self-contained unit of meaning that covers
exactly ONE of:
- A requirement or constraint (something the system must do / not do)
- Context, rationale, or background (why something is needed)
- A definition or description (what a concept means)

Rules:
- Preserve the original wording verbatim in "content".
- Do NOT merge unrelated sentences just because they are adjacent.
- Do NOT split a single coherent idea across multiple paragraphs.
- A "label" is a short noun-phrase title (max 80 chars) capturing the core idea.
Return ONLY a valid JSON array; no prose before or after it.
Each element: {{"label": "<short title>", "content": "<verbatim text>", "para_type": "<type>"}}
Valid para_type values: heading, paragraph, note, list_item, table_row, figure

TEXT:
---
{text}
---
"""


@dataclass
class _Chunk:
    """A single semantic paragraph produced by the segmenter.

    Attributes:
        label: Short noun-phrase title.
        content: Verbatim paragraph text.
        para_type: Classification of the paragraph content.
    """

    label: str
    content: str
    para_type: str = "paragraph"


class SemanticSegmenter:
    """Call the configured LLM to segment a block of text into semantic chunks.

    Falls back to naive blank-line splitting if the LLM is unavailable or
    returns unparseable JSON.
    """

    def __init__(self, llm_config: LLMConfig | None = None) -> None:
        self._cfg = llm_config

    async def segment(self, text: str) -> list[_Chunk]:
        """Segment text into semantic chunks (async, non-blocking)."""
        stripped = text.strip()
        if not stripped:
            return []

        if self._cfg is not None:
            try:
                return await self._llm_segment(stripped)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "semantic_segmenter.llm_failed — falling back to blank-line split",
                    extra={"error": str(exc)},
                )

        return self._naive_segment(stripped)

    async def _llm_segment(self, text: str) -> list[_Chunk]:
        """Call the LLM asynchronously and parse its JSON response."""
        import litellm

        cfg = self._cfg
        assert cfg is not None

        model = cfg.model_for_phase(1)

        api_key = os.environ.get(cfg.api_key_env, "") or ""
        if not api_key:
            if cfg.provider == "ollama":
                api_key = "ollama"
            else:
                raise RuntimeError(
                    f"LLM API key not set: expected env var {cfg.api_key_env!r} "
                    f"for provider {cfg.provider!r}. "
                    "Set the env var before starting Forge."
                )

        # Add litellm provider prefix for routing.
        from backend.tools.analysis import _litellm_model

        llm_provider = getattr(cfg, "provider", "")
        effective_model = _litellm_model(model, llm_provider, cfg.base_url)

        kwargs: dict[str, Any] = {
            "model": effective_model,
            "api_key": api_key,
            "messages": [{"role": "user", "content": _SEGMENT_PROMPT.format(text=text)}],
            "temperature": getattr(cfg.options, "temperature", 0.1),
            "timeout": cfg.request_timeout,
        }
        # OpenRouter: litellm handles base_url via env var; others need it explicit.
        if cfg.base_url and llm_provider != "openrouter":
            kwargs["base_url"] = cfg.base_url

        response = await litellm.acompletion(**kwargs)
        raw = response.choices[0].message.content or ""

        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            raise RuntimeError(f"No JSON array in LLM response: {raw[:200]}")
        parsed = json.loads(raw[start : end + 1])
        if not isinstance(parsed, list):
            raise RuntimeError(f"LLM returned non-list: {type(parsed)}")

        chunks: list[_Chunk] = []
        for item in parsed:
            label = str(item.get("label", "")).strip()
            content = str(item.get("content", "")).strip()
            para_type = str(item.get("para_type", "paragraph")).strip()
            if content:
                chunks.append(_Chunk(
                    label=label or content[:80],
                    content=content,
                    para_type=para_type,
                ))

        if not chunks:
            raise RuntimeError("LLM returned an empty segment list")

        return chunks

    @staticmethod
    def _naive_segment(text: str) -> list[_Chunk]:
        """Fallback: split on blank lines."""
        chunks: list[_Chunk] = []
        for block in re.split(r"\n\s*\n", text):
            content = block.strip()
            if not content:
                continue
            label = (content[:80] + "…") if len(content) > 80 else content
            sentence_end = re.search(r"[.!?]", label)
            if sentence_end and sentence_end.start() > 0:
                label = label[: sentence_end.start() + 1]
            chunks.append(_Chunk(label=label.strip(), content=content, para_type="paragraph"))
        return chunks


# ── Section scanner ───────────────────────────────────────────────────────────


def _scan_sections(content: str) -> list[dict[str, Any]]:
    """Pass 1: scan document for headings and body text sections.

    Returns a flat list of section dicts WITHOUT node IDs.
    Heading dicts include ``parent_heading_path`` for parent tracking.
    Body dicts include ``section_path`` for parent lookup during write.
    """
    lines = content.split("\n")
    sections: list[dict[str, Any]] = []
    body_buffer: list[str] = []
    heading_stack: list[tuple[int, str]] = []  # (level, heading_path)

    def _current_path() -> str:
        return heading_stack[-1][1] if heading_stack else ""

    def flush_body() -> None:
        text = "\n".join(body_buffer).strip()
        body_buffer.clear()
        if text:
            sections.append({"kind": "body", "section_path": _current_path(), "raw_text": text})

    for idx, line in enumerate(lines):
        m_atx = re.match(r"^(#{1,6})\s+(.+)$", line)
        next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        m_setext_eq = bool(re.match(r"^=+\s*$", next_line)) and len(next_line.strip()) >= 2
        m_setext_dash = bool(re.match(r"^-+\s*$", next_line)) and len(next_line.strip()) >= 2
        is_setext = (m_setext_eq or m_setext_dash) and bool(line.strip())
        prev_line = lines[idx - 1] if idx > 0 else ""
        is_underline_row = (
            bool(re.match(r"^[=\-]+\s*$", line))
            and len(line.strip()) >= 2
            and bool(prev_line.strip())
        )

        if is_underline_row:
            continue

        m = m_atx
        if m:
            flush_body()
            level, title = len(m.group(1)), m.group(2).strip()
        elif is_setext:
            flush_body()
            level = 1 if m_setext_eq else 2
            title = re.sub(r"^\d+(\.\d+)*[.\s]+", "", line.strip()).strip()
        else:
            body_buffer.append(line)
            continue

        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        parent_path = _current_path()
        heading_path = f"{parent_path}/{title}" if parent_path else title
        heading_stack.append((level, heading_path))
        sections.append({
            "kind": "heading",
            "level": level,
            "title": title,
            "heading_path": heading_path,
            "parent_heading_path": parent_path,
        })

    flush_body()
    return sections


# ── Document parser ───────────────────────────────────────────────────────────


class DocumentParser:
    """Parse a markdown document into a hierarchy of DOCUMENT and PARA nodes.

    Node IDs use the global sequential format TYPE-NNNN allocated by the
    graph engine (e.g. DOCUMENT-0001, PARA-0003).

    Attributes:
        _segmenter: The SemanticSegmenter used for body text segmentation.
    """

    def __init__(self, llm_config: LLMConfig | None = None) -> None:
        self._segmenter = SemanticSegmenter(llm_config)

    async def parse(
        self,
        document_slug: str,
        content: str,
        graph: ProjectGraph,
        changed_by: str,
        doc_type: str = "reference",
        doc_order: int = 99,
        project_node_id: str | None = None,
    ) -> ParseResult:
        """Parse content and upsert the resulting nodes into graph."""
        result = ParseResult()
        doc_node_id = await self._upsert_document(
            document_slug, content, graph, changed_by,
            doc_type, doc_order, project_node_id, result,
        )
        await self._purge_para_children(doc_node_id, graph, result)
        sections = await self._build_sections(content, document_slug)
        await self._write_para_nodes(sections, doc_node_id, document_slug, graph, changed_by, result)
        return result

    async def _upsert_document(
        self,
        document_slug: str,
        content: str,
        graph: ProjectGraph,
        changed_by: str,
        doc_type: str,
        doc_order: int,
        project_node_id: str | None,
        result: ParseResult,
    ) -> str:
        """Find or create the DOCUMENT node; return its node_id."""
        doc_node = await graph.find_node_by_slug(document_slug)
        if doc_node is None:
            node_id = await graph.allocate_node_id("DOCUMENT")
            doc_node = GraphNode(
                node_id=node_id, node_type=NodeType.DOCUMENT.value, layer=1,
                title=document_slug.replace("-", " ").title(),
                content=content, parent_id=project_node_id,
                properties={"doc_type": doc_type, "doc_order": doc_order, "slug": document_slug},
                lifecycle=LifecycleState.DRAFT, created_by=changed_by,
            )
            doc_node = await graph.add_node(doc_node)
            result.created.append(doc_node)
        elif doc_node.content != content or (project_node_id and doc_node.parent_id is None):
            doc_node.content = content
            if project_node_id and doc_node.parent_id is None:
                doc_node.parent_id = project_node_id
            await graph.add_node(doc_node)
        return doc_node.node_id

    async def _purge_para_children(
        self, doc_node_id: str, graph: ProjectGraph, result: ParseResult
    ) -> None:
        """Delete all existing PARA children of the document node."""
        for child in await graph.children(doc_node_id):
            await graph.delete_children_recursive(child.node_id)
            await graph.delete_node(child.node_id)
            result.purged.append(child.node_id)

    async def _build_sections(self, content: str, slug: str) -> list[dict[str, Any]]:
        """Pass 1 + 2: structural scan followed by async LLM body segmentation."""
        import asyncio

        sections = _scan_sections(content)
        body_indices = [i for i, s in enumerate(sections) if s["kind"] == "body"]
        if body_indices:
            chunks_list = await asyncio.gather(
                *[self._segmenter.segment(sections[i]["raw_text"]) for i in body_indices]
            )
            for i, bi in enumerate(body_indices):
                sections[bi]["chunks"] = chunks_list[i]
        return sections

    async def _write_para_nodes(
        self,
        sections: list[dict[str, Any]],
        doc_node_id: str,
        slug: str,
        graph: ProjectGraph,
        changed_by: str,
        result: ParseResult,
    ) -> None:
        """Pass 3: allocate sequential IDs and write PARA nodes to the graph."""
        heading_id_map: dict[str, str] = {}
        seq_by_section: dict[str, int] = {}
        for sec in sections:
            if sec["kind"] == "heading":
                node_id = await graph.allocate_node_id("PARA")
                heading_id_map[sec["heading_path"]] = node_id
                parent_id = heading_id_map.get(sec["parent_heading_path"], doc_node_id)
                node = GraphNode(
                    node_id=node_id, node_type=NodeType.PARA.value, layer=1,
                    title=sec["title"], content=sec["title"],
                    parent_id=parent_id,
                    para_type="heading",
                    properties={"heading_level": sec["level"],
                                "document_slug": slug, "section_path": sec["heading_path"], "seq": 0},
                    lifecycle=LifecycleState.DRAFT, created_by=changed_by,
                )
                result.created.append(await graph.add_node(node))
            else:
                sp = sec["section_path"]
                parent_id = heading_id_map.get(sp, doc_node_id)
                for chunk in sec.get("chunks", []):
                    seq = seq_by_section.get(sp, 0) + 1
                    seq_by_section[sp] = seq
                    node_id = await graph.allocate_node_id("PARA")
                    node = GraphNode(
                        node_id=node_id, node_type=NodeType.PARA.value, layer=1,
                        title=chunk.label, content=chunk.content,
                        parent_id=parent_id,
                        para_type=chunk.para_type,
                        properties={"document_slug": slug,
                                    "section_path": sp, "seq": seq},
                        lifecycle=LifecycleState.DRAFT, created_by=changed_by,
                    )
                    result.created.append(await graph.add_node(node))
