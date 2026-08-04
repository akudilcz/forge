"""text_matching — whitespace-tolerant text matching and replacement.

Extracted from file_patch.py to keep modules focused.  These functions
implement the matching cascade inspired by Aider and RooCode:

1. **Perfect match** — exact line-by-line comparison.
2. **Whitespace-flexible match** — uniform leading whitespace offset
   (Aider) or stripped match with indent re-scaling.
3. **Range resolution** — convert 1-indexed start/end to 0-indexed.
"""

from __future__ import annotations

from functools import reduce
from math import gcd

# ── Range resolution ──────────────────────────────────────────────────────────


def resolve_range(
    lines: list[str], start: int, end: int,
) -> tuple[int, int]:
    """Convert 1-indexed start/end to 0-indexed half-open range."""
    s = max(start - 1, 0) if start > 0 else 0
    e = min(end, len(lines)) if end > 0 else len(lines)
    return s, e


# ── Line preparation ──────────────────────────────────────────────────────────


def prep(text: str) -> list[str]:
    """Split text into lines with endings preserved."""
    if not text:
        return []
    lines = text.splitlines(keepends=True)
    # Ensure last line has a newline
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines


# ── Top-level cascade ─────────────────────────────────────────────────────────


def perfect_or_whitespace(
    whole_lines: list[str],
    range_start: int,
    range_end: int,
    old_text: str,
    new_text: str,
) -> tuple[str, int] | None:
    """Try perfect match, then whitespace-flexible match."""
    part_lines = prep(old_text)
    replace_lines = prep(new_text)

    region = whole_lines[range_start:range_end]

    # 1. Perfect match
    result = perfect_replace(region, part_lines, replace_lines)
    if result is not None:
        merged = merge(whole_lines, range_start, range_end, result)
        match_line = range_start + find_span_start(region, part_lines) + 1
        return merged, match_line

    # 2. Whitespace-flexible match (Aider's approach)
    result = whitespace_replace(region, part_lines, replace_lines)
    if result is not None:
        merged = merge(whole_lines, range_start, range_end, result)
        match_line = range_start + find_ws_span_start(region, part_lines) + 1
        return merged, match_line

    return None


# ── Perfect (exact) replacement ───────────────────────────────────────────────


def perfect_replace(
    whole_lines: list[str],
    part_lines: list[str],
    replace_lines: list[str],
) -> list[str] | None:
    """Exact line-by-line match. Returns new whole_lines or None."""
    part_tup = tuple(part_lines)
    part_len = len(part_lines)
    matches = []

    for i in range(len(whole_lines) - part_len + 1):
        if tuple(whole_lines[i : i + part_len]) == part_tup:
            matches.append(i)

    if len(matches) != 1:
        return None

    i = matches[0]
    return whole_lines[:i] + replace_lines + whole_lines[i + part_len :]


# ── Whitespace-flexible replacement ──────────────────────────────────────────


def whitespace_replace(
    whole_lines: list[str],
    part_lines: list[str],
    replace_lines: list[str],
) -> list[str] | None:
    """Match ignoring leading whitespace differences.

    Two strategies (tried in order):
    1. Uniform offset (Aider) — all lines differ by the same prefix.
    2. Stripped match — all lines match after lstrip(), and we map
       the file's per-line indentation onto the replacement.
       Handles 2-space vs 4-space indent differences.
    """
    num_part = len(part_lines)

    # Strategy 1: Uniform offset (Aider's approach)
    uniform_matches: list[tuple[int, int]] = []
    for i in range(len(whole_lines) - num_part + 1):
        offset = match_uniform_offset(
            whole_lines[i : i + num_part], part_lines,
        )
        if offset is not None:
            uniform_matches.append((i, offset))

    if len(uniform_matches) == 1:
        i, offset = uniform_matches[0]
        indented = apply_indent_offset(replace_lines, offset)
        return whole_lines[:i] + indented + whole_lines[i + num_part :]

    # Strategy 2: Stripped match (handles different indent widths)
    matches = find_stripped_matches(whole_lines, part_lines)
    if len(matches) != 1:
        return None

    i = matches[0]
    matched = whole_lines[i : i + num_part]
    reindented = reindent_replacement(matched, part_lines, replace_lines)
    return whole_lines[:i] + reindented + whole_lines[i + num_part :]


# ── Uniform offset matching ──────────────────────────────────────────────────


