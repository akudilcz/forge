"""Mission agent — single long-lived agent for Phase 12 code generation.

Replaces the fragmented triage-gap-closer pipeline with one capable agent
in one continuous conversation. The agent gets full graph context up front,
writes source and test files using real tools, and calls evaluate_progress
to get gap feedback whenever it wants.

The graph acts as scoreboard (checking completeness via value function),
not as project manager (prescribing workflow).

Design reference: design/22_phase_12_generate_code.md
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
from backend.codegen.gap_finder import Gap, find_gaps
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

_SYSTEM_PROMPT = """\
You are implementing a DO-178C DAL-B safety-critical software system to
specification. The full design context is provided below.

TRACEABILITY INVARIANT (the whole point of Phase 12):
You are done only when ALL FOUR of these hold at the same time:
  1. Statement coverage = 100% — every source line is exercised.
  2. MC/DC coverage = 100% — every boolean sub-condition has
     independently affected the outcome.
  3. Every LLR is traced — each LLR has at least one passing test with
     a matching @traces(LLR-…) annotation.
  4. Every function in src/ has @traces — including __init__, every
     other dunder (__repr__, __eq__, __enter__, …) and every private
     helper (_foo). No exemptions.

These are a joint invariant, not four independent thresholds. If a
function cannot be traced to any LLR, it is not required — inline it
into its caller or delete it. "Implementation detail of LLR-X" just
means "traces LLR-X"; there is no separate untraced category. A
private helper inherits the LLR(s) of whatever public method calls it.

Call evaluate_progress to check your score. Keep working until it
reports all_gaps_closed: true.

BUILD SYSTEM:
- Bazel workspace — BUILD files are auto-generated, do not edit them
- Dependencies via requirements.txt (Bazel pip rules)
- evaluate_progress: runs ALL tests + full coverage analysis
- To run a SINGLE test file: shell_exec('bazel test //tests:test_foo')
  (replace test_foo with the file stem, e.g. test_duplicate_skip)
- Do NOT use 'python -m pytest' — it runs outside the sandbox and fails
- Use workspace_doctor if you hit persistent build issues

CONVENTIONS:
- Source files in src/, test files in tests/ (prefixed test_)
- Import as: from src.<module> import <Class>
- @traces("LLR-XXXX") on EVERY function in src/ — no exemptions,
  including __init__, dunder methods, and private helpers
- @traces("LLR-XXXX", case="CASE_LLR-XXXX") on test functions
- conftest.py is infrastructure — do not delete it

Examples of correct annotation coverage:

    class Planner:
        @traces("LLR-0003")
        def __init__(self, grid): ...

        @traces("LLR-0003")
        def plan(self, start, goal): ...

        @traces("LLR-0003")   # helper of plan() → inherits LLR-0003
        def _reconstruct(self, came_from, node): ...

