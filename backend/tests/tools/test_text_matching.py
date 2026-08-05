"""Tests for the text_matching module (extracted from file_patch)."""

from __future__ import annotations

from backend.tools.text_matching import (
    apply_indent_offset,
    detect_indent_unit,
    find_span_start,
    find_stripped_matches,
    find_ws_span_start,
    has_tab_indent,
    match_uniform_offset,
    merge,
    perfect_or_whitespace,
    perfect_replace,
    prep,
    reindent_replacement,
    resolve_range,
    whitespace_replace,
)

# ── resolve_range ─────────────────────────────────────────────────────────────


class TestResolveRange:
    def test_no_range(self) -> None:
        lines = ["a\n", "b\n", "c\n"]
        assert resolve_range(lines, 0, 0) == (0, 3)

    def test_one_indexed_to_zero_indexed(self) -> None:
        lines = ["a\n", "b\n", "c\n", "d\n"]
        assert resolve_range(lines, 2, 3) == (1, 3)

    def test_end_clamped_to_length(self) -> None:
        lines = ["a\n", "b\n"]
        assert resolve_range(lines, 1, 9999) == (0, 2)

    def test_start_clamped_to_zero(self) -> None:
        lines = ["a\n", "b\n"]
        assert resolve_range(lines, -5, 2) == (0, 2)


# ── prep ──────────────────────────────────────────────────────────────────────


class TestPrep:
    def test_empty_string(self) -> None:
        assert prep("") == []

    def test_ensures_trailing_newline(self) -> None:
        result = prep("hello")
        assert result == ["hello\n"]

    def test_preserves_existing_newlines(self) -> None:
        result = prep("a\nb\n")
        assert result == ["a\n", "b\n"]

    def test_multiline_without_trailing(self) -> None:
        result = prep("a\nb")
        assert result == ["a\n", "b\n"]


# ── perfect_replace ──────────────────────────────────────────────────────────


class TestPerfectReplace:
    def test_unique_match(self) -> None:
        whole = ["a\n", "b\n", "c\n"]
        result = perfect_replace(whole, ["b\n"], ["x\n"])
        assert result == ["a\n", "x\n", "c\n"]

    def test_no_match(self) -> None:
        whole = ["a\n", "b\n"]
        assert perfect_replace(whole, ["z\n"], ["x\n"]) is None

    def test_multiple_matches(self) -> None:
        whole = ["a\n", "b\n", "a\n"]
        assert perfect_replace(whole, ["a\n"], ["x\n"]) is None

    def test_multiline_part(self) -> None:
        whole = ["a\n", "b\n", "c\n", "d\n"]
        result = perfect_replace(whole, ["b\n", "c\n"], ["x\n"])
        assert result == ["a\n", "x\n", "d\n"]


# ── match_uniform_offset ────────────────────────────────────────────────────


class TestMatchUniformOffset:
    def test_same_indent(self) -> None:
        whole = ["    a\n", "    b\n"]
        part = ["    a\n", "    b\n"]
        assert match_uniform_offset(whole, part) == 0

    def test_positive_offset(self) -> None:
        whole = ["    a\n", "    b\n"]
        part = ["  a\n", "  b\n"]
        assert match_uniform_offset(whole, part) == 2

    def test_negative_offset(self) -> None:
        whole = ["  a\n", "  b\n"]
        part = ["    a\n", "    b\n"]
        assert match_uniform_offset(whole, part) == -2

    def test_non_uniform_offset(self) -> None:
        whole = ["    a\n", "  b\n"]
        part = ["  a\n", "  b\n"]
        # First line differs by 2, second by 0 -- not uniform
        assert match_uniform_offset(whole, part) is None

    def test_content_mismatch(self) -> None:
        whole = ["  a\n"]
        part = ["  z\n"]
        assert match_uniform_offset(whole, part) is None

    def test_blank_lines_ignored(self) -> None:
        whole = ["    a\n", "\n", "    b\n"]
        part = ["  a\n", "\n", "  b\n"]
        assert match_uniform_offset(whole, part) == 2


