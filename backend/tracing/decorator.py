"""Requirement traceability decorator.

Attaches LLR and CASE IDs to functions and classes as metadata that is
introspectable at runtime and detectable statically via AST.

Usage — source functions::

    @traces("LLR-0002", "LLR-0003")
    def plan(self, start, goal):
        ...

Usage — test functions::

    @traces("LLR-0003", case="CASE_LLR-0003")
    def test_plan_avoids_obstacles(self):
        ...

Multiple cases::

    @traces("LLR-0001", case=["CASE_LLR-0001-01", "CASE_LLR-0001-02"])
    def test_auth_flow(self):
        ...
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])


def traces(
    *llr_ids: str,
    case: str | list[str] | None = None,
) -> Callable[[_F], _F]:
    """Annotate a function or class with requirement trace IDs.

    Parameters
    ----------
    *llr_ids:
        One or more LLR IDs (e.g. ``"LLR-0001"``, ``"LLR-0002"``).
    case:
        Optional CASE ID(s) for test functions.
    """
    if case is None:
        case_list: list[str] = []
    elif isinstance(case, str):
        case_list = [case]
    else:
        case_list = list(case)

    def decorator(obj: _F) -> _F:
        obj._trace_llrs = list(llr_ids)  # type: ignore[attr-defined]
        obj._trace_cases = case_list  # type: ignore[attr-defined]
        return obj

    return decorator
