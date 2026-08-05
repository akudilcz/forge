"""Tests for the SOTA file_patch tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tools.file_patch import FilePatchTool

# ── Helpers ──────────────────────────────────────────────────────────────────


def _tool(tmp_path: Path) -> FilePatchTool:
    return FilePatchTool(workspace=str(tmp_path))


def _write(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    return f


# ── Exact match (basic) ─────────────────────────────────────────────────────


def test_exact_match_success(tmp_path: Path) -> None:
    """Simple exact replacement works."""
    _write(tmp_path, "a.py", "def old(): pass\n")
    result = _tool(tmp_path)._execute(path="a.py", old_text="def old(): pass", new_text="def new(): pass")
    assert "OK" in result
    assert (tmp_path / "a.py").read_text() == "def new(): pass\n"


def test_exact_match_multiline(tmp_path: Path) -> None:
    """Multi-line exact replacement works."""
    _write(tmp_path, "a.py", "def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def foo():\n    return 1",
        new_text="def foo():\n    return 42",
    )
    assert "OK" in result
    assert "return 42" in (tmp_path / "a.py").read_text()


def test_exact_match_not_found(tmp_path: Path) -> None:
    """Missing old_text returns error with actual file content."""
    _write(tmp_path, "a.py", "def foo():\n    pass\n")
    result = _tool(tmp_path)._execute(path="a.py", old_text="def bar():", new_text="x")
    assert "ERROR" in result
    assert "not found" in result
    # Should show actual content
    assert "def foo():" in result


def test_exact_match_multiple_no_range(tmp_path: Path) -> None:
    """Multiple occurrences without range hints returns error."""
    _write(tmp_path, "a.py", "@traces('A')\ndef f1(): pass\n\n@traces('A')\ndef f2(): pass\n")
    result = _tool(tmp_path)._execute(path="a.py", old_text="@traces('A')", new_text="@traces('B')")
    assert "ERROR" in result
    assert "not found" in result or "Hint" in result


def test_file_not_found(tmp_path: Path) -> None:
    """Non-existent file returns error."""
    result = _tool(tmp_path)._execute(path="missing.py", old_text="x", new_text="y")
    assert "ERROR" in result
    assert "not found" in result


# ── Line range disambiguation (SWE-agent --range) ───────────────────────────


def test_range_disambiguates_first_occurrence(tmp_path: Path) -> None:
    """start_line/end_line narrows search to first occurrence."""
    _write(tmp_path, "a.py", "@dec\ndef f1(): pass\n\n@dec\ndef f2(): pass\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="@dec", new_text="@new_dec",
        start_line=1, end_line=2,
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert content.startswith("@new_dec\n")
    # Second occurrence unchanged
    assert content.count("@dec") == 1
    assert content.count("@new_dec") == 1


def test_range_disambiguates_second_occurrence(tmp_path: Path) -> None:
    """Line range targets the second occurrence."""
    _write(tmp_path, "a.py", "@dec\ndef f1(): pass\n\n@dec\ndef f2(): pass\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="@dec", new_text="@new_dec",
        start_line=4, end_line=5,
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    lines = content.splitlines()
    assert lines[0] == "@dec"  # first unchanged
    assert lines[3] == "@new_dec"  # second changed


def test_range_fallback_when_unique_outside_range(tmp_path: Path) -> None:
    """Unique old_text outside range succeeds via full-file fallback."""
    _write(tmp_path, "a.py", "line1\nline2\nline3\nline4\nline5\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="line1", new_text="changed",
        start_line=3, end_line=5,
    )
    assert "OK" in result
    assert (tmp_path / "a.py").read_text().startswith("changed\n")


def test_range_error_when_ambiguous_outside_range(tmp_path: Path) -> None:
    """Multiple occurrences outside range still errors (range needed)."""
    _write(tmp_path, "a.py", "x = 1\nline2\nx = 1\nline4\nline5\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="x = 1", new_text="changed",
        start_line=4, end_line=5,
    )
    assert "ERROR" in result


# ── Whitespace-tolerant fuzzy matching (RooCode) ────────────────────────────


def test_fuzzy_match_indentation_difference(tmp_path: Path) -> None:
    """Matches despite different indentation width."""
    # File uses 4-space indent
    _write(tmp_path, "a.py", "class Foo:\n    def bar(self):\n        return 1\n")
    # Agent provides 2-space indent
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="class Foo:\n  def bar(self):\n    return 1",
        new_text="class Foo:\n    def bar(self):\n        return 42",
    )
    assert "OK" in result
    assert "return 42" in (tmp_path / "a.py").read_text()


def test_fuzzy_match_trailing_whitespace(tmp_path: Path) -> None:
    """Matches despite trailing whitespace differences."""
    _write(tmp_path, "a.py", "def foo():   \n    pass\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="def foo():\n    pass", new_text="def foo():\n    return 1",
    )
    assert "OK" in result
    assert "return 1" in (tmp_path / "a.py").read_text()


def test_fuzzy_match_uses_new_text_verbatim(tmp_path: Path) -> None:
    """Fuzzy match finds the location despite indent mismatch; new_text applied as-is."""
    _write(tmp_path, "a.py", "class C:\n    def method(self):\n        old_code\n")
    # Agent provides new_text with the correct target indentation
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def method(self):\n    old_code",  # agent uses less indent to FIND
        new_text="    def method(self):\n        new_code",  # correct indent for replacement
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "    def method(self):" in content
    assert "        new_code" in content


def test_fuzzy_no_match_when_content_differs(tmp_path: Path) -> None:
    """Fuzzy matching doesn't match when actual content differs."""
    _write(tmp_path, "a.py", "def foo():\n    return 1\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="def bar():\n    return 1", new_text="x",
    )
    assert "ERROR" in result