# ── apply_indent_offset ─────────────────────────────────────────────────────


class TestApplyIndentOffset:
    def test_zero_offset(self) -> None:
        lines = ["  a\n"]
        assert apply_indent_offset(lines, 0) == ["  a\n"]

    def test_positive_offset(self) -> None:
        result = apply_indent_offset(["a\n", "  b\n"], 4)
        assert result == ["    a\n", "      b\n"]

    def test_negative_offset(self) -> None:
        result = apply_indent_offset(["    a\n", "      b\n"], -4)
        assert result == ["a\n", "  b\n"]

    def test_blank_lines_unchanged(self) -> None:
        result = apply_indent_offset(["  a\n", "\n", "  b\n"], 2)
        assert result[1] == "\n"

    def test_negative_offset_clamped(self) -> None:
        """Stripping more than available indent stops at column 0."""
        result = apply_indent_offset(["  a\n"], -10)
        assert result == ["a\n"]


# ── find_stripped_matches ────────────────────────────────────────────────────


class TestFindStrippedMatches:
    def test_single_match(self) -> None:
        whole = ["    def foo():\n", "        pass\n"]
        part = ["def foo():\n", "    pass\n"]
        assert find_stripped_matches(whole, part) == [0]

    def test_no_match(self) -> None:
        whole = ["def foo():\n"]
        part = ["def bar():\n"]
        assert find_stripped_matches(whole, part) == []

    def test_multiple_matches(self) -> None:
        whole = ["    x\n", "    y\n", "    x\n", "    y\n"]
        part = ["x\n", "y\n"]
        assert find_stripped_matches(whole, part) == [0, 2]


# ── reindent_replacement ────────────────────────────────────────────────────


class TestReindentReplacement:
    def test_scales_2_to_4_space_indent(self) -> None:
        matched = ["    def foo():\n", "        return 1\n"]
        part = ["  def foo():\n", "    return 1\n"]
        replace = ["  def foo():\n", "    return 42\n"]
        result = reindent_replacement(matched, part, replace)
        assert result == ["    def foo():\n", "        return 42\n"]

    def test_preserves_blank_lines(self) -> None:
        matched = ["    a\n", "\n", "    b\n"]
        part = ["  a\n", "\n", "  b\n"]
        replace = ["  a\n", "\n", "  c\n"]
        result = reindent_replacement(matched, part, replace)
        assert result[1] == "\n"

    def test_tab_indent_returns_as_is(self) -> None:
        matched = ["\tdef foo():\n", "\t\treturn 1\n"]
        part = ["def foo():\n", "    return 1\n"]
        replace = ["def foo():\n", "    return 42\n"]
        result = reindent_replacement(matched, part, replace)
        # Tab indented matched lines -> returns replace as-is
        assert result == list(replace)

    def test_zero_indent_unit_returns_as_is(self) -> None:
        """When all lines are at column 0 (indent unit = 0), return as-is."""
        matched = ["a\n", "b\n"]
        part = ["a\n", "b\n"]
        replace = ["c\n", "d\n"]
        result = reindent_replacement(matched, part, replace)
        assert result == list(replace)


# ── has_tab_indent ───────────────────────────────────────────────────────────


class TestHasTabIndent:
    def test_no_tabs(self) -> None:
        assert has_tab_indent(["    a\n", "    b\n"]) is False

    def test_tab_indent(self) -> None:
        assert has_tab_indent(["\ta\n", "\tb\n"]) is True

    def test_mixed_tab_in_indent(self) -> None:
        assert has_tab_indent(["  \ta\n"]) is True

    def test_tab_in_content_not_indent(self) -> None:
        """Tab within content (after non-whitespace) is not tab indent."""
        assert has_tab_indent(["a\tb\n"]) is False

    def test_blank_lines_ignored(self) -> None:
        assert has_tab_indent(["\n", "  \n"]) is False


