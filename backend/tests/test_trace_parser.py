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
