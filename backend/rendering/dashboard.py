"""Dashboard — Phase 11 deterministic document renderer.

Generates one Markdown file per phase (3–10) into [workspace]/docs/,
summarising the graph nodes produced by each phase. No LLM calls —
purely deterministic extraction from the project graph.

The rendered docs are the primary context for Phase 12 code generation
agents. Each node section inlines the full text of traced requirements
so agents never have to cross-reference documents.

Design reference: specs/03-build-pipeline.md
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Phase metadata: (phase_number, filename, title, node_types)
_PHASE_DOCS: list[tuple[int, str, str, list[str]]] = [
    (3, "03-HLR.md", "High-Level Requirements", ["HLR"]),
    (4, "04-Architecture.md", "Architecture", ["ARCHITECTURE"]),
    (5, "05-Modules.md", "Modules", ["MODULE"]),
    (6, "06-Contracts.md", "Contracts", ["CONTRACT"]),
    (7, "07-LLR.md", "Low-Level Requirements", ["LLR"]),
    (8, "08-Design.md", "Design Specifications", ["DESIGN"]),
    (9, "09-Test-Suite.md", "Test Suite Strategy", ["SUITE"]),
    (10, "10-Verification.md", "Verification Cases", ["CASE_HLR", "CASE_LLR"]),
]


async def render_dashboard(graph: Any, workspace: Path) -> list[Path]:
    """Render phase 3–10 docs into workspace/docs/ and return paths written."""
    docs_dir = workspace / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for phase_num, filename, title, node_types in _PHASE_DOCS:
        nodes = _nodes_for_types(graph, node_types)
        content = _render_phase_doc(phase_num, title, nodes, graph)
        path = docs_dir / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
        logger.info("dashboard.rendered %s (%d nodes)", filename, len(nodes))

    return written


def _nodes_for_types(graph: Any, node_types: list[str]) -> list[Any]:
    """Collect all nodes matching the given types, sorted by node_id."""
    return sorted(
        (n for n in graph.all_nodes() if n.node_type in node_types),
        key=lambda n: n.node_id,
    )


def _render_phase_doc(
    phase: int,
    title: str,
    nodes: list[Any],
    graph: Any,
) -> str:
    """Render a single phase document as Markdown."""
    lines: list[str] = [
        f"# Phase {phase} — {title}",
        "",
        f"*{len(nodes)} node(s)*",
        "",
        "---",
        "",
    ]

    if not nodes:
        lines.append("*No nodes produced for this phase.*\n")
        return "\n".join(lines)

    for node in nodes:
        lines.extend(_render_node_section(node, graph))

    return "\n".join(lines)


def _render_node_section(node: Any, graph: Any) -> list[str]:
    """Render a single node with full inlined context."""
    lines: list[str] = [
        f"## {node.node_id}: {node.title or '(untitled)'}",
        "",
    ]

    # Parent context (MODULE for DESIGNs, etc.)
    if node.parent_id:
        parent = graph.node_sync(node.parent_id)
        if parent:
            lines.append(f"**Parent:** {parent.node_id} — {parent.title}")
            lines.append("")

    # Node content — the core specification
    if node.content:
        lines.append(node.content.strip())
        lines.append("")

    # Inline traced requirements with full text
    if node.trace_to:
        lines.extend(_render_traced_requirements(node, graph))

    # For DESIGN nodes: inline sibling CONTRACTs (the public interface)
    if node.node_type == "DESIGN" and node.parent_id:
        lines.extend(_render_sibling_contracts(node.parent_id, graph))

    lines.append("---")
    lines.append("")
    return lines


def _render_traced_requirements(
    node: Any,
    graph: Any,
) -> list[str]:
    """Inline the full text of every traced requirement."""
    lines: list[str] = []
    label = _trace_label(node.node_type)
    lines.append(f"### {label}")
    lines.append("")

    for ref_id in node.trace_to:
        ref = graph.node_sync(ref_id)
        if not ref:
            lines.append(f"- **{ref_id}**: *(not found)*")
            continue
        lines.append(f"**{ref.node_id}: {ref.title or ''}**")
        if ref.content:
            lines.append("")
            lines.append(ref.content.strip())
        lines.append("")

    return lines


def _trace_label(node_type: str) -> str:
    """Human-readable label for the traced requirements section."""
    labels = {
        "LLR": "Traced HLR (High-Level Requirements)",
        "DESIGN": "Requirements This Design Implements",
        "CASE_HLR": "Requirements Under Test",
        "CASE_LLR": "Requirements Under Test",
    }
    return labels.get(node_type, "Traced Requirements")


def _render_sibling_contracts(
    parent_id: str,
    graph: Any,
) -> list[str]:
    """Inline CONTRACT siblings — the public interface specification."""
    contracts = [c for c in graph.children_sync(parent_id) if c.node_type == "CONTRACT"]
    if not contracts:
        return []

    lines: list[str] = [
        "### Public Interface (CONTRACT)",
        "",
    ]
    for contract in contracts:
        lines.append(f"**{contract.node_id}: {contract.title or ''}**")
        if contract.content:
            lines.append("")
            lines.append(contract.content.strip())
        lines.append("")

    return lines
