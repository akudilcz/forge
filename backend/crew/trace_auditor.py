"""Trace auditor — LLM-assisted verification and annotation of LLR traces.

After code generation, this module analyses each file for untraced
functions and asks an LLM to assign correct LLR IDs based on call
graph analysis and design context.

For safety-critical traceability, every line of code must map to a
requirement.  The auditor produces two kinds of output:

- **confirmed**: existing ``@traces`` decorators validated by the LLM
- **suggested**: new ``@traces`` decorators proposed by the LLM for untraced code

Suggested traces are persisted with ``confidence`` and ``rationale``
so an engineer can review and accept/reject them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backend.crew.trace_parser import LineTrace, analyse_traces
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class SuggestedTrace:
    """An LLM-suggested LLR annotation for an untraced function."""

    function_name: str
    start: int
    end: int
    suggested_llr_ids: list[str]
    rationale: str
    confidence: str  # "high", "medium", "low"


@dataclass
class InvalidTrace:
    """An existing LLR annotation that references a non-existent or wrong ID."""

    function_name: str
    start: int
    end: int
    invalid_llr_ids: list[str]
    reason: str  # "unknown_id", "misattributed"


@dataclass
class FileAuditResult:
    """Audit result for a single source file."""

    file_path: str
    confirmed_traces: list[LineTrace]
    suggested_traces: list[SuggestedTrace]
    invalid_traces: list[InvalidTrace] = field(default_factory=list)
    untraced_count: int = 0
    total_functions: int = 0
    fully_traced: bool = False


# ── Public API ───────────────────────────────────────────────────────────────

async def audit_traces(
    workspace: Path,
    file_paths: list[str],
    graph: Any,
) -> list[FileAuditResult]:
    """Audit all generated files for trace completeness.

    For files with untraced functions, invokes the LLM to suggest
    correct LLR annotations based on call relationships and design.
    """
    forge_logger.emit(
        "INFO", "AUDIT",
        f"Trace audit: analysing {len(file_paths)} file(s)",
    )

    # Gather valid LLR IDs and descriptions in one pass
    llr_id_list, llr_descs = _get_llr_data(graph)
    valid_llr_ids = set(llr_id_list)

    results: list[FileAuditResult] = []
    files_needing_audit: list[tuple[str, str, list[dict[str, Any]]]] = []

    for rel_path in file_paths:
        full_path = workspace / rel_path
        if not full_path.exists():
            continue

        code = full_path.read_text(encoding="utf-8")
        analysis = analyse_traces(code)

        # Validate existing annotations reference real LLR IDs
        invalid = _validate_trace_ids(analysis.traces, valid_llr_ids)
        if invalid:
            forge_logger.emit(
                "WARN", "AUDIT",
                f"  {rel_path}: {len(invalid)} invalid trace(s): "
                + ", ".join(f"{t.function_name}→{t.invalid_llr_ids}" for t in invalid),
            )

        result = FileAuditResult(
            file_path=rel_path,
            confirmed_traces=analysis.traces,
            suggested_traces=[],
            invalid_traces=invalid,
            untraced_count=len(analysis.untraced),
            total_functions=analysis.total_functions,
            fully_traced=len(analysis.untraced) == 0 and len(invalid) == 0,
        )
        results.append(result)

        if analysis.untraced:
            untraced_info = [
                {"name": u.name, "start": u.start, "end": u.end, "private": u.is_private}
                for u in analysis.untraced
            ]
            files_needing_audit.append((rel_path, code, untraced_info))
            forge_logger.emit(
                "WARN", "AUDIT",
                f"  {rel_path}: {len(analysis.untraced)} untraced function(s): "
                + ", ".join(u.name for u in analysis.untraced),
            )
        else:
            forge_logger.emit(
                "INFO", "AUDIT",
                f"  {rel_path}: fully traced "
                f"({analysis.traced_functions}/{analysis.total_functions})",
            )

    # Ask LLM to suggest traces for untraced functions
    if files_needing_audit:
        suggestions = await _llm_suggest_traces(
            files_needing_audit, workspace, llr_id_list, llr_descs,
        )
        _apply_suggestions(results, suggestions)

    traced = sum(1 for r in results if r.fully_traced)
    total_invalid = sum(len(r.invalid_traces) for r in results)
    summary = f"Trace audit complete: {traced}/{len(results)} files fully traced"
    if total_invalid:
        summary += f", {total_invalid} invalid annotation(s) found"
    forge_logger.emit("INFO", "AUDIT", summary)

    return results


async def persist_audit_results(
    results: list[FileAuditResult],
    graph: Any,
    file_node_map: dict[str, str],
) -> None:
    """Persist audit results (including suggestions) to graph node properties."""
    for result in results:
        node_id = file_node_map.get(result.file_path)
        if not node_id:
            continue

        node = graph.node_sync(node_id)
        if not node:
            continue

        props = dict(node.properties or {})
        props["trace_audit"] = {
            "total_functions": result.total_functions,
            "untraced_count": result.untraced_count,
            "fully_traced": result.fully_traced,
            "suggested_traces": [asdict(s) for s in result.suggested_traces],
            "invalid_traces": [asdict(t) for t in result.invalid_traces],
        }

        await graph.update_node(
            node_id, content=None, properties=props,
            changed_by="trace_auditor",
            change_reason="Persist trace audit results",
        )


# ── LLM suggestion ──────────────────────────────────────────────────────────

async def _llm_suggest_traces(
    files: list[tuple[str, str, list[dict[str, Any]]]],
    workspace: Path,
    llr_ids: list[str],
    llr_descs: dict[str, str],
) -> dict[str, list[SuggestedTrace]]:
    """Ask LLM to suggest LLR annotations for untraced functions."""
    forge_logger.emit(
        "INFO", "AUDIT",
        f"Requesting LLM trace suggestions for {len(files)} file(s)",
    )

    context = _build_audit_prompt(files, llr_ids, llr_descs)
    instruction = (
        "You are a trace auditor. Below is context listing source files, their "
        "untraced functions, and available LLR IDs with descriptions.\n\n"
        "For each UNTRACED function: determine which LLR(s) it implements "
        "by reading the function body and matching to requirement text.\n"
        "For each EXISTING `@traces` decorator: verify the LLR IDs are correct.\n\n"
        "Return ONLY a JSON object where each key is a file path, value is "
        "an array of:\n"
        '  {"function_name": "...", "suggested_llr_ids": [...], "rationale": "...", '
        '"confidence": "high"|"medium"|"low", "action": "add"|"correct"}\n\n'
        "Only suggest LLR IDs that genuinely apply. Use 'low' if uncertain.\n\n"
        f"{context}"
    )

    try:
        from backend.agents.factory import build_llm
        from backend.config.loader import load_config
        config = load_config()
        llm = build_llm(config, cacheable=True)
        response = await llm.ainvoke([{"role": "user", "content": instruction}])
        raw = response.content or ""
        if not isinstance(raw, str):
            msg = f"LLM returned non-text content: {type(raw).__name__}"
            raise TypeError(msg)

        # Extract JSON from response
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            forge_logger.emit("WARN", "AUDIT", "LLM returned no JSON")
            return {}

        parsed = json.loads(raw[start:end + 1])

        # Write to suggestions file for _parse_suggestions
        suggestions_path = workspace / ".forge" / "trace_suggestions.json"
        suggestions_path.parent.mkdir(parents=True, exist_ok=True)
        suggestions_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")

    except Exception as exc:  # noqa: BLE001
        forge_logger.emit("WARN", "AUDIT", f"LLM trace suggestion failed: {exc}")
        return {}

    return _parse_suggestions(workspace, files)


def _build_audit_prompt(
    files: list[tuple[str, str, list[dict[str, Any]]]],
    llr_ids: list[str],
    llr_descriptions: dict[str, str] | None = None,
) -> str:
    """Build the context document for LLM trace audit."""
    sections = ["# Trace Audit Context\n"]

    sections.append("## Available LLR IDs\n")
    descs = llr_descriptions or {}
    for llr_id in llr_ids:
        desc = descs.get(llr_id, "")
        suffix = f" — {desc}" if desc else ""
        sections.append(f"- {llr_id}{suffix}")
    sections.append("")

    for rel_path, code, untraced in files:
        sections.append(f"## File: {rel_path}\n")
        sections.append(f"### Untraced functions ({len(untraced)})\n")
        for u in untraced:
            private = " (private helper)" if u["private"] else ""
            sections.append(
                f"- `{u['name']}`{private} — lines {u['start']}–{u['end']}"
            )
        sections.append(
            "\n### Instructions\n"
            "Verify ALL existing `@traces` decorators have correct LLR IDs. "
            "Also assign LLR IDs to untraced functions.\n"
        )
        sections.append(f"\n### Source code\n\n```python\n{code}\n```\n")

    return "\n".join(sections)


def _get_llr_data(graph: Any) -> tuple[list[str], dict[str, str]]:
    """Get sorted LLR IDs and descriptions in a single graph traversal."""
    try:
        ids: list[str] = []
        descs: dict[str, str] = {}
        for n in graph.all_nodes():
            if n.node_type == "LLR":
                ids.append(n.node_id)
                descs[n.node_id] = n.title or (n.content[:80] if n.content else "")
        ids.sort()
        return ids, descs
    except Exception as exc:  # noqa: BLE001
        forge_logger.emit("ERROR", "AUDIT", f"Failed to read LLR data from graph: {exc}")
        return [], {}


def _parse_suggestions(
    workspace: Path,
    files: list[tuple[str, str, list[dict[str, Any]]]],
) -> dict[str, list[SuggestedTrace]]:
    """Parse LLM-generated suggestions JSON."""
    suggestions_path = workspace / ".forge" / "trace_suggestions.json"
    if not suggestions_path.exists():
        forge_logger.emit("WARN", "AUDIT", "No suggestions file written")
        return {}

    try:
        raw = json.loads(suggestions_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        forge_logger.emit("WARN", "AUDIT", f"Failed to parse suggestions: {exc}")
        return {}

    # Build lookup: file_path → untraced functions with line ranges
    untraced_lookup: dict[str, dict[str, dict[str, Any]]] = {}
    for rel_path, _code, untraced in files:
        untraced_lookup[rel_path] = {u["name"]: u for u in untraced}

    result: dict[str, list[SuggestedTrace]] = {}
    for file_path, suggestions in raw.items():
        if not isinstance(suggestions, list):
            continue
        parsed: list[SuggestedTrace] = []
        file_funcs = untraced_lookup.get(file_path, {})
        for s in suggestions:
            func_name = s.get("function_name", "")
            func_info = file_funcs.get(func_name)
            if not func_info:
                continue
            parsed.append(SuggestedTrace(
                function_name=func_name,
                start=func_info["start"],
                end=func_info["end"],
                suggested_llr_ids=s.get("suggested_llr_ids", []),
                rationale=s.get("rationale", ""),
                confidence=s.get("confidence", "low"),
            ))
        if parsed:
            result[file_path] = parsed

    total = sum(len(v) for v in result.values())
    forge_logger.emit("INFO", "AUDIT", f"Parsed {total} trace suggestion(s)")
    return result


def _validate_trace_ids(
    traces: list[LineTrace],
    valid_ids: set[str],
) -> list[InvalidTrace]:
    """Check that all LLR IDs in annotations exist in the graph."""
    invalid: list[InvalidTrace] = []
    for trace in traces:
        bad_ids = [lid for lid in trace.llr_ids if lid not in valid_ids]
        if bad_ids:
            invalid.append(InvalidTrace(
                function_name=trace.symbol,
                start=trace.start,
                end=trace.end,
                invalid_llr_ids=bad_ids,
                reason="unknown_id",
            ))
    return invalid


def _apply_suggestions(
    results: list[FileAuditResult],
    suggestions: dict[str, list[SuggestedTrace]],
) -> None:
    """Merge LLM suggestions into audit results."""
    for result in results:
        file_suggestions = suggestions.get(result.file_path, [])
        result.suggested_traces = file_suggestions
