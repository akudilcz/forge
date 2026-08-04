"""Tests for the trace auditor module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.crew.trace_auditor import (
    FileAuditResult,
    InvalidTrace,
    SuggestedTrace,
    _apply_suggestions,
    _build_audit_prompt,
    _get_llr_data,
    _parse_suggestions,
    _validate_trace_ids,
    audit_traces,
    persist_audit_results,
)
from backend.crew.trace_parser import LineTrace

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_node(
    node_id: str, node_type: str, title: str = "", content: str = ""
) -> MagicMock:
    """Create a mock graph node."""
    node = MagicMock()
    node.node_id = node_id
    node.node_type = node_type
    node.title = title
    node.content = content
    node.properties = {}
    return node


def _make_graph(nodes: list[MagicMock]) -> MagicMock:
    """Create a mock graph with common operations."""
    graph = MagicMock()
    node_map = {n.node_id: n for n in nodes}
    graph.all_nodes.return_value = list(nodes)
    graph.node_sync.side_effect = lambda nid: node_map.get(nid)
    graph.update_node = AsyncMock()
    return graph


def _traced_python_code() -> str:
    """Python source where all functions have @traces decorators."""
    return (
        '@traces("LLR-0001")\n'
        "def foo():\n"
        "    return 1\n"
        "\n"
        '@traces("LLR-0002")\n'
        "def bar():\n"
        "    return 2\n"
    )


def _partially_traced_code() -> str:
    """Python source with one traced and one untraced function."""
    return (
        '@traces("LLR-0001")\n'
        "def traced_fn():\n"
        "    return 1\n"
        "\n"
        "def untraced_fn():\n"
        "    return 2\n"
    )


def _invalid_trace_code() -> str:
    """Python source with a @traces decorator referencing a non-existent ID."""
    return (
        '@traces("LLR-9999")\n'
        "def bad_fn():\n"
        "    return 0\n"
    )


# ── Data models ──────────────────────────────────────────────────────────────


class TestDataModels:
    """Test SuggestedTrace, InvalidTrace, FileAuditResult creation."""

    def test_suggested_trace_creation(self) -> None:
        st = SuggestedTrace(
            function_name="foo",
            start=1,
            end=5,
            suggested_llr_ids=["LLR-0001"],
            rationale="implements logging",
            confidence="high",
        )
        assert st.function_name == "foo"
        assert st.suggested_llr_ids == ["LLR-0001"]
        assert st.confidence == "high"

    def test_invalid_trace_creation(self) -> None:
        it = InvalidTrace(
            function_name="bar",
            start=10,
            end=15,
            invalid_llr_ids=["LLR-9999"],
            reason="unknown_id",
        )
        assert it.function_name == "bar"
        assert it.invalid_llr_ids == ["LLR-9999"]
        assert it.reason == "unknown_id"

    def test_file_audit_result_defaults(self) -> None:
        far = FileAuditResult(
            file_path="src/main.py",
            confirmed_traces=[],
            suggested_traces=[],
        )
        assert far.invalid_traces == []
        assert far.untraced_count == 0
        assert far.total_functions == 0
        assert far.fully_traced is False

    def test_file_audit_result_with_values(self) -> None:
        trace = LineTrace(start=1, end=3, llr_ids=["LLR-0001"], symbol="foo")
        far = FileAuditResult(
            file_path="src/main.py",
            confirmed_traces=[trace],
            suggested_traces=[],
            untraced_count=0,
            total_functions=1,
            fully_traced=True,
        )
        assert far.total_functions == 1
        assert far.fully_traced is True
        assert len(far.confirmed_traces) == 1


# ── _validate_trace_ids ──────────────────────────────────────────────────────


class TestValidateTraceIds:
    """Edge-case tests for _validate_trace_ids (complements test_code_gen.py)."""

    def test_empty_traces_list(self) -> None:
        assert _validate_trace_ids([], {"LLR-0001"}) == []

    def test_all_valid(self) -> None:
        traces = [
            LineTrace(start=1, end=3, llr_ids=["LLR-0001"], symbol="a"),
            LineTrace(start=5, end=8, llr_ids=["LLR-0002"], symbol="b"),
        ]
        assert _validate_trace_ids(traces, {"LLR-0001", "LLR-0002"}) == []

    def test_all_invalid(self) -> None:
        traces = [
            LineTrace(start=1, end=3, llr_ids=["BAD-1"], symbol="a"),
            LineTrace(start=5, end=8, llr_ids=["BAD-2"], symbol="b"),
        ]
        invalid = _validate_trace_ids(traces, {"LLR-0001"})
        assert len(invalid) == 2
        assert invalid[0].invalid_llr_ids == ["BAD-1"]
        assert invalid[1].invalid_llr_ids == ["BAD-2"]

    def test_mixed_valid_and_invalid(self) -> None:
        traces = [
            LineTrace(start=1, end=3, llr_ids=["LLR-0001"], symbol="good"),
            LineTrace(start=5, end=8, llr_ids=["LLR-0001", "BAD-1"], symbol="mixed"),
            LineTrace(start=10, end=12, llr_ids=["BAD-2"], symbol="bad"),
        ]
        valid_ids = {"LLR-0001"}
        invalid = _validate_trace_ids(traces, valid_ids)
        assert len(invalid) == 2
        # "mixed" has one bad ID filtered out
        assert invalid[0].function_name == "mixed"
        assert invalid[0].invalid_llr_ids == ["BAD-1"]
        # "bad" has its only ID invalid
        assert invalid[1].function_name == "bad"
        assert invalid[1].invalid_llr_ids == ["BAD-2"]

    def test_empty_valid_ids_set(self) -> None:
        traces = [LineTrace(start=1, end=3, llr_ids=["LLR-0001"], symbol="x")]
        invalid = _validate_trace_ids(traces, set())
        assert len(invalid) == 1
        assert invalid[0].reason == "unknown_id"


# ── _get_llr_data ────────────────────────────────────────────────────────────


class TestGetLlrData:
    """Tests for _get_llr_data extracting LLR info from graph."""

    def test_graph_with_llr_nodes(self) -> None:
        llr_b = _make_node("LLR-0002", "LLR", title="Second req")
        llr_a = _make_node("LLR-0001", "LLR", title="First req")
        other = _make_node("HLR-0001", "HLR", title="High level")
        graph = _make_graph([llr_b, llr_a, other])

        ids, descs = _get_llr_data(graph)
        assert ids == ["LLR-0001", "LLR-0002"]  # sorted
        assert descs["LLR-0001"] == "First req"
        assert descs["LLR-0002"] == "Second req"

    def test_graph_with_no_llr_nodes(self) -> None:
        other = _make_node("HLR-0001", "HLR", title="High level")
        graph = _make_graph([other])

        ids, descs = _get_llr_data(graph)
        assert ids == []
        assert descs == {}

    def test_graph_all_nodes_raises(self) -> None:
        graph = MagicMock()
        graph.all_nodes.side_effect = RuntimeError("DB error")

        ids, descs = _get_llr_data(graph)
        assert ids == []
        assert descs == {}

    def test_llr_without_title_uses_content_prefix(self) -> None:
        content = "A" * 100
        llr = _make_node("LLR-0003", "LLR", title="", content=content)
        graph = _make_graph([llr])

        ids, descs = _get_llr_data(graph)
        assert ids == ["LLR-0003"]
        assert descs["LLR-0003"] == content[:80]

    def test_llr_without_title_or_content(self) -> None:
        llr = _make_node("LLR-0004", "LLR", title="", content="")
        graph = _make_graph([llr])

        ids, descs = _get_llr_data(graph)
        assert descs["LLR-0004"] == ""


# ── _build_audit_prompt ──────────────────────────────────────────────────────


class TestBuildAuditPrompt:
    """Tests for building the LLM context prompt."""

    def test_llr_ids_listed(self) -> None:
        files = [("src/a.py", "def foo(): pass", [{"name": "foo", "start": 1, "end": 1, "private": False}])]
        prompt = _build_audit_prompt(files, ["LLR-0001", "LLR-0002"])
        assert "LLR-0001" in prompt
        assert "LLR-0002" in prompt

    def test_file_paths_and_untraced_functions_included(self) -> None:
        untraced = [{"name": "my_func", "start": 5, "end": 10, "private": False}]
        files = [("src/module.py", "code here", untraced)]
        prompt = _build_audit_prompt(files, ["LLR-0001"])
        assert "src/module.py" in prompt
        assert "`my_func`" in prompt
        assert "lines 5" in prompt

    def test_private_function_marking(self) -> None:
        untraced = [{"name": "_helper", "start": 1, "end": 3, "private": True}]
        files = [("src/a.py", "code", untraced)]
        prompt = _build_audit_prompt(files, ["LLR-0001"])
        assert "(private helper)" in prompt

    def test_public_function_no_private_label(self) -> None:
        untraced = [{"name": "public_fn", "start": 1, "end": 3, "private": False}]
        files = [("src/a.py", "code", untraced)]
        prompt = _build_audit_prompt(files, ["LLR-0001"])
        assert "(private helper)" not in prompt

    def test_llr_descriptions_appended(self) -> None:
        descs = {"LLR-0001": "Handle user login", "LLR-0002": "Validate tokens"}
        files = [("a.py", "code", [{"name": "f", "start": 1, "end": 1, "private": False}])]
        prompt = _build_audit_prompt(files, ["LLR-0001", "LLR-0002"], descs)
        assert "Handle user login" in prompt
        assert "Validate tokens" in prompt
        # Descriptions are formatted with dash separator
        assert "LLR-0001 — Handle user login" in prompt

    def test_no_descriptions(self) -> None:
        files = [("a.py", "code", [{"name": "f", "start": 1, "end": 1, "private": False}])]
        prompt = _build_audit_prompt(files, ["LLR-0001"], None)
        assert "LLR-0001" in prompt
        # LLR line should not have a description suffix
        assert "LLR-0001 — " not in prompt

    def test_source_code_included(self) -> None:
        code = "def example():\n    return 42\n"
        files = [("src/ex.py", code, [{"name": "example", "start": 1, "end": 2, "private": False}])]
        prompt = _build_audit_prompt(files, ["LLR-0001"])
        assert "return 42" in prompt
        assert "```python" in prompt


# ── _parse_suggestions ───────────────────────────────────────────────────────


class TestParseSuggestions:
    """Tests for parsing LLM-generated trace suggestions from JSON."""

    def test_valid_json_suggestions(self, tmp_path: Path) -> None:
        suggestions_data = {
            "src/a.py": [
                {
                    "function_name": "foo",
                    "suggested_llr_ids": ["LLR-0001"],
                    "rationale": "implements login",
                    "confidence": "high",
                }
            ]
        }
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "trace_suggestions.json").write_text(
            json.dumps(suggestions_data), encoding="utf-8"
        )

        files = [("src/a.py", "code", [{"name": "foo", "start": 1, "end": 5, "private": False}])]
        result = _parse_suggestions(tmp_path, files)

        assert "src/a.py" in result
        assert len(result["src/a.py"]) == 1
        st = result["src/a.py"][0]
        assert st.function_name == "foo"
        assert st.suggested_llr_ids == ["LLR-0001"]
        assert st.confidence == "high"
        assert st.start == 1
        assert st.end == 5

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        files = [("src/a.py", "code", [{"name": "foo", "start": 1, "end": 5, "private": False}])]
        result = _parse_suggestions(tmp_path, files)
        assert result == {}

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "trace_suggestions.json").write_text(
            "not valid json!!!", encoding="utf-8"
        )
        files = [("src/a.py", "code", [{"name": "foo", "start": 1, "end": 5, "private": False}])]
        result = _parse_suggestions(tmp_path, files)
        assert result == {}

    def test_unknown_function_names_filtered_out(self, tmp_path: Path) -> None:
        suggestions_data = {
            "src/a.py": [
                {
                    "function_name": "unknown_func",
                    "suggested_llr_ids": ["LLR-0001"],
                    "rationale": "guess",
                    "confidence": "low",
                },
                {
                    "function_name": "known_func",
                    "suggested_llr_ids": ["LLR-0002"],
                    "rationale": "real match",
                    "confidence": "high",
                },
            ]
        }
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "trace_suggestions.json").write_text(
            json.dumps(suggestions_data), encoding="utf-8"
        )

        # Only "known_func" is in the untraced list
        files = [("src/a.py", "code", [{"name": "known_func", "start": 3, "end": 7, "private": False}])]
        result = _parse_suggestions(tmp_path, files)

        assert len(result["src/a.py"]) == 1
        assert result["src/a.py"][0].function_name == "known_func"

    def test_non_list_suggestions_skipped(self, tmp_path: Path) -> None:
        """If a file's suggestions is not a list, it should be skipped."""
        suggestions_data = {"src/a.py": "not a list"}
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "trace_suggestions.json").write_text(
            json.dumps(suggestions_data), encoding="utf-8"
        )
        files = [("src/a.py", "code", [{"name": "foo", "start": 1, "end": 5, "private": False}])]
        result = _parse_suggestions(tmp_path, files)
        assert result == {}


