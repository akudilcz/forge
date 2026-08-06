"""Batch step functions for phases with competing gaps.

Instead of dispatching one gap at a time (which causes circular thrashing
when gaps compete for shared resources), batch steps give the agent a full
static graph snapshot so it makes globally consistent assignments.

Because batch authoring *output* scales with item count, a single call over
the whole phase truncates at the provider output-token limit on large
documents (design/02 §Batch prompts — live evidence trace.1614841.jsonl).
Each batch step therefore chunks its item list to
``LLMConfig.batch_author_chunk_size`` items per LLM call:

1. Snapshot the static prompt prefix once (byte-identical across chunks).
2. For each chunk: invoke the agent, re-scan, retry that chunk with only
   its unresolved items (up to _MAX_BATCH_ATTEMPTS per chunk).
3. Items still unresolved after a chunk's attempts exhaust are stragglers;
   the step falls back to individual per-gap structural dispatch for them.

Used by phases 3, 5, 7, 8 (naturally chunked per MODULE) and 10. Other
phases use the per-gap structural loop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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


def _chunked(ids: list[str], size: int) -> list[list[str]]:
    """Split ``ids`` into consecutive chunks of at most ``size`` items."""
    if size < 1:
        raise ValueError(f"batch_author_chunk_size must be >= 1, got {size}")
    return [ids[i : i + size] for i in range(0, len(ids), size)]


async def _run_chunked_batch(
    flow: Any,
    phase: int,
    gap_type: GapType,
    collect_ids: Callable[[], list[str]],
    prompt_for: Callable[[list[str]], str],
    *,
    allow_gap_types: list[GapType] | None = None,
) -> set[str]:
    """Drive chunked batch authoring: one agent call per chunk, per-chunk retry.

    ``collect_ids`` returns the currently-unresolved item ids; ``prompt_for``
    builds the chunk prompt (static prefix snapshotted by the caller, so it is
    byte-identical across chunk calls — design/02 §Batch prompts). Returns the
    ids still unresolved after every chunk's attempts (stragglers), which the
    caller must hand to per-gap structural dispatch.
    """
    chunk_size = int(flow.config.llm.batch_author_chunk_size)
    unresolved: set[str] = set()
    chunks = _chunked(collect_ids(), chunk_size)
    for index, chunk in enumerate(chunks, start=1):
        label = f"chunk {index}/{len(chunks)}"
        unresolved.update(
            await _run_chunk_attempts(
                flow, phase, gap_type, collect_ids, prompt_for, chunk, label,
                allow_gap_types=allow_gap_types,
            )
        )
    if unresolved:
        forge_logger.emit(
            "WARN", "BATCH",
            f"Phase {phase} · {len(unresolved)} straggler(s) after chunk "
            f"attempts — per-gap dispatch required",
            phase=phase, stragglers=len(unresolved),
        )
    return unresolved


async def _run_chunk_attempts(
    flow: Any,
    phase: int,
    gap_type: GapType,
    collect_ids: Callable[[], list[str]],
    prompt_for: Callable[[list[str]], str],
    chunk: list[str],
    label: str,
    *,
    allow_gap_types: list[GapType] | None,
) -> list[str]:
    """Retry one chunk up to _MAX_BATCH_ATTEMPTS; return its unresolved ids."""
    remaining = list(chunk)
    for attempt in range(1, _MAX_BATCH_ATTEMPTS + 1):
        forge_logger.emit(
            "INFO", "BATCH",
            f"Phase {phase} · {label} · attempt {attempt}/{_MAX_BATCH_ATTEMPTS}: "
            f"{len(remaining)} item(s)",
            phase=phase, attempt=attempt, items=len(remaining),
        )
        try:
            await _run_batch_agent(
                flow, gap_type, prompt_for(remaining), phase,
                allow_gap_types=allow_gap_types,
            )
        except Exception as exc:  # noqa: BLE001
            forge_logger.emit(
                "WARN", "BATCH", f"Phase {phase} · {label} failed: {exc}", phase=phase,
            )
            break
        live = set(collect_ids())
        remaining = [i for i in remaining if i in live]
        if not remaining:
            break
    return remaining


def _gap_ids_collector(flow: Any, phase: int) -> Callable[[], list[str]]:
    """Collector returning node ids of this phase's unresolved structural gaps."""

    def collect() -> list[str]:
        return [g.node_id for g in flow._collect_phase_gaps(phase, set())]

    return collect


def _node_dicts_for_ids(flow: Any, ids: list[str]) -> list[dict[str, Any]]:
    """Resolve node ids to prompt dicts, skipping ids no longer in the graph."""
    nodes = (flow.graph.node_sync(i) for i in ids)
    return [_node_to_dict(n) for n in nodes if n]


