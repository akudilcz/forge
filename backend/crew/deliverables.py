"""Deliverables — Phase 14 deterministic deliverables pack builder.

Assembles a professional documentation pack from the project graph and
workspace files. No LLM calls — purely deterministic extraction,
formatting, and ZIP bundling.

Design reference: design/24_phase_14_build_deliverables.md
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.crew.deliverables_helpers import (
    build_trace_map as _build_trace_map,  # noqa: F401 — re-exported
)
from backend.crew.deliverables_helpers import (
    node_lookup as _node_lookup,
)
from backend.crew.deliverables_helpers import (
    nodes_by_type as _nodes_by_type,
)
from backend.crew.deliverables_helpers import (
    pct as _pct,
)
from backend.crew.deliverables_helpers import (
    req_section as _req_section,
)
from backend.crew.deliverables_helpers import (
    write_file as _write,
)

logger = logging.getLogger(__name__)


async def build_deliverables_pack(graph: Any, workspace: Path) -> Path:
    """Build the deliverables ZIP and return the path to the archive.

    Creates ``workspace/deliverables/`` with rendered docs and copies of
    source/test/config files, then compresses into ``workspace/deliverables.zip``.
    """
    dest = workspace / "deliverables"
    if dest.exists():
        shutil.rmtree(dest)
    docs_dir = dest / "docs"
    docs_dir.mkdir(parents=True)

    # Render each document
    _write(docs_dir / "01-Requirements-Specification.md", _render_requirements(graph))
    _write(docs_dir / "02-Architecture.md", _render_architecture(graph))
    _write(docs_dir / "03-Interface-Specification.md", _render_interfaces(graph))
    _write(docs_dir / "04-Design-Specification.md", _render_design(graph))
    _write(docs_dir / "05-Test-Plan.md", _render_test_plan(graph))
    _write(docs_dir / "06-Traceability-Matrix.md", _render_traceability_matrix(graph))
    _write(docs_dir / "07-Coverage-Report.md", _render_coverage_report(graph))

    # Copy workspace artefacts
    _copy_workspace_files(workspace, dest)

    # README last — it references copied files
    _write(dest / "README.md", _render_readme(graph, workspace, dest))

    # Create ZIP
    zip_path = workspace / "deliverables.zip"
    _create_zip(dest, zip_path)

    logger.info("deliverables.pack_built zip=%s", zip_path)
    return zip_path


def _copy_workspace_files(workspace: Path, dest: Path) -> None:
    """Copy source, tests, and build config into the deliverables tree.

    ``tracing/`` is not optional. Phase 12 seeds that package into the workspace
    and the codegen prompt mandates ``from tracing import traces`` at the top of
    every generated file, so omitting it shipped a bundle whose every module
    raised ImportError on the first line — the delivered code could not be
    imported, let alone tested.
    """
    for dirname in ("src", "tests", "tracing"):
        src_dir = workspace / dirname
        if src_dir.exists():
            shutil.copytree(src_dir, dest / dirname, dirs_exist_ok=True)

    for config_file in (
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Makefile",
        "requirements.txt",
    ):
        src = workspace / config_file
        if src.exists():
            shutil.copy2(src, dest / config_file)


def _create_zip(source_dir: Path, zip_path: Path) -> None:
    """Create a ZIP archive from a directory tree."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                arcname = file_path.relative_to(source_dir.parent)
                zf.write(file_path, arcname)


def _render_readme(graph: Any, workspace: Path, dest: Path) -> str:
    """Generate the project README with manifest and quick-start."""
    project = next(
        (n for n in graph.all_nodes() if n.node_type == "PROJECT"),
        None,
    )
    name = project.title if project else "Project"
    description = project.content.strip() if project and project.content else ""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    # Build manifest from what was actually written
    manifest_lines: list[str] = []
    for path in sorted(dest.rglob("*")):
        if path.is_file() and path.name != "README.md":
            rel = path.relative_to(dest)
            manifest_lines.append(f"- `{rel}`")

    lines = [
        f"# {name}",
        "",
    ]
    if description:
        lines += [description, ""]
    lines += [
        f"*Generated: {timestamp}*",
        "",
        "---",
        "",
        "## Contents",
        "",
        *manifest_lines,
        "",
        "---",
        "",
        "## Quick Start",
        "",
        "```bash",
        "# Install dependencies",
        "pip install -e .",
        "",
        "# Run tests",
        "pytest tests/",
        "```",
        "",
    ]
    return "\n".join(lines)