# ── Syntax validation (SWE-agent linter guardrail) ──────────────────────────


def test_syntax_validation_rejects_broken_python(tmp_path: Path) -> None:
    """Edit that breaks Python syntax is rejected."""
    _write(tmp_path, "a.py", "def foo():\n    return 1\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def foo():\n    return 1",
        new_text="def foo(\n    return 1",  # missing closing paren
    )
    assert "ERROR" in result
    assert "invalid Python" in result
    # Original file should be unchanged
    assert "def foo():" in (tmp_path / "a.py").read_text()


def test_syntax_validation_accepts_valid_edit(tmp_path: Path) -> None:
    """Edit that produces valid Python is accepted."""
    _write(tmp_path, "a.py", "x = 1\n")
    result = _tool(tmp_path)._execute(path="a.py", old_text="x = 1", new_text="x = 2")
    assert "OK" in result


def test_partial_fix_still_rejected_when_syntax_errors_remain(tmp_path: Path) -> None:
    """A patch that leaves the .py still unparseable must NOT be persisted.

    Live failure mode: a partially-fixed patch used to be written with only
    a WARNING, landing invalid Python that surfaced later as the phase-13
    'generated file is not valid Python' oracle failure.
    """
    original = (
        "def good_func():\n"
        "    pass\n\n"
        "def bad_ name():\n"
        "    pass\n\n"
        "@stray_decorator\n"
        "x = 1 +\n"  # second syntax error
    )
    _write(tmp_path, "a.py", original)
    # Fix just the space-in-name error — file would still not parse
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def bad_ name():",
        new_text="def bad_name():",
    )
    assert "ERROR" in result
    assert "invalid Python" in result
    assert "line" in result.lower()
    # File untouched — invalid content is never persisted
    assert (tmp_path / "a.py").read_text() == original


def test_convergent_patch_rejects_if_no_improvement(tmp_path: Path) -> None:
    """Edit that doesn't reduce error count should still be rejected."""
    # File has one syntax error
    _write(tmp_path, "a.py", "def foo(\n    return 1\n")
    # Try to add ANOTHER syntax error
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="return 1",
        new_text="return [1,",  # adds another broken bracket
    )
    assert "ERROR" in result
    # Original should be unchanged
    assert "return 1" in (tmp_path / "a.py").read_text()


def test_syntax_error_hints_about_decorator_placement(tmp_path: Path) -> None:
    """Decorator placed inside function body gets a helpful hint."""
    _write(tmp_path, "a.py", (
        "def create_grid():\n"
        "    \"\"\"Make a grid.\"\"\"\n"
        "    return []\n"
    ))
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def create_grid():\n    \"\"\"Make a grid.\"\"\"\n    return []",
        new_text="def create_grid():\n    @traces(\"LLR-0005\")\n    \"\"\"Make a grid.\"\"\"\n    return []",
    )
    assert "ERROR" in result
    assert "BEFORE the def" in result or "call form" in result


def test_syntax_validation_skipped_for_non_python(tmp_path: Path) -> None:
    """Non-.py files skip syntax validation."""
    _write(tmp_path, "a.txt", "hello world\n")
    result = _tool(tmp_path)._execute(
        path="a.txt",
        old_text="hello world",
        new_text="def broken(",  # would be invalid Python
    )
    assert "OK" in result


def test_syntax_error_message_includes_details(tmp_path: Path) -> None:
    """Syntax error feedback includes line number and error text."""
    _write(tmp_path, "a.py", "def foo():\n    return 1\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="return 1",
        new_text="return [1, 2,",  # unclosed bracket
    )
    assert "ERROR" in result
    assert "line" in result.lower()


# ── Workspace containment ────────────────────────────────────────────────────


def test_path_escape_raises(tmp_path: Path) -> None:
    """A path that resolves outside the workspace raises before any I/O."""
    import pytest

    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    with pytest.raises(ValueError, match="outside the workspace"):
        _tool(workspace)._execute(
            path="../outside.py", old_text="x = 1", new_text="x = 2",
        )
    assert outside.read_text() == "x = 1\n"


def test_absolute_path_escape_raises(tmp_path: Path) -> None:
    import pytest

    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    with pytest.raises(ValueError, match="outside the workspace"):
        _tool(workspace)._execute(
            path=str(outside), old_text="x = 1", new_text="x = 2",
        )
    assert outside.read_text() == "x = 1\n"


# ── Post-edit feedback (SWE-agent context response) ─────────────────────────


def test_success_shows_edited_context(tmp_path: Path) -> None:
    """Successful edit returns the modified region with line numbers."""
    lines = "\n".join(f"line{i}" for i in range(1, 21))
    _write(tmp_path, "a.py", lines + "\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="line10", new_text="CHANGED",
    )
    assert "OK" in result
    # Should show line numbers around the edit
    assert "CHANGED" in result
    # Should include surrounding context
    assert "line9" in result or "line11" in result


# ── Error feedback (rich context) ────────────────────────────────────────────


def test_error_shows_actual_content(tmp_path: Path) -> None:
    """Error response shows what's actually in the file."""
    _write(tmp_path, "a.py", "def real_function():\n    pass\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def imagined_function():",
        new_text="x",
    )
    assert "ERROR" in result
    assert "real_function" in result  # shows actual content


