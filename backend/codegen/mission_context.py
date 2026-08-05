"""Mission context assembly — full-graph prompt context for the mission agent.

Builds the single prompt string that gives the Phase 12 mission agent
complete visibility: graph nodes (architecture, modules, contracts,
designs, requirements, test strategy, cases), rendered docs, the tracing
decorator source, and any existing workspace files.

Design reference: design/22_phase_12_generate_code.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from backend.graph.engine import ProjectGraph


def build_mission_context(graph: ProjectGraph, workspace: Path) -> str:
    """Assemble all graph context the agent needs into one prompt string."""
    nodes = graph.all_nodes()
    sections: list[str] = []

    node_groups: dict[str, list[Any]] = {}
    for n in nodes:
        node_groups.setdefault(n.node_type, []).append(n)

    _format_graph_nodes(graph, nodes, node_groups, sections)
    _include_rendered_docs(workspace, sections)
    _include_tracing_source(workspace, sections)
    _include_existing_files(workspace, sections)

    return "\n\n---\n\n".join(sections)


def _format_graph_nodes(
    graph: ProjectGraph,
    nodes: list[Any],
    node_groups: dict[str, list[Any]],
    sections: list[str],
) -> None:
    """Format architecture, module, design, requirement, and case nodes."""
    for arch in node_groups.get("ARCHITECTURE", []):
        sections.append(f"## ARCHITECTURE: {arch.title}\n{arch.content}")

    for mod in node_groups.get("MODULE", []):
        sections.append(f"## MODULE: {mod.node_id} — {mod.title}\n{mod.content}")
        for child in graph.children_sync(mod.node_id):
            if child.node_type == "CONTRACT":
                sections.append(f"### CONTRACT: {child.node_id}\n{child.content}")

    for design in node_groups.get("DESIGN", []):
        traces = ", ".join(design.trace_to) if design.trace_to else "none"
        sections.append(
            f"## DESIGN: {design.node_id} — {design.title}\nTraces to: {traces}\n\n{design.content}"
        )

    for ntype, heading in [("LLR", "LOW-LEVEL REQUIREMENTS"), ("HLR", "HIGH-LEVEL REQUIREMENTS")]:
        items = node_groups.get(ntype, [])
        if items:
            lines = [f"- {n.node_id}: {n.content}" for n in items]
            sections.append(f"## {heading}\n" + "\n".join(lines))

    # SUITE (test strategy) — scope, approach, tools, entry/exit criteria.
    # Grounds the agent's test-writing in the documented strategy rather
    # than making them invent categories on the fly.
    for suite in node_groups.get("SUITE", []):
        sections.append(
            f"## TEST STRATEGY (SUITE {suite.node_id}): {suite.title}\n{suite.content}"
        )

    cases = [n for n in nodes if n.node_type in ("CASE_HLR", "CASE_LLR")]
    if cases:
        parts = []
        for c in cases:
            traces = ", ".join(c.trace_to) if c.trace_to else "none"
            parts.append(f"### {c.node_id}: {c.title}\nTraces to: {traces}\n\n{c.content}")
        sections.append("## TEST CASES\n" + "\n\n".join(parts))


def _include_rendered_docs(workspace: Path, sections: list[str]) -> None:
    """Include key rendered docs (LLR, Design) inline."""
    docs_dir = workspace / "docs"
    if not docs_dir.is_dir():
        return
    useful_docs = ["07-LLR.md", "08-Design.md", "06-Contracts.md", "09-Test-Suite.md"]
    for name in useful_docs:
        doc = docs_dir / name
        if doc.is_file():
            try:
                content = doc.read_text(encoding="utf-8")
                sections.append(f"## RENDERED DOC: {name}\n{content}")
            except OSError:
                pass


def _include_tracing_source(workspace: Path, sections: list[str]) -> None:
    """Include the tracing decorator source so the agent knows the API."""
    decorator = workspace / "tracing" / "decorator.py"
    if decorator.is_file():
        try:
            content = decorator.read_text(encoding="utf-8")
            sections.append(
                f"## TRACING DECORATOR (tracing/decorator.py)\n```python\n{content}\n```"
            )
        except OSError:
            pass


def _include_existing_files(workspace: Path, sections: list[str]) -> None:
    """Include contents of existing src/ and tests/ files."""
    for subdir in ("src", "tests"):
        target = workspace / subdir
        if not target.is_dir():
            continue
        for f in sorted(target.glob("*.py")):
            if f.name == "__pycache__":
                continue
            try:
                content = f.read_text(encoding="utf-8")
                if len(content) > 20_000:
                    content = content[:20_000] + f"\n... (truncated, {len(content)} chars total)"
                sections.append(f"## EXISTING FILE: {subdir}/{f.name}\n```python\n{content}\n```")
            except OSError:
                pass