def _render_requirements(graph: Any) -> str:
    """Render the consolidated requirements specification."""
    lookup = _node_lookup(graph)
    hlrs = _nodes_by_type(graph, "HLR")
    llrs = _nodes_by_type(graph, "LLR")

    lines = [
        "# Requirements Specification",
        "",
        "---",
        "",
        "## High-Level Requirements",
        "",
        f"*{len(hlrs)} requirement(s)*",
        "",
    ]

    for hlr in hlrs:
        lines += _req_section(hlr, lookup, heading="###")

    lines += [
        "---",
        "",
        "## Low-Level Requirements",
        "",
        f"*{len(llrs)} requirement(s)*",
        "",
    ]

    for llr in llrs:
        lines += _req_section(llr, lookup, heading="###")
        # Show traced HLRs
        if llr.trace_to:
            lines.append("**Traces to:**")
            for ref_id in llr.trace_to:
                ref = lookup.get(ref_id)
                title = f" — {ref.title}" if ref and ref.title else ""
                lines.append(f"- {ref_id}{title}")
            lines.append("")
        # Show parent module
        if llr.parent_id:
            parent = lookup.get(llr.parent_id)
            if parent:
                lines.append(f"**Module:** {parent.node_id} — {parent.title}")
                lines.append("")

    return "\n".join(lines)


def _render_architecture(graph: Any) -> str:
    """Render architecture decisions and module decomposition."""
    archs = _nodes_by_type(graph, "ARCHITECTURE")
    modules = _nodes_by_type(graph, "MODULE")

    lines = [
        "# Architecture",
        "",
        "---",
        "",
        "## Architecture Decisions",
        "",
        f"*{len(archs)} decision(s)*",
        "",
    ]
    for arch in archs:
        lines += [f"### {arch.node_id}: {arch.title or '(untitled)'}", ""]
        if arch.content:
            lines += [arch.content.strip(), ""]
        lines += ["---", ""]

    lines += ["## Module Decomposition", "", f"*{len(modules)} module(s)*", ""]
    _render_modules(lines, modules, graph)
    return "\n".join(lines)


def _render_modules(lines: list[str], modules: list[Any], graph: Any) -> None:
    """Render module entries with child type counts."""
    for mod in modules:
        children = [n for n in graph.all_nodes() if n.parent_id == mod.node_id]
        type_counts: dict[str, int] = {}
        for c in children:
            type_counts[c.node_type] = type_counts.get(c.node_type, 0) + 1
        counts_str = ", ".join(f"{v} {k}" for k, v in sorted(type_counts.items()))

        lines += [f"### {mod.node_id}: {mod.title or '(untitled)'}", ""]
        if mod.content:
            lines += [mod.content.strip(), ""]
        if counts_str:
            lines += [f"**Children:** {counts_str}", ""]
        lines += ["---", ""]


def _render_interfaces(graph: Any) -> str:
    """Render public API contract specifications."""
    lookup = _node_lookup(graph)
    contracts = _nodes_by_type(graph, "CONTRACT")

    lines = [
        "# Interface Specification",
        "",
        "---",
        "",
        f"*{len(contracts)} contract(s)*",
        "",
    ]

    for contract in contracts:
        lines += [
            f"## {contract.node_id}: {contract.title or '(untitled)'}",
            "",
        ]
        if contract.parent_id:
            parent = lookup.get(contract.parent_id)
            if parent:
                lines.append(f"**Module:** {parent.node_id} — {parent.title}")
                lines.append("")
        if contract.content:
            lines += [contract.content.strip(), ""]

        # Find sibling DESIGNs that implement against this contract
        if contract.parent_id:
            siblings = [
                n
                for n in graph.all_nodes()
                if n.parent_id == contract.parent_id and n.node_type == "DESIGN"
            ]
            if siblings:
                lines.append("**Implemented by:**")
                for s in sorted(siblings, key=lambda n: n.node_id):
                    lines.append(f"- {s.node_id}: {s.title}")
                lines.append("")

        lines += ["---", ""]

    return "\n".join(lines)