def test_error_hints_at_fuzzy_matches(tmp_path: Path) -> None:
    """Error suggests line numbers when similar content exists."""
    _write(tmp_path, "a.py", (
        "@traces('A')\ndef f1(): pass\n\n"
        "@traces('A')\ndef f2(): pass\n"
    ))
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="@traces('A')",
        new_text="@traces('B')",
    )
    # Should either succeed via fuzzy (if unique) or hint about multiple
    assert "OK" in result or "Hint" in result or "line" in result.lower()


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_empty_old_text(tmp_path: Path) -> None:
    """Empty old_text is handled gracefully."""
    _write(tmp_path, "a.py", "content\n")
    result = _tool(tmp_path)._execute(path="a.py", old_text="", new_text="x")
    # Should not corrupt the file — either error or reject
    assert "ERROR" in result or (tmp_path / "a.py").read_text() != ""


def test_empty_new_text_deletes(tmp_path: Path) -> None:
    """Empty new_text effectively deletes the matched text."""
    _write(tmp_path, "a.py", "keep\nremove_me\nkeep\n")
    result = _tool(tmp_path)._execute(path="a.py", old_text="remove_me\n", new_text="")
    assert "OK" in result
    assert (tmp_path / "a.py").read_text() == "keep\nkeep\n"


def test_large_file_performance(tmp_path: Path) -> None:
    """Works on files with many lines."""
    content = "\n".join(f"line_{i} = {i}" for i in range(1000))
    _write(tmp_path, "big.py", content + "\n")
    result = _tool(tmp_path)._execute(
        path="big.py", old_text="line_500 = 500", new_text="line_500 = 999",
    )
    assert "OK" in result
    assert "line_500 = 999" in (tmp_path / "big.py").read_text()


def test_range_with_fuzzy_match(tmp_path: Path) -> None:
    """Line range + fuzzy matching work together."""
    _write(tmp_path, "a.py", (
        "class C:\n"
        "    @traces('A')\n    def f1(self): pass\n\n"
        "    @traces('A')\n    def f2(self): pass\n"
    ))
    # Agent provides without leading indent, scoped to second occurrence
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="@traces('A')\ndef f2(self): pass",
        new_text="@traces('B')\ndef f2(self): pass",
        start_line=5, end_line=6,
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "@traces('B')" in content


# ── Backwards compatibility ─────────────────────────────────────────────────


def test_backwards_compatible_simple_call(tmp_path: Path) -> None:
    """Old 3-arg call style still works (no start_line/end_line)."""
    _write(tmp_path, "a.py", "old\n")
    result = _tool(tmp_path)._execute(path="a.py", old_text="old", new_text="new")
    assert "OK" in result
    assert (tmp_path / "a.py").read_text() == "new\n"


# ── Extended edge cases ──────────────────────────────────────────────────────


def test_tabs_vs_spaces(tmp_path: Path) -> None:
    """Tab-indented file, space-indented old_text."""
    _write(tmp_path, "a.py", "def foo():\n\treturn 1\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def foo():\n    return 1",
        new_text="def foo():\n\treturn 2",
    )
    assert "OK" in result
    assert "return 2" in (tmp_path / "a.py").read_text()


def test_crlf_line_endings(tmp_path: Path) -> None:
    """Windows-style line endings should still match."""
    _write(tmp_path, "a.txt", "line1\r\nline2\r\nline3\r\n")
    result = _tool(tmp_path)._execute(
        path="a.txt", old_text="line2", new_text="changed",
    )
    assert "OK" in result
    assert "changed" in (tmp_path / "a.txt").read_text()


def test_multiline_replacement_adds_lines(tmp_path: Path) -> None:
    """Replacing 1 line with 3 lines."""
    _write(tmp_path, "a.py", "x = 1\ny = 2\nz = 3\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="y = 2",
        new_text="y = 20\ny2 = 21\ny3 = 22",
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "y = 20" in content
    assert "y3 = 22" in content
    assert content.count("\n") >= 5


def test_multiline_replacement_removes_lines(tmp_path: Path) -> None:
    """Replacing 3 lines with 1 line."""
    _write(tmp_path, "a.py", "a = 1\nb = 2\nc = 3\nd = 4\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="a = 1\nb = 2\nc = 3",
        new_text="combined = 6",
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "combined = 6" in content
    assert "b = 2" not in content


def test_decorator_replacement_exact(tmp_path: Path) -> None:
    """The most common operation: replacing a decorator line."""
    _write(tmp_path, "a.py", (
        "from tracing import traces\n\n"
        "@traces('LLR-0001')\n"
        "def foo():\n    pass\n"
    ))
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="@traces('LLR-0001')\ndef foo():",
        new_text="@traces('LLR-0002')\ndef foo():",
    )
    assert "OK" in result
    assert "@traces('LLR-0002')" in (tmp_path / "a.py").read_text()


def test_decorator_replacement_in_class(tmp_path: Path) -> None:
    """Decorator replacement inside a class — agent omits class indent."""
    _write(tmp_path, "a.py", (
        "from tracing import traces\n\n"
        "class Calc:\n"
        "    @traces('LLR-0001')\n"
        "    def add(self, a, b):\n"
        "        return a + b\n"
    ))
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="@traces('LLR-0001')\ndef add(self, a, b):",
        new_text="@traces('LLR-0002')\ndef add(self, a, b):",
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "    @traces('LLR-0002')" in content


def test_unicode_content(tmp_path: Path) -> None:
    """Non-ASCII content is handled."""
    _write(tmp_path, "a.py", '"""Module — provides café support."""\nx = 1\n')
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="x = 1", new_text="x = 2",
    )
    assert "OK" in result
    assert "café" in (tmp_path / "a.py").read_text()


def test_empty_file(tmp_path: Path) -> None:
    """Empty file returns appropriate error."""
    _write(tmp_path, "a.py", "")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="something", new_text="else",
    )
    assert "ERROR" in result