# ── _apply_suggestions ───────────────────────────────────────────────────────


class TestApplySuggestions:
    """Tests for merging suggestions into audit results."""

    def test_suggestions_merged_into_matching_result(self) -> None:
        result_a = FileAuditResult(
            file_path="src/a.py", confirmed_traces=[], suggested_traces=[],
        )
        result_b = FileAuditResult(
            file_path="src/b.py", confirmed_traces=[], suggested_traces=[],
        )
        suggestion = SuggestedTrace(
            function_name="foo", start=1, end=5,
            suggested_llr_ids=["LLR-0001"], rationale="reason", confidence="high",
        )
        suggestions = {"src/a.py": [suggestion]}
        _apply_suggestions([result_a, result_b], suggestions)

        assert len(result_a.suggested_traces) == 1
        assert result_a.suggested_traces[0].function_name == "foo"
        assert result_b.suggested_traces == []

    def test_no_suggestions_for_file(self) -> None:
        result = FileAuditResult(
            file_path="src/c.py", confirmed_traces=[], suggested_traces=[],
        )
        _apply_suggestions([result], {})
        assert result.suggested_traces == []

    def test_suggestions_overwrite_previous(self) -> None:
        old_suggestion = SuggestedTrace(
            function_name="old", start=1, end=2,
            suggested_llr_ids=["LLR-OLD"], rationale="", confidence="low",
        )
        result = FileAuditResult(
            file_path="src/a.py", confirmed_traces=[],
            suggested_traces=[old_suggestion],
        )
        new_suggestion = SuggestedTrace(
            function_name="new", start=3, end=5,
            suggested_llr_ids=["LLR-NEW"], rationale="", confidence="high",
        )
        _apply_suggestions([result], {"src/a.py": [new_suggestion]})
        assert len(result.suggested_traces) == 1
        assert result.suggested_traces[0].function_name == "new"


