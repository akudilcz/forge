"""Argument-parsing helpers shared by the graph-write tool family.

Extracted from ``graph_write.py`` (which re-exports them unchanged) so the
tool module stays within the project file-size budget. ``multi_graph_write``
uses them for its batch pre-validation dry run.
"""

from __future__ import annotations

import json
from typing import Any


def _parse_json_obj(raw: str) -> dict[str, Any]:
    """Parse a JSON string into a dict, returning empty dict on failure."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class _TraceToCoerceError(ValueError):
    """Raised by ``_coerce_to_list`` when the input cannot be interpreted as
    a trace_to value (caller converts this to a tool-level ERROR string)."""


def _coerce_to_list(raw: Any) -> list[str]:
    """Coerce a ``trace_to`` field into ``list[str]``.

    Accepts:
      * ``None`` / empty string / ``"[]"`` → ``[]``
      * ``list`` / ``tuple``               → cast elements to str
      * a JSON-string list ``'["x","y"]'`` → parse

    Raises ``_TraceToCoerceError`` on anything else (a bare string that
    isn't valid JSON, a dict, etc.) so callers can surface a clear
    ``ERROR: trace_to must be a JSON array of node ID strings`` back to
    the agent.

    Agents sometimes pass a native ``list`` rather than the tool's
    documented JSON-string shape; this helper handles that common case
    without raising ``'list' object has no attribute 'strip'``.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s == "[]":
            return []
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError as exc:
            raise _TraceToCoerceError(
                f"trace_to is not a JSON array: {s!r}"
            ) from exc
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        raise _TraceToCoerceError(
            f"trace_to must be a JSON array; got {type(parsed).__name__}"
        )
    raise _TraceToCoerceError(f"trace_to has unsupported type {type(raw).__name__}")


def _parse_trace_to(kwargs: dict[str, Any], props: dict[str, Any]) -> list[str]:
    """Extract trace_to targets from kwargs, falling back to props.

    Tolerant of both JSON-string lists and native Python lists (agents
    sometimes pass a list even though the tool schema specifies a JSON
    string). Returns ``[]`` on any parse failure (add_node callers
    proceed without trace_to rather than failing the whole op — the
    update-trace path is the strict one).

    The ``props`` fallback is extra-lenient: a bare string is wrapped in
    a list (historical behaviour preserved for compat).
    """
    try:
        trace_targets = _coerce_to_list(kwargs.get("trace_to"))
    except _TraceToCoerceError:
        trace_targets = []
    if not trace_targets:
        fallback = props.pop("trace_to", None)
        if isinstance(fallback, str) and fallback:
            trace_targets = [fallback]
        else:
            try:
                trace_targets = _coerce_to_list(fallback)
            except _TraceToCoerceError:
                trace_targets = []
    return trace_targets