def test_single_char_replacement(tmp_path: Path) -> None:
    """Single character replacement."""
    _write(tmp_path, "a.py", "x = 1\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="1", new_text="2",
    )
    assert "OK" in result
    assert (tmp_path / "a.py").read_text() == "x = 2\n"


def test_old_text_spans_empty_lines(tmp_path: Path) -> None:
    """old_text that includes blank lines in the middle."""
    _write(tmp_path, "a.py", "def foo():\n    pass\n\n\ndef bar():\n    pass\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def foo():\n    pass\n\n\ndef bar():\n    pass",
        new_text="def combined():\n    pass",
    )
    assert "OK" in result
    assert "combined" in (tmp_path / "a.py").read_text()


def test_new_text_identical_to_old_text(tmp_path: Path) -> None:
    """Replacing text with itself is a no-op but should succeed."""
    _write(tmp_path, "a.py", "x = 1\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="x = 1", new_text="x = 1",
    )
    assert "OK" in result
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


def test_nested_class_method_indent(tmp_path: Path) -> None:
    """Deeply nested method — agent uses 2-space indent, file uses 4."""
    _write(tmp_path, "a.py", (
        "class Outer:\n"
        "    class Inner:\n"
        "        def method(self):\n"
        "            return 1\n"
    ))
    # Agent provides correct target indent in new_text
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def method(self):\n      return 1",  # agent's wrong indent to FIND
        new_text="        def method(self):\n            return 42\n",  # correct indent
    )
    assert "OK" in result
    assert "return 42" in (tmp_path / "a.py").read_text()
    # Verify overall file is valid Python
    compile((tmp_path / "a.py").read_text(), "a.py", "exec")


def test_range_at_end_of_file(tmp_path: Path) -> None:
    """Line range targeting the last lines of the file."""
    _write(tmp_path, "a.py", "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="e = 5", new_text="e = 50",
        start_line=5, end_line=5,
    )
    assert "OK" in result
    assert "e = 50" in (tmp_path / "a.py").read_text()


def test_range_at_start_of_file(tmp_path: Path) -> None:
    """Line range targeting the first line."""
    _write(tmp_path, "a.py", "a = 1\nb = 2\nc = 3\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="a = 1", new_text="a = 10",
        start_line=1, end_line=1,
    )
    assert "OK" in result
    lines = (tmp_path / "a.py").read_text().splitlines()
    assert lines[0] == "a = 10"
    assert lines[1] == "b = 2"  # unchanged


def test_syntax_error_preserves_original(tmp_path: Path) -> None:
    """Failed syntax check does NOT write the broken content."""
    original = "def foo():\n    return 1\n"
    _write(tmp_path, "a.py", original)
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def foo():\n    return 1",
        new_text="def foo(\n    return 1",
    )
    assert "ERROR" in result
    assert (tmp_path / "a.py").read_text() == original


def test_response_includes_line_numbers(tmp_path: Path) -> None:
    """Success response includes line numbers for verification."""
    lines = "\n".join(f"line{i} = {i}" for i in range(20))
    _write(tmp_path, "a.py", lines + "\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="line10 = 10", new_text="line10 = 99",
    )
    assert "OK" in result
    # Should contain numbered lines in output
    assert "10" in result  # line number
    assert "line10 = 99" in result  # new content


def test_similar_lines_hint_on_error(tmp_path: Path) -> None:
    """Error response suggests similar lines when old_text is close."""
    _write(tmp_path, "a.py", "def calculate_total(items):\n    return sum(items)\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def calcualte_total(items):",  # typo in old_text
        new_text="def calculate_total(items, tax=0):",
    )
    assert "ERROR" in result
    # Should suggest the similar function name
    assert "calculate_total" in result


def test_multiple_occurrences_with_range(tmp_path: Path) -> None:
    """Multiple exact matches + line range selects the right one."""
    _write(tmp_path, "a.py", (
        "x = 1\n"
        "print(x)\n"
        "x = 1\n"
        "print(x)\n"
        "x = 1\n"
    ))
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="x = 1", new_text="x = 99",
        start_line=3, end_line=3,
    )
    assert "OK" in result
    lines = (tmp_path / "a.py").read_text().splitlines()
    assert lines[0] == "x = 1"   # first unchanged
    assert lines[2] == "x = 99"  # middle changed
    assert lines[4] == "x = 1"   # last unchanged


def test_subdirectory_path(tmp_path: Path) -> None:
    """File in a subdirectory works."""
    (tmp_path / "src").mkdir()
    _write(tmp_path, "src/module.py", "x = 1\n")
    result = _tool(tmp_path)._execute(
        path="src/module.py", old_text="x = 1", new_text="x = 2",
    )
    assert "OK" in result
    assert (tmp_path / "src" / "module.py").read_text() == "x = 2\n"


def test_very_long_old_text(tmp_path: Path) -> None:
    """old_text spanning 20+ lines."""
    block = "\n".join(f"    line_{i} = {i}" for i in range(25))
    content = f"class Big:\n{block}\n"
    _write(tmp_path, "a.py", content)
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="    line_12 = 12\n    line_13 = 13",
        new_text="    line_12 = 120\n    line_13 = 130",
    )
    assert "OK" in result
    assert "line_12 = 120" in (tmp_path / "a.py").read_text()


