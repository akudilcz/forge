"""file_patch — SOTA text replacement with fuzzy matching, line hints, and lint.

Combines the best approaches from SWE-agent, Aider, and RooCode:

1. **Exact match** — fast path, identical to Claude Code / OpenCode.
2. **Whitespace-tolerant match** (Aider's approach) — strips minimum
   common leading whitespace from old_text, finds where the stripped
   version matches in the file, then re-indents new_text to match
   the file's actual indentation.
3. **Line range narrowing** (SWE-agent ``--range``) — optional
   start_line/end_line to disambiguate when old_text appears
   multiple times.
4. **Syntax validation** (SWE-agent linter) — rejects edits that
   produce invalid Python, with the parse error fed back immediately.
5. **Rich error feedback** — when old_text isn't found, shows actual
   file content and suggests similar lines (Aider's find_similar_lines).
6. **Post-edit context** — returns the modified region with line
   numbers so the agent can verify (SWE-agent feedback).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool
from backend.tools.text_matching import perfect_or_whitespace, resolve_range
from backend.tools.write_validation import check_syntax, resolve_in_workspace

# ── Constants ────────────────────────────────────────────────────────────────

_CONTEXT_LINES = 3
_SIMILARITY_THRESHOLD = 0.6


# ── Args ─────────────────────────────────────────────────────────────────────


class _Args(BaseModel):
    path: str = Field(description="Relative path to the file to patch.")
    old_text: str = Field(
        description=(
            "Text to find and replace. Must be unique in the file "
            "(or unique within start_line..end_line). Minor leading "
            "whitespace differences are tolerated."
        ),
    )
    new_text: str = Field(description="Replacement text.")
    start_line: int = Field(
        default=0,
        description=(
            "Narrow search to lines >= start_line (1-indexed). "
            "Use with end_line when old_text appears multiple times."
        ),
    )
    end_line: int = Field(
        default=0,
        description=(
            "Narrow search to lines <= end_line (1-indexed, inclusive). "
            "Use with start_line when old_text appears multiple times."
        ),
    )


# ── Tool ─────────────────────────────────────────────────────────────────────


class FilePatchTool(ForgeTool):
    """Apply a targeted text replacement with whitespace tolerance and lint.

    Matching cascade (like Aider):
    1. Perfect exact match
    2. Whitespace-flexible match (uniform leading whitespace offset)
    On success for .py files, validates syntax before writing.
    On error, shows actual file content and suggests similar lines.
    """

    name: str = "file_patch"
    description: str = (
        "Replace text in a file. old_text must be unique (or unique within "
        "start_line..end_line). Tolerates minor leading whitespace "
        "differences. For .py files, rejects edits that break syntax. "
        "On error, shows actual file content. On success, shows the "
        "edited region with line numbers."
    )
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""

    def __init__(self, workspace: str) -> None:
        super().__init__()
        object.__setattr__(self, "_workspace", workspace)

    def _execute(
        self,
        path: str,
        old_text: str,
        new_text: str,
        start_line: int = 0,
        end_line: int = 0,
    ) -> str:
        target = resolve_in_workspace(self._workspace, path)
        if not target.exists():
            return f"ERROR: File not found: {path}"
        try:
            original = target.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return f"ERROR reading {path}: {exc}"

        whole_lines = original.splitlines(keepends=True)
        s, e = resolve_range(whole_lines, start_line, end_line)

        result = _try_matching_cascade(original, whole_lines, s, e, old_text, new_text)
        if result is None:
            return _not_found_error(path, whole_lines, s, e, old_text)

        patched, match_line = result
        return _validate_and_write(target, path, patched, new_text, match_line)


# ── Matching cascade ─────────────────────────────────────────────────────────


def _try_matching_cascade(
    original: str,
    whole_lines: list[str],
    s: int,
    e: int,
    old_text: str,
    new_text: str,
) -> tuple[str, int] | None:
    """Run the full matching cascade: string, perfect, whitespace-flex.

    Tries within the given range first, then falls back to the full file
    if a range was specified and the first attempt failed.
    """
    # Step 0: string-level exact match (handles sub-line edits)
    result = _try_string_match(original, whole_lines, s, e, old_text, new_text)

    # Step 1+2: line-level exact + whitespace-flexible
    if result is None:
        result = perfect_or_whitespace(whole_lines, s, e, old_text, new_text)

    # Fallback: if range was specified and text is unique in full file
    if result is None and (s > 0 or e < len(whole_lines)):
        result = _try_string_match(
            original, whole_lines, 0, len(whole_lines), old_text, new_text,
        )
        if result is None:
            result = perfect_or_whitespace(
                whole_lines, 0, len(whole_lines), old_text, new_text,
            )

    return result


# ── Syntax validation + write ─────────────────────────────────────────────────


def _validate_and_write(
    target: Path,
    path: str,
    patched: str,
    new_text: str,
    match_line: int,
) -> str:
    """Validate Python syntax (if applicable), write file, return response.

    A ``.py`` file whose post-patch content fails ``ast.parse`` is never
    persisted — even when the patch reduces the number of syntax errors.
    The parse error (with line number) is returned so the agent can send
    a corrected patch instead of landing invalid Python that would only
    surface at the next full scan.
    """
    if path.endswith(".py"):
        lint_err = check_syntax(patched, path)
        if lint_err:
            hint = _syntax_hint(patched, new_text, lint_err)
            return (
                f"ERROR: Edit would produce invalid Python in {path}:\n"
                f"{lint_err}\n{hint}"
            )

    try:
        target.write_text(patched, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"ERROR writing {path}: {exc}"

    return _success_response(path, patched, match_line)


# ── String-level matching (sub-line edits) ───────────────────────────────────


def _try_string_match(
    original: str,
    whole_lines: list[str],
    range_start: int,
    range_end: int,
    old_text: str,
    new_text: str,
) -> tuple[str, int] | None:
    """Simple string replacement — handles sub-line and single-line edits."""
    if not old_text:
        return None

    if range_start == 0 and range_end == len(whole_lines):
        region = original
    else:
        region = "".join(whole_lines[range_start:range_end])

    count = region.count(old_text)
    if count != 1:
        return None

    # Find the line number of the match
    idx = region.find(old_text)
    match_line = range_start + region[:idx].count("\n") + 1

    if range_start == 0 and range_end == len(whole_lines):
        patched = original.replace(old_text, new_text, 1)
    else:
        before = "".join(whole_lines[:range_start])
        after = "".join(whole_lines[range_end:])
        patched = before + region.replace(old_text, new_text, 1) + after

    return patched, match_line


# ── Syntax-error hints ───────────────────────────────────────────────────────


def _syntax_hint(patched: str, new_text: str, error: str) -> str:
    """Provide actionable guidance for common syntax errors.

    Detects patterns like @traces placed inside a function body
    and suggests the correct approach.
    """
    if "@traces" not in new_text and "@traces" not in error:
        return "Fix the new_text and retry."

    for line in new_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("@traces"):
            continue
        if not stripped.startswith("@traces("):
            continue
        idx = patched.find(stripped)
        if idx <= 0:
            continue
        before = patched[:idx].rstrip()
        last_line = before.splitlines()[-1].strip() if before else ""
        if _is_decorator_misplaced(last_line):
            return (
                "Hint: @traces decorator must go BEFORE the def line, "
                "not inside the function body.\n"
                "If the function is already defined, use the call form "
                "instead:\n"
                '  func_name = traces("LLR-XXXX")(func_name)'
            )

    return "Fix the new_text and retry."


def _is_decorator_misplaced(last_line: str) -> bool:
    """Check if a decorator appears after a def or docstring line."""
    return (
        last_line.startswith("def ")
        or last_line.startswith('"""')
        or last_line.startswith("'''")
    )