# ── Phase 3: UNCOVERED_PARA ─────────────────────────────────────────────────


async def batch_phase3(flow: Any, phase: int) -> StepResult:
    """Chunk-assign HLRs to uncovered PARAs with per-chunk retry.

    Static prefix (all HLRs + LLRs) is snapshotted once so every chunk call
    shares a byte-identical cacheable prefix; only the PARA list varies.
    Stragglers fall through to per-gap structural dispatch.
    """
    forge_logger.emit("INFO", "BATCH", f"Phase {phase} · batch: UNCOVERED_PARA")
    before_ids = _snapshot_node_ids(flow, "HLR")

    all_nodes = flow.graph.all_nodes()
    hlrs = [_node_to_dict(n) for n in all_nodes if n.node_type == "HLR"]
    llrs = [_node_to_dict(n) for n in all_nodes if n.node_type == "LLR"]

    def prompt_for(ids: list[str]) -> str:
        return build_batch_phase3_prompt(_node_dicts_for_ids(flow, ids), hlrs, llrs)

    unresolved = await _run_chunked_batch(
        flow, phase, GapType.UNCOVERED_PARA,
        _gap_ids_collector(flow, phase), prompt_for,
    )

    result = StepResult(step_name="batch_phase3", deletions=0)
    if unresolved:
        result = await _fallback_structural(flow, phase)
    _track_new_nodes(flow, "HLR", before_ids)
    return result


# ── Phase 5: UNMODULARISED ──────────────────────────────────────────────────


async def batch_phase5(flow: Any, phase: int) -> StepResult:
    """Chunk-assign HLRs to MODULEs with per-chunk retry.

    Static context (MODULEs + CONTRACTs + ARCHITECTURE) is snapshotted once
    and shared across chunk calls; only the unassigned-HLR list varies.
    Stragglers fall through to per-gap structural dispatch.
    """
    forge_logger.emit("INFO", "BATCH", f"Phase {phase} · batch: UNMODULARISED")
    before_ids = _snapshot_node_ids(flow, "MODULE")

    all_nodes = flow.graph.all_nodes()
    modules = [_node_to_dict(n) for n in all_nodes if n.node_type == "MODULE"]
    contracts = [_node_to_dict(n) for n in all_nodes if n.node_type == "CONTRACT"]
    arch = next((n for n in all_nodes if n.node_type == "ARCHITECTURE"), None)
    arch_dict = _node_to_dict(arch) if arch else None

    def prompt_for(ids: list[str]) -> str:
        return build_batch_phase5_prompt(
            _node_dicts_for_ids(flow, ids), modules, arch_dict, contracts,
        )

    unresolved = await _run_chunked_batch(
        flow, phase, GapType.UNMODULARISED,
        _gap_ids_collector(flow, phase), prompt_for,
    )

    result = StepResult(step_name="batch_phase5", deletions=0)
    if unresolved:
        result = await _fallback_structural(flow, phase)
    _track_new_nodes(flow, "MODULE", before_ids)
    return result


# ── Phase 7: UNREFINED_HLR ──────────────────────────────────────────────────


async def batch_phase7(flow: Any, phase: int) -> StepResult:
    """Chunk-derive LLRs from HLRs with per-chunk retry.

    Static context (all LLRs + MODULE/CONTRACT content) is snapshotted once
    and shared across chunk calls; only the unrefined-HLR list varies.
    Stragglers fall through to per-gap structural dispatch.
    """
    forge_logger.emit("INFO", "BATCH", f"Phase {phase} · batch: UNREFINED_HLR")
    before_ids = _snapshot_node_ids(flow, "LLR")

    all_nodes = flow.graph.all_nodes()
    all_llrs = [_node_to_dict(n) for n in all_nodes if n.node_type == "LLR"]
    mc = [_node_to_dict(n) for n in all_nodes if n.node_type in ("MODULE", "CONTRACT")]

    def prompt_for(ids: list[str]) -> str:
        return build_batch_phase7_prompt(_node_dicts_for_ids(flow, ids), all_llrs, mc)

    unresolved = await _run_chunked_batch(
        flow, phase, GapType.UNREFINED_HLR,
        _gap_ids_collector(flow, phase), prompt_for,
    )

    result = StepResult(step_name="batch_phase7", deletions=0)
    if unresolved:
        result = await _fallback_structural(flow, phase)
    _track_new_nodes(flow, "LLR", before_ids)
    return result


# ── Phase 10: UNTESTED_HLR + UNTESTED_LLR → CASE_HLR/CASE_LLR batch ─────────


