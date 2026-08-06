"""Tests for CONTRACT↔DESIGN signature alignment."""

from __future__ import annotations

from backend.quality.signature_validator import (
    extract_function_names,
    extract_signature_declarations,
    find_design_contract_mismatches,
    find_public_api_conflicts,
    parse_signature,
)


def test_extract_function_names_from_python_signature() -> None:
    md = "def parse(doc: str) -> list[Paragraph]:\n    ..."
    assert extract_function_names(md) == {"parse"}


def test_extract_function_names_from_prose_signature() -> None:
    md = "Method `fetch(user_id)` retrieves the user."
    assert extract_function_names(md) == {"fetch"}


def test_extract_function_names_ignores_stopwords() -> None:
    md = "if len(result) and isinstance(result, str): return str(result)"
    assert extract_function_names(md) == set()


def test_extract_function_names_multiple() -> None:
    md = "- parse(doc)\n- chunk(section)\n- emit(para)\n"
    assert extract_function_names(md) == {"parse", "chunk", "emit"}


def test_find_design_contract_mismatches_aligned() -> None:
    contract = "def parse(doc) -> list\ndef chunk(section) -> para"
    design = "def parse(doc): ...\ndef chunk(section): ..."
    assert find_design_contract_mismatches(contract, design) == []


def test_find_design_contract_mismatches_extra_in_design() -> None:
    contract = "def parse(doc): ..."
    design = "def parse(doc): ...\ndef commit(): ..."
    assert find_design_contract_mismatches(contract, design) == ["commit"]


def test_find_design_contract_mismatches_empty_contract_surfaces_all() -> None:
    contract = ""
    design = "def parse(doc): ..."
    assert find_design_contract_mismatches(contract, design) == ["parse"]


def test_find_design_contract_mismatches_empty_design_ok() -> None:
    contract = "def parse(doc): ..."
    design = ""
    assert find_design_contract_mismatches(contract, design) == []


def test_method_call_dot_not_treated_as_declaration() -> None:
    """`self.parse(x)` isn't a declaration — leading dot rules it out."""
    md = "inside another method, we call self.parse(x)"
    assert extract_function_names(md) == set()


def test_extract_function_names_ignores_prose_parentheticals() -> None:
    """English words followed by a spaced parenthetical are prose, not
    signatures — live builds flagged 'thereafter (…)' as a function."""
    md = (
        "The mean is updated thereafter (using Welford's method) and "
        "consecutive (adjacent) operators are rejected; the problem "
        "(malformed input) raises."
    )
    assert extract_function_names(md) == set()


def test_extract_function_names_ignores_dunders() -> None:
    """Constructors/dunders are implementation detail; CONTRACTs never
    list them, so extracting __init__ produced false mismatches."""
    md = "class Stats:\n    def __init__(self) -> None: ...\n    def add(self, x: float) -> None: ..."
    assert extract_function_names(md) == {"add"}


def test_extract_function_names_ignores_private_helpers() -> None:
    """Leading-underscore names are private by convention; CONTRACTs describe
    the public surface, so private helpers in a DESIGN are not mismatches."""
    md = "def _convert(tok: str) -> float: ...\ndef evaluate(expr: str) -> float: ..."
    assert extract_function_names(md) == {"evaluate"}


# ── public_api-aware checking (structured CONTRACT surface, specs/13) ───────

_API = [
    {
        "module": "toposort", "symbol": "topological_sort", "kind": "function",
        "signature": (
            "topological_sort(graph: Mapping[Any, Iterable[Any]], *, "
            "tie_breaker: Callable[[Any], Any] | None = None) -> list[Any]"
        ),
    },
    {
        "module": "toposort", "symbol": "descendants", "kind": "function",
        "signature": (
            "descendants(graph: Mapping[Any, Iterable[Any]], node: Any) "
            "-> set[Any]"
        ),
    },
    {
        "module": "toposort", "symbol": "CyclicGraphError.cycle",
        "kind": "method", "signature": "cycle: list[Any]",
    },
    {
        "module": "toposort", "symbol": "CyclicGraphError", "kind": "class",
        "signature": "class CyclicGraphError(ValueError)",
    },
]


