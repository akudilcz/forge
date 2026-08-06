"""Mutation round — WEAK_CASE detection via a minimal internal AST mutator.

U10 phase-12 rebalance: once FAILING_TESTS clears, one bounded mutation
round runs per completion attempt. Each DESIGN's source file is mutated
one operator at a time on lines inside ``@traces``-annotated function
spans; a mutant the whole test suite still passes is a *survivor* and
becomes a ``GapKind.WEAK_CASE`` gap carrying the mutant diff, dispatched
to the mission agent ("write a test case this diff fails").

Tooling decision: mutmut was examined and rejected — mutmut 3.x loads
global configuration at import time (crashing without a ``[mutmut]``
setup.cfg section), hardcodes a whole-project copy + pytest runner, and
offers no per-file programmatic API, so it fights this scoped,
subprocess-driven integration. The internal mutator below covers the
classic operator set: comparison flips (``==``/``!=``, ``<``/``<=``,
``>``/``>=``), ``+``/``-`` swaps, and return-constant perturbation.

Bounds (loud, never silent): the whole round is capped at
``MUTATION_ROUND_TIMEOUT_S`` wall-clock seconds and each file at
``MAX_MUTANTS_PER_FILE`` mutants — exceeding either logs a WARN skip.
A skipped remainder never blocks completion.

Design reference: specs/03-build-pipeline.md §Mutation round
"""

from __future__ import annotations

import ast
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.codegen.gap_model import Gap, GapKind
from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from pathlib import Path

#: Wall-clock budget for one whole mutation round.
MUTATION_ROUND_TIMEOUT_S = 300.0
#: Cap on a single mutant's test-suite run (a hung mutant counts as killed).
MUTANT_TEST_TIMEOUT_S = 120
#: Per-file mutant cap — keeps a single large file from eating the budget.
MAX_MUTANTS_PER_FILE = 20

# Patchable clock seam (unit tests drive the round budget deterministically).
_monotonic = time.monotonic

#: One traced function span: (start line, end line, LLR ids).
TracedSpan = tuple[int, int, tuple[str, ...]]

_CMP_FLIPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE, ast.LtE: ast.Lt,
    ast.Gt: ast.GtE, ast.GtE: ast.Gt,
}
_BIN_FLIPS: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub, ast.Sub: ast.Add,
}


@dataclass(frozen=True)
class Mutant:
    """One single-operator mutation of a source file."""

    line: int
    description: str
    diff: str
    mutated_source: str
    llr_ids: tuple[str, ...]


# ── Mutant generation ───────────────────────────────────────────────────────


def generate_mutants(source: str, traced_spans: list[TracedSpan]) -> list[Mutant]:
    """Generate single-change mutants on lines within traced spans.

    Raises:
        SyntaxError: if *source* does not parse — mutation only runs after
            the workspace scan proved the file syntactically valid, so a
            parse failure here is a pipeline bug and must be loud.
    """
    base_nodes = list(ast.walk(ast.parse(source)))
    site_indices: list[int] = []
    for i, node in enumerate(base_nodes):
        line = _site_line(node)
        if line is not None and _span_for(line, traced_spans) is not None:
            site_indices.append(i)

    mutants: list[Mutant] = []
    for idx in site_indices:
        tree = ast.parse(source)
        node = list(ast.walk(tree))[idx]
        line = _site_line(node)
        if line is None:  # unreachable — same tree shape as the base walk
            raise RuntimeError(f"mutation site {idx} vanished on re-parse")
        span = _span_for(line, traced_spans)
        if span is None:  # unreachable — same tree shape as the base walk
            raise RuntimeError(f"mutation site at line {line} lost its span")
        original = ast.unparse(node)
        _mutate_in_place(node)
        mutated = ast.unparse(node)
        mutants.append(Mutant(
            line=line,
            description=f"line {line}: `{original}` -> `{mutated}`",
            diff=f"- {original}\n+ {mutated}",
            mutated_source=ast.unparse(ast.fix_missing_locations(tree)),
            llr_ids=span[2],
        ))
    return mutants


def _span_for(line: int, spans: list[TracedSpan]) -> TracedSpan | None:
    """Return the first traced span containing *line*, if any."""
    for span in spans:
        if span[0] <= line <= span[1]:
            return span
    return None


def _site_line(node: ast.AST) -> int | None:
    """Line number of a supported mutation site; None if not a site."""
    if isinstance(node, ast.Compare):
        if len(node.ops) == 1 and type(node.ops[0]) in _CMP_FLIPS:
            return node.lineno
        return None
    if isinstance(node, ast.BinOp):
        return node.lineno if type(node.op) in _BIN_FLIPS else None
    if isinstance(node, ast.Return):
        if (
            isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, bool | int | float)
        ):
            return node.lineno
        return None
    return None


