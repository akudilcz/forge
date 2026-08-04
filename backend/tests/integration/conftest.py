"""Shared fixtures and markers for integration tests."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env so API keys are available without manual export
load_dotenv()

from backend.config.models import ForgeConfig, LLMConfig
from backend.server.forge_logger import forge_logger
from backend.tools.base import ForgeTool
from backend.tools.file_patch import FilePatchTool
from backend.tools.file_read import FileReadTool
from backend.tools.file_rename import FileRenameTool
from backend.tools.file_write import FileWriteTool
from backend.tools.insert_lines import InsertLinesTool
from backend.tools.list_dir import ListDirTool
from backend.tools.list_files import ListFilesTool
from backend.tools.multi_file_write import MultiFileWriteTool
from backend.tools.python_lint import PythonLintTool
from backend.tools.read_docs import ReadDocsTool
from backend.tools.shell_exec import ShellExecTool


def _has_bazel() -> bool:
    """Return True if bazel is available on PATH."""
    try:
        result = subprocess.run(
            ["bazel", "version"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


HAS_BAZEL = _has_bazel()


@pytest.fixture(autouse=True)
def _enable_forge_logging() -> Iterator[None]:
    """Route forge_logger output to stdout so pytest -s captures it with test output."""
    forge_logger.enable_stdout()
    yield
    forge_logger.disable_stdout()


_REQUIRED_TEST_ENV_VARS = (
    "FORGE_TEST_API_KEY_ENV",  # name of the env var holding the key
    "FORGE_TEST_BASE_URL",     # LLM provider base URL
    "FORGE_TEST_MODEL",        # model for phases 1-11 agents
    "FORGE_TEST_MODEL_P12",    # model for phase 12 code gen
)

_ALL_ROLES = (
    "Document Specialist",
    "Requirements Engineer",
    "Design Architect",
    "Software Engineer",
    "Test Engineer",
    "Quality Auditor",
    "Console",
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        pytest.fail(
            f"Integration config error: {name} is not set. "
            f"Set it in .env — integration tests have no code-level defaults."
        )
    return value


@pytest.fixture
def integration_config() -> ForgeConfig:
    """Build a ForgeConfig from .env — single source of truth.

    All four FORGE_TEST_* variables must be set explicitly in .env.
    There are no code-level defaults: configuration lives in exactly
    one place so runs are reproducible and there is never confusion
    about which model or endpoint is in use.

    Required env vars (all mandatory):
        FORGE_TEST_API_KEY_ENV — name of the env var holding the API key
        FORGE_TEST_BASE_URL   — LLM provider base URL
        FORGE_TEST_MODEL      — model for phases 1-11 agents
        FORGE_TEST_MODEL_P12  — model for phase 12 code gen
    """
    for name in _REQUIRED_TEST_ENV_VARS:
        _require_env(name)

    api_key_env = os.environ["FORGE_TEST_API_KEY_ENV"]
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        pytest.skip(f"Integration test skipped: {api_key_env} not set")

    base_url = os.environ["FORGE_TEST_BASE_URL"]
    agent_model = os.environ["FORGE_TEST_MODEL"]
    codegen_model = os.environ["FORGE_TEST_MODEL_P12"]

    llm = LLMConfig(
        base_url=base_url,
        api_key_env=api_key_env,
        agents=dict.fromkeys(_ALL_ROLES, agent_model),
        phase_models=({str(i): agent_model for i in range(1, 12)} | {"12": codegen_model}),
        model_context_windows={
            agent_model: 128_000,
            codegen_model: 200_000,
        },
    )
    return ForgeConfig(llm=llm)


@pytest.fixture
def scenario_workspace(tmp_path: Path) -> Path:
    """Create a workspace with src/ and tests/ directories."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    return tmp_path


@pytest.fixture
def scenario_tools(scenario_workspace: Path) -> list[ForgeTool]:
    """Create the tool instances used by Phase 12 agents."""
    ws = str(scenario_workspace)
    allowlist = ["*"]  # integration tests allow all shell commands
    return [
        FileReadTool(ws),
        FileWriteTool(ws),
        FilePatchTool(ws),
        FileRenameTool(ws),
        PythonLintTool(ws),
        ShellExecTool(ws, allowlist),
        ListFilesTool(ws),
        ListDirTool(ws),
        ReadDocsTool(ws),
        InsertLinesTool(ws),
        MultiFileWriteTool(ws),
    ]
