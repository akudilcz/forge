"""Tests for backend.crew.trace_parser — LLR/CASE trace extraction."""

from backend.crew.trace_parser import analyse_traces, find_untraced_functions, parse_llr_traces


def test_traced_function_found() -> None:
    code = (
        '@traces("LLR-001")\n'
        "def foo():\n"
        "    return 1\n"
    )
    traces = parse_llr_traces(code)
    assert len(traces) == 1
    assert traces[0].symbol == "foo"
    assert traces[0].llr_ids == ["LLR-001"]


def test_untraced_function_reported() -> None:
    code = (
        "def foo():\n"
        "    return 1\n"
    )
    analysis = analyse_traces(code)
    assert len(analysis.untraced) == 1
    assert analysis.untraced[0].name == "foo"


def test_duplicate_name_traced_and_untraced() -> None:
    """When a function name appears both traced and untraced (e.g. nested
    helpers with the same name in different test functions), the untraced
    instance should be removed — gap closer can't target by name."""
    code = (
        '@traces("LLR-001")\n'
        "def test_a():\n"
        '    @traces("LLR-001")\n'
        "    def helper():\n"
        "        pass\n"
        "    helper()\n"
        "\n"
        '@traces("LLR-002")\n'
        "def test_b():\n"
        "    def helper():\n"
        "        pass\n"
        "    helper()\n"
    )
    analysis = analyse_traces(code)
    # 'helper' appears twice: one traced, one not.
    # After dedup, 'helper' should NOT be in untraced.
    untraced_names = [u.name for u in analysis.untraced]
    assert "helper" not in untraced_names


def test_duplicate_name_both_untraced() -> None:
    """If all instances of a name are untraced, it should still be reported."""
    code = (
        '@traces("LLR-001")\n'
        "def test_a():\n"
        "    def helper():\n"
        "        pass\n"
        "    helper()\n"
        "\n"
        '@traces("LLR-002")\n'
        "def test_b():\n"
        "    def helper():\n"
        "        pass\n"
        "    helper()\n"
    )
    analysis = analyse_traces(code)
    untraced_names = [u.name for u in analysis.untraced]
    assert "helper" in untraced_names


def test_find_untraced_functions_deduplicates() -> None:
    """find_untraced_functions should not list names that are traced elsewhere."""
    code = (
        '@traces("LLR-001")\n'
        "def test_a():\n"
        '    @traces("LLR-001")\n'
        "    def fake_search():\n"
        "        return []\n"
        "    fake_search()\n"
        "\n"
        '@traces("LLR-002")\n'
        "def test_b():\n"
        "    def fake_search():\n"
        "        return []\n"
        "    fake_search()\n"
    )
    names = find_untraced_functions(code)
    assert "fake_search" not in names


def test_case_annotation_parsed() -> None:
    code = (
        '@traces("LLR-001", case="CASE-001")\n'
        "def test_foo():\n"
        "    assert True\n"
    )
    traces = parse_llr_traces(code)
    assert len(traces) == 1
    assert traces[0].case_ids == ["CASE-001"]


def test_total_functions_count() -> None:
    code = (
        '@traces("LLR-001")\n'
        "def traced():\n"
        "    pass\n"
        "\n"
        "def untraced():\n"
        "    pass\n"
    )
    analysis = analyse_traces(code)
    assert analysis.total_functions == 2
    assert analysis.traced_functions == 1


# ── Decorator-shape edge cases ───────────────────────────────────────────────


def test_bare_traces_decorator_without_call_yields_no_traces() -> None:
    """A bare ``@traces`` (no parentheses) is not a valid annotation."""
    code = (
        "@traces\n"
        "def foo():\n"
        "    return 1\n"
    )
    assert parse_llr_traces(code) == []


def test_unrelated_call_decorator_is_ignored() -> None:
    """Call decorators with other names (e.g. pytest marks) are not traces."""
    code = (
        '@pytest.mark.parametrize("x", [1])\n'
        "def test_foo(x):\n"
        "    assert x\n"
    )
    assert parse_llr_traces(code) == []


def test_non_string_positional_args_are_skipped() -> None:
    """Non-string positional args in @traces do not become LLR IDs."""
    code = (
        '@traces(123, "LLR-002")\n'
        "def foo():\n"
        "    return 1\n"
    )
    traces = parse_llr_traces(code)
    assert traces[0].llr_ids == ["LLR-002"]


def test_non_case_keyword_is_ignored() -> None:
    """Keywords other than ``case=`` contribute no CASE IDs."""
    code = (
        '@traces("LLR-001", note="irrelevant")\n'
        "def foo():\n"
        "    return 1\n"
    )
    traces = parse_llr_traces(code)
    assert traces[0].case_ids == []


def test_non_string_case_value_is_ignored() -> None:
    """A ``case=`` value that is neither a string nor a list yields nothing."""
    code = (
        '@traces("LLR-001", case=42)\n'
        "def foo():\n"
        "    return 1\n"
    )
    assert parse_llr_traces(code)[0].case_ids == []


def test_case_list_with_non_string_elements_keeps_only_strings() -> None:
    code = (
        '@traces("LLR-001", case=["CASE-001", 7])\n'
        "def foo():\n"
        "    return 1\n"
    )
    assert parse_llr_traces(code)[0].case_ids == ["CASE-001"]


def test_attribute_decorator_name_resolves_to_trailing_attr() -> None:
    """``@tracing.traces(...)`` resolves via the trailing attribute name."""
    code = (
        '@tracing.traces("LLR-009")\n'
        "def foo():\n"
        "    return 1\n"
    )
    assert parse_llr_traces(code)[0].llr_ids == ["LLR-009"]


def test_complex_decorator_expression_yields_no_name() -> None:
    """A decorator whose func is itself a call has no resolvable name."""
    code = (
        '@make_decorator()("LLR-001")\n'
        "def foo():\n"
        "    return 1\n"
    )
    assert parse_llr_traces(code) == []


def test_protocol_class_methods_are_exempt_from_tracing() -> None:
    """Protocol classes declare interfaces — methods need no @traces."""
    code = (
        "from typing import Protocol\n"
        "class Reader(Protocol):\n"
        "    def read(self) -> str: ...\n"
    )
    analysis = analyse_traces(code)
    assert analysis.total_functions == 0


def test_walker_descends_nested_module_nodes() -> None:
    """The AST walker recurses through Module children of synthetic wrappers."""
    import ast

    from backend.crew.trace_parser import _ScopedFunc, _walk_ast

    class _Wrapper(ast.AST):
        _fields = ("body",)
        body: ast.Module

    wrapper = _Wrapper()
    wrapper.body = ast.parse("def foo():\n    pass\n")
    out: list[_ScopedFunc] = []
    _walk_ast(wrapper, out)
    assert [s.node.name for s in out] == ["foo"]