def _render_design(graph: Any) -> str:
    """Render design specifications with traced requirements."""
    lookup = _node_lookup(graph)
    designs = _nodes_by_type(graph, "DESIGN")

    lines = [
        "# Design Specification",
        "",
        "---",
        "",
        f"*{len(designs)} design(s)*",
        "",
    ]

    for design in designs:
        lines += [
            f"## {design.node_id}: {design.title or '(untitled)'}",
            "",
        ]
        if design.parent_id:
            parent = lookup.get(design.parent_id)
            if parent:
                lines.append(f"**Module:** {parent.node_id} — {parent.title}")
                lines.append("")
        if design.content:
            lines += [design.content.strip(), ""]

        # Traced LLRs
        if design.trace_to:
            lines.append("### Requirements Implemented")
            lines.append("")
            for ref_id in design.trace_to:
                ref = lookup.get(ref_id)
                if ref:
                    lines.append(f"**{ref.node_id}: {ref.title or ''}**")
                    if ref.content:
                        lines += ["", ref.content.strip()]
                    lines.append("")
                else:
                    lines.append(f"- {ref_id} *(not found)*")
                    lines.append("")

        # Linked source file
        file_path = design.properties.get("file_path", "")
        if file_path:
            lines += [f"**Source:** `{file_path}`", ""]

        lines += ["---", ""]

    return "\n".join(lines)


def _render_test_plan(graph: Any) -> str:
    """Render test strategy and verification cases."""
    lookup = _node_lookup(graph)
    suites = _nodes_by_type(graph, "SUITE")
    cases = _nodes_by_type(graph, "CASE_HLR", "CASE_LLR")

    lines = ["# Test Plan", "", "---", "", "## Test Strategy", ""]
    _render_test_strategy(lines, suites)
    lines += ["---", "", "## Verification Cases", "", f"*{len(cases)} case(s)*", ""]
    _render_test_cases(lines, cases, lookup)
    return "\n".join(lines)


def _render_test_strategy(lines: list[str], suites: list[Any]) -> None:
    """Render test strategy section from SUITE nodes."""
    if suites:
        for suite in suites:
            lines += [f"### {suite.node_id}: {suite.title or '(untitled)'}", ""]
            if suite.content:
                lines += [suite.content.strip(), ""]
    else:
        lines += ["*No test strategy defined.*", ""]


def _render_test_cases(lines: list[str], cases: list[Any], lookup: dict[str, Any]) -> None:
    """Render individual verification case entries."""
    for case in cases:
        case_type = "HLR" if case.node_type == "CASE_HLR" else "LLR"
        lines += [f"### {case.node_id}: {case.title or '(untitled)'} ({case_type})", ""]
        if case.content:
            lines += [case.content.strip(), ""]
        if case.trace_to:
            lines.append("**Verifies:**")
            for ref_id in case.trace_to:
                ref = lookup.get(ref_id)
                title = f" — {ref.title}" if ref and ref.title else ""
                lines.append(f"- {ref_id}{title}")
            lines.append("")
        file_path = case.properties.get("file_path", "")
        if file_path:
            lines += [f"**Test file:** `{file_path}`", ""]
        lines += ["---", ""]