def match_uniform_offset(
    whole_lines: list[str], part_lines: list[str],
) -> int | None:
    """Check if lines match ignoring a uniform leading whitespace offset.

    Returns the integer offset (file_indent - part_indent), or None if
    no match.  Positive means the file has more indent than the search
    text; negative means the search text has excess indent.
    Adapted from Aider's match_but_for_leading_whitespace.
    """
    num = len(whole_lines)
    if num != len(part_lines):
        return None

    if not all(
        whole_lines[i].strip() == part_lines[i].strip()
        for i in range(num)
    ):
        return None

    offsets = set()
    for i in range(num):
        if not whole_lines[i].strip():
            continue
        w_ws = len(whole_lines[i]) - len(whole_lines[i].lstrip())
        p_ws = len(part_lines[i]) - len(part_lines[i].lstrip())
        offsets.add(w_ws - p_ws)

    if len(offsets) != 1:
        return None
    return offsets.pop()


def apply_indent_offset(lines: list[str], offset: int) -> list[str]:
    """Apply a uniform whitespace offset to replacement lines.

    Positive offset adds spaces; negative offset strips leading characters.
    """
    if offset == 0:
        return list(lines)
    result: list[str] = []
    for line in lines:
        if not line.strip():
            result.append(line)
            continue
        if offset > 0:
            result.append(" " * offset + line)
        else:
            leading = len(line) - len(line.lstrip())
            strip = min(-offset, leading)
            result.append(line[strip:])
    return result


# ── Stripped matching ─────────────────────────────────────────────────────────


def find_stripped_matches(
    whole_lines: list[str], part_lines: list[str],
) -> list[int]:
    """Find positions where lines match after lstrip()."""
    num_part = len(part_lines)
    matches: list[int] = []

    for i in range(len(whole_lines) - num_part + 1):
        if all(
            whole_lines[i + j].strip() == part_lines[j].strip()
            for j in range(num_part)
        ):
            matches.append(i)
    return matches


# ── Indent re-scaling ─────────────────────────────────────────────────────────


def reindent_replacement(
    matched_lines: list[str],
    part_lines: list[str],
    replace_lines: list[str],
) -> list[str]:
    """Map the file's indentation onto replacement lines.

    For each non-empty line in replace_lines, compute its indent
    relative to the part_lines base, then apply the matched_lines
    indent scale.
    """
    # If file or replacement uses tab indentation, character-count
    # scaling doesn't work — return replacement as-is.
    if has_tab_indent(matched_lines) or has_tab_indent(replace_lines):
        return list(replace_lines)

    # Compute indent ratio from first indented line
    part_widths = [len(ln) - len(ln.lstrip()) for ln in part_lines if ln.strip()]
    file_widths = [
        len(ln) - len(ln.lstrip()) for ln in matched_lines if ln.strip()
    ]

    # Find the indent unit used in each (e.g. 2 vs 4)
    part_unit = detect_indent_unit(part_widths)
    file_unit = detect_indent_unit(file_widths)

    if part_unit == 0 or file_unit == 0:
        # Can't determine ratio — use replacement as-is
        return list(replace_lines)

    result: list[str] = []
    for line in replace_lines:
        if not line.strip():
            result.append(line)
            continue
        old_indent = len(line) - len(line.lstrip())
        # Scale the indent: convert from part's indent width to file's
        new_indent = (old_indent * file_unit) // part_unit
        result.append(" " * new_indent + line.lstrip())
    return result


def has_tab_indent(lines: list[str]) -> bool:
    """Check if any non-empty line uses tab indentation."""
    return any(
        "\t" in line[: len(line) - len(line.lstrip())]
        for line in lines
        if line.strip()
    )


def detect_indent_unit(widths: list[int]) -> int:
    """Detect the smallest non-zero indent difference (e.g. 2 or 4)."""
    if not widths:
        return 0
    diffs = set()
    for w in widths:
        if w > 0:
            diffs.add(w)
    if not diffs:
        return 0
    # GCD of all non-zero widths gives the indent unit
    return reduce(gcd, diffs)


# ── Span finding ──────────────────────────────────────────────────────────────


def find_span_start(
    whole_lines: list[str], part_lines: list[str],
) -> int:
    """Find the start index of a perfect match (for line number reporting)."""
    part_tup = tuple(part_lines)
    part_len = len(part_lines)
    for i in range(len(whole_lines) - part_len + 1):
        if tuple(whole_lines[i : i + part_len]) == part_tup:
            return i
    return 0


def find_ws_span_start(
    whole_lines: list[str], part_lines: list[str],
) -> int:
    """Find start index of a whitespace-flexible match."""
    matches = find_stripped_matches(whole_lines, part_lines)
    return matches[0] if matches else 0


# ── Merge ─────────────────────────────────────────────────────────────────────


def merge(
    whole_lines: list[str],
    range_start: int,
    range_end: int,
    region_result: list[str],
) -> str:
    """Merge a patched region back into the full file."""
    before = whole_lines[:range_start]
    after = whole_lines[range_end:]
    return "".join(before + region_result + after)
