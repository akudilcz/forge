"""Mission prompts — system prompt and per-pass prompt assembly for Phase 12.

Holds the mission agent's system prompt, the gap-list renderer, and the
follow-up/regeneration prompt builders. Split from mission_agent.py so
the agent loop stays small and prompt wording changes are isolated.

Design reference: specs/03-build-pipeline.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.codegen.gap_model import Gap, GapKind
from backend.codegen.naming import slugify as _slugify
from backend.codegen.repair_ledger import _src_modules_imported_by
from backend.codegen.trace_persistence import _owning_contract_content
from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from backend.graph.engine import ProjectGraph

#: Max characters of failing-evidence quoted per regeneration brief.
_EVIDENCE_LIMIT = 2000

_SYSTEM_PROMPT = """\
You are implementing a DO-178C DAL-B safety-critical software system to
specification. The full design context is provided below.

TRACEABILITY INVARIANT (the whole point of Phase 12):
You are done only when ALL THREE of these hold at the same time:
  1. Every test passes — no failures or errors (a skip is not a pass).
  2. Every LLR is covered — at least one source function carries a
     matching @traces(LLR-…) annotation AND at least one passing test
     function carries @traces(LLR-…).
  3. Every function in src/ has @traces — including __init__, every
     other dunder (__repr__, __eq__, __enter__, …) and every private
     helper (_foo). No exemptions.

Requirements coverage is the hard gate. Statement, branch, and MC/DC
coverage percentages are measured and reported for the record, but they
are NOT gates — do not chase coverage percentages; write tests that
verify requirement behaviour.

These are a joint invariant, not independent thresholds. If a
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
- FILE LAYOUT IS DECIDED BY THE DESIGNS: one source file per DESIGN at
  its declared path. Do NOT invent additional modules beyond the DESIGN
  nodes — no facades, orchestration shims, or split-out helpers. A past
  build fragmented one module into ten files and lost the public API.
- API SURFACE: every CONTRACT properties.public_api entry must be
  importable from the module it names (src/<module>.py) with the exact
  symbol name and kind. The API_SURFACE_MISMATCH gap enforces this.
- absolute imports only in src/ — a relative import (from .x import y)
  breaks top-level importability and is flagged as a gap
- PROHIBITED CONSTRUCTS: every CONTRACT properties.prohibited_constructs
  entry is a HARD BAN inside src/ — no calls, imports, or aliased uses
  of the construct (the PROHIBITED_CONSTRUCT gap enforces this
  statically). Implement the behaviour yourself; delegating to a banned
  construct is spec evasion, not implementation. Tests may use anything.
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

If a WEAK_CASE gap appears, it quotes a surviving mutant diff: a small
behavioural change to the source that the whole test suite failed to
detect. Write a test case that FAILS on the mutated code and PASSES on
the real code — do not change the source to dodge the mutant.
"""


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


def build_followup_prompt(
    context: str,
    remaining_gaps: list[Any],
    next_pass_num: int,
    regeneration_briefs: dict[str, str],
) -> str:
    """Build a targeted prompt for a mission pass after gaps remained.

    Regeneration briefs (clusters whose repair budget is exhausted — see
    repair_ledger.py) come first: those slices are rewritten from
    scratch, so their FAILING_TESTS gaps are folded into the brief
    instead of being listed as patch targets. Uncovered-requirement gaps
    are highlighted separately because they are the most common
    convergence failure and benefit from an explicit per-LLR directive.
    """
    regenerating = set(regeneration_briefs)
    listed = [g for g in remaining_gaps if (g.file_path or "") not in regenerating]
    uncovered = [g for g in listed if g.kind.name == "UNCOVERED_REQUIREMENT"]
    other = [g for g in listed if g.kind.name != "UNCOVERED_REQUIREMENT"]

    lines: list[str] = [
        f"## MISSION PASS {next_pass_num} — targeted remediation",
        "",
        "The previous pass stopped but gaps remain. Work through the "
        "specific items below.",
        "",
    ]

    for key in sorted(regeneration_briefs):
        lines.extend([regeneration_briefs[key], ""])

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


def build_regeneration_briefs(
    regenerating: set[str],
    graph: ProjectGraph,
    gaps: list[Gap],
    ws_state: Any,
) -> dict[str, str]:
    """Build one REGENERATE brief per exhausted cluster.

    Each brief deliberately carries only the slice's CONTRACT record, its
    DESIGN, and a summary of the failing evidence — a context reset for
    that slice (Olausson et al.: fresh diverse regeneration beats deep
    repair chains on the accumulated conversation).
    """
    briefs: dict[str, str] = {}
    for key in sorted(regenerating):
        design = _design_for_source_path(graph, key)
        if design is None:
            forge_logger.emit(
                "WARN", "CGEN ",
                f"Repair budget exhausted for {key} but no DESIGN node maps "
                f"to it — cannot regenerate, continuing normal repair",
            )
            continue
        contract = _owning_contract_content(graph, design)
        evidence = _failing_evidence_for(key, gaps, ws_state)
        briefs[key] = "\n".join([
            f"### REGENERATE {key} — repair budget exhausted, rewrite from scratch",
            "",
            "This slice failed to converge after repeated repair passes. Do",
            f"NOT read or patch the existing {key} — rewrite the whole file",
            "from scratch against the contract and design below, then make",
            "its tests pass. Keep the file at exactly this path.",
            "",
            f"CONTRACT:\n{contract or '(module has no CONTRACT record)'}",
            "",
            f"DESIGN ({design.node_id} — {design.title}):\n{design.content or ''}",
            "",
            f"FAILING EVIDENCE (from the previous pass):\n{evidence or '(none captured)'}",
        ])
    return briefs


def _design_for_source_path(graph: ProjectGraph, source_path: str) -> Any | None:
    """Find the DESIGN node whose generated source path is *source_path*."""
    for node in graph.all_nodes():
        if node.node_type != "DESIGN":
            continue
        slug = _slugify(node.title or node.node_id)
        if f"src/{slug}.py" == source_path:
            return node
    return None


def _failing_evidence_for(key: str, gaps: list[Gap], ws_state: Any) -> str:
    """Summarise FAILING_TESTS evidence belonging to cluster *key*."""
    parts: list[str] = []
    for gap in gaps:
        if gap.kind is not GapKind.FAILING_TESTS:
            continue
        path = gap.file_path or ""
        in_cluster = path == key or (
            path.startswith("tests/")
            and key in _src_modules_imported_by(path, ws_state)
        )
        if not in_cluster:
            continue
        parts.append(f"{path}: {gap.details}")
        parts.extend(gap.context.get("error_summaries") or [])
    return "\n".join(parts)[:_EVIDENCE_LIMIT]
