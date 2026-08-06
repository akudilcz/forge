"""Deliverables reports — traceability matrix and coverage report renderers.

Deterministic renderers for the two analytical documents of the Phase 14
deliverables pack (06-Traceability-Matrix.md and 07-Coverage-Report.md).
Re-exported by :mod:`backend.rendering.deliverables`, which remains the
public facade.

Design reference: specs/09-deliverables.md
"""

from __future__ import annotations

from typing import Any

from backend.rendering.deliverables_helpers import (
    node_lookup as _node_lookup,
)
from backend.rendering.deliverables_helpers import (
    nodes_by_type as _nodes_by_type,
)
from backend.rendering.deliverables_helpers import (
    pct as _pct,
)


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