def _render_traceability_matrix(graph: Any) -> str:
    """Render bidirectional traceability matrix."""
    lookup = _node_lookup(graph)
    hlrs = _nodes_by_type(graph, "HLR")
    llrs = _nodes_by_type(graph, "LLR")
    designs = _nodes_by_type(graph, "DESIGN")
    cases = _nodes_by_type(graph, "CASE_HLR", "CASE_LLR")

    lines = ["# Traceability Matrix", "", "---", ""]
    _render_forward_trace(lines, hlrs, llrs, designs, cases)
    _render_reverse_trace(lines, designs, lookup)
    _render_trace_gaps(lines, llrs, designs, cases)
    return "\n".join(lines)


def _parent_hlr_ids(llr: Any) -> set[str]:
    """HLR ids an LLR belongs to, by containment OR by trace.

    Phase 7 creates LLRs with ``parent_id=<hlr_id>`` and leaves ``trace_to``
    empty, so joining these tables on ``trace_to`` alone produced an entirely
    blank Traceability Matrix in every real build — every HLR row rendered as
    ``| HLR-0001 | — | — | — | — | — |`` while the Gaps section cheerfully
    reported "No traceability gaps detected". Since this is FORGE's headline
    guarantee, the join accepts either representation rather than depending on
    which one a given phase happened to populate.
    """
    ids = {llr.parent_id} if llr.parent_id else set()
    return ids | set(llr.trace_to or [])


def _render_forward_trace(
    lines: list[str],
    hlrs: list[Any],
    llrs: list[Any],
    designs: list[Any],
    cases: list[Any],
) -> None:
    """Build the forward trace table (HLR → LLR → DESIGN → CASE)."""
    lines += [
        "## Forward Trace (Requirements to Implementation)",
        "",
        "| HLR | LLR | DESIGN | Source File | CASE | Test File |",
        "|-----|-----|--------|-------------|------|-----------|",
    ]
    for hlr in hlrs:
        child_llrs = [lr for lr in llrs if hlr.node_id in _parent_hlr_ids(lr)]
        if not child_llrs:
            lines.append(f"| {hlr.node_id} | — | — | — | — | — |")
            continue
        for llr in child_llrs:
            child_designs = [d for d in designs if llr.node_id in (d.trace_to or [])]
            child_cases = [c for c in cases if llr.node_id in (c.trace_to or [])]
            d_str = ", ".join(d.node_id for d in child_designs) or "—"
            src = (
                ", ".join(
                    f"`{d.properties.get('file_path', '?')}`"
                    for d in child_designs
                    if d.properties.get("file_path")
                )
                or "—"
            )
            c_str = ", ".join(c.node_id for c in child_cases) or "—"
            t_str = (
                ", ".join(
                    f"`{c.properties.get('file_path', '?')}`"
                    for c in child_cases
                    if c.properties.get("file_path")
                )
                or "—"
            )
            lines.append(f"| {hlr.node_id} | {llr.node_id} | {d_str} | {src} | {c_str} | {t_str} |")


def _render_reverse_trace(
    lines: list[str],
    designs: list[Any],
    lookup: dict[str, Any],
) -> None:
    """Build the reverse trace table (DESIGN → LLR → HLR)."""
    lines += [
        "",
        "---",
        "",
        "## Reverse Trace (Implementation to Requirements)",
        "",
        "| Source File | DESIGN | LLR(s) | HLR(s) |",
        "|-------------|--------|--------|--------|",
    ]
    for design in designs:
        src = design.properties.get("file_path", "—")
        src_display = f"`{src}`" if src != "—" else "—"
        traced_llrs = design.trace_to or []
        traced_hlrs: set[str] = set()
        for llr_id in traced_llrs:
            llr_node = lookup.get(llr_id)
            if llr_node:
                traced_hlrs |= _parent_hlr_ids(llr_node)
        llr_str = ", ".join(sorted(traced_llrs)) or "—"
        hlr_str = ", ".join(sorted(traced_hlrs)) or "—"
        lines.append(f"| {src_display} | {design.node_id} | {llr_str} | {hlr_str} |")


