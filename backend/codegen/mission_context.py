"""Mission context assembly — slim up-front prompt for the mission agent.

Builds the single prompt string that gives the Phase 12 mission agent
its working context. Authoritative graph content (architecture, modules,
contracts, designs, requirements, test strategy, cases) and the tracing
decorator source are inlined; anything the agent can cheaply re-fetch
with its own tools is only *listed*: rendered docs (name + size, read
via ``read_docs``) and existing workspace files (path + line/char
counts, read via ``file_read``). Measured live, the old
everything-inline context was 52-179k tokens re-sent on every one of
140-250 LLM calls; the slim context targets ≤30k on a merge_sort-sized
build. The assembled token count is logged loudly.

Design reference: specs/03-build-pipeline.md §Context Pre-Loading
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.prompting.context_budget import count_tokens
from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from pathlib import Path

    from backend.graph.engine import ProjectGraph


def build_mission_context(graph: ProjectGraph, workspace: Path) -> str:
    """Assemble the mission agent's initial context into one prompt string."""
    nodes = graph.all_nodes()
    sections: list[str] = []

    node_groups: dict[str, list[Any]] = {}
    for n in nodes:
        node_groups.setdefault(n.node_type, []).append(n)

    _format_graph_nodes(graph, nodes, node_groups, sections)
    _include_rendered_docs(workspace, sections)
    _include_tracing_source(workspace, sections)
    _include_existing_files(workspace, sections)

    context = "\n\n---\n\n".join(sections)
    forge_logger.emit(
        "INFO",
        "CGEN ",
        f"Mission context assembled: {count_tokens(context)} tokens "
        f"across {len(sections)} section(s)",
    )
    return context


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
        sections.append(f"## TEST STRATEGY (SUITE {suite.node_id}): {suite.title}\n{suite.content}")

    cases = [n for n in nodes if n.node_type in ("CASE_HLR", "CASE_LLR")]
    if cases:
        parts = []
        for c in cases:
            traces = ", ".join(c.trace_to) if c.trace_to else "none"
            parts.append(f"### {c.node_id}: {c.title}\nTraces to: {traces}\n\n{c.content}")
        sections.append("## TEST CASES\n" + "\n\n".join(parts))


def _include_rendered_docs(workspace: Path, sections: list[str]) -> None:
    """List rendered docs (name + size); bodies are read via read_docs.

    Doc bodies duplicate the graph nodes already inlined above, so
    inlining them doubled the up-front context for no new information.
    """
    docs_dir = workspace / "docs"
    if not docs_dir.is_dir():
        return
    lines: list[str] = []
    for doc in sorted(docs_dir.glob("*.md")):
        try:
            size = doc.stat().st_size
        except OSError:
            continue
        lines.append(f"- {doc.name} ({size} chars)")
    if lines:
        sections.append(
            "## RENDERED DOCS (bodies not inlined — use read_docs if you "
            "need one; the graph sections above are authoritative)\n" + "\n".join(lines)
        )


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
    """List existing src/ and tests/ files; bodies are read via file_read.

    On re-runs the agent reads only the files it actually needs to
    touch, instead of every body being re-sent on every LLM call.
    """
    lines: list[str] = []
    for subdir in ("src", "tests"):
        target = workspace / subdir
        if not target.is_dir():
            continue
        for f in sorted(target.glob("*.py")):
            try:
                content = f.read_text(encoding="utf-8")
            except OSError:
                continue
            lines.append(
                f"- {subdir}/{f.name} ({len(content.splitlines())} lines, {len(content)} chars)"
            )
    if lines:
        sections.append(
            "## EXISTING FILES (contents not inlined — use file_read "
            "before modifying any of them)\n" + "\n".join(lines)
        )
