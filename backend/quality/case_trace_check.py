"""Case trace coverage checker — plain text LLM, no structured output.

For each CASE node, evaluates whether its test content covers the
requirement(s) it traces to. Removes bad traces; deletes empty CASEs.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a test coverage auditor. Given a REQUIREMENT and a TEST CASE,
determine whether the test case functionally covers the requirement —
i.e. executing the test would verify the requirement is satisfied.

Respond with exactly one line:
COVERS - <brief reason>
or
NO_COVERAGE - <brief reason>
"""


_BATCH_SYSTEM_PROMPT = """\
You are a test coverage auditor. You will be given ONE test case and a
list of N requirements it claims to cover. For each requirement, decide
independently whether the test case functionally covers it.

Respond with EXACTLY N lines, one per requirement in the order given:
  <REQ_ID>: COVERS - <brief reason>
or
  <REQ_ID>: NO_COVERAGE - <brief reason>

Do not add any other text. Do not merge lines. Do not repeat a REQ_ID.
"""


def create_case_trace_checker(llm: Any, graph: Any) -> Any:
    """Return an async callable that checks CASE nodes' trace coverage.

    Accepts an optional *only_ids* set.  When provided, only CASEs whose
    ``node_id`` is in the set are checked — this avoids re-checking the
    entire CASE population on every pipeline cycle.
    """

    async def check_all_cases(only_ids: set[str] | None = None) -> int:
        case_types = ("CASE_HLR", "CASE_LLR")
        all_cases = [n for n in graph.all_nodes() if n.node_type in case_types]
        if only_ids is not None:
            cases = [c for c in all_cases if c.node_id in only_ids]
        else:
            cases = all_cases

        if not cases:
            forge_logger.emit("INFO", "CTRC ", "No CASE nodes to check")
            return 0

        total_removed = 0
        total = len(cases)

        for idx, case in enumerate(cases, 1):
            trace_ids = case.trace_to or []
            if not trace_ids:
                continue

            forge_logger.emit(
                "INFO",
                "CTRC ",
                f"Checking {case.node_id} ({idx}/{total}) — {len(trace_ids)} trace(s)",
            )

            bad_traces = await _check_case_traces(llm, graph, case, trace_ids)
            if bad_traces:
                total_removed += await _remove_bad_traces(graph, case, bad_traces)

        forge_logger.emit(
            "INFO",
            "CTRC ",
            f"Case trace check complete — {total} case(s) checked, "
            f"{total_removed} trace(s) removed",
        )
        return total_removed

    return check_all_cases


async def _check_case_traces(
    llm: Any,
    graph: Any,
    case: Any,
    trace_ids: list[str],
) -> list[str]:
    """Return trace IDs that don't provide functional coverage.

    All of ``case``'s traces are checked in a single LLM call — one request
    per CASE rather than per (CASE, requirement) pair.
    """
    case_content = case.content or "(no content)"
    bad: list[str] = []

    # Resolve requirements, flagging missing ones immediately.
    resolved: list[tuple[str, Any]] = []
    for req_id in trace_ids:
        req = graph.node_sync(req_id)
        if req is None:
            forge_logger.emit("WARN", "CTRC ", f"{case.node_id} traces to missing {req_id}")
            bad.append(req_id)
            continue
        resolved.append((req_id, req))

    if not resolved:
        return bad

    req_block = "\n\n".join(
        f"REQUIREMENT ({rid}, type={r.node_type}):\n{r.content or '(no content)'}"
        for rid, r in resolved
    )
    expected_ids = [rid for rid, _ in resolved]
    prompt = (
        f"TEST CASE ({case.node_id}, type={case.node_type}):\n{case_content}\n\n"
        f"REQUIREMENTS this CASE claims to cover (N={len(expected_ids)}):\n"
        f"{req_block}\n\n"
        f"Emit one line per requirement, in the order above:\n"
        + "\n".join(f"{rid}: <COVERS | NO_COVERAGE> - <reason>" for rid in expected_ids)
    )

    response = await llm.ainvoke(
        [SystemMessage(content=_BATCH_SYSTEM_PROMPT), HumanMessage(content=prompt)]
    )
    text = (response.content if hasattr(response, "content") else str(response)).strip()

    # Parse per-requirement verdicts.
    verdicts: dict[str, bool] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        rid, _, rest = line.partition(":")
        rid = rid.strip()
        if rid not in expected_ids:
            continue
        verdicts[rid] = rest.strip().upper().startswith("COVERS")

    for rid in expected_ids:
        if rid not in verdicts:
            raise RuntimeError(
                f"case_trace_check: LLM verdict missing for {case.node_id}→{rid}; "
                f"raw response: {text!r}"
            )
        label = "COVERS" if verdicts[rid] else "NO COVERAGE"
        forge_logger.decision(
            "case_trace_coverage", label,
            f"{case.node_id} → {rid}",
            node_id=case.node_id,
            target_req=rid,
        )
        if not verdicts[rid]:
            bad.append(rid)

    return bad


async def _remove_bad_traces(graph: Any, case: Any, bad_traces: list[str]) -> int:
    """Remove bad traces from a CASE node. Delete the CASE if no traces remain.

    Guard: never remove a trace when this CASE is the sole coverage for that
    requirement — removing it would recreate an UNTESTED_HLR/LLR gap and
    cause an infinite create-delete cycle.
    """
    # Filter out any bad trace where this CASE is the only CASE pointing to that req.
    safe_bad: list[str] = []
    for req_id in bad_traces:
        other_covers = [
            c for c in graph.all_nodes()
            if c.node_type in ("CASE_HLR", "CASE_LLR")
            and c.node_id != case.node_id
            and req_id in (c.trace_to or [])
        ]
        if not other_covers:
            forge_logger.emit(
                "INFO",
                "CTRC ",
                f"Skip {case.node_id}→{req_id} — sole coverage (would recreate UNTESTED)",
            )
        else:
            safe_bad.append(req_id)
    bad_traces = safe_bad

    if not bad_traces:
        return 0

    current_traces = list(case.trace_to or [])
    remaining = [t for t in current_traces if t not in bad_traces]
    removed = len(current_traces) - len(remaining)

    if not remaining:
        forge_logger.emit("INFO", "CTRC ", f"Deleting {case.node_id} — no valid traces remain")
        await graph.delete_node(case.node_id)
    else:
        forge_logger.emit(
            "INFO", "CTRC ", f"Updating {case.node_id}: removed {removed} bad trace(s)"
        )
        await graph.update_node(
            case.node_id,
            content=None,
            properties=None,
            changed_by="case_trace_check",
            change_reason=f"Removed {removed} non-covering trace(s)",
            trace_to=remaining,
        )

    return removed