# ── persist_audit_results ────────────────────────────────────────────────────


class TestPersistAuditResults:
    """Tests for persisting audit results to graph node properties."""

    @pytest.mark.asyncio
    async def test_writes_trace_audit_to_node(self) -> None:
        node = _make_node("FILE-001", "FILE")
        graph = _make_graph([node])

        result = FileAuditResult(
            file_path="src/main.py",
            confirmed_traces=[],
            suggested_traces=[],
            total_functions=3,
            untraced_count=1,
            fully_traced=False,
        )
        file_node_map = {"src/main.py": "FILE-001"}

        await persist_audit_results([result], graph, file_node_map)

        graph.update_node.assert_awaited_once()
        call_kwargs = graph.update_node.call_args
        props = call_kwargs.kwargs.get("properties") or call_kwargs[1].get("properties")
        assert "trace_audit" in props
        assert props["trace_audit"]["total_functions"] == 3
        assert props["trace_audit"]["untraced_count"] == 1
        assert props["trace_audit"]["fully_traced"] is False

    @pytest.mark.asyncio
    async def test_skips_nodes_not_in_file_node_map(self) -> None:
        graph = _make_graph([])

        result = FileAuditResult(
            file_path="src/unknown.py",
            confirmed_traces=[], suggested_traces=[],
        )
        file_node_map: dict[str, str] = {}  # no mapping for this file

        await persist_audit_results([result], graph, file_node_map)
        graph.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_missing_node_gracefully(self) -> None:
        """Node ID in map but node_sync returns None."""
        graph = MagicMock()
        graph.node_sync.return_value = None
        graph.update_node = AsyncMock()

        result = FileAuditResult(
            file_path="src/gone.py",
            confirmed_traces=[], suggested_traces=[],
        )
        file_node_map = {"src/gone.py": "MISSING-NODE"}

        await persist_audit_results([result], graph, file_node_map)
        graph.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_persists_suggested_and_invalid_traces(self) -> None:
        node = _make_node("FILE-002", "FILE")
        graph = _make_graph([node])

        suggestion = SuggestedTrace(
            function_name="foo", start=1, end=5,
            suggested_llr_ids=["LLR-0001"], rationale="reason", confidence="high",
        )
        invalid = InvalidTrace(
            function_name="bar", start=10, end=15,
            invalid_llr_ids=["LLR-9999"], reason="unknown_id",
        )
        result = FileAuditResult(
            file_path="src/mod.py",
            confirmed_traces=[],
            suggested_traces=[suggestion],
            invalid_traces=[invalid],
            total_functions=2,
            untraced_count=1,
        )
        file_node_map = {"src/mod.py": "FILE-002"}

        await persist_audit_results([result], graph, file_node_map)

        call_kwargs = graph.update_node.call_args
        props = call_kwargs.kwargs.get("properties") or call_kwargs[1].get("properties")
        audit = props["trace_audit"]
        assert len(audit["suggested_traces"]) == 1
        assert audit["suggested_traces"][0]["function_name"] == "foo"
        assert len(audit["invalid_traces"]) == 1
        assert audit["invalid_traces"][0]["function_name"] == "bar"