QUALITY GATE:
Once all structural gaps close, the system automatically runs quality
checks. If SCOPE_CREEP gaps appear, address them by removing unrequired
functions and their tests. Prefer deletion/inlining over inventing an
LLR to justify existing code. You can also call
check_trace_quality(file_path) on any source file to get per-function
verdicts (PASS/WEAK/SCOPE_CREEP) assessing whether each function
genuinely implements its traced LLR.
"""


@dataclass
class MissionStats:
    """Statistics collected across the mission agent run."""

    total_tool_calls: int = 0
    total_elapsed_s: float = 0.0
    final_score: float = 0.0
    final_gap_count: int = 0
    stop_reason: str = ""


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


def compute_value(ws_state: WorkspaceState, graph: ProjectGraph) -> float:
    """Compute a deterministic fitness score from workspace state + graph."""
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

    cov_score = (ws_state.coverage_pct or 0) / 100.0
    mcdc_score = (ws_state.branch_coverage_pct or 0) / 100.0

    return min(test_score, trace_score, deco_score, cov_score, mcdc_score)


def format_gaps(gaps: list[Gap]) -> str:
    """Render gap list into actionable feedback grouped by category."""
    if not gaps:
        return "No gaps remaining — all requirements satisfied."

    by_kind: dict[str, list[Gap]] = {}
    for g in gaps:
        by_kind.setdefault(g.kind.name, []).append(g)

    sections: list[str] = []
    for kind_name, kind_gaps in sorted(by_kind.items()):
        lines = [f"### {kind_name} ({len(kind_gaps)})"]
        for g in kind_gaps:
            parts = []
            if g.file_path:
                parts.append(g.file_path)
            if g.node_id:
                parts.append(g.node_id)
            if g.details:
                parts.append(g.details)
            lines.append(f"- {' — '.join(parts)}")
            error_msg = g.context.get("error_message") or g.context.get("error_detail")
            if error_msg:
                short = error_msg[:200].replace("\n", " ")
                lines.append(f"  Error: {short}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


# Tools without which the mission cannot function: writing files,
# running tests, and the primary feedback mechanism. A missing tool is a
# wiring bug at the entry point (lifespan.py / ForgeBuilder), never
# something to degrade around — the live run's agent had no
# evaluate_progress at all and worked blind for the whole session.
_REQUIRED_MISSION_TOOLS = frozenset({"file_write", "shell_exec", "evaluate_progress"})


def create_mission_agent(
    config: ForgeConfig,
    tool_instances: list[Any],
) -> Any:
    """Create the mission ReAct agent with a lean tool set.

    Raises:
        RuntimeError: if the filtered tool set lacks any required mission
            tool (see ``_REQUIRED_MISSION_TOOLS`` and design/22).
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
    llm = build_llm(config, model=config.llm.model_for_phase(12), cacheable=True)

    return create_react_agent(
        model=llm,
        tools=tools,
        prompt=_SYSTEM_PROMPT,
        checkpointer=MemorySaver(),
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

    The agent has an evaluate_progress tool to check its own score. Each
    pass is a single continuous conversation that runs until the agent
    stops itself or hits LangGraph's ``RECURSION_LIMIT`` tool calls.

    After each pass we re-scan gaps. If any remain, a second pass is
    dispatched with a **targeted** prompt that lists only the remaining
    gaps (with emphasis on uncovered requirements, since those are the
    most common convergence failure: the agent writes tests that exercise
    the behaviour but forgets to ``@traces`` them to the specific LLR).
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

    agent = create_mission_agent(config, tool_instances)
    model = config.llm.model_for_phase(12)
    stats = MissionStats()

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
        thread_id = f"mission-{_time.monotonic_ns()}"
        calls = await _run_agent_iteration(
            agent, prompt, model, thread_id, mission_pass,
        )
        tool_call_count += calls

        ws_state = await scan_workspace(workspace)
        gaps = _scan_gaps(ws_state, graph)
        if not gaps:
            stats.stop_reason = "all_gaps_closed"
            break

        forge_logger.emit(
            "INFO", "CGEN ",
            f"Mission pass {mission_pass}/{MAX_MISSION_PASSES} stopped with "
            f"{len(gaps)} gap(s) remaining — re-dispatching with targeted prompt",
        )

        # Build a targeted prompt for the next pass. The uncovered-requirement
        # case is the most important to call out explicitly, since the usual
        # failure is "I wrote a test for X but forgot @traces('LLR-X') on it".
        prompt = _build_followup_prompt(context, gaps, mission_pass + 1)
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


def _build_followup_prompt(
    context: str,
    remaining_gaps: list[Any],
    next_pass_num: int,
) -> str:
    """Build a targeted prompt for a mission pass after gaps remained.

    Highlights UNCOVERED_REQUIREMENT gaps separately because they are the
    most common convergence failure and benefit from an explicit
    per-LLR directive: "write a test that exercises LLR-X and carries
    @traces('LLR-X') on its test function".
    """
    uncovered = [
        g for g in remaining_gaps
        if g.kind.name == "UNCOVERED_REQUIREMENT"
    ]
    other = [g for g in remaining_gaps if g.kind.name != "UNCOVERED_REQUIREMENT"]

    lines: list[str] = [
        f"## MISSION PASS {next_pass_num} — targeted remediation",
        "",
        "The previous pass stopped but gaps remain. Work through the "
        "specific items below.",
        "",
    ]

    if uncovered:
        lines.extend([
            f"### UNCOVERED REQUIREMENTS ({len(uncovered)}) — HIGHEST PRIORITY",
            "",
            "Each of these LLRs has no passing test function with a matching",
            "`@traces` decorator. For EACH one, do both of:",
            "  A) Ensure at least one existing or new test FUNCTION exercises",
            "     the LLR's behaviour and passes.",
            "  B) Add `@traces(\"<LLR-ID>\")` (and `case=\"...\"` if a CASE_LLR",
            "     node exists) to that passing test function.",
            "",
            "Just annotating without a meaningful assertion is NOT enough;",
            "the test function must run and pass for coverage to count.",
            "",
        ])
        for g in uncovered:
            lines.append(f"- {g.node_id}: {g.details or '(no details)'}")
        lines.append("")

    if other:
        lines.extend([
            f"### OTHER REMAINING GAPS ({len(other)})",
            "",
            format_gaps(other),
            "",
        ])

    lines.extend([
        "After fixing, call evaluate_progress to verify all_gaps_closed is true.",
        "Only stop when the score reaches 100% and zero gaps remain.",
    ])

    return f"{context}\n\n" + "\n".join(lines)


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
    """Detailed breakdown showing exactly what's missing for 100% coverage."""
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
        parts.append(f"Statement: {cov:.0f}%")
        if cov < 100 and ws_state.uncovered_lines:
            for path, lines in ws_state.uncovered_lines.items():
                if lines:
                    parts.append(
                        f"  UNCOVERED in {path}: lines {', '.join(str(ln) for ln in lines[:20])}"
                    )
    if mcdc is not None:
        parts.append(f"MC/DC: {mcdc:.0f}%")

    return "\n".join(parts)
