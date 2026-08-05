"""Batch step functions for phases with competing gaps.

Instead of dispatching one gap at a time (which causes circular thrashing
when gaps compete for shared resources), batch steps present ALL gaps to
the agent in a single prompt. The agent sees the full picture and makes
a globally optimal assignment in one pass.

Each batch step follows a retry pattern:
1. Collect gaps, build batch prompt
2. Invoke agent
3. Re-scan to check which gaps remain
4. If gaps remain, retry with only the unresolved ones (up to _MAX_BATCH_ATTEMPTS)
5. If still unresolved, fall back to individual structural dispatch

Used by phases 3, 5, 7, 8. Other phases use the per-gap structural loop.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage

from backend.analysis.gaps import Gap, GapType
from backend.pipeline.batch_graph import (
    _group_undesigned_by_module as _group_undesigned_by_module,
)
from backend.pipeline.batch_graph import (
    _node_to_dict as _node_to_dict,
)
from backend.pipeline.batch_graph import (
    _snapshot_node_ids as _snapshot_node_ids,
)
from backend.pipeline.batch_graph import (
    _track_new_nodes as _track_new_nodes,
)
from backend.pipeline.phase_constraints import (
    reset_phase_constraints,
    set_phase_constraints,
    set_phase_constraints_union,
)
from backend.pipeline.phase_context import phase_context
from backend.pipeline.steps import StepResult
from backend.prompting.batch_prompts import (
    build_batch_phase3_prompt,
    build_batch_phase5_prompt,
    build_batch_phase7_prompt,
    build_batch_phase8_prompt,
    build_batch_phase10_prompt,
)
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

_MAX_BATCH_ATTEMPTS = 3
_CHARS_PER_TOKEN = 4


# ── Shared infrastructure ───────────────────────────────────────────────────


async def _run_batch_agent(
    flow: Any,
    gap_type: GapType,
    prompt: str,
    phase: int,
    *,
    allow_gap_types: list[GapType] | None = None,
) -> int:
    """Invoke the agent with a batch prompt. Returns tool call count.

    ``allow_gap_types`` widens the phase-create allowlist for batch steps
    that legitimately emit nodes for multiple gap types in one LLM turn
    (e.g. batch_phase10 produces CASE_HLR AND CASE_LLR). When omitted,
    only the primary gap_type's allowlist applies.
    """
    import time  # noqa: PLC0415

    agent = flow.pool.get_agent_for_gap(gap_type)
    if agent is None:
        forge_logger.emit(
            "WARN", "BATCH", f"No agent for {gap_type.value}",
            gap_type=gap_type.value,
        )
        return 0

    prompt_chars = len(prompt)
    prompt_tokens = _estimate_prompt_tokens(prompt)
    forge_logger.emit(
        "INFO", "BATCH",
        f"{gap_type.value} prompt: {prompt_chars} chars ~{prompt_tokens}t",
        gap_type=gap_type.value,
        phase=phase,
        prompt_chars=prompt_chars,
        prompt_tokens=prompt_tokens,
    )
    t0 = time.monotonic()

    thread_id = phase_context.get_thread_id(phase, gap_type, "batch")
    config: dict[str, Any] = {
        "recursion_limit": 500,
        "configurable": {"thread_id": thread_id},
    }

    model = _get_model(flow, phase)
    if allow_gap_types:
        token = set_phase_constraints_union(allow_gap_types)
    else:
        token = set_phase_constraints(gap_type)
    tool_call_count = 0
    try:
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=prompt)]},
            version="v2",
            config=config,
        ):
            if event.get("event") != "on_chat_model_end":
                continue
            if model:
                forge_logger.emit(
                    "INFO",
                    "BATCH",
                    f"LLM → batch {gap_type.value}",
                    model=model,
                )
            msg = event.get("data", {}).get("output")
            if msg is None:
                continue
            if hasattr(msg, "message"):
                msg = msg.message
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                    forge_logger.crew_tool_call(str(name), str(args))
                    tool_call_count += 1
    finally:
        reset_phase_constraints(token)

    import time  # noqa: PLC0415

    duration_ms = int((time.monotonic() - t0) * 1000)
    forge_logger.emit(
        "INFO", "BATCH",
        f"{gap_type.value} done: {tool_call_count} tool call(s) in {duration_ms}ms",
        gap_type=gap_type.value,
        phase=phase,
        tool_call_count=tool_call_count,
        duration_ms=duration_ms,
    )
    return tool_call_count


def _get_model(flow: Any, phase: int) -> str:
    try:
        model: str = flow.config.llm.model_for_phase(phase)
    except Exception:  # noqa: BLE001
        return ""
    return model


def _estimate_prompt_tokens(prompt: str) -> int:
    return len(prompt) // _CHARS_PER_TOKEN


async def _fallback_structural(flow: Any, phase: int) -> StepResult:
    """Fall back to per-gap structural loop when batch fails."""
    forge_logger.emit(
        "WARN", "BATCH",
        f"Phase {phase} · falling back to structural loop",
        phase=phase,
        fallback="structural",
    )
    from backend.pipeline.steps import structural  # noqa: PLC0415

    return await structural(flow, phase)


# ── Phase 3: UNCOVERED_PARA ─────────────────────────────────────────────────


async def batch_phase3(flow: Any, phase: int) -> StepResult:
    """Batch-assign HLRs to uncovered PARAs with retry on unresolved gaps."""
    forge_logger.emit("INFO", "BATCH", f"Phase {phase} · batch: UNCOVERED_PARA")
    before_ids = _snapshot_node_ids(flow, "HLR")

    for attempt in range(1, _MAX_BATCH_ATTEMPTS + 1):
        gaps = flow._collect_phase_gaps(phase, set())
        if not gaps:
            break

        all_nodes = flow.graph.all_nodes()
        paras = [
            _node_to_dict(flow.graph.node_sync(g.node_id))
            for g in gaps
            if flow.graph.node_sync(g.node_id)
        ]
        hlrs = [_node_to_dict(n) for n in all_nodes if n.node_type == "HLR"]
        llrs = [_node_to_dict(n) for n in all_nodes if n.node_type == "LLR"]

        prompt = build_batch_phase3_prompt(paras, hlrs, llrs)
        forge_logger.emit(
            "INFO",
            "BATCH",
            f"Phase {phase} · attempt {attempt}/{_MAX_BATCH_ATTEMPTS}: "
            f"{len(paras)} PARAs, {len(hlrs)} HLRs",
        )

        try:
            await _run_batch_agent(flow, GapType.UNCOVERED_PARA, prompt, phase)
        except Exception as exc:  # noqa: BLE001
            forge_logger.emit("WARN", "BATCH", f"Batch failed: {exc}")
            return await _fallback_structural(flow, phase)

    _track_new_nodes(flow, "HLR", before_ids)
    return StepResult(step_name="batch_phase3", deletions=0)


# ── Phase 5: UNMODULARISED ──────────────────────────────────────────────────


async def batch_phase5(flow: Any, phase: int) -> StepResult:
    """Batch-assign HLRs to MODULEs with retry on unresolved gaps."""
    forge_logger.emit("INFO", "BATCH", f"Phase {phase} · batch: UNMODULARISED")
    before_ids = _snapshot_node_ids(flow, "MODULE")

    for attempt in range(1, _MAX_BATCH_ATTEMPTS + 1):
        gaps = flow._collect_phase_gaps(phase, set())
        if not gaps:
            break

        all_nodes = flow.graph.all_nodes()
        unassigned = [
            _node_to_dict(flow.graph.node_sync(g.node_id))
            for g in gaps
            if flow.graph.node_sync(g.node_id)
        ]
        modules = [_node_to_dict(n) for n in all_nodes if n.node_type == "MODULE"]
        contracts = [_node_to_dict(n) for n in all_nodes if n.node_type == "CONTRACT"]
        arch = next((n for n in all_nodes if n.node_type == "ARCHITECTURE"), None)

        prompt = build_batch_phase5_prompt(
            unassigned,
            modules,
            _node_to_dict(arch) if arch else None,
            contracts,
        )
        forge_logger.emit(
            "INFO",
            "BATCH",
            f"Phase {phase} · attempt {attempt}/{_MAX_BATCH_ATTEMPTS}: "
            f"{len(unassigned)} HLRs, {len(modules)} MODULEs",
        )

        try:
            await _run_batch_agent(flow, GapType.UNMODULARISED, prompt, phase)
        except Exception as exc:  # noqa: BLE001
            forge_logger.emit("WARN", "BATCH", f"Batch failed: {exc}")
            return await _fallback_structural(flow, phase)

    _track_new_nodes(flow, "MODULE", before_ids)
    return StepResult(step_name="batch_phase5", deletions=0)


# ── Phase 7: UNREFINED_HLR ──────────────────────────────────────────────────


async def batch_phase7(flow: Any, phase: int) -> StepResult:
    """Batch-derive LLRs from HLRs with retry on unresolved gaps."""
    forge_logger.emit("INFO", "BATCH", f"Phase {phase} · batch: UNREFINED_HLR")
    before_ids = _snapshot_node_ids(flow, "LLR")

    for attempt in range(1, _MAX_BATCH_ATTEMPTS + 1):
        gaps = flow._collect_phase_gaps(phase, set())
        if not gaps:
            break

        all_nodes = flow.graph.all_nodes()
        unrefined = [
            _node_to_dict(flow.graph.node_sync(g.node_id))
            for g in gaps
            if flow.graph.node_sync(g.node_id)
        ]
        all_llrs = [_node_to_dict(n) for n in all_nodes if n.node_type == "LLR"]
        mc = [_node_to_dict(n) for n in all_nodes if n.node_type in ("MODULE", "CONTRACT")]

        prompt = build_batch_phase7_prompt(unrefined, all_llrs, mc)
        forge_logger.emit(
            "INFO",
            "BATCH",
            f"Phase {phase} · attempt {attempt}/{_MAX_BATCH_ATTEMPTS}: "
            f"{len(unrefined)} HLRs, {len(all_llrs)} LLRs",
        )

        try:
            await _run_batch_agent(flow, GapType.UNREFINED_HLR, prompt, phase)
        except Exception as exc:  # noqa: BLE001
            forge_logger.emit("WARN", "BATCH", f"Batch failed: {exc}")
            return await _fallback_structural(flow, phase)

    _track_new_nodes(flow, "LLR", before_ids)
    return StepResult(step_name="batch_phase7", deletions=0)


# ── Phase 10: UNTESTED_HLR + UNTESTED_LLR → CASE_HLR/CASE_LLR batch ─────────


async def batch_phase10(flow: Any, phase: int) -> StepResult:
    """Author every missing CASE_HLR + CASE_LLR in a single batched prompt.

    Previously phase 10 ran the per-gap ``structural`` loop: 24+ untested
    requirements → 24+ sequential agent dispatches → 600s timeout. With
    ``multi_graph_write`` exposed, one LLM turn can emit every new case.
    """
    forge_logger.emit("INFO", "BATCH", f"Phase {phase} · batch: UNTESTED_HLR+LLR")
    before_cases = _snapshot_node_ids(flow, "CASE_HLR") | _snapshot_node_ids(flow, "CASE_LLR")

    for attempt in range(1, _MAX_BATCH_ATTEMPTS + 1):
        gaps = flow._collect_phase_gaps(phase, set())
        if not gaps:
            break

        all_nodes = flow.graph.all_nodes()
        # Which HLRs / LLRs still need a case?
        traced_hlr_ids: set[str] = set()
        traced_llr_ids: set[str] = set()
        cases: list[dict[str, Any]] = []
        for n in all_nodes:
            if n.node_type == "CASE_HLR":
                traced_hlr_ids.update(n.trace_to or [])
                cases.append(_node_to_dict(n))
            elif n.node_type == "CASE_LLR":
                traced_llr_ids.update(n.trace_to or [])
                cases.append(_node_to_dict(n))

        untested_hlrs = [
            _node_to_dict(n)
            for n in all_nodes
            if n.node_type == "HLR" and n.node_id not in traced_hlr_ids
        ]
        untested_llrs = [
            _node_to_dict(n)
            for n in all_nodes
            if n.node_type == "LLR" and n.node_id not in traced_llr_ids
        ]
        suite = next((_node_to_dict(n) for n in all_nodes if n.node_type == "SUITE"), None)

        if not untested_hlrs and not untested_llrs:
            break

        prompt = build_batch_phase10_prompt(
            untested_hlrs, untested_llrs, suite, cases,
        )
        forge_logger.emit(
            "INFO",
            "BATCH",
            f"Phase {phase} · attempt {attempt}/{_MAX_BATCH_ATTEMPTS}: "
            f"{len(untested_hlrs)} HLRs, {len(untested_llrs)} LLRs need CASEs",
        )

        try:
            await _run_batch_agent(
                flow,
                GapType.UNTESTED_HLR,
                prompt,
                phase,
                allow_gap_types=[GapType.UNTESTED_HLR, GapType.UNTESTED_LLR],
            )
        except Exception as exc:  # noqa: BLE001
            forge_logger.emit("WARN", "BATCH", f"Batch failed: {exc}")
            return await _fallback_structural(flow, phase)

    new_cases = (_snapshot_node_ids(flow, "CASE_HLR") | _snapshot_node_ids(flow, "CASE_LLR")) - before_cases
    flow._batch_new_node_ids = new_cases
    return StepResult(step_name="batch_phase10", deletions=0)


# ── Phase 8: UNDESIGNED per MODULE ──────────────────────────────────────────


async def batch_phase8(flow: Any, phase: int) -> StepResult:
    """Batch-assign LLRs to DESIGNs with fast-path + per-MODULE batch.

    The fast-path runs at the start of every cycle, not just pre-loop, so
    LLRs created by deletions in prior cycles still benefit from
    deterministic trace linking before any LLM work happens.
    """
    forge_logger.emit("INFO", "BATCH", f"Phase {phase} · batch: UNDESIGNED")
    before_ids = _snapshot_node_ids(flow, "DESIGN")

    for attempt in range(1, _MAX_BATCH_ATTEMPTS + 1):
        gaps = flow._collect_phase_gaps(phase, set())
        if not gaps:
            break

        # Fast-path each cycle: link LLRs that can be matched without LLM.
        resolved = await _run_fast_traces(flow, gaps)
        if resolved:
            forge_logger.emit(
                "INFO",
                "BATCH",
                f"Phase {phase} · attempt {attempt} · fast-path resolved {resolved}",
            )
            gaps = flow._collect_phase_gaps(phase, set())
            if not gaps:
                break

        total_calls = 0
        for module_id, context in _group_undesigned_by_module(flow, gaps):
            prompt = build_batch_phase8_prompt(**context)
            forge_logger.emit(
                "INFO",
                "BATCH",
                f"Phase {phase} · attempt {attempt} · MODULE {module_id}: "
                f"{len(context['undesigned_llrs'])} LLRs",
            )
            try:
                calls = await _run_batch_agent(
                    flow,
                    GapType.UNDESIGNED,
                    prompt,
                    phase,
                )
                total_calls += calls
            except Exception as exc:  # noqa: BLE001
                forge_logger.emit("WARN", "BATCH", f"Batch failed for {module_id}: {exc}")

        if total_calls == 0:
            return await _fallback_structural(flow, phase)

    _track_new_nodes(flow, "DESIGN", before_ids)
    return StepResult(step_name="batch_phase8", deletions=0)


async def _run_fast_traces(flow: Any, gaps: list[Gap]) -> int:
    """Run fast-path trace linking for UNDESIGNED gaps. Returns count resolved."""
    from backend.pipeline.dispatch import try_fast_trace  # noqa: PLC0415

    resolved = 0
    for gap in gaps:
        if await try_fast_trace(flow, gap):
            resolved += 1
    return resolved


