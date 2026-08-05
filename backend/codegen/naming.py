"""Shared naming utilities for code generation modules."""

from __future__ import annotations

import re


def slugify(title: str) -> str:
    """Convert a node title to a snake_case filename stem.

    Strips trailing "design"/"implementation"/"spec", lowercases,
    and replaces non-alphanumeric runs with underscores.
    """
    s = title.lower().strip()
    for suffix in ("design", "implementation", "spec"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "unnamed"