def _render_trace_gaps(
    lines: list[str],
    llrs: list[Any],
    designs: list[Any],
    cases: list[Any],
) -> None:
    """Detect and render traceability gaps."""
    lines += ["", "---", "", "## Gaps", ""]

    unimplemented = [
        lr for lr in llrs if not any(lr.node_id in (d.trace_to or []) for d in designs)
    ]
    ungenerated = [d for d in designs if not d.properties.get("file_path")]
    untested = [c for c in cases if not c.properties.get("file_path")]

    for label, items in [
        ("Unimplemented LLRs", unimplemented),
        ("DESIGNs without source file", ungenerated),
        ("CASEs without test file", untested),
    ]:
        if items:
            lines.append(f"**{label}** ({len(items)}):")
            for item in items:
                lines.append(f"- {item.node_id}: {item.title}")
            lines.append("")

    if not (unimplemented or ungenerated or untested):
        lines.append("*No traceability gaps detected.*")
        lines.append("")


def _render_coverage_report(graph: Any) -> str:
    """Render coverage summary with metrics."""
    metrics = _compute_coverage_metrics(graph)
    lines = [
        "# Coverage Report",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Requirement coverage | {metrics['req_covered']}/{metrics['req_total']}"
        f" ({_pct(metrics['req_covered'], metrics['req_total'])}) |",
        f"| Function coverage | {metrics['fn_traced']}/{metrics['fn_total']}"
        f" ({_pct(metrics['fn_traced'], metrics['fn_total'])}) |",
        f"| Test pass rate | {metrics['passed']}/{metrics['total_results']}"
        f" ({_pct(metrics['passed'], metrics['total_results'])}) |",
        f"| Total HLRs | {metrics['hlr_count']} |",
        f"| Total LLRs | {metrics['req_total']} |",
        f"| Total DESIGNs | {metrics['design_count']} |",
        f"| Total CASEs | {metrics['case_count']} |",
        "",
    ]
    _render_gap_breakdown(lines, graph)
    return "\n".join(lines)


def _compute_coverage_metrics(graph: Any) -> dict[str, int]:
    """Compute coverage metrics from graph nodes."""
    llrs = _nodes_by_type(graph, "LLR")
    designs = _nodes_by_type(graph, "DESIGN")
    cases = _nodes_by_type(graph, "CASE_HLR", "CASE_LLR")
    results = _nodes_by_type(graph, "RESULT")

    llrs_with_case = {ref for c in cases for ref in (c.trace_to or [])}
    fn_traced = sum(d.properties.get("trace_coverage", {}).get("traced", 0) for d in designs)
    fn_total = sum(d.properties.get("trace_coverage", {}).get("total", 0) for d in designs)
    # `record_results` stores the parser's own vocabulary — "passed"/"failed"/
    # "error"/"skipped" (result_recorder.py:176). Comparing against "pass" never
    # matched, so the shipped Coverage Report always claimed a 0% pass rate no
    # matter how many tests passed.
    passed = sum(1 for r in results if r.properties.get("status") == "passed")

    return {
        "req_covered": len([lr for lr in llrs if lr.node_id in llrs_with_case]),
        "req_total": len(llrs),
        "fn_traced": fn_traced,
        "fn_total": fn_total,
        "passed": passed,
        "total_results": len(results),
        "hlr_count": len(_nodes_by_type(graph, "HLR")),
        "design_count": len(designs),
        "case_count": len(cases),
    }


def _render_gap_breakdown(lines: list[str], graph: Any) -> None:
    """Render outstanding gaps section."""
    from backend.analysis.gap_analyser import GapAnalyser

    gaps = GapAnalyser().analyse(graph)
    if gaps:
        lines += [
            "---",
            "",
            "## Outstanding Gaps",
            "",
            f"*{len(gaps)} gap(s)*",
            "",
            "| Type | Node | Description |",
            "|------|------|-------------|",
        ]
        for g in gaps:
            lines.append(f"| {g.type.value} | {g.node_id} | {g.description} |")
        lines.append("")
    else:
        lines += ["---", "", "*No outstanding gaps.*", ""]
