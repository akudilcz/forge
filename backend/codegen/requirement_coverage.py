"""Requirement-coverage checkers — LLR implementation and test evidence.

The two legs of the single coverage definition (design/22):
source-side (every LLR cited by a source ``@traces``) and test-side
(every LLR cited by a passing traced test function).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.codegen.gap_model import Gap, GapKind

if TYPE_CHECKING:
    from backend.graph.engine import ProjectGraph
    from backend.workspace.scanner import FileState


def _check_unimplemented_requirement(
    gaps: list[Gap],
    source_files: dict[str, FileState],
    graph: ProjectGraph,
) -> None:
    """Add UNIMPLEMENTED_REQUIREMENT gaps for LLRs with no source ``@traces``.

    An LLR is *implemented* iff at least one source-file function carries
    a ``@traces`` annotation citing it. This is the source-side leg of the
    single coverage definition (design/22): a passing traced test alone is
    NOT coverage. Without this check, an LLR with no implementing code
    passed every completion gate — the live run reported "Req 53/53"
    while 15 LLRs never reached src/.
    """
    implemented: set[str] = {
        llr_id
        for file_state in source_files.values()
        for trace in file_state.traces
        for llr_id in trace.llr_ids
    }

    for node in graph.all_nodes():
        if node.node_type != "LLR":
            continue
        if node.node_id in implemented:
            continue
        shall = (node.content or "").strip().replace("\n", " ")
        if len(shall) > 240:
            shall = shall[:240] + "…"
        gaps.append(Gap(
            kind=GapKind.UNIMPLEMENTED_REQUIREMENT,
            node_id=node.node_id,
            file_path="",
            details=(
                f'{node.node_id} content: "{shall}" '
                f'No source function carries @traces("{node.node_id}"). '
                f'Fix: implement this requirement in src/ and annotate the '
                f'implementing function(s) with @traces("{node.node_id}").'
            ),
        ))


def _check_uncovered_requirement(
    gaps: list[Gap],
    test_files: dict[str, FileState],
    test_results: list[Any],
    graph: ProjectGraph,
) -> None:
    """Add UNCOVERED_REQUIREMENT gaps for LLRs with no passing test evidence.

    An LLR is 'covered' iff a *specific test function* that passed carries
    a ``@traces`` decorator listing it. Strict per-function match — no
    file-level fallback. A file-level fallback (previously enabled for
    bazel stubs that omit per-function detail) would let the mission
    agent declare "done" for LLRs that no specific passing test actually
    cites, while the coverage gate (which is strict) still blocks. The
    two must use the same definition for the mission to converge.
    """
    # Map (path, base_function_name) -> True if ANY parametrised variant passed.
    # pytest names parametrised cases as ``test_foo[param0]``, but the
    # ``@traces`` decorator is on the bare function ``test_foo``. We strip the
    # parameterisation suffix so traces on the base name match any passing
    # variant. A function is considered "passing" iff at least one of its
    # parametrisations passed and none failed.
    import re as _re
    _param_re = _re.compile(r"\[.*\]$")

    def _base(name: str) -> str:
        return _param_re.sub("", name) if name else name

    passed_bases: set[tuple[str, str]] = set()
    failed_bases: set[tuple[str, str]] = set()
    for result in test_results:
        if not result.function_name:
            continue
        key = (result.file_path, _base(result.function_name))
        if result.status == "passed":
            passed_bases.add(key)
        elif result.status in ("failed", "error"):
            failed_bases.add(key)
    # Only trust a function as "passing" if no variant failed.
    passing_fns = passed_bases - failed_bases

    covered_llrs: set[str] = set()
    for path, file_state in test_files.items():
        for trace in file_state.traces:
            if (path, trace.symbol) in passing_fns:
                covered_llrs.update(trace.llr_ids)

    # Pre-index CASE_LLR trace_to → LLR so each gap can cite the planned CASE.
    case_llr_for: dict[str, list[str]] = {}
    for case in graph.all_nodes():
        if case.node_type != "CASE_LLR":
            continue
        for llr_id in (case.trace_to or []):
            case_llr_for.setdefault(llr_id, []).append(case.node_id)

    for node in graph.all_nodes():
        if node.node_type != "LLR":
            continue
        if node.node_id in covered_llrs:
            continue
        shall = (node.content or "").strip().replace("\n", " ")
        if len(shall) > 240:
            shall = shall[:240] + "…"
        linked_cases = case_llr_for.get(node.node_id, [])
        case_hint = (
            f" Linked test case(s): {', '.join(linked_cases)}."
            if linked_cases else " No linked CASE_LLR — design a direct test."
        )
        gaps.append(Gap(
            kind=GapKind.UNCOVERED_REQUIREMENT,
            node_id=node.node_id,
            file_path="",
            details=(
                f'{node.node_id} content: "{shall}"{case_hint} '
                f'Fix: write (or reuse) a passing test function that exercises '
                f'this behaviour and carries @traces("{node.node_id}") on the '
                f'test function itself.'
            ),
        ))