def _mutate_in_place(node: ast.AST) -> None:
    """Apply the single supported mutation for *node* (see ``_site_line``)."""
    if isinstance(node, ast.Compare):
        node.ops[0] = _CMP_FLIPS[type(node.ops[0])]()
    elif isinstance(node, ast.BinOp):
        node.op = _BIN_FLIPS[type(node.op)]()
    elif isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
        value = node.value.value
        if isinstance(value, bool):
            node.value.value = not value
        elif isinstance(value, int | float):
            node.value.value = value + 1
        else:
            raise ValueError(f"unsupported return constant: {value!r}")
    else:
        raise ValueError(f"not a mutation site: {ast.dump(node)}")


# ── Mutation round ──────────────────────────────────────────────────────────


def run_mutation_round(workspace: Path, source_files: dict[str, Any]) -> list[Gap]:
    """Run one bounded mutation round; return WEAK_CASE gaps for survivors.

    ``source_files`` is the workspace scan's path -> FileState map; only
    files with ``@traces`` annotations are mutated, and only on lines
    inside traced function spans (a survivor there is direct evidence the
    LLR's test cases are weak).
    """
    t0 = _monotonic()
    gaps: list[Gap] = []
    forge_logger.emit(
        "INFO", "MUTA ",
        f"Mutation round started — budget {MUTATION_ROUND_TIMEOUT_S:.0f}s, "
        f"{len(source_files)} source file(s)",
    )

    for path in sorted(source_files):
        file_state = source_files[path]
        spans: list[TracedSpan] = [
            (t.start, t.end, tuple(t.llr_ids)) for t in file_state.traces
        ]
        if not spans:
            continue
        target = workspace / path
        if not target.is_file():
            forge_logger.emit(
                "WARN", "MUTA ",
                f"Mutation skipping {path} — scanned file missing from disk",
            )
            continue
        original = target.read_text(encoding="utf-8")
        mutants = generate_mutants(original, spans)
        if len(mutants) > MAX_MUTANTS_PER_FILE:
            forge_logger.emit(
                "WARN", "MUTA ",
                f"Mutation skipping {len(mutants) - MAX_MUTANTS_PER_FILE} "
                f"mutant(s) in {path} — per-file cap {MAX_MUTANTS_PER_FILE}",
            )
            mutants = mutants[:MAX_MUTANTS_PER_FILE]

        for mutant in mutants:
            if _monotonic() - t0 > MUTATION_ROUND_TIMEOUT_S:
                forge_logger.emit(
                    "WARN", "MUTA ",
                    f"Mutation round exceeded {MUTATION_ROUND_TIMEOUT_S:.0f}s "
                    f"budget — skipping remaining mutants (loud skip; does "
                    f"not block completion)",
                )
                return gaps
            if _mutant_survives(target, original, mutant, workspace):
                gaps.append(_weak_case_gap(path, mutant))

    forge_logger.emit(
        "INFO", "MUTA ",
        f"Mutation round complete — {len(gaps)} surviving mutant(s), "
        f"{_monotonic() - t0:.1f}s",
    )
    return gaps


def _mutant_survives(
    target: Path, original: str, mutant: Mutant, workspace: Path,
) -> bool:
    """Write the mutant, run the suite, restore the original."""
    try:
        target.write_text(mutant.mutated_source, encoding="utf-8")
        return _tests_pass(workspace)
    finally:
        target.write_text(original, encoding="utf-8")


def _weak_case_gap(path: str, mutant: Mutant) -> Gap:
    return Gap(
        kind=GapKind.WEAK_CASE,
        node_id="",
        file_path=path,
        details=(
            f"Surviving mutant at {path}:{mutant.line} "
            f"(traces {', '.join(mutant.llr_ids)}): {mutant.description}. "
            f"The full test suite still passes with this change applied — "
            f"write a test case this diff fails:\n{mutant.diff}"
        ),
        context={
            "line": mutant.line,
            "mutation": mutant.description,
            "diff": mutant.diff,
            "llr_ids": list(mutant.llr_ids),
        },
    )


def _tests_pass(workspace: Path) -> bool:
    """Run the workspace suite once; True iff every test passed.

    ``-x`` stops at the first failure (any failure kills the mutant), and
    a hung run (``MUTANT_TEST_TIMEOUT_S``) counts as killed — an infinite
    loop is a behavioural change the suite caught by not finishing.
    """
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest", "tests/", "-x", "-q",
                "--timeout=10", "-p", "no:cacheprovider",
            ],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=MUTANT_TEST_TIMEOUT_S,
            env={**os.environ, "PYTHONPATH": str(workspace)},
        )
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0
