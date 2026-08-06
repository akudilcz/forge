"""Tests for backend.codegen.api_surface — the phase-12 API-surface gate.

Live trace (merge_sort e2e build, oracle 1/24): the whitepaper's required
public API (sort, sorted_copy, is_sorted) existed nowhere in the generated
workspace. Codegen fragmented one module into ten invented files, the
facade used relative imports that fail as top-level modules, and nothing
exported the required names. The gate makes each CONTRACT ``public_api``
entry a deterministic, blocking phase-12 check (specs/03, specs/13).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from backend.codegen.api_surface import check_api_surface
from backend.codegen.gap_model import Gap, GapKind
from backend.workspace.scanner import FileState


def _contract_node(public_api: list[dict[str, str]] | None) -> MagicMock:
    node = MagicMock()
    node.node_id = "CONTRACT-0001"
    node.node_type = "CONTRACT"
    node.properties = {} if public_api is None else {"public_api": public_api}
    return node


def _graph_with(*nodes: MagicMock) -> MagicMock:
    graph = MagicMock()
    graph.all_nodes.return_value = list(nodes)
    return graph


def _entry(
    module: str, symbol: str, kind: str, signature: str,
) -> dict[str, str]:
    return {
        "module": module, "symbol": symbol, "kind": kind, "signature": signature,
    }


def _file_state(
    path: str,
    symbols: dict[str, str],
    relative_imports: list[str],
) -> FileState:
    return FileState(
        path=path, symbols=symbols, relative_imports=relative_imports,
    )


# ── Happy path ──────────────────────────────────────────────────────────────


def test_full_surface_present_no_gaps() -> None:
    """All declared symbols exist with matching kinds: zero gaps."""
    contract = _contract_node([
        _entry("merge_sort", "sort", "function", "def sort(items)"),
        _entry("merge_sort", "SortStats", "class", "class SortStats"),
        _entry("merge_sort", "SortStats.merge", "method", "def merge(self, o)"),
    ])
    sources = {
        "src/merge_sort.py": _file_state(
            "src/merge_sort.py",
            {"sort": "function", "SortStats": "class", "SortStats.merge": "method"},
            [],
        ),
    }
    gaps: list[Gap] = []
    check_api_surface(gaps, sources, _graph_with(contract))
    assert gaps == []


def test_reexport_via_absolute_import_counts_as_present() -> None:
    """A function re-exported by absolute import satisfies the surface."""
    contract = _contract_node([
        _entry("merge_sort", "sort", "function", "def sort(items)"),
    ])
    sources = {
        "src/merge_sort.py": _file_state(
            "src/merge_sort.py", {"sort": "import"}, [],
        ),
    }
    gaps: list[Gap] = []
    check_api_surface(gaps, sources, _graph_with(contract))
    assert gaps == []


# ── Missing surface ─────────────────────────────────────────────────────────


def test_missing_module_emits_gap() -> None:
    """Required module file absent from the workspace: loud gap."""
    contract = _contract_node([
        _entry("merge_sort", "sort", "function", "def sort(items)"),
    ])
    gaps: list[Gap] = []
    check_api_surface(gaps, {}, _graph_with(contract))
    assert len(gaps) == 1
    assert gaps[0].kind == GapKind.API_SURFACE_MISMATCH
    assert "src/merge_sort.py" in gaps[0].details
    assert gaps[0].node_id == "CONTRACT-0001"


def test_missing_symbol_emits_gap() -> None:
    """Module exists but the required symbol is not defined in it."""
    contract = _contract_node([
        _entry("merge_sort", "sorted_copy", "function", "def sorted_copy(x)"),
    ])
    sources = {
        "src/merge_sort.py": _file_state(
            "src/merge_sort.py", {"sort": "function"}, [],
        ),
    }
    gaps: list[Gap] = []
    check_api_surface(gaps, sources, _graph_with(contract))
    assert len(gaps) == 1
    assert gaps[0].kind == GapKind.API_SURFACE_MISMATCH
    assert "sorted_copy" in gaps[0].details


def test_kind_mismatch_emits_gap() -> None:
    """Symbol exists but as the wrong kind (class where function required)."""
    contract = _contract_node([
        _entry("merge_sort", "sort", "function", "def sort(items)"),
    ])
    sources = {
        "src/merge_sort.py": _file_state(
            "src/merge_sort.py", {"sort": "class"}, [],
        ),
    }
    gaps: list[Gap] = []
    check_api_surface(gaps, sources, _graph_with(contract))
    assert len(gaps) == 1
    assert gaps[0].kind == GapKind.API_SURFACE_MISMATCH
    assert "function" in gaps[0].details
    assert "class" in gaps[0].details


def test_relative_import_emits_gap() -> None:
    """Relative imports in src/ break top-level importability: loud gap."""
    contract = _contract_node([
        _entry("merge_sort", "sort", "function", "def sort(items)"),
    ])
    sources = {
        "src/merge_sort.py": _file_state(
            "src/merge_sort.py", {"sort": "function"}, ["from .galloper import gallop"],
        ),
    }
    gaps: list[Gap] = []
    check_api_surface(gaps, sources, _graph_with(contract))
    assert len(gaps) == 1
    assert gaps[0].kind == GapKind.API_SURFACE_MISMATCH
    assert "relative import" in gaps[0].details.lower()
    assert "from .galloper import gallop" in gaps[0].details


def test_relative_import_flagged_even_without_contract_entry_for_file() -> None:
    """Any src/ file with relative imports is flagged, not just API modules."""
    contract = _contract_node([
        _entry("merge_sort", "sort", "function", "def sort(items)"),
    ])
    sources = {
        "src/merge_sort.py": _file_state(
            "src/merge_sort.py", {"sort": "function"}, [],
        ),
        "src/sortapi_facade.py": _file_state(
            "src/sortapi_facade.py", {}, ["from .merge_sort import sort"],
        ),
    }
    gaps: list[Gap] = []
    check_api_surface(gaps, sources, _graph_with(contract))
    assert len(gaps) == 1
    assert gaps[0].file_path == "src/sortapi_facade.py"


# ── Contracts without the schema ────────────────────────────────────────────


def test_contract_without_public_api_is_skipped() -> None:
    """Legacy CONTRACT without public_api: no gap (phase 6 enforces presence)."""
    contract = _contract_node(None)
    gaps: list[Gap] = []
    check_api_surface(gaps, {}, _graph_with(contract))
    assert gaps == []


def test_non_contract_nodes_ignored() -> None:
    """Only CONTRACT nodes contribute API-surface entries."""
    module = MagicMock()
    module.node_type = "MODULE"
    module.properties = {"public_api": [_entry("m", "f", "function", "def f()")]}
    gaps: list[Gap] = []
    check_api_surface(gaps, {}, _graph_with(module))
    assert gaps == []


# ── Prohibited constructs (specs/13 "Prohibited constructs") ───────────────
#
# Live trace (expression_evaluator e2e): generated tokenizer_scan.py
# delegated to compile() despite the whitepaper's §12 ban on
# eval/compile/ast — functionally green while implementing nothing.

from backend.codegen.api_surface import check_prohibited_constructs  # noqa: E402
from backend.workspace.scanner import _collect_api_facts  # noqa: E402


def _ban_contract(*constructs: str) -> MagicMock:
    node = MagicMock()
    node.node_id = "CONTRACT-0002"
    node.node_type = "CONTRACT"
    node.properties = {
        "public_api": [_entry("evaluator", "evaluate", "function", "def evaluate(s)")],
        "prohibited_constructs": [
            {"construct": c, "rationale": f"§12 forbids {c}"} for c in constructs
        ],
    }
    return node


def _state_from_code(path: str, code: str) -> FileState:
    facts = _collect_api_facts(code)
    return FileState(
        path=path,
        symbols=facts.symbols,
        relative_imports=facts.relative_imports,
        imported_modules=facts.imported_modules,
        call_targets=facts.call_targets,
    )


def test_prohibited_direct_call_flagged() -> None:
    """A bare eval(...) call in src/ violates the ban, with line quoted."""
    state = _state_from_code(
        "src/evaluator.py", "def evaluate(s):\n    return eval(s)\n",
    )
    gaps: list[Gap] = []
    check_prohibited_constructs(
        gaps, {"src/evaluator.py": state}, _graph_with(_ban_contract("eval")),
    )
    assert len(gaps) == 1
    assert gaps[0].kind == GapKind.PROHIBITED_CONSTRUCT
    assert "eval" in gaps[0].details
    assert "line 2" in gaps[0].details
    assert "§12 forbids eval" in gaps[0].details


def test_prohibited_from_import_flagged() -> None:
    """from ast import literal_eval violates a ban on ast."""
    state = _state_from_code(
        "src/evaluator.py",
        "from ast import literal_eval\n\ndef evaluate(s):\n    return literal_eval(s)\n",
    )
    gaps: list[Gap] = []
    check_prohibited_constructs(
        gaps, {"src/evaluator.py": state}, _graph_with(_ban_contract("ast")),
    )
    assert gaps
    assert all(g.kind == GapKind.PROHIBITED_CONSTRUCT for g in gaps)
    assert any("ast" in g.details for g in gaps)


def test_prohibited_aliased_import_call_flagged() -> None:
    """import ast as tree_mod; tree_mod.parse(...) still resolves to ast."""
    state = _state_from_code(
        "src/evaluator.py",
        "import ast as tree_mod\n\ndef evaluate(s):\n    return tree_mod.parse(s)\n",
    )
    gaps: list[Gap] = []
    check_prohibited_constructs(
        gaps, {"src/evaluator.py": state}, _graph_with(_ban_contract("ast")),
    )
    assert gaps
    assert all(g.kind == GapKind.PROHIBITED_CONSTRUCT for g in gaps)


def test_prohibited_attribute_call_flagged() -> None:
    """A ban on the specific member ast.literal_eval catches ast.literal_eval(...)."""
    state = _state_from_code(
        "src/evaluator.py",
        "import ast\n\ndef evaluate(s):\n    return ast.literal_eval(s)\n",
    )
    gaps: list[Gap] = []
    check_prohibited_constructs(
        gaps,
        {"src/evaluator.py": state},
        _graph_with(_ban_contract("ast.literal_eval")),
    )
    assert len(gaps) == 1
    assert "ast.literal_eval" in gaps[0].details


def test_clean_file_passes_prohibitions() -> None:
    """Code not touching any banned construct emits no gaps."""
    state = _state_from_code(
        "src/evaluator.py",
        "def evaluate(s):\n    return sum(ord(c) for c in s)\n",
    )
    gaps: list[Gap] = []
    check_prohibited_constructs(
        gaps,
        {"src/evaluator.py": state},
        _graph_with(_ban_contract("eval", "compile", "ast")),
    )
    assert gaps == []


def test_contract_without_prohibitions_skips() -> None:
    """prohibited_constructs is optional — absent means nothing is banned."""
    contract = _contract_node([
        _entry("evaluator", "evaluate", "function", "def evaluate(s)"),
    ])
    state = _state_from_code(
        "src/evaluator.py", "def evaluate(s):\n    return eval(s)\n",
    )
    gaps: list[Gap] = []
    check_prohibited_constructs(
        gaps, {"src/evaluator.py": state}, _graph_with(contract),
    )
    assert gaps == []


def test_test_files_exempt_from_prohibitions() -> None:
    """find_gaps scans only src/ for prohibitions — tests may use anything."""
    from backend.codegen.gap_finder import find_gaps

    test_state = _state_from_code(
        "tests/test_evaluator.py",
        'import ast\n\ndef test_oracle():\n    assert ast.literal_eval("1") == 1\n',
    )
    gaps = find_gaps(
        {},
        {"tests/test_evaluator.py": test_state},
        [],
        _graph_with(_ban_contract("ast")),
    )
    assert all(g.kind != GapKind.PROHIBITED_CONSTRUCT for g in gaps)