def test_fuzzy_match_excess_indent_in_old_text(tmp_path: Path) -> None:
    """LLM sends old_text with MORE indent than the file (e.g. 5 spaces vs 4).

    This reproduces a real bug where haiku consistently sent 5-space indent
    for 4-space indented code, causing file_patch to produce invalid Python
    and loop forever on retries.
    """
    _write(tmp_path, "a.py", (
        "class TestFoo:\n"
        "    def test_bar(self):\n"
        "        x = 1\n"
        "        return x\n"
    ))
    # LLM sends 5-space indent instead of 4-space (the actual bug)
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="     def test_bar(self):\n         x = 1\n         return x",
        new_text="     def test_bar(self):\n         x = 2\n         return x",
    )
    assert "OK" in result
    lines = (tmp_path / "a.py").read_text().splitlines()
    # Must produce EXACTLY 4-space indent (matching the file), not 5-space
    assert lines[1] == "    def test_bar(self):"   # 4 spaces, not 5
    assert lines[2] == "        x = 2"              # 8 spaces, not 9
    assert lines[3] == "        return x"            # 8 spaces, not 9
    # Must be valid Python
    compile("\n".join(lines), "a.py", "exec")


def test_mixed_indent_real_world(tmp_path: Path) -> None:
    """Real-world scenario: agent copies from file_read output with wrong indent."""
    _write(tmp_path, "a.py", (
        "class PathPlanner:\n"
        "    def plan(self, start, goal):\n"
        "        if not start:\n"
        "            raise ValueError('bad start')\n"
        "        return self._search(start, goal)\n"
    ))
    # Agent provides with no class-level indent (common mistake)
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def plan(self, start, goal):\n    if not start:\n        raise ValueError('bad start')\n    return self._search(start, goal)",
        new_text="    def plan(self, start, goal):\n        if not start:\n            raise ValueError('bad start')\n        return self._search(start, goal)\n        # added comment",
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "# added comment" in content


# ── Range fallback regression tests (from real agent failures) ───────────────


def test_fallback_old_text_extends_beyond_end_line(tmp_path: Path) -> None:
    """Reproduces: old_text spans lines 1-11 but agent set end_line=10.

    Real failure: agent read lines 1-10 via file_read, built old_text
    that included line 11 (the tracing import), but kept end_line=10.
    The match existed at line 1 but the 11-line old_text crossed the
    end_line boundary.
    """
    _write(tmp_path, "a.py", (
        '"""Module docstring."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import math\n"
        "\n"
        "import numpy as np\n"
        "import pytest\n"
        "\n"
        "from src.planner import Planner\n"
        "from tracing.decorator import traces\n"
        "\n"
        "\n"
        "def test_something():\n"
        "    pass\n"
    ))
    # Agent's old_text covers lines 1-11, but end_line=10 (off by one)
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text=(
            '"""Module docstring."""\n'
            "\n"
            "from __future__ import annotations\n"
            "\n"
            "import math\n"
            "\n"
            "import numpy as np\n"
            "import pytest\n"
            "\n"
            "from src.planner import Planner\n"
            "from tracing.decorator import traces\n"
        ),
        new_text=(
            '"""Module docstring."""\n'
            "\n"
            "from __future__ import annotations\n"
            "\n"
            "import math\n"
            "\n"
            "import numpy as np\n"
            "import pytest\n"
            "\n"
            "from src.planner import Planner\n"
            "from tracing.decorator import traces\n"
            "\n"
            "\n"
            "ADDED_CONSTANT = 42\n"
        ),
        start_line=1,
        end_line=10,  # off by one — old_text actually reaches line 11
    )
    assert "OK" in result
    assert "ADDED_CONSTANT = 42" in (tmp_path / "a.py").read_text()


def test_fallback_line_numbers_stale_after_prior_edit(tmp_path: Path) -> None:
    """Reproduces: prior file_write shifted lines, making cached numbers wrong.

    Real failure: agent read lines 78-90 and saw '"pytest"' at line 86.
    A prior file_write rewrote the file, shifting content. The agent's
    next file_patch still used the old line numbers, so the range missed.
    """
    _write(tmp_path, "a.py", (
        "# header\n"                  # 1
        "allowed = {\n"               # 2
        '    "__future__",\n'         # 3
        '    "ast",\n'               # 4
        '    "math",\n'              # 5
        '    "numpy",\n'             # 6
        '    "pytest",\n'            # 7
        '    "src",\n'               # 8
        '    "tracing",\n'           # 9
        '    "typing",\n'            # 10
        "}\n"                         # 11
    ))
    # Agent thinks "tracing","typing" are at lines 12-13 (stale from before an edit)
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text='    "tracing",\n    "typing",\n',
        new_text='    "tracing",\n    "typing",\n    "decorator",\n',
        start_line=12,
        end_line=15,
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert '"decorator"' in content
    # Original entries still present
    assert '"tracing"' in content
    assert '"typing"' in content


def test_fallback_fuzzy_match_outside_range(tmp_path: Path) -> None:
    """Reproduces: whitespace-flexible match needed but range is wrong.

    Real failure: agent's old_text matched via whitespace-flex at line 40
    but agent specified start_line=37, end_line=47 — which missed due to
    blank lines shifting the function down.
    """
    _write(tmp_path, "a.py", (
        "import math\n"
        "\n"
        "def helper():\n"
        "    return 0\n"
        "\n"
        "\n"
        "def target_func() -> float:\n"     # line 7
        '    """Docstring."""\n'
        "    return math.radians(135.0)\n"   # line 9
        "\n"
        "\n"
        "def other():\n"
        "    pass\n"
    ))
    # Agent thinks target_func starts at line 4 (stale), actual is line 7
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text=(
            "def target_func() -> float:\n"
            '    """Docstring."""\n'
            "    return math.radians(135.0)\n"
        ),
        new_text=(
            "def target_func() -> float:\n"
            '    """Docstring."""\n'
            "    return math.atan2(0.1, 0.1)\n"
        ),
        start_line=4,
        end_line=6,  # wrong range — actual content is at lines 7-9
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "math.atan2(0.1, 0.1)" in content
    assert "math.radians" not in content


