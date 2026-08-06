"""Mission agent — single long-lived agent for Phase 12 code generation.

Replaces the fragmented triage-gap-closer pipeline with one capable agent
in one continuous conversation. The agent gets full graph context up front,
writes source and test files using real tools, and calls evaluate_progress
to get gap feedback whenever it wants.

The graph acts as scoreboard (checking completeness via value function),
not as project manager (prescribing workflow).

U10 rebalance: repair depth per gap cluster is capped (repair_ledger.py) —
an exhausted cluster is regenerated from scratch with a temperature bump;
after FAILING_TESTS clears, one bounded mutation round (mutation.py) turns
surviving mutants into WEAK_CASE gaps.

Design reference: specs/03-build-pipeline.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

from backend.agents.factory import build_llm
from backend.agents.streaming import iter_agent_turns
from backend.codegen.gap_finder import Gap, GapKind, find_gaps
from backend.codegen.mission_context import (  # noqa: F401 — re-exported for tests
    _format_graph_nodes as _format_graph_nodes,
)
from backend.codegen.mission_context import (
    _include_existing_files as _include_existing_files,
)
from backend.codegen.mission_context import (
    _include_rendered_docs as _include_rendered_docs,
)
from backend.codegen.mission_context import (
    _include_tracing_source as _include_tracing_source,
)
from backend.codegen.mission_context import (
    build_mission_context as build_mission_context,
)
from backend.codegen.mission_history import make_mission_trim_hook
from backend.codegen.mission_prompts import (  # noqa: F401 — re-exported for tests/tools
    _SYSTEM_PROMPT as _SYSTEM_PROMPT,
)
from backend.codegen.mission_prompts import (
    build_followup_prompt,
    build_regeneration_briefs,
)
from backend.codegen.mission_prompts import (
    format_gaps as format_gaps,
)
from backend.codegen.mutation import run_mutation_round
from backend.codegen.repair_ledger import RepairLedger, cluster_keys
from backend.core.work_queue import work_queue
from backend.server.forge_logger import forge_logger
from backend.workspace.result_recorder import is_passed
from backend.workspace.scanner import WorkspaceState, scan_workspace

if TYPE_CHECKING:
    from backend.config.models import ForgeConfig
    from backend.graph.engine import ProjectGraph

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 200

# Tools the mission agent can use — lean set for focused work.
_MISSION_TOOLS = frozenset(
    {
        "file_write",
        "multi_file_write",  # batch write for when many tests/files need creation in one turn
        "file_read",
        "file_patch",
        "shell_exec",
        "list_dir",
        "list_files",
        "read_docs",
        "python_lint",
        "workspace_doctor",
        "evaluate_progress",
        "check_trace_quality",
    }
)


@dataclass
class MissionStats:
    """Statistics collected across the mission agent run."""

    total_tool_calls: int = 0
    total_elapsed_s: float = 0.0
    final_score: float = 0.0
    final_gap_count: int = 0
    stop_reason: str = ""
    # One bounded mutation round per phase-12 completion attempt (U10):
    # set the first time FAILING_TESTS clears so the round never repeats.
    mutation_round_completed: bool = False


def compute_value(ws_state: WorkspaceState, graph: ProjectGraph) -> float:
    """Compute a deterministic fitness score from workspace state + graph.

    Dimensions mirror the phase-12 hard gate: passing tests, LLR trace
    coverage, and per-function ``@traces`` coverage. Statement and MC/DC
    percentages are report-only metrics (U10 gate rebalance — Inozemtseva
    & Holmes) and deliberately do not gate the score.
    """
    tests = ws_state.test_results
    test_score = sum(1 for t in tests if is_passed(t.status)) / max(len(tests), 1)

    traced_llrs = {
        llr_id for f in ws_state.source_files.values() for t in f.traces for llr_id in t.llr_ids
    }
    all_llrs = [n for n in graph.all_nodes() if n.node_type == "LLR"]
    trace_score = len(traced_llrs) / max(len(all_llrs), 1)

    total_fn = sum(f.total_functions for f in ws_state.source_files.values())
    traced_fn = sum(f.traced_functions for f in ws_state.source_files.values())
    deco_score = traced_fn / max(total_fn, 1)

    return min(test_score, trace_score, deco_score)


# Tools without which the mission cannot function: writing files,
# running tests, and the primary feedback mechanism. A missing tool is a
# wiring bug at the entry point (lifespan.py / ForgeBuilder), never
# something to degrade around — the live run's agent had no
# evaluate_progress at all and worked blind for the whole session.
_REQUIRED_MISSION_TOOLS = frozenset({"file_write", "shell_exec", "evaluate_progress"})


def create_mission_agent(
    config: ForgeConfig,
    tool_instances: list[Any],
    temperature: float | None,
) -> Any:
    """Create the mission ReAct agent with a lean tool set.

    Args:
        config: Forge configuration.
        tool_instances: Candidate tools; filtered to ``_MISSION_TOOLS``.
        temperature: Explicit sampling temperature for this pass's model
            (regeneration passes bump it for diversity); ``None`` uses the
            configured default.

    Raises:
        RuntimeError: if the filtered tool set lacks any required mission
            tool (see ``_REQUIRED_MISSION_TOOLS`` and specs/03).
    """
    from langgraph.checkpoint.memory import MemorySaver

    tools = [t for t in tool_instances if t.name in _MISSION_TOOLS]
    missing = sorted(_REQUIRED_MISSION_TOOLS - {t.name for t in tools})
    if missing:
        raise RuntimeError(
            f"Mission agent tool set is missing required tool(s): "
            f"{', '.join(missing)}. Register them where the tool list is "
            f"built (server: lifespan._init_tools; e2e: "
            f"ForgeBuilder._build_tools)."
        )
    llm = build_llm(
        config,
        model=config.llm.model_for_phase(12),
        temperature=temperature,
        cacheable=True,
    )

    # History compaction: the mission thread runs 100+ sequential LLM
    # calls, so an unbounded conversation dominates build cost (measured
    # 52k→250k-token prompts). The hook enforces llm.mission_token_budget
    # with the preservation rule in specs/03 §History Compaction.
    return create_react_agent(
        model=llm,
        tools=tools,
        prompt=_SYSTEM_PROMPT,
        checkpointer=MemorySaver(),
        pre_model_hook=make_mission_trim_hook(config.llm.mission_token_budget),
    )


MAX_MISSION_PASSES = 4


async def run_mission_agent(
    workspace: Path,
    graph: ProjectGraph,
    config: ForgeConfig,
    tool_instances: list[Any],
    extra_prompt: str = "",
) -> tuple[WorkspaceState, MissionStats]:
    """Run the mission agent with a convergence loop.

    Each pass is a fresh conversation thread that runs until the agent
    stops itself or hits LangGraph's ``RECURSION_LIMIT`` tool calls, then
    the workspace is re-scanned. Between passes (U10):

    - the repair ledger records which gap clusters persisted; a cluster
      surviving ``REPAIR_DEPTH_CAP`` passes is marked REGENERATE in the
      next prompt (contract + design + failing evidence only, rewrite
      from scratch) and that pass's model gets a temperature bump;
    - the first time no FAILING_TESTS gap remains, one bounded mutation
      round runs and surviving mutants join the gap list as WEAK_CASE.

    Regeneration and mutation remediation both count as normal passes
    within ``MAX_MISSION_PASSES``.
    """
    import time as _time

    context = build_mission_context(graph, workspace)
    ws_state = await scan_workspace(workspace)
    gaps = _scan_gaps(ws_state, graph)

    prompt = f"{context}\n\n## CURRENT GAPS ({len(gaps)})\n{format_gaps(gaps)}\n\n"
    if extra_prompt:
        prompt += f"\n\n{extra_prompt}\n\n"
    prompt += (
        "Work through all gaps. Call evaluate_progress when you want "
        "to check your score. Keep going until all gaps are closed."
    )

    model = config.llm.model_for_phase(12)
    stats = MissionStats()
    ledger = RepairLedger()

    work_queue.clear_phase(12)
    work_queue.add(
        phase=12,
        category="MISSION",
        description=f"Mission agent: {len(gaps)} gaps to close",
        target="workspace",
        effort="high",
        rationale="Continuous code generation",
    )

    t0 = _time.monotonic()
    tool_call_count = 0

    for mission_pass in range(1, MAX_MISSION_PASSES + 1):
        temperature = _pass_temperature(config, ledger)
        agent = create_mission_agent(config, tool_instances, temperature)
        thread_id = f"mission-{_time.monotonic_ns()}"
        calls = await _run_agent_iteration(
            agent, prompt, model, thread_id, mission_pass,
        )
        tool_call_count += calls

        ws_state = await scan_workspace(workspace)
        gaps = _scan_gaps(ws_state, graph)

        gaps = _maybe_run_mutation_round(workspace, ws_state, gaps, stats)
        if not gaps:
            stats.stop_reason = "all_gaps_closed"
            break

        forge_logger.emit(
            "INFO", "CGEN ",
            f"Mission pass {mission_pass}/{MAX_MISSION_PASSES} stopped with "
            f"{len(gaps)} gap(s) remaining — re-dispatching with targeted prompt",
        )

        # Record which clusters persisted, then build the next prompt —
        # exhausted clusters get a REGENERATE brief (context reset).
        ledger.record_pass(cluster_keys(gaps, ws_state))
        briefs = build_regeneration_briefs(
            ledger.regeneration_clusters(), graph, gaps, ws_state,
        )
        prompt = build_followup_prompt(context, gaps, mission_pass + 1, briefs)
    else:
        stats.stop_reason = f"max_passes_reached_after_{MAX_MISSION_PASSES}"

    score = compute_value(ws_state, graph)
    elapsed = _time.monotonic() - t0
    stats.total_tool_calls = tool_call_count
    stats.total_elapsed_s = elapsed
    stats.final_score = score
    stats.final_gap_count = len(gaps)

    forge_logger.emit(
        "INFO",
        "CGEN ",
        f"Mission complete — {tool_call_count} tool calls, "
        f"{elapsed:.1f}s, score {score:.0%}, {len(gaps)} gap(s) remaining. "
        f"Stop reason: {stats.stop_reason}",
    )

    work_queue.clear_phase(12)
    return ws_state, stats


def _pass_temperature(config: ForgeConfig, ledger: RepairLedger) -> float | None:
    """Temperature for the next pass: bumped when any cluster regenerates.

    Diverse regeneration wants diverse samples (Olausson et al.); the
    ``build_llm`` seam takes an explicit per-construction temperature, so
    the bump applies cleanly to just the regeneration pass's model.
    """
    if not ledger.regeneration_clusters():
        return None
    return config.llm.options.temperature + config.llm.regeneration_temperature_bump


def _maybe_run_mutation_round(
    workspace: Path,
    ws_state: WorkspaceState,
    gaps: list[Gap],
    stats: MissionStats,
) -> list[Gap]:
    """Run the single mutation round once FAILING_TESTS has cleared.

    Bounded to exactly one round per completion attempt via
    ``stats.mutation_round_completed`` — surviving mutants get one
    remediation pass and are never re-verified (specs/13 bounds).
    """
    if stats.mutation_round_completed:
        return gaps
    if any(g.kind is GapKind.FAILING_TESTS for g in gaps):
        return gaps
    stats.mutation_round_completed = True
    return gaps + run_mutation_round(workspace, ws_state.source_files)


async def _run_agent_iteration(
    agent: Any,
    prompt: str,
    model: str,
    thread_id: str,
    iteration: int,
) -> int:
    """Run one agent iteration. Returns tool call count."""
    tool_call_count = 0
    try:
        async for turn in iter_agent_turns(
            agent,
            [HumanMessage(content=prompt)],
            recursion_limit=RECURSION_LIMIT,
            label=f"MISSION-I{iteration}",
            model=model,
            thread_id=thread_id,
        ):
            tool_call_count += len(turn.tool_calls)
    except Exception as exc:  # noqa: BLE001
        forge_logger.emit(
            "WARN",
            "CGEN ",
            f"Mission agent error in iteration {iteration}: {type(exc).__name__}: {exc}",
        )
    return tool_call_count


def _scan_gaps(ws_state: WorkspaceState, graph: ProjectGraph) -> list[Gap]:
    """Run gap analysis on current workspace state."""
    return find_gaps(
        ws_state.source_files,
        ws_state.test_files,
        ws_state.test_results,
        graph,
        test_run_error=ws_state.test_run_error,
        coverage_by_file=ws_state.coverage_by_file,
        uncovered_lines=ws_state.uncovered_lines,
        branch_coverage_pct=ws_state.branch_coverage_pct,
    )


def _score_breakdown(ws_state: WorkspaceState, graph: ProjectGraph) -> str:
    """Detailed breakdown showing exactly what's missing for completion.

    Statement/MC-DC figures are included for the record (report-only —
    they do not gate completion).
    """
    tests = ws_state.test_results
    passed = sum(1 for t in tests if t.status == "passed")
    failed = sum(1 for t in tests if t.status == "failed")
    total = len(tests)

    traced_llrs = {
        llr_id for f in ws_state.source_files.values() for t in f.traces for llr_id in t.llr_ids
    }
    all_llrs = {n.node_id for n in graph.all_nodes() if n.node_type == "LLR"}
    missing_llrs = all_llrs - traced_llrs

    total_fn = sum(f.total_functions for f in ws_state.source_files.values())
    traced_fn = sum(f.traced_functions for f in ws_state.source_files.values())

    parts = [f"Tests: {passed}/{total} pass"]
    if failed:
        parts.append(f"({failed} FAILING)")

    if missing_llrs:
        parts.append(
            f"LLR trace: {len(traced_llrs)}/{len(all_llrs)} — MISSING: {', '.join(sorted(missing_llrs))}"
        )
    else:
        parts.append(f"LLR trace: {len(traced_llrs)}/{len(all_llrs)} (100%)")

    if traced_fn < total_fn:
        untraced = []
        for path, fs in ws_state.source_files.items():
            for uf in fs.untraced_functions:
                qualified = f"{uf.class_name}.{uf.name}" if uf.class_name else uf.name
                untraced.append(f"{path}:{qualified}")
        parts.append(f"@traces: {traced_fn}/{total_fn} — UNTRACED: {', '.join(untraced[:10])}")
    else:
        parts.append(f"@traces: {traced_fn}/{total_fn} (100%)")

    cov = ws_state.coverage_pct
    mcdc = ws_state.branch_coverage_pct
    if cov is not None:
        parts.append(f"Statement: {cov:.0f}% (report-only)")
        if cov < 100 and ws_state.uncovered_lines:
            for path, lines in ws_state.uncovered_lines.items():
                if lines:
                    parts.append(
                        f"  UNCOVERED in {path}: lines {', '.join(str(ln) for ln in lines[:20])}"
                    )
    if mcdc is not None:
        parts.append(f"MC/DC: {mcdc:.0f}% (report-only)")

    return "\n".join(parts)