def test_parse_signature_with_def_prefix_and_return() -> None:
    name, params, ret = parse_signature(  # type: ignore[misc]
        "def tokenize(expr: str) -> list[Token]"
    )
    assert name == "tokenize"
    assert params == ["expr"]
    assert ret == "list[Token]"


def test_parse_signature_garbage_returns_none() -> None:
    assert parse_signature("cycle: list[Any]") is None


def test_extract_declarations_requires_annotated_params() -> None:
    """Shorthand prose mentions like `is_acyclic(graph) -> bool` are
    references, not declarations — only annotated parameter lists count."""
    md = "wrapper is_acyclic(graph) -> bool delegates; sort_key(node) too"
    assert extract_signature_declarations(md) == {}


def test_extract_declarations_captures_params_and_return() -> None:
    md = "- descendants(node: Any) -> set[Any] — BFS over forward adjacency."
    decls = extract_signature_declarations(md)
    assert decls == {"descendants": [(["node"], "set[Any]")]}


def test_public_api_conflicts_internal_helpers_are_not_violations() -> None:
    """Live gap (topological_sort r3, DESIGN-0001): internal accessors of a
    private class must not be flagged just because public_api lacks them."""
    design = (
        "class _Graph (internal): __init__(self, graph: Mapping[Any, "
        "Iterable[Any]]) normalizes input. Accessors: in_degree(node: int) "
        "-> int; successors(node: Any) -> list[Any]."
    )
    assert find_public_api_conflicts(_API, design) == []


def test_public_api_conflicts_flags_contradictory_signature() -> None:
    design = "def topological_sort(graph: Mapping[Any, Any], reverse: bool = False) -> list[Any]"
    assert find_public_api_conflicts(_API, design) == ["topological_sort"]


def test_public_api_conflicts_shadow_plus_matching_public_ok() -> None:
    """Live case (DESIGN-0007): an internal method reusing a public name is
    fine when the DESIGN also states the matching public signature."""
    design = (
        "Internal: descendants(node: Any) -> set[Any] walks adjacency.\n"
        "Public wrapper: descendants(graph: Mapping[Any, Iterable[Any]], "
        "node: Any) -> set[Any] delegates to it."
    )
    assert find_public_api_conflicts(_API, design) == []


def test_public_api_conflicts_shadow_without_public_form_flagged() -> None:
    design = "Only: descendants(node: Any) -> set[Any] is provided."
    assert find_public_api_conflicts(_API, design) == ["descendants"]


def test_public_api_conflicts_paraphrased_types_match_by_param_names() -> None:
    """Type paraphrase is tolerated (phase-12 gate owns type drift); the
    deterministic contract here is the parameter-name sequence + return."""
    design = (
        "topological_sort(graph: Mapping, *, tie_breaker: Callable = None) "
        "-> list[Any] orchestrates the run."
    )
    assert find_public_api_conflicts(_API, design) == []


def test_public_api_conflicts_return_type_contradiction_flagged() -> None:
    design = (
        "descendants(graph: Mapping[Any, Iterable[Any]], node: Any) "
        "-> list[Any] returns a LIST, contradicting the contract."
    )
    assert find_public_api_conflicts(_API, design) == ["descendants"]


def test_public_api_conflicts_method_and_class_entries_ignored() -> None:
    """kind=method/class entries never collide with module-level prose
    (live case: CycleRecoverer's internal cycle() accessor vs the public
    CyclicGraphError.cycle attribute)."""
    design = "def cycle(depth: int) -> list[Any] | None — internal accessor."
    assert find_public_api_conflicts(_API, design) == []