def test_fallback_still_errors_when_ambiguous(tmp_path: Path) -> None:
    """Fallback must NOT succeed when old_text appears multiple times.

    The range is genuinely needed for disambiguation — falling back to
    full-file search would find 2+ matches and correctly reject.
    """
    _write(tmp_path, "a.py", (
        "def f1():\n"
        "    return math.radians(135.0)\n"
        "\n"
        "def f2():\n"
        "    return math.radians(135.0)\n"
    ))
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="    return math.radians(135.0)\n",
        new_text="    return math.atan2(0.1, 0.1)\n",
        start_line=10,  # totally wrong range
        end_line=15,
    )
    assert "ERROR" in result


def test_fallback_with_range_and_fuzzy_indent(tmp_path: Path) -> None:
    """Fallback + whitespace-flex combined: wrong range AND indent mismatch.

    Real pattern: agent copies from file_read (which strips class indent),
    and provides stale line numbers.
    """
    _write(tmp_path, "a.py", (
        "class Planner:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "\n"
        "    def plan(self):\n"        # line 5 (4-space indent)
        "        return self.x\n"
        "\n"
        "    def other(self):\n"
        "        pass\n"
    ))
    # Agent provides 0-indent old_text (copied from file_read) with wrong range
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def plan(self):\n    return self.x",  # no class indent
        new_text="def plan(self):\n    return self.x * 2",
        start_line=2,
        end_line=3,  # wrong — plan() is actually at lines 5-6
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "self.x * 2" in content
    # Must preserve the class-level indent
    assert "    def plan(self):" in content


# ── Duplicate-heavy files ────────────────────────────────────────────────────


def test_three_identical_lines_correct_range_selects_middle(tmp_path: Path) -> None:
    """Three identical lines — correct range picks the middle one."""
    _write(tmp_path, "a.py", "x = 1\na = 0\nx = 1\nb = 0\nx = 1\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="x = 1", new_text="x = 99",
        start_line=3, end_line=3,
    )
    assert "OK" in result
    lines = (tmp_path / "a.py").read_text().splitlines()
    assert lines == ["x = 1", "a = 0", "x = 99", "b = 0", "x = 1"]


def test_three_identical_lines_wrong_range_errors(tmp_path: Path) -> None:
    """Three identical lines — wrong range cannot be resolved by fallback."""
    _write(tmp_path, "a.py", "x = 1\na = 0\nx = 1\nb = 0\nx = 1\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="x = 1", new_text="x = 99",
        start_line=7, end_line=10,  # past end of file
    )
    assert "ERROR" in result


def test_many_duplicates_with_surrounding_context_unique(tmp_path: Path) -> None:
    """old_text includes enough context to be unique despite duplicates."""
    _write(tmp_path, "a.py", (
        "pass\ndef f1():\n    pass\n\n"
        "pass\ndef f2():\n    pass\n\n"
        "pass\ndef f3():\n    pass\n"
    ))
    # "pass" alone appears 6 times, but "pass\ndef f2():" is unique
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="pass\ndef f2():\n    pass",
        new_text="pass\ndef f2_renamed():\n    pass",
        start_line=1, end_line=3,  # wrong range — f2 is at lines 5-7
    )
    assert "OK" in result
    assert "f2_renamed" in (tmp_path / "a.py").read_text()


def test_identical_method_bodies_different_classes(tmp_path: Path) -> None:
    """Same method body in two classes — range needed, fallback must fail."""
    _write(tmp_path, "a.py", (
        "class A:\n"
        "    def run(self):\n"
        "        return 1\n"
        "\n"
        "class B:\n"
        "    def run(self):\n"
        "        return 1\n"
    ))
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="    def run(self):\n        return 1",
        new_text="    def run(self):\n        return 2",
        start_line=20, end_line=25,  # totally wrong
    )
    assert "ERROR" in result


def test_identical_method_bodies_correct_range_works(tmp_path: Path) -> None:
    """Same method body in two classes — correct range patches only one."""
    _write(tmp_path, "a.py", (
        "class A:\n"
        "    def run(self):\n"
        "        return 1\n"
        "\n"
        "class B:\n"
        "    def run(self):\n"
        "        return 1\n"
    ))
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="    def run(self):\n        return 1",
        new_text="    def run(self):\n        return 2",
        start_line=5, end_line=7,
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert content.count("return 1") == 1
    assert content.count("return 2") == 1
    # Class A unchanged, class B changed
    assert "class A:\n    def run(self):\n        return 1" in content


def test_duplicate_in_comment_and_code(tmp_path: Path) -> None:
    """old_text appears in a comment and in code — unique with context."""
    _write(tmp_path, "a.py", (
        "# TODO: x = calculate()\n"
        "y = 1\n"
        "x = calculate()\n"
        "z = 2\n"
    ))
    # "x = calculate()" appears twice, but full line match is unique in code
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="x = calculate()\nz = 2",
        new_text="x = calculate(data)\nz = 2",
        start_line=1, end_line=2,  # wrong range
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "x = calculate(data)" in content
    assert "# TODO: x = calculate()" in content  # comment unchanged


# ── Near-match scenarios ─────────────────────────────────────────────────────


def test_near_match_typo_in_old_text_still_fails(tmp_path: Path) -> None:
    """old_text with a typo should fail even with fallback — not found."""
    _write(tmp_path, "a.py", "def calculate_total(items):\n    return sum(items)\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def calcualte_total(items):",  # typo
        new_text="def calculate_total(items, tax=0):",
        start_line=1, end_line=5,
    )
    assert "ERROR" in result
    # Should suggest the similar line
    assert "calculate_total" in result


