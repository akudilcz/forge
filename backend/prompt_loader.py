"""Jinja2 template loader for LLM prompts.

All prompt templates live in ``templates/`` at the repository root.
This module provides a single ``render()`` function that loads and
renders a template by path relative to that directory.

Usage::

    from backend.prompt_loader import render

    prompt = render("codegen/slice_system.j2", src_path="src/foo.py", ...)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@lru_cache(maxsize=1)
def _env() -> Environment:
    """Return a cached Jinja2 environment rooted at templates/."""
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def render(template_path: str, **kwargs: object) -> str:
    """Render a Jinja2 template with the given variables.

    Args:
        template_path: Path relative to templates/, e.g. "codegen/slice_system.j2".
        **kwargs: Template variables.

    Returns:
        Rendered string with trailing whitespace stripped.
    """
    template = _env().get_template(template_path)
    return template.render(**kwargs).rstrip()
