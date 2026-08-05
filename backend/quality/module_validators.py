"""Validators that enforce invariants around MODULE class plans.

Keeps the rule that ``#DESIGN children of a MODULE ≤ #classes in its
class plan`` out of the graph engine (domain-specific) and out of
agent prompts (unenforced). Callers invoke these before invoking
``graph.add_node`` for a DESIGN.
"""

from __future__ import annotations

import re
from typing import Any

# Class-name patterns we'll accept in a MODULE class plan. We match:
#   - lines like "- Foo: does X"
#   - "1. Foo — ..." / "Foo — ..."
#   - "class Foo" / "Class: Foo"
# Python class names start with an uppercase letter, contain letters/digits/underscores.
_CLASS_NAME_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Za-z0-9_]{1,})(?![A-Za-z0-9_])")

# Only lines in a "class plan" section (or the whole content if absent).
_CLASS_PLAN_HEADER_RE = re.compile(
    r"(?:^|\n)\s*(?:##?\s+)?[Cc]lass[\s\-]*[Pp]lan\b.*?\n(.*?)(?:\n\s*##?\s+\S|\Z)",
    re.DOTALL,
)

# Words that look like class names but are reserved prose (to reduce false positives).
_STOPWORDS: frozenset[str] = frozenset({
    "The", "This", "That", "These", "Those",
    "It", "They", "We", "You", "I",
    "A", "An", "As", "At", "And", "Or",
    "If", "Is", "Are", "Be", "Do", "Does",
    "Module", "Class", "Classes", "Plan", "Role", "Roles",
    "Step", "Steps",
    "Responsibility", "Responsibilities",
    "Yes", "No",
    "Python", "System",
})


def count_planned_classes(module_content: str) -> int:
    """Return the number of distinct class names named in a MODULE's class plan.

    Returns 0 when the content is empty. Returns at least 1 when a class plan
    section exists but no names are detected (so a MODULE isn't accidentally
    capped to zero DESIGNs by a poorly-formatted plan).
    """
    if not module_content:
        return 0
    match = _CLASS_PLAN_HEADER_RE.search(module_content)
    plan = match.group(1) if match else module_content
    candidates = {
        name
        for name in _CLASS_NAME_RE.findall(plan)
        if name not in _STOPWORDS
    }
    if match and not candidates:
        # Class plan exists but is free prose — fall back to 1 class.
        return 1
    return len(candidates)


def check_design_count_allowed(
    graph: Any,
    module_id: str,
) -> str | None:
    """Return an error string when creating another DESIGN under ``module_id``
    would exceed the class-plan count. Returns ``None`` when allowed.
    """
    module = graph.node_sync(module_id)
    if module is None or module.node_type != "MODULE":
        return None
    limit = count_planned_classes(module.content or "")
    if limit <= 0:
        return None
    existing = sum(
        1 for c in graph.children_sync(module_id)
        if c.node_type == "DESIGN"
    )
    if existing < limit:
        return None
    return (
        f"Refusing to create another DESIGN under MODULE {module_id}: "
        f"{existing} DESIGN children already exist and the class plan names "
        f"{limit} class(es). Extend an existing DESIGN's trace_to instead, "
        f"or update the MODULE's class plan first if a new class is genuinely needed."
    )
