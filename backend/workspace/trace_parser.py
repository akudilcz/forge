"""Trace parser — extracts requirement annotations from generated Python code.

Uses Python's ``ast`` module to inspect ``@traces(...)`` decorators on
functions and methods. This is the sole annotation mechanism — no comment-based
fallbacks.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class LineTrace:
    """A function/method traced to one or more LLRs."""

    start: int
    end: int
    llr_ids: list[str]
    symbol: str = ""
    case_ids: list[str] = field(default_factory=list)
    class_name: str = ""


@dataclass
class UntracedFunction:
    """A function/method that has no @traces decorator — a traceability gap."""

    name: str
    start: int  # 1-based line number
    end: int
    is_private: bool
    class_name: str = ""


@dataclass
class TraceAnalysis:
    """Complete trace analysis of a source file."""

    traces: list[LineTrace]
    untraced: list[UntracedFunction]
    total_functions: int
    traced_functions: int


# ── Public API ───────────────────────────────────────────────────────────────

def parse_llr_traces(code: str) -> list[LineTrace]:
    """Extract LLR traces from ``@traces(...)`` decorators via AST."""
    func_nodes = _find_function_nodes(code)
    if func_nodes is None:
        return []
    return _collect_traces(func_nodes)


def analyse_traces(code: str) -> TraceAnalysis:
    """Full trace analysis: traced functions, untraced gaps, counts."""
    func_nodes = _find_function_nodes(code)
    if func_nodes is None:
        return TraceAnalysis(traces=[], untraced=[], total_functions=0, traced_functions=0)

    traces = _collect_traces(func_nodes)
    traced_keys = {(t.class_name, t.symbol) for t in traces}
    untraced = [
        UntracedFunction(
            name=scoped.node.name,
            start=scoped.node.lineno,
            end=scoped.node.end_lineno or scoped.node.lineno,
            is_private=scoped.node.name.startswith("_"),
            class_name=scoped.class_name,
        )
        for scoped in func_nodes
        if (scoped.class_name, scoped.node.name) not in traced_keys
    ]

    return TraceAnalysis(
        traces=traces,
        untraced=untraced,
        total_functions=len(func_nodes),
        traced_functions=len(traces),
    )


def find_untraced_functions(code: str) -> list[str]:
    """Return names of ALL functions/methods lacking a @traces decorator."""
    return [u.name for u in analyse_traces(code).untraced]


# ── Trace collection ────────────────────────────────────────────────────────

_FuncNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass
class _ScopedFunc:
    """A function/method AST node paired with its owning class name."""

    node: _FuncNode
    class_name: str


def _collect_traces(func_nodes: list[_ScopedFunc]) -> list[LineTrace]:
    """Extract traces from @traces decorators on each function node."""
    traces: list[LineTrace] = []
    for scoped in func_nodes:
        node = scoped.node
        llr_ids, case_ids = _extract_traces_decorator(node)
        if llr_ids:
            traces.append(LineTrace(
                start=node.lineno,
                end=node.end_lineno or node.lineno,
                llr_ids=llr_ids,
                symbol=node.name,
                case_ids=case_ids,
                class_name=scoped.class_name,
            ))
    return traces


def _extract_traces_decorator(
    func_node: _FuncNode,
) -> tuple[list[str], list[str]]:
    """Extract LLR and CASE IDs from a ``@traces(...)`` decorator, if present.

    Returns ``(llr_ids, case_ids)`` — both empty if no decorator found.
    """
    llr_ids: list[str] = []
    case_ids: list[str] = []
    for dec in func_node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        name = _decorator_name(dec.func)
        if name != "traces":
            continue
        # Positional args → LLR IDs
        for arg in dec.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                llr_ids.append(arg.value)
        # keyword case= → CASE IDs
        for kw in dec.keywords:
            if kw.arg != "case":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                case_ids.append(kw.value.value)
            elif isinstance(kw.value, (ast.List, ast.Tuple)):
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        case_ids.append(elt.value)
    return llr_ids, case_ids


def _decorator_name(node: ast.expr) -> str | None:
    """Return the trailing name of a decorator expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# ── AST function finder ─────────────────────────────────────────────────────

def _find_function_nodes(code: str) -> list[_ScopedFunc] | None:
    """Parse code and return all function/method AST nodes.

    Each node is paired with the name of its owning class (empty string
    for module-level functions).

    Returns None if the code cannot be parsed as Python.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    nodes: list[_ScopedFunc] = []
    _walk_ast(tree, nodes, class_name="")
    return nodes


def _walk_ast(node: ast.AST, out: list[_ScopedFunc], *, class_name: str = "") -> None:
    """Recursively collect function definition AST nodes with class context."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(_ScopedFunc(node=child, class_name=class_name))
            _walk_ast(child, out, class_name=class_name)
        elif isinstance(child, ast.ClassDef) and not _is_protocol(child):
            _walk_ast(child, out, class_name=child.name)
        elif isinstance(child, ast.Module):
            _walk_ast(child, out, class_name=class_name)


def _is_protocol(cls: ast.ClassDef) -> bool:
    """Return True if the class inherits from Protocol."""
    return any(
        (isinstance(b, ast.Name) and b.id == "Protocol")
        or (isinstance(b, ast.Attribute) and b.attr == "Protocol")
        for b in cls.bases
    )