# ── audit_traces (main flow) ─────────────────────────────────────────────────


class TestAuditTraces:
    """Integration tests for the audit_traces main flow."""

    @pytest.mark.asyncio
    async def test_fully_traced_file_no_llm_call(self, tmp_path: Path) -> None:
        """File with all functions traced should not trigger LLM."""
        src = tmp_path / "src" / "a.py"
        src.parent.mkdir(parents=True)
        src.write_text(_traced_python_code(), encoding="utf-8")

        llr_a = _make_node("LLR-0001", "LLR", title="First")
        llr_b = _make_node("LLR-0002", "LLR", title="Second")
        graph = _make_graph([llr_a, llr_b])

        with patch("backend.crew.trace_auditor.forge_logger"):
            results = await audit_traces(tmp_path, ["src/a.py"], graph)

        assert len(results) == 1
        assert results[0].fully_traced is True
        assert results[0].untraced_count == 0
        assert results[0].suggested_traces == []

    @pytest.mark.asyncio
    async def test_untraced_functions_trigger_llm(self, tmp_path: Path) -> None:
        """File with untraced functions should invoke LLM for suggestions."""
        src = tmp_path / "src" / "b.py"
        src.parent.mkdir(parents=True)
        src.write_text(_partially_traced_code(), encoding="utf-8")

        llr = _make_node("LLR-0001", "LLR", title="Req")
        graph = _make_graph([llr])

        mock_llm = MagicMock()
        llm_response = MagicMock()
        llm_response.content = json.dumps({
            "src/b.py": [
                {
                    "function_name": "untraced_fn",
                    "suggested_llr_ids": ["LLR-0001"],
                    "rationale": "implements requirement",
                    "confidence": "medium",
                    "action": "add",
                }
            ]
        })
        mock_llm.ainvoke = AsyncMock(return_value=llm_response)

        with (
            patch("backend.crew.trace_auditor.forge_logger"),
            patch("backend.agents.factory.build_llm", return_value=mock_llm),
            patch("backend.config.loader.load_config", return_value={}),
        ):
            results = await audit_traces(tmp_path, ["src/b.py"], graph)

        assert len(results) == 1
        assert results[0].untraced_count == 1
        assert results[0].fully_traced is False
        # LLM was called
        mock_llm.ainvoke.assert_awaited_once()
        # Suggestion was parsed and applied
        assert len(results[0].suggested_traces) == 1
        assert results[0].suggested_traces[0].function_name == "untraced_fn"

    @pytest.mark.asyncio
    async def test_invalid_traces_detected(self, tmp_path: Path) -> None:
        """Invalid LLR annotations should be flagged in results."""
        src = tmp_path / "src" / "c.py"
        src.parent.mkdir(parents=True)
        src.write_text(_invalid_trace_code(), encoding="utf-8")

        # Graph has no LLR-9999
        graph = _make_graph([])

        with patch("backend.crew.trace_auditor.forge_logger"):
            results = await audit_traces(tmp_path, ["src/c.py"], graph)

        assert len(results) == 1
        assert len(results[0].invalid_traces) == 1
        assert results[0].invalid_traces[0].invalid_llr_ids == ["LLR-9999"]
        assert results[0].fully_traced is False

    @pytest.mark.asyncio
    async def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        """Non-existent files should be silently skipped."""
        graph = _make_graph([])

        with patch("backend.crew.trace_auditor.forge_logger"):
            results = await audit_traces(tmp_path, ["does_not_exist.py"], graph)

        assert results == []

    @pytest.mark.asyncio
    async def test_llm_failure_returns_results_without_suggestions(self, tmp_path: Path) -> None:
        """If LLM call fails, results are still returned (without suggestions)."""
        src = tmp_path / "src" / "d.py"
        src.parent.mkdir(parents=True)
        src.write_text(_partially_traced_code(), encoding="utf-8")

        llr = _make_node("LLR-0001", "LLR", title="Req")
        graph = _make_graph([llr])

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))

        with (
            patch("backend.crew.trace_auditor.forge_logger"),
            patch("backend.agents.factory.build_llm", return_value=mock_llm),
            patch("backend.config.loader.load_config", return_value={}),
        ):
            results = await audit_traces(tmp_path, ["src/d.py"], graph)

        assert len(results) == 1
        assert results[0].untraced_count == 1
        # No suggestions because LLM failed
        assert results[0].suggested_traces == []
