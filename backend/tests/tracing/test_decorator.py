"""Tests for backend/tracing/decorator.py — the @traces annotation."""

from __future__ import annotations

from backend.tracing import traces


def test_traces_attaches_llr_ids_without_case() -> None:
    @traces("LLR-0001", "LLR-0002")
    def fn() -> str:
        return "ok"

    assert fn._trace_llrs == ["LLR-0001", "LLR-0002"]  # type: ignore[attr-defined]
    assert fn._trace_cases == []  # type: ignore[attr-defined]
    assert fn() == "ok"  # decorated function still callable, unchanged


def test_traces_with_single_case_string() -> None:
    @traces("LLR-0003", case="CASE_LLR-0003")
    def test_fn() -> None:
        pass

    assert test_fn._trace_llrs == ["LLR-0003"]  # type: ignore[attr-defined]
    assert test_fn._trace_cases == ["CASE_LLR-0003"]  # type: ignore[attr-defined]


def test_traces_with_case_list() -> None:
    @traces("LLR-0001", case=["CASE_LLR-0001-01", "CASE_LLR-0001-02"])
    def test_fn() -> None:
        pass

    assert test_fn._trace_cases == ["CASE_LLR-0001-01", "CASE_LLR-0001-02"]  # type: ignore[attr-defined]


def test_traces_annotates_classes() -> None:
    @traces("LLR-0009")
    class Widget:
        pass

    assert Widget._trace_llrs == ["LLR-0009"]  # type: ignore[attr-defined]
    assert Widget._trace_cases == []  # type: ignore[attr-defined]
