"""Extracted helpers for Phase 12 Code Gen.

Pure-logic helpers split from code_gen.py to keep the main module
under 500 lines per project conventions.
"""

from __future__ import annotations

import ast as _ast
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.codegen.slice_gen import CodeGenResult
    from backend.graph.engine import ProjectGraph

from backend.codegen.naming import slugify as _slugify


def find_available_modules(workspace: Path) -> set[str]:
    """Build the set of importable module names from src/."""
    src_dir = workspace / "src"
    available: set[str] = {"src"}
    if src_dir.is_dir():
        for f in src_dir.glob("*.py"):
            if f.name != "__init__.py":
                available.add(f.stem)
            available.add(f"src.{f.stem}")
    return available


def has_broken_imports(code: str, available_modules: set[str]) -> bool:
    """Return True iff *code* imports a ``src.*`` module absent from the workspace.

    Imports are enumerated with ``ast.parse`` — a regex scan previously
    matched import-shaped lines inside docstrings. Only dangling
    workspace imports (``src.<name>`` with no matching file in src/) mark
    a file as broken; unknown third-party roots surface later as
    TEST_ENV_BROKEN gaps and stdlib recognition is irrelevant here, so
    neither is ever grounds for deletion. Design: specs/03 (Step 2).
    """
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        # Unparseable files are the SYNTAX_ERROR path (has_syntax_error),
        # not an import problem.
        return False
    return any(
        name not in available_modules
        for name in _iter_src_import_names(tree)
    )


def _iter_src_import_names(tree: _ast.AST) -> list[str]:
    """Collect ``src.<module>`` names imported anywhere in *tree*."""
    names: list[str] = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "src" and len(parts) >= 2:
                    names.append(f"src.{parts[1]}")
        elif isinstance(node, _ast.ImportFrom) and node.module and node.level == 0:
            parts = node.module.split(".")
            if parts[0] != "src":
                continue
            if len(parts) >= 2:
                names.append(f"src.{parts[1]}")
            else:
                # ``from src import foo`` — each alias is a src module.
                names.extend(f"src.{alias.name}" for alias in node.names)
    return names


def has_syntax_error(code: str) -> bool:
    """Return True if *code* cannot be parsed as valid Python."""
    try:
        _ast.parse(code)
        return False
    except SyntaxError:
        return True


def find_graph_orphans(workspace: Path, graph: ProjectGraph) -> list[str]:
    """Find source and test files on disk that don't match any graph node.

    Orphans source files not matching a DESIGN node and test files not
    matching a CASE node.
    """
    expected_src: set[str] = set()
    expected_test: set[str] = set()
    for node in graph.all_nodes():
        slug = _slugify(node.title or node.node_id)
        if node.node_type == "DESIGN":
            expected_src.add(f"src/{slug}.py")
        elif node.node_type in ("CASE_HLR", "CASE_LLR"):
            expected_test.add(f"tests/test_{slug}.py")

    orphans: list[str] = []

    src_dir = workspace / "src"
    if src_dir.exists():
        for f in src_dir.glob("*.py"):
            if f.name == "__init__.py":
                continue
            rel = f"src/{f.name}"
            if rel not in expected_src:
                orphans.append(rel)

    tests_dir = workspace / "tests"
    if tests_dir.exists():
        for f in tests_dir.glob("test_*.py"):
            rel = f"tests/{f.name}"
            if rel not in expected_test:
                orphans.append(rel)

    return sorted(orphans)


def compute_function_coverage(result: CodeGenResult) -> str:
    """Compute function coverage: traced / total across all source files."""
    traced = sum(g.traced_functions for g in result.source_files)
    total = sum(g.total_functions for g in result.source_files)
    return f"{traced}/{total}" if total > 0 else "0/0"


def compute_requirement_coverage(state: Any, graph: ProjectGraph) -> str:
    """Compute requirement coverage: LLRs with passing test evidence / total.

    Returns a ``"covered/total"`` string for legacy callers. Prefer
    :func:`compute_requirement_coverage_detail` when the uncovered IDs
    are also needed.
    """
    detail = compute_requirement_coverage_detail(state, graph)
    return f"{len(detail['covered'])}/{detail['total']}" if detail["total"] > 0 else "0/0"


def compute_requirement_coverage_detail(
    state: Any,
    graph: ProjectGraph,
) -> dict[str, Any]:
    """Detailed requirement coverage: covered/uncovered LLR IDs + totals.

    An LLR is *covered* iff BOTH legs hold (specs/03, coverage model):

    * at least one **source** function carries ``@traces`` citing it
      (the requirement is implemented), AND
    * at least one **passing test** function carries ``@traces`` citing
      it (the behaviour is verified).

    A test function is considered evidence for its traced LLRs only if that
    specific function passed — not if the whole file passed. Matches each
    passing ``SingleTestResult`` to the ``LineTrace`` entries in the same
    file whose ``symbol`` matches the function name.

    Returns::

        {"covered": {"LLR-0001", ...}, "uncovered": ["LLR-0003", ...],
         "unimplemented": ["LLR-0003", ...], "total": 15}

    where ``unimplemented`` lists LLRs absent from all source-file
    ``@traces`` (a passing test alone is NOT coverage — the live-run
    failure this guards against logged "Req 53/53" while 15 LLRs never
    reached src/).
    """
    # Pytest parametrised tests are emitted as ``test_foo[param0]`` while the
    # ``@traces`` decorator is on the bare function ``test_foo``. Strip the
    # parameterisation suffix so traces match any passing variant. A function
    # is considered "passing" iff at least one variant passed and none failed.
    import re as _re
    _param_re = _re.compile(r"\[.*\]$")

    def _base(name: str) -> str:
        return _param_re.sub("", name) if name else name

    passed_bases: set[tuple[str, str]] = set()
    failed_bases: set[tuple[str, str]] = set()
    for r in state.test_results:
        key = (r.file_path, _base(r.function_name))
        if r.status == "passed":
            passed_bases.add(key)
        elif r.status in ("failed", "error"):
            failed_bases.add(key)
    passing_fns = passed_bases - failed_bases

    tested: set[str] = set()
    for path, fs in state.test_files.items():
        for trace in fs.traces:
            if (path, trace.symbol) in passing_fns:
                tested.update(trace.llr_ids)

    implemented: set[str] = {
        llr_id
        for fs in state.source_files.values()
        for trace in fs.traces
        for llr_id in trace.llr_ids
    }

    all_llr_ids = {n.node_id for n in graph.all_nodes() if n.node_type == "LLR"}
    covered = tested & implemented & all_llr_ids
    return {
        "covered": covered,
        "uncovered": sorted(all_llr_ids - covered),
        "unimplemented": sorted(all_llr_ids - implemented),
        "total": len(all_llr_ids),
    }


def strip_markdown_fences(code: str) -> str:
    """Remove wrapping markdown code fences if present."""
    lines = code.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines) + "\n"