# ── detect_indent_unit ───────────────────────────────────────────────────────


class TestDetectIndentUnit:
    def test_empty_widths(self) -> None:
        assert detect_indent_unit([]) == 0

    def test_all_zeros(self) -> None:
        assert detect_indent_unit([0, 0, 0]) == 0

    def test_uniform_4_space(self) -> None:
        assert detect_indent_unit([4, 8, 12]) == 4

    def test_uniform_2_space(self) -> None:
        assert detect_indent_unit([2, 4, 6]) == 2

    def test_mixed_with_gcd(self) -> None:
        assert detect_indent_unit([6, 4, 2]) == 2

    def test_single_indent(self) -> None:
        assert detect_indent_unit([4]) == 4

    def test_zero_and_nonzero(self) -> None:
        """Zero widths are filtered out before computing GCD."""
        assert detect_indent_unit([0, 4, 8]) == 4


# ── whitespace_replace ──────────────────────────────────────────────────────


class TestWhitespaceReplace:
    def test_uniform_offset_match(self) -> None:
        whole = ["    def foo():\n", "        return 1\n"]
        part = ["def foo():\n", "    return 1\n"]
        replace = ["def foo():\n", "    return 42\n"]
        result = whitespace_replace(whole, part, replace)
        assert result is not None
        assert "        return 42\n" in result

    def test_no_match_different_content(self) -> None:
        whole = ["def foo():\n"]
        part = ["def bar():\n"]
        replace = ["def baz():\n"]
        assert whitespace_replace(whole, part, replace) is None


# ── perfect_or_whitespace ────────────────────────────────────────────────────


class TestPerfectOrWhitespace:
    def test_perfect_match(self) -> None:
        lines = ["a\n", "b\n", "c\n"]
        result = perfect_or_whitespace(lines, 0, 3, "b", "x")
        assert result is not None
        patched, line_num = result
        assert "x\n" in patched
        assert line_num == 2

    def test_whitespace_flexible_match(self) -> None:
        lines = ["    def foo():\n", "        return 1\n"]
        result = perfect_or_whitespace(
            lines, 0, 2,
            "def foo():\n    return 1",
            "def foo():\n    return 42",
        )
        assert result is not None

    def test_no_match(self) -> None:
        lines = ["a\n", "b\n"]
        result = perfect_or_whitespace(lines, 0, 2, "zzz", "xxx")
        assert result is None


# ── find_span_start ──────────────────────────────────────────────────────────


class TestFindSpanStart:
    def test_finds_match(self) -> None:
        whole = ["a\n", "b\n", "c\n"]
        assert find_span_start(whole, ["b\n"]) == 1

    def test_no_match_returns_zero(self) -> None:
        whole = ["a\n", "b\n"]
        assert find_span_start(whole, ["z\n"]) == 0


# ── find_ws_span_start ──────────────────────────────────────────────────────


class TestFindWsSpanStart:
    def test_finds_match(self) -> None:
        whole = ["    a\n", "    b\n"]
        part = ["a\n", "b\n"]
        assert find_ws_span_start(whole, part) == 0

    def test_no_match_returns_zero(self) -> None:
        whole = ["a\n"]
        part = ["z\n"]
        assert find_ws_span_start(whole, part) == 0


# ── merge ─────────────────────────────────────────────────────────────────────


class TestMerge:
    def test_merge_replaces_region(self) -> None:
        whole = ["a\n", "b\n", "c\n", "d\n"]
        result = merge(whole, 1, 3, ["x\n"])
        assert result == "a\nx\nd\n"

    def test_merge_at_start(self) -> None:
        whole = ["a\n", "b\n", "c\n"]
        result = merge(whole, 0, 1, ["x\n", "y\n"])
        assert result == "x\ny\nb\nc\n"

    def test_merge_at_end(self) -> None:
        whole = ["a\n", "b\n", "c\n"]
        result = merge(whole, 2, 3, ["z\n"])
        assert result == "a\nb\nz\n"
