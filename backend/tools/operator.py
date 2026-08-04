"""Operator tools — phase lifecycle actions for the Console agent.

Each tool is a thin wrapper around :class:`OperatorService`, translating
LLM function-call arguments into service method calls.  All execution
happens on the main event loop via ``service.run_on_main_loop``.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool

# ── Arg schemas ───────────────────────────────────────────────────────


class _RunPhaseArgs(BaseModel):
    phase: int = Field(description="Phase number to run (1-13).")
    end_phase: int | None = Field(
        default=None,
        description="End phase for running a range. Omit to run a single phase.",
    )


class _PhaseArgs(BaseModel):
    phase: int = Field(description="Phase number (2-13).")


class _NoArgs(BaseModel):
    pass


# ── Shared base ───────────────────────────────────────────────────────


class _OperatorTool(ForgeTool):
    """Base for operator tools — owns the service handle and the arg adapter.

    ``_execute`` is ForgeTool's generic entry point: it receives whatever
    keyword arguments the LLM produced for the tool's ``args_schema`` and
    forwards them to ``_invoke``, which each tool declares with its real,
    schema-matching signature.
    """

    def __init__(self, service: Any) -> None:
        super().__init__()
        self._service = service

    def _execute(self, **kwargs: Any) -> str:
        return self._invoke(**kwargs)

    def _invoke(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError


# ── Tools ─────────────────────────────────────────────────────────────


class RunPhaseTool(_OperatorTool):
    """Run a single FORGE phase or a contiguous range of phases."""

    name: str = "run_phase"
    description: str = (
        "Run a FORGE phase. Provide a phase number (1-13) to run that single "
        "phase end-to-end (scan, fix, qual, semantic). Optionally provide "
        "end_phase to run a contiguous range (e.g. phase=3, end_phase=8). "
        "Returns immediately; the phase runs in the background."
    )
    args_schema: type[BaseModel] = _RunPhaseArgs

    def _invoke(self, phase: int, end_phase: int | None = None) -> str:
        result = self._service.run_on_main_loop(
            self._service.run_phase(phase, end_phase),
        )
        return json.dumps(result)


class StopBuildTool(_OperatorTool):
    """Stop the currently running FORGE build flow."""

    name: str = "stop_build"
    description: str = (
        "Cancel the currently running build flow. "
        "All agents are set to idle. Safe to call when nothing is running."
    )
    args_schema: type[BaseModel] = _NoArgs

    def _invoke(self) -> str:
        result = self._service.run_on_main_loop(self._service.stop_build())
        return json.dumps(result)


class StopAllAgentsTool(_OperatorTool):
    """Stop every running agent — build flow, console, everything."""

    name: str = "stop_all_agents"
    description: str = (
        "Kill / stop ALL running agents immediately. Cancels the build "
        "flow and the console agent task, then resets every agent to idle. "
        "Use when the user says 'stop the agents', 'kill everything', etc."
    )
    args_schema: type[BaseModel] = _NoArgs

    def _invoke(self) -> str:
        result = self._service.run_on_main_loop(self._service.stop_all())
        return json.dumps(result)


class ScanGapsTool(_OperatorTool):
    """Scan for structural gaps in a phase."""

    name: str = "scan_gaps"
    description: str = (
        "Run the gap analyser for a phase and return the count and types "
        "of structural gaps found. Does not fix anything — observation only."
    )
    args_schema: type[BaseModel] = _PhaseArgs

    def _invoke(self, phase: int) -> str:
        result = self._service.run_on_main_loop(
            self._service.scan_gaps(phase),
        )
        return json.dumps(result)


class ScanQualityTool(_OperatorTool):
    """Detect quality gaps in a phase using LLM analysis."""

    name: str = "scan_quality"
    description: str = (
        "Surface quality gaps for a phase using LLM detect-only mode. "
        "Does not fix anything — returns the count of quality findings."
    )
    args_schema: type[BaseModel] = _PhaseArgs

    def _invoke(self, phase: int) -> str:
        result = self._service.run_on_main_loop(
            self._service.scan_quality(phase), timeout=300,
        )
        return json.dumps(result)


class QualCheckTool(_OperatorTool):
    """Dispatch quality auditor checks to fix quality gaps in a phase."""

    name: str = "qual_check"
    description: str = (
        "Dispatch the Quality Auditor to fix quality gaps in a phase. "
        "Returns immediately; the checks run in the background."
    )
    args_schema: type[BaseModel] = _PhaseArgs

    def _invoke(self, phase: int) -> str:
        result = self._service.run_on_main_loop(
            self._service.qual_check(phase),
        )
        return json.dumps(result)


class PurgeDerivedTool(_OperatorTool):
    """Delete all derived nodes and reset phases 2-13."""

    name: str = "purge_derived"
    description: str = (
        "Delete all derived graph nodes (everything except PROJECT and "
        "DOCUMENT) and reset phases 2-13 to pending. Use when you want "
        "to re-derive the entire graph from the source document."
    )
    args_schema: type[BaseModel] = _NoArgs

    def _invoke(self) -> str:
        result = self._service.run_on_main_loop(
            self._service.purge_derived(), timeout=300,
        )
        return json.dumps(result)


class IngestDocumentTool(_OperatorTool):
    """Read forge.md from the workspace and ingest it into the graph."""

    name: str = "ingest_document"
    description: str = (
        "Read the forge.md file from the workspace and create or update "
        "the DOCUMENT node in the graph. This is Phase 1 — it must be "
        "done before any other phases can run."
    )
    args_schema: type[BaseModel] = _NoArgs

    def _invoke(self) -> str:
        result = self._service.run_on_main_loop(
            self._service.ingest_document(),
        )
        return json.dumps(result)
