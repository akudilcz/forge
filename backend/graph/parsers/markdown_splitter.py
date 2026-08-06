"""Deterministic markdown → PARA-tree splitter — the phase 2 primary route.

Whitepapers are markdown, and deterministic header splitting outperforms
LLM/semantic chunking on structured documents (arXiv:2410.13070), so a
DOCUMENT that *qualifies* — at least ``MIN_QUALIFYING_HEADINGS`` ATX
headings outside fenced code blocks — is split into its PARA tree with zero
LLM calls. Documents without that structure take the existing LLM chunking
route (``_doc_chunk`` prompt via the structural loop). This is a documented
primary/exception split, not a fallback: both routes log which one ran and
why (specs/03-build-pipeline.md §Phase 2).

Splitting rules:

* ATX headings (``#`` … ``######``) become heading PARAs, nested by level,
  with **empty content by design** — the analyser's EMPTY_CONTENT and PARA
  dedup exemptions already cover ``para_type == "heading"`` nodes.
* Under each heading, every paragraph, bullet group (a contiguous list,
  including blank-line-separated items), and fenced code block becomes one
  body PARA whose content is **verbatim source text** — never truncated,
  never summarised.
* ``sub_type`` is assigned by conservative, documented heuristics:
  - ``functional`` — normative keywords (shall/must/return(s)/raise(s)/
    never/always) present, or a fenced code block (API-signature blocks are
    normative requirement sources, specs/03 §Phase 3 must-capture).
  - ``constraint`` — limit wording (at most/at least/no more than/
    maximum/minimum/upper/lower bound/big-O) with no normative keyword.
  - ``rationale`` — everything else.
  Misclassification between the non-heading types is safe: downstream
  phases only distinguish heading vs non-heading for requirement sourcing.

Setext headings are deliberately not counted: whitepapers use ATX, and a
setext-only document conservatively takes the LLM route.

Node IDs come from the normal allocator (``graph.allocate_node_id``) and
``derived_from_hash`` stamping happens in ``ProjectGraph.add_node`` as for
every other creation path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.graph.models import GraphNode, LifecycleState, NodeType

if TYPE_CHECKING:
    from backend.graph.engine import ProjectGraph

#: A document qualifies for deterministic parsing at this many ATX headings.
MIN_QUALIFYING_HEADINGS = 2

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_NORMATIVE_RE = re.compile(
    r"\b(shall|must|returns?|raises?|never|always)\b", re.IGNORECASE
)
_CONSTRAINT_RE = re.compile(
    r"(\bat (?:most|least)\b|\bno (?:more|fewer) than\b|\bnot exceed\b"
    r"|\bmaximum\b|\bminimum\b|\b(?:upper|lower) bound\b|\bO\([^)]+\))",
    re.IGNORECASE,
)
_TITLE_MAX = 80


@dataclass(frozen=True)
class ParaBlock:
    """One PARA-to-be, in source order.

    Attributes:
        kind: ``"heading"`` or ``"body"``.
        level: ATX heading level (1-6); 0 for body blocks.
        title: Heading text, or a short label derived from the first line.
        content: ``""`` for headings; verbatim source text for body blocks.
        sub_type: heading | functional | constraint | rationale.
        parent: Index of the parent heading block in the list, or ``None``
            when the block sits directly under the DOCUMENT.
        section_path: ``/``-joined heading path of the enclosing section.
    """

    kind: str
    level: int
    title: str
    content: str
    sub_type: str
    parent: int | None
    section_path: str


# ── Qualification ────────────────────────────────────────────────────────────


def count_markdown_headings(content: str) -> int:
    """Count ATX headings outside fenced code blocks."""
    count = 0
    in_fence = False
    for line in content.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and _ATX_HEADING_RE.match(line):
            count += 1
    return count


def qualifies_for_deterministic_parse(content: str) -> bool:
    """True when the document has enough markdown structure to split
    deterministically (>= ``MIN_QUALIFYING_HEADINGS`` ATX headings)."""
    return count_markdown_headings(content) >= MIN_QUALIFYING_HEADINGS


# ── Tokenizer: lines → (headings | code fences | text runs) ──────────────────


@dataclass(frozen=True)
class _Token:
    """One tokenized markdown segment.

    Attributes:
        kind: ``"heading"``, ``"code"``, or ``"text"``.
        level: Heading level; 0 for non-headings.
        title: Heading title; ``""`` for non-headings.
        text: Verbatim fence text (``code``) or joined run (``text``).
        lines: The raw lines of a ``text`` run; empty otherwise.
    """

    kind: str
    level: int
    title: str
    text: str
    lines: tuple[str, ...]


def _tokenize(content: str) -> list[_Token]:
    """Split lines into heading, code-fence, and text-run tokens.

    Fences are atomic and verbatim (an unterminated fence runs to EOF)."""
    tokens: list[_Token] = []
    text_buffer: list[str] = []
    lines = content.split("\n")
    i = 0

    def flush_text() -> None:
        if any(line.strip() for line in text_buffer):
            joined = "\n".join(text_buffer)
            tokens.append(_Token("text", 0, "", joined, tuple(text_buffer)))
        text_buffer.clear()

    while i < len(lines):
        line = lines[i]
        if _FENCE_RE.match(line):
            flush_text()
            fence_lines = [line]
            i += 1
            while i < len(lines):
                fence_lines.append(lines[i])
                if _FENCE_RE.match(lines[i]):
                    break
                i += 1
            tokens.append(_Token("code", 0, "", "\n".join(fence_lines), ()))
            i += 1
            continue
        heading = _ATX_HEADING_RE.match(line)
        if heading:
            flush_text()
            tokens.append(_Token("heading", len(heading.group(1)), heading.group(2), "", ()))
        else:
            text_buffer.append(line)
        i += 1
    flush_text()
    return tokens


def _text_blocks(lines: list[str]) -> list[str]:
    """Split a text run into verbatim blocks on blank lines.

    A list block absorbs blank lines while the next non-blank line is still a
    list item or an indented continuation, so a loose bullet group stays one
    block (one PARA per bullet *group*, not per bullet).
    """
    blocks: list[str] = []
    current: list[str] = []
    for idx, line in enumerate(lines):
        if line.strip():
            current.append(line)
            continue
        if current and _is_list_block(current) and _next_continues_list(lines, idx):
            current.append(line)
            continue
        if current:
            blocks.append("\n".join(current).strip("\n"))
            current = []
    if current:
        blocks.append("\n".join(current).strip("\n"))
    return [b for b in blocks if b.strip()]


def _is_list_block(block_lines: list[str]) -> bool:
    return bool(_LIST_ITEM_RE.match(block_lines[0]))


def _next_continues_list(lines: list[str], blank_idx: int) -> bool:
    for line in lines[blank_idx + 1 :]:
        if line.strip():
            return bool(_LIST_ITEM_RE.match(line)) or line.startswith(("  ", "\t"))
    return False


# ── Classification and labelling ─────────────────────────────────────────────


def _classify(text: str, is_code: bool) -> str:
    """Conservative sub_type heuristic — see module docstring."""
    if is_code:
        return "functional"
    if _NORMATIVE_RE.search(text):
        return "functional"
    if _CONSTRAINT_RE.search(text):
        return "constraint"
    return "rationale"


def _label(text: str, is_code: bool) -> str:
    """Short title from the first meaningful line (titles may be shortened;
    only *content* is contractually verbatim)."""
    for line in text.split("\n"):
        stripped = _LIST_ITEM_RE.sub("", line.strip()).strip("`#> ").strip()
        if is_code and _FENCE_RE.match(line):
            continue
        if stripped:
            return stripped[:_TITLE_MAX]
    return "Code block" if is_code else text.strip()[:_TITLE_MAX]


# ── Splitter ─────────────────────────────────────────────────────────────────


def split_markdown(content: str) -> list[ParaBlock]:
    """Deterministically split markdown into an ordered list of PARA blocks.

    Headings nest by ATX level; body blocks attach to the innermost open
    heading (or the document root before the first heading).
    """
    blocks: list[ParaBlock] = []
    # Stack of (level, index-into-blocks) for currently open headings.
    heading_stack: list[tuple[int, int]] = []

    def current_parent() -> int | None:
        return heading_stack[-1][1] if heading_stack else None

    def current_path() -> str:
        return blocks[heading_stack[-1][1]].section_path if heading_stack else ""

    for token in _tokenize(content):
        if token.kind == "heading":
            while heading_stack and heading_stack[-1][0] >= token.level:
                heading_stack.pop()
            parent_path = current_path()
            path = f"{parent_path}/{token.title}" if parent_path else token.title
            blocks.append(ParaBlock(
                kind="heading", level=token.level, title=token.title, content="",
                sub_type="heading", parent=current_parent(), section_path=path,
            ))
            heading_stack.append((token.level, len(blocks) - 1))
        elif token.kind == "code":
            blocks.append(ParaBlock(
                kind="body", level=0, title=_label(token.text, True),
                content=token.text, sub_type=_classify(token.text, True),
                parent=current_parent(), section_path=current_path(),
            ))
        else:
            for text in _text_blocks(list(token.lines)):
                blocks.append(ParaBlock(
                    kind="body", level=0, title=_label(text, False), content=text,
                    sub_type=_classify(text, False), parent=current_parent(),
                    section_path=current_path(),
                ))
    return blocks


# ── Writer ───────────────────────────────────────────────────────────────────


async def write_para_tree(
    graph: ProjectGraph, doc_node: GraphNode, changed_by: str
) -> list[GraphNode]:
    """Write the deterministic PARA tree for ``doc_node`` into the graph.

    IDs come from the normal ``PARA`` allocator; ``derived_from_hash`` is
    stamped by ``ProjectGraph.add_node`` as for every creation path.
    Raises loudly when the DOCUMENT has no ``properties['slug']``.
    """
    properties = doc_node.properties or {}
    if "slug" not in properties:
        raise ValueError(
            f"DOCUMENT {doc_node.node_id} has no properties['slug'] — "
            "cannot attribute PARA nodes to a document"
        )
    slug = properties["slug"]

    blocks = split_markdown(doc_node.content or "")
    created: list[GraphNode] = []
    block_ids: list[str] = []
    seq_by_parent: dict[str, int] = {}
    for block in blocks:
        node_id = await graph.allocate_node_id("PARA")
        parent_id = doc_node.node_id if block.parent is None else block_ids[block.parent]
        if parent_id not in seq_by_parent:
            seq_by_parent[parent_id] = 0
        seq_by_parent[parent_id] += 1
        node_props: dict[str, Any] = {
            "document_slug": slug,
            "section_path": block.section_path,
            "seq": seq_by_parent[parent_id],
        }
        if block.kind == "heading":
            node_props["heading_level"] = block.level
        node = GraphNode(
            node_id=node_id, node_type=NodeType.PARA.value, layer=1,
            title=block.title, content=block.content, parent_id=parent_id,
            para_type=block.sub_type, properties=node_props,
            lifecycle=LifecycleState.DRAFT, created_by=changed_by,
        )
        created.append(await graph.add_node(node))
        block_ids.append(node_id)
    return created
