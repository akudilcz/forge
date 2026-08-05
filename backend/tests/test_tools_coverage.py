"""Coverage-focused tests for tool modules.

Targets uncovered lines across shell_exec, graph_ops, analysis,
work_queue_tools, graph_write, file_write, insert_lines, code_search,
file_read, graph_grep, list_dir, read_docs, and file_rename.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

# ═══════════════════════════════════════════════════════════════════════════
# 1. shell_exec
# ═══════════════════════════════════════════════════════════════════════════
from backend.tools.shell_exec import (
    ShellExecTool,
    _format_test_error,
    _is_bazel_test,
    _is_test_command,
    _log_junit_results,
    _regen_build_files,
)


class TestIsTestCommand:
    def test_pytest_detected(self) -> None:
        assert _is_test_command("pytest tests/") is True

    def test_bazel_test_detected(self) -> None:
        assert _is_test_command("bazel test //tests:all") is True

    def test_bazel_coverage_detected(self) -> None:
        assert _is_test_command("bazel coverage //tests:all") is True

    def test_bazel_run_tests_detected(self) -> None:
        assert _is_test_command("bazel run //tests:foo") is True

    def test_non_test_command(self) -> None:
        assert _is_test_command("echo hello") is False

    def test_random_unrelated(self) -> None:
        assert _is_test_command("ls -la") is False


class TestIsBazelTest:
    def test_bazel_test(self) -> None:
        assert _is_bazel_test("bazel test //...") is True

    def test_bazel_coverage(self) -> None:
        assert _is_bazel_test("bazel coverage //tests:all") is True

    def test_not_bazel(self) -> None:
        assert _is_bazel_test("pytest tests/") is False

    def test_echo_is_not_bazel(self) -> None:
        assert _is_bazel_test("echo hello") is False


class TestRegenBuildFiles:
    @patch("backend.codegen.bazel_gen.init_bazel_workspace")
    def test_calls_init_bazel_workspace(self, mock_init: MagicMock) -> None:
        _regen_build_files("/my/workspace")
        mock_init.assert_called_once_with(Path("/my/workspace"))


class TestFormatTestError:
    def test_missing_log_file(self) -> None:
        log = MagicMock()
        log.exists.return_value = False
        assert _format_test_error(log) == "(no test.log)"

    def test_unreadable_log_file(self) -> None:
        log = MagicMock()
        log.exists.return_value = True
        log.read_text.side_effect = OSError("perm denied")
        assert _format_test_error(log) == "(unreadable test.log)"

    def test_empty_log_file(self) -> None:
        log = MagicMock()
        log.exists.return_value = True
        log.read_text.return_value = "   \n  \n  "
        assert _format_test_error(log) == "(empty test.log)"

    def test_short_log_returns_all_lines(self) -> None:
        log = MagicMock()
        log.exists.return_value = True
        log.read_text.return_value = "line1\nline2\nline3"
        result = _format_test_error(log)
        assert "line1" in result
        assert "line3" in result

    def test_long_log_returns_last_5(self) -> None:
        log = MagicMock()
        log.exists.return_value = True
        lines = "\n".join(f"line{i}" for i in range(20))
        log.read_text.return_value = lines
        result = _format_test_error(log)
        assert "line15" in result
        assert "line19" in result
        # first lines should NOT be present
        assert "line0" not in result


class TestLogJunitResults:
    def test_no_testlogs_dir(self, tmp_path: Path) -> None:
        logger = MagicMock()
        _log_junit_results(str(tmp_path), logger)
        logger.emit.assert_not_called()

    def test_parse_error_warns(self, tmp_path: Path) -> None:
        testlogs = tmp_path / "bazel-testlogs" / "tests" / "mytest"
        testlogs.mkdir(parents=True)
        (testlogs / "test.xml").write_text("NOT VALID XML <<<")
        logger = MagicMock()
        _log_junit_results(str(tmp_path), logger)
        logger.emit.assert_called_once()
        assert "WARN" == logger.emit.call_args[0][0]

    def test_pass_test(self, tmp_path: Path) -> None:
        testlogs = tmp_path / "bazel-testlogs" / "tests" / "mytest"
        testlogs.mkdir(parents=True)
        xml_content = (
            '<?xml version="1.0"?>'
            '<testsuite><testcase name="test_ok" classname="mod"/></testsuite>'
        )
        (testlogs / "test.xml").write_text(xml_content)
        logger = MagicMock()
        _log_junit_results(str(tmp_path), logger)
        logger.emit.assert_called_once()
        assert "PASS" in logger.emit.call_args[0][2]

    def test_fail_test_reads_log(self, tmp_path: Path) -> None:
        testlogs = tmp_path / "bazel-testlogs" / "tests" / "mytest"
        testlogs.mkdir(parents=True)
        xml_content = (
            '<?xml version="1.0"?>'
            '<testsuite><testcase name="test_bad" classname="mod">'
            '<failure message="boom"/>'
            '</testcase></testsuite>'
        )
        (testlogs / "test.xml").write_text(xml_content)
        (testlogs / "test.log").write_text("traceback line\nassert False")
        logger = MagicMock()
        _log_junit_results(str(tmp_path), logger)
        logger.emit.assert_called_once()
        assert "FAIL" in logger.emit.call_args[0][2]
        assert "assert False" in logger.emit.call_args[0][2]

    def test_error_test(self, tmp_path: Path) -> None:
        testlogs = tmp_path / "bazel-testlogs" / "tests" / "mytest"
        testlogs.mkdir(parents=True)
        xml_content = (
            '<?xml version="1.0"?>'
            '<testsuite><testcase name="test_err" classname="mod">'
            '<error message="crash"/>'
            '</testcase></testsuite>'
        )
        (testlogs / "test.xml").write_text(xml_content)
        (testlogs / "test.log").write_text("RuntimeError: crash")
        logger = MagicMock()
        _log_junit_results(str(tmp_path), logger)
        assert "ERROR" in logger.emit.call_args[0][2]

    def test_skipped_test(self, tmp_path: Path) -> None:
        testlogs = tmp_path / "bazel-testlogs" / "tests" / "mytest"
        testlogs.mkdir(parents=True)
        xml_content = (
            '<?xml version="1.0"?>'
            '<testsuite><testcase name="test_skip" classname="mod">'
            '<skipped/>'
            '</testcase></testsuite>'
        )
        (testlogs / "test.xml").write_text(xml_content)
        logger = MagicMock()
        _log_junit_results(str(tmp_path), logger)
        assert "SKIP" in logger.emit.call_args[0][2]


class TestShellExecRun:
    """Test the _run override with bazel/pytest post-processing."""

    @patch("backend.tools.shell_exec._log_junit_results")
    @patch("backend.server.forge_logger.forge_logger")
    def test_run_pytest_command_logs_junit(
        self,
        mock_logger: MagicMock,
        mock_junit: MagicMock,
    ) -> None:
        tool = ShellExecTool(workspace="/tmp", allowlist=["pytest*"])
        with patch.object(tool, "_execute", return_value="1 passed"):
            result = tool._run(command="pytest tests/")
            mock_junit.assert_called_once()
            assert result == "1 passed"

    @patch("backend.tools.shell_exec._regen_build_files")
    @patch("backend.tools.shell_exec._log_junit_results")
    @patch("backend.server.forge_logger.forge_logger")
    def test_run_bazel_test_regens_build_files(
        self,
        mock_logger: MagicMock,
        mock_junit: MagicMock,
        mock_regen: MagicMock,
    ) -> None:
        tool = ShellExecTool(workspace="/tmp", allowlist=["bazel*"])
        with patch.object(tool, "_execute", return_value="OK"):
            tool._run(command="bazel test //...")
            mock_regen.assert_called_once_with("/tmp")

    @patch("backend.server.forge_logger.forge_logger")
    def test_run_exception_returns_tool_error(self, mock_logger: MagicMock) -> None:
        tool = ShellExecTool(workspace="/tmp", allowlist=["echo*"])
        with patch.object(tool, "_execute", side_effect=RuntimeError("boom")):
            result = tool._run(command="echo hi")
            assert "TOOL_ERROR" in result
            assert "boom" in result

    @patch("backend.tools.shell_exec._log_junit_results")
    @patch("backend.server.forge_logger.forge_logger")
    def test_run_failed_command_logged_as_failure(
        self,
        mock_logger: MagicMock,
        mock_junit: MagicMock,
    ) -> None:
        tool = ShellExecTool(workspace="/tmp", allowlist=["false*"])
        with patch.object(tool, "_execute", return_value="EXIT 1:\nsome error"):
            result = tool._run(command="false")
            # Should still return the result
            assert "EXIT 1" in result

    @patch("backend.server.forge_logger.forge_logger")
    def test_run_non_test_command_skips_junit(self, mock_logger: MagicMock) -> None:
        tool = ShellExecTool(workspace="/tmp", allowlist=["echo*"])
        with patch.object(tool, "_execute", return_value="hello"):
            with patch("backend.tools.shell_exec._log_junit_results") as mock_junit:
                tool._run(command="echo hello")
                mock_junit.assert_not_called()

    @patch("backend.server.forge_logger.forge_logger")
    def test_run_empty_output(self, mock_logger: MagicMock) -> None:
        tool = ShellExecTool(workspace="/tmp", allowlist=["true*"])
        with patch.object(tool, "_execute", return_value=""):
            result = tool._run(command="true")
            assert result == ""


# ═══════════════════════════════════════════════════════════════════════════
# 2. graph_ops
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.graph_ops import (
    GraphAddNodeTool,
    GraphDeleteNodeTool,
    GraphUpdateNodeTool,
)


def _graph_tool[T](tool_cls: type[T], graph: object) -> T:
    """Build a single-operation graph tool around ``graph``.

    ``_GraphMutationTool`` defines ``__init__(self, graph=None)``, but it also
    declares ``_graph`` as a class-level annotation on a pydantic model, so
    mypy synthesises a keyword-only ``__init__`` from the fields and never sees
    the real one. Route construction through the true runtime signature.
    """
    factory = cast("Callable[[object], T]", tool_cls)
    return factory(graph)


class TestGraphMutationToolBase:
    def test_execute_no_graph_returns_error(self) -> None:
        tool = _graph_tool(GraphAddNodeTool, None)
        result = tool._execute(node_type="HLR")
        assert result == "ERROR: Graph not available"

    def test_execute_async_error_returns_error(self) -> None:
        mock_graph = MagicMock()
        tool = _graph_tool(GraphAddNodeTool, mock_graph)
        with patch("backend.tools.graph_ops.run_async", side_effect=RuntimeError("async fail")):
            result = tool._execute(node_type="HLR")
            assert "ERROR" in result
            assert "async fail" in result


class TestGraphAddNodeTool:
    def test_delegates_to_graph_write(self) -> None:
        from backend.graph.models import GraphNode, NodeType
        new_node = GraphNode(node_id="HLR-001", node_type=NodeType.HLR.value, title="Test")
        mock_graph = MagicMock()
        mock_graph.add_node = AsyncMock(return_value=new_node)

        tool = _graph_tool(GraphAddNodeTool, mock_graph)
        with patch(
            "backend.tools.graph_write.GraphWriteTool._op_add_node",
            new_callable=AsyncMock,
            return_value="OK: added node HLR-001",
        ):
            result = tool._execute(node_type="HLR", title="Test")
            assert "OK" in result


class TestGraphUpdateNodeTool:
    def test_delegates_to_graph_write(self) -> None:
        mock_graph = MagicMock()
        tool = _graph_tool(GraphUpdateNodeTool, mock_graph)
        with patch(
            "backend.tools.graph_write.GraphWriteTool._op_update_node",
            new_callable=AsyncMock,
            return_value="OK: updated HLR-001",
        ):
            result = tool._execute(node_id="HLR-001", title="Updated")
            assert "OK" in result


class TestGraphDeleteNodeTool:
    def test_delegates_to_graph_write(self) -> None:
        mock_graph = MagicMock()
        tool = _graph_tool(GraphDeleteNodeTool, mock_graph)
        with patch(
            "backend.tools.graph_write.GraphWriteTool._op_delete_node",
            new_callable=AsyncMock,
            return_value="OK: deleted HLR-001",
        ):
            result = tool._execute(node_id="HLR-001")
            assert "OK" in result


# ═══════════════════════════════════════════════════════════════════════════
# 3. analysis
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.analysis import (
    CheckAtomicityTool,
    CheckConsistencyTool,
    DeriveRequirementTool,
    _is_openrouter,
    _litellm_call,
    _litellm_model,
    _resolve_model,
)


class TestIsOpenRouter:
    def test_provider_openrouter(self) -> None:
        assert _is_openrouter("openrouter", None) is True

    def test_base_url_openrouter(self) -> None:
        assert _is_openrouter("custom", "https://openrouter.ai/api/v1") is True

    def test_not_openrouter(self) -> None:
        assert _is_openrouter("ollama", "http://localhost:11434") is False

    def test_empty(self) -> None:
        assert _is_openrouter("", None) is False


class TestLitellmModel:
    def test_openrouter_provider_prepends_prefix(self) -> None:
        result = _litellm_model("mistralai/mistral-small", "openrouter", None)
        assert result == "openrouter/mistralai/mistral-small"

    def test_openrouter_already_prefixed(self) -> None:
        result = _litellm_model("openrouter/some-model", "openrouter", None)
        assert result == "openrouter/some-model"

    def test_openrouter_via_base_url(self) -> None:
        result = _litellm_model(
            "google/gemma", "", "https://openrouter.ai/api/v1"
        )
        assert result == "openrouter/google/gemma"

    def test_model_with_slash_returned_as_is(self) -> None:
        result = _litellm_model("ollama/llama3", "", None)
        assert result == "ollama/llama3"

    def test_base_url_without_slash_prepends_openai(self) -> None:
        result = _litellm_model("my-model", "", "http://localhost:8080")
        assert result == "openai/my-model"

    def test_bare_model_no_base_url(self) -> None:
        result = _litellm_model("gpt-4", "", None)
        assert result == "gpt-4"


class TestLitellmCall:
    @patch("litellm.completion")
    def test_basic_call(self, mock_completion: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "hello"
        mock_completion.return_value = mock_response

        result = _litellm_call("gpt-4", "Say hello", api_key="key123")
        assert result == "hello"
        kwargs = mock_completion.call_args[1]
        assert kwargs["api_key"] == "key123"

    @patch("litellm.completion")
    def test_base_url_passed_for_non_openrouter(self, mock_completion: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_completion.return_value = mock_response

        _litellm_call(
            "my-model", "hi",
            base_url="http://localhost:8080",
            provider="local",
        )
        kwargs = mock_completion.call_args[1]
        assert kwargs["base_url"] == "http://localhost:8080"

    @patch("litellm.completion")
    def test_base_url_not_passed_for_openrouter(self, mock_completion: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_completion.return_value = mock_response

        _litellm_call(
            "mistral/small", "hi",
            base_url="https://openrouter.ai/api/v1",
            provider="openrouter",
        )
        kwargs = mock_completion.call_args[1]
        assert "base_url" not in kwargs

    @patch("litellm.completion")
    def test_empty_content_returns_empty_string(self, mock_completion: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_completion.return_value = mock_response

        result = _litellm_call("model", "prompt")
        assert result == ""


class TestResolveModel:
    @patch.dict("os.environ", {
        "FORGE_LLM_MODEL": "test-model",
        "FORGE_API_KEY": "test-key",
        "FORGE_LLM_BASE_URL": "http://test:1234",
    })
    def test_no_config_uses_env(self) -> None:
        model, api_key, base_url, temp, provider = _resolve_model(None)
        assert model == "test-model"
        assert api_key == "test-key"
        assert base_url == "http://test:1234"
        assert temp == 0.2
        assert provider == ""

    def test_config_object(self) -> None:
        cfg = SimpleNamespace(
            model_for_phase=lambda p: "cfg-model",
            api_key_env="MY_KEY",
            base_url="http://cfg:5678",
            options=SimpleNamespace(temperature=0.5),
            active_provider="openrouter",
        )
        with patch.dict("os.environ", {"MY_KEY": "cfg-api-key"}):
            model, api_key, base_url, temp, provider = _resolve_model(cfg)
        assert model == "cfg-model"
        assert api_key == "cfg-api-key"
        assert base_url == "http://cfg:5678"
        assert temp == 0.5
        assert provider == "openrouter"

    def test_config_without_options(self) -> None:
        cfg = SimpleNamespace(
            model_for_phase=lambda p: "model",
            api_key_env="KEY",
            base_url=None,
            options=None,
            active_provider="",
        )
        with patch.dict("os.environ", {"KEY": "k"}):
            _, _, _, temp, _ = _resolve_model(cfg)
        assert temp == 0.2


class TestDeriveRequirementTool:
    @patch("backend.tools.analysis._litellm_call")
    def test_llm_success(self, mock_call: MagicMock) -> None:
        mock_call.return_value = '{"req_text": "The system shall X", "verification_method": "test", "derived": false, "derived_rationale": ""}'
        tool = DeriveRequirementTool(llm_config=None)
        result = tool._execute(parent_content="Some source text", level="hlr")
        assert result["req_text"] == "The system shall X"
        assert result["verification_method"] == "test"

    @patch("backend.tools.analysis._litellm_call", side_effect=RuntimeError("LLM down"))
    def test_llm_failure_returns_error(self, mock_call: MagicMock) -> None:
        tool = DeriveRequirementTool(llm_config=None)
        result = tool._execute(parent_content="text", level="hlr")
        assert "TOOL_ERROR" in result

    @patch("backend.tools.analysis._litellm_call")
    def test_llm_returns_non_json(self, mock_call: MagicMock) -> None:
        mock_call.return_value = "This is not JSON at all"
        tool = DeriveRequirementTool(llm_config=None)
        # When no JSON found, start=-1 and end=0, so nothing parsed
        # The function falls through without returning, so returns None
        result = tool._execute(parent_content="text", level="hlr")
        assert result is None


class TestCheckConsistencyTool:
    def test_missing_content_returns_instruction(self) -> None:
        tool = CheckConsistencyTool()
        result = tool._execute(node_id="HLR-001", child_content="", parent_content="")
        assert result["consistent"] is None
        assert len(result["issues"]) == 1
        assert "graph_read" in result["issues"][0]

    @patch("backend.tools.analysis._litellm_call")
    def test_llm_success(self, mock_call: MagicMock) -> None:
        mock_call.return_value = '{"consistent": true, "issues": [], "suggested_content": null}'
        tool = CheckConsistencyTool()
        result = tool._execute(
            node_id="HLR-001",
            child_content="The system shall log in",
            parent_content="Auth module with login",
        )
        assert result["consistent"] is True

    @patch("backend.tools.analysis._litellm_call", side_effect=RuntimeError("fail"))
    def test_llm_failure_is_not_reported_as_consistent(self, mock_call: MagicMock) -> None:
        tool = CheckConsistencyTool()
        result = tool._execute(
            node_id="N1",
            child_content="child",
            parent_content="parent",
        )
        assert result["consistent"] is None
        assert result["issues"], "no explanation given to the agent"

    @patch("backend.tools.analysis._litellm_call")
    def test_llm_returns_non_json_is_not_a_verdict(self, mock_call: MagicMock) -> None:
        mock_call.return_value = "not json"
        tool = CheckConsistencyTool()
        result = tool._execute(
            node_id="N1",
            child_content="child",
            parent_content="parent",
        )
        assert result["consistent"] is None


class TestCheckAtomicityTool:
    def test_empty_content(self) -> None:
        tool = CheckAtomicityTool()
        result = tool._execute(requirement_content="   ")
        assert result["atomic"] is True
        assert result["reason"] == "empty content"

    @patch("backend.tools.analysis._litellm_call")
    def test_llm_success(self, mock_call: MagicMock) -> None:
        mock_call.return_value = '{"atomic": false, "obligations": ["A", "B"], "reason": "two obligations"}'
        tool = CheckAtomicityTool()
        result = tool._execute(requirement_content="The system shall A and B")
        assert result["atomic"] is False
        assert len(result["obligations"]) == 2

    @patch("backend.tools.analysis._litellm_call", side_effect=RuntimeError("fail"))
    def test_llm_failure_is_not_reported_as_atomic(self, mock_call: MagicMock) -> None:
        tool = CheckAtomicityTool()
        result = tool._execute(requirement_content="The system shall do X")
        assert result["atomic"] is None
        assert result["reason"]

    @patch("backend.tools.analysis._litellm_call")
    def test_llm_returns_non_json_is_not_a_verdict(self, mock_call: MagicMock) -> None:
        mock_call.return_value = "just text no braces"
        tool = CheckAtomicityTool()
        result = tool._execute(requirement_content="Req text")
        assert result["atomic"] is None
        assert result["reason"]


# ═══════════════════════════════════════════════════════════════════════════
# 4. work_queue_tools
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.work_queue_tools import (
    QueueAddTool,
    QueuePromoteTool,
    QueueRemoveTool,
)


class TestQueueAddTool:
    @patch("backend.work_queue.work_queue")
    def test_add_success(self, mock_wq: MagicMock) -> None:
        mock_wq.add.return_value = "wq-001"
        tool = QueueAddTool(phase=3)
        result = tool._execute(
            category="missing_import",
            description="Add os import to util.py",
            target="util.py",
            effort="low",
        )
        assert "OK" in result
        assert "wq-001" in result
        mock_wq.add.assert_called_once()
        assert mock_wq.add.call_args[1]["phase"] == 3

    @patch("backend.work_queue.work_queue")
    def test_add_invalid_effort(self, mock_wq: MagicMock) -> None:
        tool = QueueAddTool()
        result = tool._execute(
            category="bug", description="Fix it", effort="extreme",
        )
        assert "ERROR" in result
        assert "extreme" in result
        mock_wq.add.assert_not_called()

    @patch("backend.work_queue.work_queue")
    def test_add_with_all_fields(self, mock_wq: MagicMock) -> None:
        mock_wq.add.return_value = "wq-002"
        tool = QueueAddTool(phase=5)
        result = tool._execute(
            category="api_mismatch",
            description="Fix API call",
            target="api.py",
            affected_files=["api.py", "client.py"],
            effort="high",
            urgency="critical",
            importance="high",
            rationale="Blocking deployment",
        )
        assert "OK" in result
        call_kwargs = mock_wq.add.call_args[1]
        assert call_kwargs["urgency"] == "critical"
        assert call_kwargs["importance"] == "high"
        assert call_kwargs["affected_files"] == ["api.py", "client.py"]


class TestQueueRemoveTool:
    @patch("backend.work_queue.work_queue")
    def test_remove_success(self, mock_wq: MagicMock) -> None:
        mock_wq.remove.return_value = True
        tool = QueueRemoveTool()
        result = tool._execute(item_id="wq-001")
        assert "OK" in result
        assert "wq-001" in result

    @patch("backend.work_queue.work_queue")
    def test_remove_not_found(self, mock_wq: MagicMock) -> None:
        mock_wq.remove.return_value = False
        tool = QueueRemoveTool()
        result = tool._execute(item_id="wq-999")
        assert "ERROR" in result
        assert "wq-999" in result


class TestQueuePromoteTool:
    @patch("backend.work_queue.work_queue")
    def test_promote_urgency(self, mock_wq: MagicMock) -> None:
        mock_wq.promote.return_value = True
        tool = QueuePromoteTool()
        result = tool._execute(item_id="wq-001", urgency="critical")
        assert "OK" in result
        assert "urgency=critical" in result

    @patch("backend.work_queue.work_queue")
    def test_promote_importance(self, mock_wq: MagicMock) -> None:
        mock_wq.promote.return_value = True
        tool = QueuePromoteTool()
        result = tool._execute(item_id="wq-001", importance="high")
        assert "OK" in result
        assert "importance=high" in result

    @patch("backend.work_queue.work_queue")
    def test_promote_both(self, mock_wq: MagicMock) -> None:
        mock_wq.promote.return_value = True
        tool = QueuePromoteTool()
        result = tool._execute(
            item_id="wq-001", urgency="high", importance="high",
        )
        assert "urgency=high" in result
        assert "importance=high" in result

    @patch("backend.work_queue.work_queue")
    def test_promote_not_found(self, mock_wq: MagicMock) -> None:
        mock_wq.promote.return_value = False
        tool = QueuePromoteTool()
        result = tool._execute(item_id="wq-999", urgency="high")
        assert "ERROR" in result
        assert "wq-999" in result


# ═══════════════════════════════════════════════════════════════════════════
# 5. graph_write — uncovered paths
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.graph_write import GraphWriteTool, _parse_json_obj, _parse_trace_to


class TestOpUpdateNodeJsonError:
    def test_invalid_properties_json_ignored(self) -> None:
        mock_graph = MagicMock()
        mock_graph.update_node = AsyncMock(return_value=(MagicMock(), MagicMock()))
        tool = GraphWriteTool(graph=mock_graph)
        result = tool._execute(
            operation="update_node",
            node_id="N1",
            properties="not valid json{{",
            content="new content",
        )
        assert "OK" in result
        # properties should be None when JSON is invalid
        call_args = mock_graph.update_node.call_args
        assert call_args[0][2] is None  # props is None


class TestOpUpdateTrace:
    def test_node_not_found(self) -> None:
        mock_graph = MagicMock()
        mock_graph.node_sync.return_value = None
        tool = GraphWriteTool(graph=mock_graph)
        result = tool._execute(
            operation="update_trace", node_id="MISSING", trace_to='["HLR-1"]',
        )
        assert "ERROR" in result
        assert "not found" in result.lower()

    def test_invalid_json_trace_to(self) -> None:
        mock_graph = MagicMock()
        tool = GraphWriteTool(graph=mock_graph)
        result = tool._execute(
            operation="update_trace", node_id="N1", trace_to="not json",
        )
        assert "ERROR" in result
        assert "JSON array" in result

    def test_native_list_trace_to_accepted(self) -> None:
        """Agent passing a native Python list instead of JSON-string works."""
        existing = MagicMock()
        existing.trace_to = ["OLD-1"]
        mock_graph = MagicMock()
        mock_graph.node_sync.return_value = existing
        mock_graph.update_node = AsyncMock(return_value=(MagicMock(), MagicMock()))
        tool = GraphWriteTool(graph=mock_graph)
        result = tool._execute(
            operation="update_trace", node_id="N1", trace_to=["NEW-1", "NEW-2"],
        )
        assert "OK" in result
        assert mock_graph.update_node.call_args[1]["trace_to"] == ["NEW-1", "NEW-2"]

    def test_success(self) -> None:
        existing = MagicMock()
        existing.trace_to = ["OLD-1"]
        mock_graph = MagicMock()
        mock_graph.node_sync.return_value = existing
        mock_graph.update_node = AsyncMock(return_value=(MagicMock(), MagicMock()))
        tool = GraphWriteTool(graph=mock_graph)
        result = tool._execute(
            operation="update_trace", node_id="N1", trace_to='["NEW-1"]',
        )
        assert "OK" in result
        assert mock_graph.update_node.call_args[1]["trace_to"] == ["NEW-1"]


class TestOpAddTraces:
    def test_invalid_json(self) -> None:
        mock_graph = MagicMock()
        tool = GraphWriteTool(graph=mock_graph)
        result = tool._execute(
            operation="add_traces", node_id="N1", trace_to="bad",
        )
        assert "ERROR" in result

    def test_node_not_found(self) -> None:
        mock_graph = MagicMock()
        mock_graph.node_sync.return_value = None
        tool = GraphWriteTool(graph=mock_graph)
        result = tool._execute(
            operation="add_traces", node_id="MISSING", trace_to='["X"]',
        )
        assert "ERROR" in result
        assert "not found" in result.lower()


class TestOpRemoveTraces:
    def test_invalid_json(self) -> None:
        mock_graph = MagicMock()
        tool = GraphWriteTool(graph=mock_graph)
        result = tool._execute(
            operation="remove_traces", node_id="N1", trace_to="bad",
        )
        assert "ERROR" in result

    def test_node_not_found(self) -> None:
        mock_graph = MagicMock()
        mock_graph.node_sync.return_value = None
        tool = GraphWriteTool(graph=mock_graph)
        result = tool._execute(
            operation="remove_traces", node_id="MISSING", trace_to='["X"]',
        )
        assert "ERROR" in result

    def test_no_matching_traces_empty_trace_to(self) -> None:
        existing = MagicMock()
        existing.trace_to = []
        mock_graph = MagicMock()
        mock_graph.node_sync.return_value = existing
        tool = GraphWriteTool(graph=mock_graph)
        result = tool._execute(
            operation="remove_traces", node_id="N1", trace_to='["X"]',
        )
        assert "no matching" in result


class TestCheckOrphanGuard:
    def test_orphan_guard_blocks(self) -> None:
        """Last same-type child cannot be moved."""
        from backend.graph.models import GraphNode, NodeType

        child = GraphNode(
            node_id="LLR-1", node_type=NodeType.LLR.value,
            title="Only LLR", parent_id="HLR-1",
        )
        mock_graph = MagicMock()
        mock_graph.children_sync.return_value = [child]

        tool = GraphWriteTool(graph=mock_graph)
        import asyncio
        result = asyncio.run(tool._check_orphan_guard(mock_graph, "LLR-1", child))
        assert result is not None
        assert "ERROR" in result
        assert "only" in result.lower()

    def test_orphan_guard_allows_when_siblings_remain(self) -> None:
        from backend.graph.models import GraphNode, NodeType

        child1 = GraphNode(
            node_id="LLR-1", node_type=NodeType.LLR.value,
            title="LLR 1", parent_id="HLR-1",
        )
        child2 = GraphNode(
            node_id="LLR-2", node_type=NodeType.LLR.value,
            title="LLR 2", parent_id="HLR-1",
        )
        mock_graph = MagicMock()
        mock_graph.children_sync.return_value = [child1, child2]

        tool = GraphWriteTool(graph=mock_graph)
        import asyncio
        result = asyncio.run(tool._check_orphan_guard(mock_graph, "LLR-1", child1))
        assert result is None


class TestParseJsonObj:
    def test_valid_json(self) -> None:
        assert _parse_json_obj('{"a": 1}') == {"a": 1}

    def test_invalid_json(self) -> None:
        assert _parse_json_obj("not json") == {}


class TestParseTraceTo:
    def test_valid_trace_to(self) -> None:
        result = _parse_trace_to({"trace_to": '["A", "B"]'}, {})
        assert result == ["A", "B"]

    def test_invalid_json_returns_empty(self) -> None:
        result = _parse_trace_to({"trace_to": "bad"}, {})
        assert result == []

    def test_fallback_to_props(self) -> None:
        props = {"trace_to": ["X", "Y"]}
        result = _parse_trace_to({}, props)
        assert result == ["X", "Y"]
        # trace_to should have been popped from props
        assert "trace_to" not in props

    def test_fallback_string_wraps_in_list(self) -> None:
        props = {"trace_to": "SINGLE"}
        result = _parse_trace_to({}, props)
        assert result == ["SINGLE"]

    def test_kwargs_native_list_accepted(self) -> None:
        """Agent sometimes passes a native list rather than a JSON-string."""
        result = _parse_trace_to({"trace_to": ["A", "B"]}, {})
        assert result == ["A", "B"]

    def test_kwargs_empty_list_string(self) -> None:
        result = _parse_trace_to({"trace_to": "[]"}, {})
        assert result == []

    def test_kwargs_none_returns_empty(self) -> None:
        result = _parse_trace_to({"trace_to": None}, {})
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════
# 6. file_write
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.file_write import FileWriteTool
from backend.tools.write_validation import check_syntax as _check_syntax


class TestCheckSyntax:
    def test_valid_python(self) -> None:
        assert _check_syntax("x = 1\n", "test.py") == ""

    def test_invalid_python(self) -> None:
        result = _check_syntax("def foo(\n", "bad.py")
        assert result  # non-empty error
        assert "line" in result.lower()

    def test_syntax_error_with_text(self) -> None:
        result = _check_syntax("def 123bad():\n  pass\n", "bad.py")
        assert result
        assert "line 1" in result


class TestFileWriteTool:
    def test_write_valid_python(self, tmp_path: Path) -> None:
        tool = FileWriteTool(workspace=str(tmp_path))
        result = tool._execute(path="hello.py", content="x = 1\n")
        assert "OK" in result
        assert (tmp_path / "hello.py").read_text() == "x = 1\n"

    def test_write_invalid_python_rejected(self, tmp_path: Path) -> None:
        tool = FileWriteTool(workspace=str(tmp_path))
        result = tool._execute(path="bad.py", content="def foo(\n")
        assert "REJECTED" in result
        assert not (tmp_path / "bad.py").exists()

    def test_write_non_python_skips_syntax_check(self, tmp_path: Path) -> None:
        tool = FileWriteTool(workspace=str(tmp_path))
        result = tool._execute(path="data.txt", content="not python {{{")
        assert "OK" in result

    def test_write_exception_path(self, tmp_path: Path) -> None:
        tool = FileWriteTool(workspace=str(tmp_path))
        (tmp_path / "target.txt").mkdir()  # dir, not file — write_text will fail
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            result = tool._execute(path="target.txt", content="data")
            assert "ERROR" in result

    def test_write_creates_subdirectories(self, tmp_path: Path) -> None:
        tool = FileWriteTool(workspace=str(tmp_path))
        result = tool._execute(path="a/b/c/d.py", content="x = 1\n")
        assert "OK" in result
        assert (tmp_path / "a" / "b" / "c" / "d.py").exists()


# ═══════════════════════════════════════════════════════════════════════════
# 7. insert_lines
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.insert_lines import InsertLinesTool


class TestInsertLinesTool:
    def test_file_not_found(self, tmp_path: Path) -> None:
        tool = InsertLinesTool(workspace=str(tmp_path))
        result = tool._execute(path="missing.py", after_line=0, text="x")
        assert "ERROR" in result
        assert "not found" in result.lower()

    def test_path_is_directory(self, tmp_path: Path) -> None:
        (tmp_path / "subdir").mkdir()
        tool = InsertLinesTool(workspace=str(tmp_path))
        result = tool._execute(path="subdir", after_line=0, text="x")
        assert "ERROR" in result
        assert "not a file" in result.lower()

    def test_after_line_out_of_range(self, tmp_path: Path) -> None:
        f = tmp_path / "small.txt"
        f.write_text("line1\n")
        tool = InsertLinesTool(workspace=str(tmp_path))
        result = tool._execute(path="small.txt", after_line=99, text="new")
        assert "ERROR" in result
        assert "out of range" in result

    def test_negative_after_line(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("line1\n")
        tool = InsertLinesTool(workspace=str(tmp_path))
        result = tool._execute(path="file.txt", after_line=-1, text="new")
        assert "ERROR" in result
        assert "out of range" in result

    def test_read_error(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("content")
        tool = InsertLinesTool(workspace=str(tmp_path))
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            result = tool._execute(path="file.txt", after_line=0, text="new")
            assert "ERROR" in result

    def test_write_error(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("line1\n")
        tool = InsertLinesTool(workspace=str(tmp_path))
        with patch.object(Path, "write_text", side_effect=PermissionError("denied")):
            result = tool._execute(path="file.txt", after_line=0, text="new")
            assert "ERROR" in result

    def test_successful_insert(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("line1\nline2\n")
        tool = InsertLinesTool(workspace=str(tmp_path))
        result = tool._execute(path="file.txt", after_line=1, text="inserted")
        assert "OK" in result
        content = f.read_text()
        assert "inserted" in content


# ═══════════════════════════════════════════════════════════════════════════
# 8. code_search
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.code_search import CodeSearchTool


class TestCodeSearchTool:
    def test_binary_suffix_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        (tmp_path / "code.py").write_text("hello world")
        tool = CodeSearchTool(workspace=str(tmp_path))
        result = tool._execute(pattern="hello", glob="*")
        assert "code.py" in result
        assert "image.png" not in result

    def test_invalid_glob(self, tmp_path: Path) -> None:
        tool = CodeSearchTool(workspace=str(tmp_path))
        result = tool._execute(pattern="x", glob="[invalid")
        assert "ERROR" in result or "No matches" in result

    def test_hidden_dir_skipped(self, tmp_path: Path) -> None:
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("password = 'x'")
        tool = CodeSearchTool(workspace=str(tmp_path))
        result = tool._execute(pattern="password", glob="**/*.py")
        assert "No matches" in result

    def test_no_matches(self, tmp_path: Path) -> None:
        (tmp_path / "code.py").write_text("x = 1")
        tool = CodeSearchTool(workspace=str(tmp_path))
        result = tool._execute(pattern="NONEXISTENT")
        assert "No matches" in result

    def test_max_results_respected(self, tmp_path: Path) -> None:
        # Write a file with many matching lines
        lines = "\n".join(f"match line {i}" for i in range(100))
        (tmp_path / "big.py").write_text(lines)
        tool = CodeSearchTool(workspace=str(tmp_path))
        result = tool._execute(pattern="match", glob="**/*.py", max_results=5)
        # Should have header + 5 matches
        assert result.count("big.py:") == 5


# ═══════════════════════════════════════════════════════════════════════════
# 9. file_read
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.file_read import FileReadTool


class TestFileReadToolException:
    def test_read_exception(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("content")
        tool = FileReadTool(workspace=str(tmp_path))
        with patch.object(Path, "read_text", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "bad")):
            result = tool._execute(path="file.txt")
            assert "ERROR" in result


# ═══════════════════════════════════════════════════════════════════════════
# 10. graph_grep
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.graph_grep import GraphGrepTool, _find_matches


class TestGraphGrepFindMatches:
    def test_title_match(self) -> None:
        import re
        regex = re.compile("hello", re.IGNORECASE)
        node = SimpleNamespace(title="Hello World", content="nothing here")
        result = _find_matches(regex, node, "title")
        assert any("[title]" in m for m in result)

    def test_content_match(self) -> None:
        import re
        regex = re.compile("target", re.IGNORECASE)
        node = SimpleNamespace(title="Unrelated", content="line1\ntarget line\nline3")
        result = _find_matches(regex, node, "content")
        assert any("target" in m for m in result)

    def test_both_field(self) -> None:
        import re
        regex = re.compile("match", re.IGNORECASE)
        node = SimpleNamespace(title="match title", content="match content")
        result = _find_matches(regex, node, "both")
        assert len(result) == 2

    def test_no_match(self) -> None:
        import re
        regex = re.compile("zzz", re.IGNORECASE)
        node = SimpleNamespace(title="abc", content="def")
        result = _find_matches(regex, node, "content")
        assert result == []

    def test_empty_content(self) -> None:
        import re
        regex = re.compile("x", re.IGNORECASE)
        node = SimpleNamespace(title=None, content=None)
        result = _find_matches(regex, node, "both")
        assert result == []


class TestGraphGrepTool:
    def test_invalid_regex(self) -> None:
        mock_graph = MagicMock()
        mock_graph.all_nodes.return_value = []
        tool = GraphGrepTool(graph=mock_graph)
        result = tool._execute(pattern="[invalid")
        assert "ERROR" in result
        assert "Invalid regex" in result

    def test_no_graph(self) -> None:
        tool = GraphGrepTool(graph=None)
        result = tool._execute(pattern="foo")
        assert "ERROR" in result

    def test_node_type_filter(self) -> None:
        node1 = SimpleNamespace(
            node_id="HLR-1", node_type="HLR", title="match",
            content="content",
        )
        node2 = SimpleNamespace(
            node_id="LLR-1", node_type="LLR", title="match",
            content="content",
        )
        mock_graph = MagicMock()
        mock_graph.all_nodes.return_value = [node1, node2]
        tool = GraphGrepTool(graph=mock_graph)
        result = tool._execute(pattern="match", field="title", node_type="HLR")
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["node_id"] == "HLR-1"


# ═══════════════════════════════════════════════════════════════════════════
# 11. list_dir
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.list_dir import ListDirTool


class TestListDirTool:
    def test_exception_during_listing(self, tmp_path: Path) -> None:
        tool = ListDirTool(workspace=str(tmp_path))
        with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
            result = tool._execute(path=".")
            assert "ERROR" in result

    def test_path_not_dir(self, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("data")
        tool = ListDirTool(workspace=str(tmp_path))
        result = tool._execute(path="file.txt")
        assert "ERROR" in result
        assert "not a directory" in result

    def test_nonexistent_returns_empty(self, tmp_path: Path) -> None:
        tool = ListDirTool(workspace=str(tmp_path))
        result = tool._execute(path="nonexistent")
        assert result == "[]"


# ═══════════════════════════════════════════════════════════════════════════
# 12. read_docs
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.read_docs import ReadDocsTool


class TestReadDocsTool:
    def test_no_docs_dir(self, tmp_path: Path) -> None:
        tool = ReadDocsTool(workspace=str(tmp_path))
        result = tool._execute()
        assert "No docs/ directory" in result

    def test_read_exception(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        f = docs / "test.md"
        f.write_text("content")
        tool = ReadDocsTool(workspace=str(tmp_path))
        with patch.object(Path, "read_text", side_effect=OSError("disk error")):
            result = tool._execute(filename="test.md")
            assert "ERROR" in result
            assert "disk error" in result

    def test_file_not_found_lists_available(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "existing.md").write_text("content")
        tool = ReadDocsTool(workspace=str(tmp_path))
        result = tool._execute(filename="missing.md")
        assert "ERROR" in result
        assert "existing.md" in result

    def test_list_docs_empty(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        tool = ReadDocsTool(workspace=str(tmp_path))
        result = tool._execute()
        assert "No documentation files" in result

    def test_list_docs_with_files(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "01-Intro.md").write_text("intro content")
        (docs / "02-Design.md").write_text("design content")
        tool = ReadDocsTool(workspace=str(tmp_path))
        result = tool._execute()
        assert "01-Intro.md" in result
        assert "02-Design.md" in result
        assert "KB" in result


# ═══════════════════════════════════════════════════════════════════════════
# 13. file_rename
# ═══════════════════════════════════════════════════════════════════════════

from backend.tools.file_rename import FileRenameTool


class TestFileRenameTool:
    def test_source_not_found(self, tmp_path: Path) -> None:
        tool = FileRenameTool(workspace=str(tmp_path))
        result = tool._execute(old_path="missing.txt", new_path="new.txt")
        assert "ERROR" in result
        assert "not found" in result

    def test_source_is_dir(self, tmp_path: Path) -> None:
        (tmp_path / "mydir").mkdir()
        tool = FileRenameTool(workspace=str(tmp_path))
        result = tool._execute(old_path="mydir", new_path="newdir")
        assert "ERROR" in result
        assert "not a file" in result

    def test_dest_exists(self, tmp_path: Path) -> None:
        (tmp_path / "old.txt").write_text("old")
        (tmp_path / "new.txt").write_text("new")
        tool = FileRenameTool(workspace=str(tmp_path))
        result = tool._execute(old_path="old.txt", new_path="new.txt")
        assert "ERROR" in result
        assert "already exists" in result

    def test_os_error_during_rename(self, tmp_path: Path) -> None:
        (tmp_path / "old.txt").write_text("data")
        tool = FileRenameTool(workspace=str(tmp_path))
        with patch.object(Path, "rename", side_effect=OSError("cross-device")):
            result = tool._execute(old_path="old.txt", new_path="sub/new.txt")
            assert "ERROR" in result
            assert "cross-device" in result

    def test_successful_rename(self, tmp_path: Path) -> None:
        (tmp_path / "old.txt").write_text("data")
        tool = FileRenameTool(workspace=str(tmp_path))
        result = tool._execute(old_path="old.txt", new_path="new.txt")
        assert "OK" in result
        assert (tmp_path / "new.txt").exists()
        assert not (tmp_path / "old.txt").exists()