# ── Error feedback ───────────────────────────────────────────────────────────


def _not_found_error(
    path: str,
    lines: list[str],
    range_start: int,
    range_end: int,
    old_text: str,
) -> str:
    """Rich error with actual content and similar-line suggestions."""
    total = len(lines)
    full_text = "".join(lines)
    full_count = full_text.count(old_text)

    parts = [f"ERROR: old_text not found in {path}"]

    if full_count > 0 and (range_start > 0 or range_end < total):
        parts.append(
            f"  (found {full_count} occurrence(s) outside the "
            f"line {range_start + 1}..{range_end} range — "
            "adjust start_line/end_line)"
        )
    elif full_count > 1:
        parts.append(
            f"  (found {full_count} occurrences — use start_line/end_line "
            "to select one, or add more context to make old_text unique)"
        )

    # Show actual content at target region
    show_s = range_start
    show_e = min(range_start + 15, range_end)
    if show_s < show_e:
        snippet = _format_lines(lines[show_s:show_e], show_s + 1)
        parts.append(f"Actual content at lines {show_s + 1}..{show_e}:")
        parts.append(snippet)

    # Suggest similar lines (Aider's find_similar_lines)
    similar = _find_similar_lines(
        old_text, "".join(lines[range_start:range_end]),
    )
    if similar:
        parts.append(f"Did you mean:\n{similar}")

    return "\n".join(parts)


def _find_similar_lines(search: str, content: str) -> str:
    """Find the most similar chunk in content (Aider's approach)."""
    search_lines = search.splitlines()
    content_lines = content.splitlines()
    if not search_lines or not content_lines:
        return ""

    best_ratio = 0.0
    best_i = 0

    for i in range(len(content_lines) - len(search_lines) + 1):
        chunk = content_lines[i : i + len(search_lines)]
        ratio = SequenceMatcher(None, search_lines, chunk).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_i = i

    if best_ratio < _SIMILARITY_THRESHOLD:
        return ""

    # Show the similar chunk with some context
    ctx_start = max(0, best_i - 2)
    ctx_end = min(len(content_lines), best_i + len(search_lines) + 2)
    return "\n".join(
        f"  {ctx_start + j + 1:4d} | {line}"
        for j, line in enumerate(content_lines[ctx_start:ctx_end])
    )


# ── Success feedback ─────────────────────────────────────────────────────────


def _format_lines(lines: list[str], start_1indexed: int) -> str:
    """Format lines with line numbers for display."""
    return "\n".join(
        f"  {start_1indexed + i:4d} | {line.rstrip()}"
        for i, line in enumerate(lines)
    )


def _success_response(path: str, patched: str, match_line: int) -> str:
    """Return OK message with a snippet of the edited region."""
    lines = patched.splitlines(keepends=True)
    total = len(lines)
    ctx_start = max(match_line - 1 - _CONTEXT_LINES, 0)
    ctx_end = min(match_line - 1 + _CONTEXT_LINES + 5, total)
    snippet = _format_lines(lines[ctx_start:ctx_end], ctx_start + 1)
    return f"OK: patched {path}\n{snippet}"