async def batch_phase10(flow: Any, phase: int) -> StepResult:
    """Author missing CASE_HLR + CASE_LLR in chunked batched prompts.

    Previously phase 10 ran the per-gap ``structural`` loop: 24+ untested
    requirements → 24+ sequential agent dispatches → 600s timeout. With
    ``multi_graph_write`` exposed, one LLM turn emits a chunk's cases; the
    untested list is chunked so the authored output never truncates at the
    provider output-token limit. Stragglers fall back to per-gap dispatch.
    """
    forge_logger.emit("INFO", "BATCH", f"Phase {phase} · batch: UNTESTED_HLR+LLR")
    before_cases = _snapshot_node_ids(flow, "CASE_HLR") | _snapshot_node_ids(flow, "CASE_LLR")

    # Static context snapshotted once: SUITE + the CASEs existing at step start.
    all_nodes = flow.graph.all_nodes()
    suite = next((_node_to_dict(n) for n in all_nodes if n.node_type == "SUITE"), None)
    cases = [_node_to_dict(n) for n in all_nodes if n.node_type in ("CASE_HLR", "CASE_LLR")]

    def collect_ids() -> list[str]:
        if not flow._collect_phase_gaps(phase, set()):
            return []
        hlr_ids, llr_ids = _untested_requirement_ids(flow)
        return hlr_ids + llr_ids

    def prompt_for(ids: list[str]) -> str:
        wanted = set(ids)
        live = flow.graph.all_nodes()
        untested_hlrs = [
            _node_to_dict(n) for n in live
            if n.node_type == "HLR" and n.node_id in wanted
        ]
        untested_llrs = [
            _node_to_dict(n) for n in live
            if n.node_type == "LLR" and n.node_id in wanted
        ]
        return build_batch_phase10_prompt(untested_hlrs, untested_llrs, suite, cases)

    unresolved = await _run_chunked_batch(
        flow, phase, GapType.UNTESTED_HLR, collect_ids, prompt_for,
        allow_gap_types=[GapType.UNTESTED_HLR, GapType.UNTESTED_LLR],
    )

    result = StepResult(step_name="batch_phase10", deletions=0)
    if unresolved:
        result = await _fallback_structural(flow, phase)
    new_cases = (_snapshot_node_ids(flow, "CASE_HLR") | _snapshot_node_ids(flow, "CASE_LLR")) - before_cases
    flow._batch_new_node_ids = new_cases
    return result


def _untested_requirement_ids(flow: Any) -> tuple[list[str], list[str]]:
    """HLR and LLR ids with no CASE tracing to them (live graph scan)."""
    all_nodes = flow.graph.all_nodes()
    traced_hlr_ids: set[str] = set()
    traced_llr_ids: set[str] = set()
    for n in all_nodes:
        if n.node_type == "CASE_HLR":
            traced_hlr_ids.update(n.trace_to or [])
        elif n.node_type == "CASE_LLR":
            traced_llr_ids.update(n.trace_to or [])
    hlr_ids = [
        n.node_id for n in all_nodes
        if n.node_type == "HLR" and n.node_id not in traced_hlr_ids
    ]
    llr_ids = [
        n.node_id for n in all_nodes
        if n.node_type == "LLR" and n.node_id not in traced_llr_ids
    ]
    return hlr_ids, llr_ids


# ── Phase 8: UNDESIGNED per MODULE ──────────────────────────────────────────


async def batch_phase8(flow: Any, phase: int) -> StepResult:
    """Batch-assign LLRs to DESIGNs with fast-path + per-MODULE batch.

    The fast-path runs at the start of every cycle, not just pre-loop, so
    LLRs created by deletions in prior cycles still benefit from
    deterministic trace linking before any LLM work happens.
    """
    forge_logger.emit("INFO", "BATCH", f"Phase {phase} · batch: UNDESIGNED")
    before_ids = _snapshot_node_ids(flow, "DESIGN")

    gaps: list[Gap] = []
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

    # Straggler fallback: batch attempts exhausted with gaps still open must
    # not end the phase with undispatched structural gaps (design/02).
    if gaps:
        gaps = flow._collect_phase_gaps(phase, set())
    result = StepResult(step_name="batch_phase8", deletions=0)
    if gaps:
        result = await _fallback_structural(flow, phase)
    _track_new_nodes(flow, "DESIGN", before_ids)
    return result


async def _run_fast_traces(flow: Any, gaps: list[Gap]) -> int:
    """Run fast-path trace linking for UNDESIGNED gaps. Returns count resolved."""
    from backend.pipeline.dispatch import try_fast_trace  # noqa: PLC0415

    resolved = 0
    for gap in gaps:
        if await try_fast_trace(flow, gap):
            resolved += 1
    return resolved