def test_near_match_extra_blank_line_in_old_text(tmp_path: Path) -> None:
    """old_text has an extra blank line the file doesn't — should fail."""
    _write(tmp_path, "a.py", "def foo():\n    return 1\ndef bar():\n    return 2\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def foo():\n    return 1\n\ndef bar():",  # extra blank line
        new_text="def foo():\n    return 1\n\ndef bar_renamed():",
    )
    assert "ERROR" in result


def test_near_match_missing_blank_line_in_old_text(tmp_path: Path) -> None:
    """old_text missing a blank line the file has — should fail."""
    _write(tmp_path, "a.py", "def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def foo():\n    return 1\ndef bar():",  # missing blank line
        new_text="def foo():\n    return 99\ndef bar():",
    )
    assert "ERROR" in result


def test_substring_match_not_confused_for_full_match(tmp_path: Path) -> None:
    """old_text is a substring of a longer line — string match handles it."""
    _write(tmp_path, "a.py", (
        "x = get_value()\n"
        "y = get_value_extended()\n"
        "z = get_value_final()\n"
    ))
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="x = get_value()",
        new_text="x = get_new_value()",
        start_line=2, end_line=3,  # wrong range
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "x = get_new_value()" in content
    assert "get_value_extended" in content  # not corrupted
    assert "get_value_final" in content


def test_old_text_is_prefix_of_duplicate_lines(tmp_path: Path) -> None:
    """old_text matches prefix of multiple lines — only full match counts."""
    _write(tmp_path, "a.py", (
        "value = 1\n"
        "value = 10\n"
        "value = 100\n"
    ))
    # "value = 1" is a prefix of all three lines but exact string match
    # finds it 3 times (as substring). Should fail — ambiguous.
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="value = 1\n",
        new_text="value = 999\n",
        start_line=5, end_line=10,  # wrong range
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    # Only the first "value = 1\n" should change, others untouched
    assert content == "value = 999\nvalue = 10\nvalue = 100\n"


# ── Range boundary edge cases ────────────────────────────────────────────────


def test_range_start_line_zero_treated_as_no_range(tmp_path: Path) -> None:
    """start_line=0 means 'from beginning' (default)."""
    _write(tmp_path, "a.py", "x = 1\ny = 2\nz = 3\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="y = 2", new_text="y = 99",
        start_line=0, end_line=2,
    )
    assert "OK" in result
    assert "y = 99" in (tmp_path / "a.py").read_text()


def test_range_end_line_beyond_file_length(tmp_path: Path) -> None:
    """end_line past EOF is clamped — should still work."""
    _write(tmp_path, "a.py", "x = 1\ny = 2\nz = 3\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="z = 3", new_text="z = 99",
        start_line=1, end_line=9999,
    )
    assert "OK" in result
    assert "z = 99" in (tmp_path / "a.py").read_text()


def test_range_start_after_end_still_falls_back(tmp_path: Path) -> None:
    """Inverted range (start > end) produces empty region — fallback saves it."""
    _write(tmp_path, "a.py", "x = 1\ny = 2\nz = 3\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="y = 2", new_text="y = 99",
        start_line=5, end_line=2,  # inverted
    )
    assert "OK" in result
    assert "y = 99" in (tmp_path / "a.py").read_text()


def test_range_single_line_exact(tmp_path: Path) -> None:
    """Range of exactly 1 line — match within that single line."""
    _write(tmp_path, "a.py", "a = 1\nb = 2\nc = 3\nd = 4\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="c = 3", new_text="c = 99",
        start_line=3, end_line=3,
    )
    assert "OK" in result
    assert (tmp_path / "a.py").read_text() == "a = 1\nb = 2\nc = 99\nd = 4\n"


def test_range_covers_entire_file_same_as_no_range(tmp_path: Path) -> None:
    """Range spanning the whole file should behave like no range."""
    _write(tmp_path, "a.py", "x = 1\ny = 2\n")
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="y = 2", new_text="y = 99",
        start_line=1, end_line=999,
    )
    assert "OK" in result
    assert "y = 99" in (tmp_path / "a.py").read_text()


def test_old_text_starts_on_last_line_of_range_extends_past(tmp_path: Path) -> None:
    """old_text begins at the boundary of end_line and extends beyond."""
    _write(tmp_path, "a.py", (
        "line1\n"
        "line2\n"
        "def target():\n"    # line 3 — last line in range
        "    return 1\n"     # line 4 — outside range
        "line5\n"
    ))
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def target():\n    return 1",
        new_text="def target():\n    return 2",
        start_line=1, end_line=3,  # range ends at line 3 but old_text needs line 4
    )
    assert "OK" in result
    assert "return 2" in (tmp_path / "a.py").read_text()


# ── Fallback + matching cascade combinations ─────────────────────────────────


def test_fallback_sub_line_edit_outside_range(tmp_path: Path) -> None:
    """Sub-line (string-level) edit where range is wrong."""
    _write(tmp_path, "a.py", 'msg = "hello world"\nx = 1\n')
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="hello world",
        new_text="hello universe",
        start_line=2, end_line=2,  # wrong — "hello world" is on line 1
    )
    assert "OK" in result
    assert '"hello universe"' in (tmp_path / "a.py").read_text()


def test_fallback_multiline_whitespace_flex_outside_range(tmp_path: Path) -> None:
    """Whitespace-flexible multiline match where range is off by many lines."""
    _write(tmp_path, "a.py", (
        "# preamble\n" * 20 +
        "class Outer:\n"
        "    class Inner:\n"
        "        def method(self):\n"
        "            return 1\n"
        "            \n"
        "    def other(self):\n"
        "        pass\n"
    ))
    # Agent provides 0-indent, thinks it's near line 5 (way off)
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def method(self):\n    return 1",
        new_text="def method(self):\n    return 42",
        start_line=3, end_line=8,
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "return 42" in content
    assert "        def method(self):" in content  # indent preserved


