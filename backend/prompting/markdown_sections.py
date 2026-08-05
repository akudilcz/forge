"""Markdown section helpers — section-aware extraction for long docs.

Thin wrapper around ``langchain_text_splitters.MarkdownHeaderTextSplitter``
(already a project dependency). Replaces positional char-range slicing
(e.g. ``content[:2000]``) with semantic extraction by heading — so we
keep entire sections in full rather than truncating mid-paragraph.

Used in Phase 4 (whitepaper digest), Phase 5 (ARCHITECTURE Module Design
and Data Flow sections), and Phase 6 (ARCHITECTURE Tech Stack and
Cross-Cutting Concerns sections).
"""

from __future__ import annotations

from langchain_text_splitters import MarkdownHeaderTextSplitter

_DEFAULT_HEADERS: list[tuple[str, str]] = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
]


def extract_sections(
    markdown: str,
    headings: list[str],
    *,
    case_insensitive: bool = True,
) -> str:
    """Return the subset of ``markdown`` whose section headings match
    ``headings``. Headings match against h2 (``##``) and h3 (``###``)
    labels. Full section content is preserved — no truncation.

    If no headings match, returns an empty string.
    """
    if not markdown or not headings:
        return ""

    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_DEFAULT_HEADERS,
        strip_headers=False,
    )
    docs = splitter.split_text(markdown)

    wanted = {h.strip().lower() if case_insensitive else h.strip() for h in headings}

    kept: list[str] = []
    for d in docs:
        meta = d.metadata or {}
        labels = [
            (meta.get("h1", "") or "").strip(),
            (meta.get("h2", "") or "").strip(),
            (meta.get("h3", "") or "").strip(),
        ]
        if case_insensitive:
            labels = [label.lower() for label in labels]
        if any(label in wanted for label in labels if label):
            kept.append(d.page_content)

    return "\n\n".join(kept)
