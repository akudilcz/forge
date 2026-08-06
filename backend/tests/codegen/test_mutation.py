"""Tests for backend.codegen.mutation — internal AST mutation round.

U10 phase-12 rebalance: after FAILING_TESTS clears, one bounded mutation
round runs per completion attempt. Surviving mutants on lines carrying
``@traces`` LLR annotations emit ``GapKind.WEAK_CASE`` gaps dispatched to
the mission agent ("write a test case this diff fails").

Decision (recorded here and in specs/03): mutmut 3.x loads global config
at import time, hardcodes a whole-project copy + pytest runner, and
cannot be scoped per file programmatically — it fights this integration,
so FORGE uses a minimal internal AST mutator instead.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.codegen.gap_model import GapKind
from backend.codegen.mutation import (
    MUTATION_ROUND_TIMEOUT_S,
    Mutant,
    generate_mutants,
    run_mutation_round,
)

# ── generate_mutants ────────────────────────────────────────────────────────


_SRC = '''\
from tracing.decorator import traces


@traces("LLR-0001")
def clamp(x, lo):
    if x < lo:
        return lo
    return x + 0
'''


def _spans_all() -> list[tuple[int, int, tuple[str, ...]]]:
    return [(4, 8, ("LLR-0001",))]


class TestGenerateMutants:
    def test_comparison_flip_generated(self) -> None:
        mutants = generate_mutants(_SRC, _spans_all())
        assert any("<" in m.description and "<=" in m.description for m in mutants)

    def test_arithmetic_flip_generated(self) -> None:
        mutants = generate_mutants(_SRC, _spans_all())
        assert any("+" in m.description and "-" in m.description for m in mutants)

    def test_equality_flip_generated(self) -> None:
        src = '@traces("LLR-1")\ndef eq(a, b):\n    return a == b\n'
        mutants = generate_mutants(src, [(2, 3, ("LLR-1",))])
        assert any("!=" in m.description for m in mutants)

    def test_return_constant_perturbed(self) -> None:
        src = '@traces("LLR-1")\ndef truthy():\n    return True\n'
        mutants = generate_mutants(src, [(2, 3, ("LLR-1",))])
        assert any("False" in m.description for m in mutants)

    def test_lines_outside_traced_spans_are_not_mutated(self) -> None:
        mutants = generate_mutants(_SRC, [(999, 1000, ("LLR-0002",))])
        assert mutants == []

    def test_mutants_carry_llr_ids_of_enclosing_span(self) -> None:
        mutants = generate_mutants(_SRC, _spans_all())
        assert mutants
        assert all(m.llr_ids == ("LLR-0001",) for m in mutants)

    def test_mutated_source_is_valid_python(self) -> None:
        import ast

        for m in generate_mutants(_SRC, _spans_all()):
            ast.parse(m.mutated_source)

    def test_each_mutant_is_a_single_change(self) -> None:
        mutants = generate_mutants(_SRC, _spans_all())
        for m in mutants:
            assert m.diff.count("\n+") == 1 or m.diff.startswith("+") is False


# ── run_mutation_round ──────────────────────────────────────────────────────


def _workspace_with_source(tmp_path: Path, code: str) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text(code, encoding="utf-8")
    return tmp_path


def _source_files() -> dict[str, SimpleNamespace]:
    trace = SimpleNamespace(start=2, end=5, llr_ids=["LLR-0001"])
    return {"src/mod.py": SimpleNamespace(traces=[trace])}


_MOD = '@traces("LLR-0001")\ndef eq(a, b):\n    return a == b\n'


class TestRunMutationRound:
    def test_surviving_mutant_emits_weak_case_gap(self, tmp_path: Path) -> None:
        ws = _workspace_with_source(tmp_path, _MOD)
        with patch("backend.codegen.mutation._tests_pass", return_value=True):
            gaps = run_mutation_round(ws, _source_files())
        assert gaps
        assert all(g.kind is GapKind.WEAK_CASE for g in gaps)
        gap = gaps[0]
        assert gap.file_path == "src/mod.py"
        assert "write a test case this diff fails" in gap.details.lower()
        assert "LLR-0001" in gap.details
        assert gap.context["diff"]

    def test_killed_mutants_emit_no_gaps(self, tmp_path: Path) -> None:
        ws = _workspace_with_source(tmp_path, _MOD)
        with patch("backend.codegen.mutation._tests_pass", return_value=False):
            gaps = run_mutation_round(ws, _source_files())
        assert gaps == []

    def test_original_source_restored_after_round(self, tmp_path: Path) -> None:
        ws = _workspace_with_source(tmp_path, _MOD)
        with patch("backend.codegen.mutation._tests_pass", return_value=True):
            run_mutation_round(ws, _source_files())
        assert (ws / "src" / "mod.py").read_text(encoding="utf-8") == _MOD

    def test_timeout_is_loud_skip_not_silent_pass(self, tmp_path: Path) -> None:
        """Exceeding the runtime budget WARNs and returns partial results —
        it never raises and never blocks completion."""
        ws = _workspace_with_source(tmp_path, _MOD)
        clock = iter([0.0, MUTATION_ROUND_TIMEOUT_S + 1.0, MUTATION_ROUND_TIMEOUT_S + 2.0])
        with (
            patch("backend.codegen.mutation._tests_pass", return_value=True),
            patch("backend.codegen.mutation._monotonic", side_effect=lambda: next(clock)),
            patch("backend.codegen.mutation.forge_logger") as logger_mock,
        ):
            gaps = run_mutation_round(ws, _source_files())
        assert gaps == []  # budget hit before the first mutant ran
        warn_calls = [c for c in logger_mock.emit.call_args_list if c.args[0] == "WARN"]
        assert warn_calls, "timeout must be logged loudly at WARN"
        assert any("skip" in c.args[2].lower() for c in warn_calls)

    def test_untraced_files_are_not_mutated(self, tmp_path: Path) -> None:
        ws = _workspace_with_source(tmp_path, _MOD)
        files = {"src/mod.py": SimpleNamespace(traces=[])}
        with patch("backend.codegen.mutation._tests_pass", return_value=True):
            assert run_mutation_round(ws, files) == []

    def test_missing_file_is_loud_skip(self, tmp_path: Path) -> None:
        """A scanned file deleted from disk is skipped with a WARN, never
        a crash or a silent pass."""
        with (
            patch("backend.codegen.mutation._tests_pass", return_value=True),
            patch("backend.codegen.mutation.forge_logger") as logger_mock,
        ):
            gaps = run_mutation_round(tmp_path, _source_files())
        assert gaps == []
        warn_calls = [c for c in logger_mock.emit.call_args_list if c.args[0] == "WARN"]
        assert warn_calls


# ── Mutant dataclass ────────────────────────────────────────────────────────


class TestMutant:
    def test_mutant_is_frozen(self) -> None:
        m = Mutant(
            line=1, description="d", diff="- a\n+ b",
            mutated_source="x = 1\n", llr_ids=("LLR-1",),
        )
        import dataclasses

        assert dataclasses.is_dataclass(m)
        try:
            m.line = 2  # type: ignore[misc]
            raise AssertionError("Mutant must be frozen")
        except dataclasses.FrozenInstanceError:
            pass


# ── Slow: real subprocess mutation round ────────────────────────────────────


@pytest.mark.slow
class TestRealMutationRound:
    """End-to-end mutation round against a real pytest subprocess.

    A weak suite (never asserts on the comparison outcome) must yield a
    surviving-mutant WEAK_CASE gap; a strong suite must kill the mutants.
    """

    _SRC = (
        "from tracing import traces\n"
        "\n"
        "\n"
        '@traces("LLR-0001")\n'
        "def is_equal(a, b):\n"
        "    return a == b\n"
    )

    def _build_workspace(self, tmp_path: Path, test_body: str) -> Path:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "src" / "mod.py").write_text(self._SRC, encoding="utf-8")
        (tmp_path / "tracing").mkdir()
        (tmp_path / "tracing" / "__init__.py").write_text(
            "def traces(*args, **kwargs):\n"
            "    def wrap(fn):\n"
            "        return fn\n"
            "    return wrap\n",
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_mod.py").write_text(test_body, encoding="utf-8")
        return tmp_path

    def _spans(self) -> dict[str, SimpleNamespace]:
        trace = SimpleNamespace(start=5, end=6, llr_ids=["LLR-0001"])
        return {"src/mod.py": SimpleNamespace(traces=[trace])}

    def test_weak_suite_yields_survivors(self, tmp_path: Path) -> None:
        ws = self._build_workspace(
            tmp_path,
            "from src.mod import is_equal\n"
            "\n"
            "\n"
            "def test_runs():\n"
            "    is_equal(1, 1)  # exercises, never asserts\n",
        )
        gaps = run_mutation_round(ws, self._spans())
        assert gaps
        assert all(g.kind is GapKind.WEAK_CASE for g in gaps)

    def test_strong_suite_kills_mutants(self, tmp_path: Path) -> None:
        ws = self._build_workspace(
            tmp_path,
            "from src.mod import is_equal\n"
            "\n"
            "\n"
            "def test_discriminates():\n"
            "    assert is_equal(1, 1) is True\n"
            "    assert is_equal(1, 2) is False\n",
        )
        gaps = run_mutation_round(ws, self._spans())
        assert gaps == []