def test_fallback_preserves_correct_range_match_priority(tmp_path: Path) -> None:
    """If old_text IS found in the range, the range match wins (no fallback)."""
    _write(tmp_path, "a.py", "x = 1\ny = 2\nx = 1\n")
    # "x = 1" appears twice — range selects the first one
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="x = 1", new_text="x = 99",
        start_line=1, end_line=1,
    )
    assert "OK" in result
    lines = (tmp_path / "a.py").read_text().splitlines()
    assert lines[0] == "x = 99"
    assert lines[2] == "x = 1"  # second occurrence untouched


# ── Tricky content patterns ──────────────────────────────────────────────────


def test_old_text_contains_regex_metacharacters(tmp_path: Path) -> None:
    """old_text with regex special chars — must be literal match."""
    _write(tmp_path, "a.py", 'pattern = r"\\d+\\.\\d+"\nx = 1\n')
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text=r'pattern = r"\d+\.\d+"',
        new_text=r'pattern = r"\w+\.\w+"',
        start_line=2, end_line=2,  # wrong range
    )
    assert "OK" in result
    assert r'r"\w+\.\w+"' in (tmp_path / "a.py").read_text()


def test_file_with_many_blank_lines_shifting_content(tmp_path: Path) -> None:
    """Lots of blank lines make line counting error-prone for LLMs."""
    content = "\n" * 50 + "target = 1\n" + "\n" * 50 + "end = 2\n"
    _write(tmp_path, "a.py", content)
    # Agent guesses line 20, actual is line 51
    result = _tool(tmp_path)._execute(
        path="a.py", old_text="target = 1", new_text="target = 99",
        start_line=18, end_line=22,
    )
    assert "OK" in result
    assert "target = 99" in (tmp_path / "a.py").read_text()


def test_old_text_only_whitespace_and_newlines(tmp_path: Path) -> None:
    """old_text is only whitespace — should fail gracefully, not corrupt."""
    _write(tmp_path, "a.py", "x = 1\n\n\ny = 2\n")
    _tool(tmp_path)._execute(
        path="a.py", old_text="\n\n", new_text="\n",
    )
    # Either succeeds (removing one blank line) or errors — must not corrupt
    content = (tmp_path / "a.py").read_text()
    assert "x = 1" in content
    assert "y = 2" in content


def test_very_long_file_unique_match_at_end(tmp_path: Path) -> None:
    """1000-line file, unique match near the end, range points to start."""
    lines = [f"line_{i} = {i}\n" for i in range(1000)]
    lines[980] = "UNIQUE_TARGET = True\n"
    _write(tmp_path, "a.py", "".join(lines))
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="UNIQUE_TARGET = True",
        new_text="UNIQUE_TARGET = False",
        start_line=1, end_line=50,  # way off — target is at line 981
    )
    assert "OK" in result
    assert "UNIQUE_TARGET = False" in (tmp_path / "a.py").read_text()


def test_duplicate_blocks_different_only_by_one_char(tmp_path: Path) -> None:
    """Two blocks differ by a single character — old_text must be precise."""
    _write(tmp_path, "a.py", (
        "def process_a():\n    return 'a'\n\n"
        "def process_b():\n    return 'b'\n"
    ))
    # old_text matches process_a uniquely
    result = _tool(tmp_path)._execute(
        path="a.py",
        old_text="def process_a():\n    return 'a'",
        new_text="def process_a():\n    return 'A'",
        start_line=4, end_line=5,  # wrong range — a is at 1-2
    )
    assert "OK" in result
    content = (tmp_path / "a.py").read_text()
    assert "return 'A'" in content
    assert "return 'b'" in content  # b unchanged


# ── I/O error paths ──────────────────────────────────────────────────────────


def test_unreadable_target_reports_read_error(tmp_path: Path) -> None:
    (tmp_path / "dir.py").mkdir()
    result = _tool(tmp_path)._execute(path="dir.py", old_text="a", new_text="b")
    assert result.startswith("ERROR reading dir.py:")


def test_write_failure_reports_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "f.txt", "hello world\n")
    tool = _tool(tmp_path)

    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)
    result = tool._execute(path="f.txt", old_text="hello", new_text="bye")
    assert result.startswith("ERROR writing f.txt:")


# ── _syntax_hint guidance ────────────────────────────────────────────────────


def test_syntax_hint_generic_when_no_traces_involved() -> None:
    from backend.tools.file_patch import _syntax_hint

    assert _syntax_hint("x = (", "x = (", "invalid syntax") == "Fix the new_text and retry."


def test_syntax_hint_traces_without_call_form_falls_through() -> None:
    from backend.tools.file_patch import _syntax_hint

    # "@traces" without parentheses never matches the decorator pattern.
    hint = _syntax_hint("body", "@traces\ndef f(): pass", "error near @traces")
    assert hint == "Fix the new_text and retry."


def test_syntax_hint_decorator_absent_from_patched_text() -> None:
    from backend.tools.file_patch import _syntax_hint

    hint = _syntax_hint("unrelated content", '@traces("LLR-1")\n', "error near @traces")
    assert hint == "Fix the new_text and retry."


def test_syntax_hint_decorator_correctly_placed_no_hint() -> None:
    from backend.tools.file_patch import _syntax_hint

    patched = 'import x\n\n@traces("LLR-1")\ndef f():\n    pass\n'
    hint = _syntax_hint(patched, '@traces("LLR-1")\ndef f():', "error near @traces")
    assert hint == "Fix the new_text and retry."
