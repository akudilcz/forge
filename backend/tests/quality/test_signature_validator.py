"""Tests for CONTRACT↔DESIGN signature alignment."""

from __future__ import annotations

from backend.quality.signature_validator import (
    extract_function_names,
    find_design_contract_mismatches,
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
