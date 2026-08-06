"""evaluate_progress — let the mission agent request gap feedback on demand.

Instead of a fixed outer loop that scans after each round, the agent
calls this tool whenever it wants to check progress. The tool runs
the full workspace scan (tests, coverage, traces) and returns a
structured gap report + score.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.async_utils import run_async
from backend.tools.base import ForgeTool

_NAME = "evaluate_progress"
_DESCRIPTION = (
    "Run full workspace evaluation: tests, coverage, trace analysis, "
    "and gap detection. Returns your current score and remaining gaps. "
    "Call this after making changes to see what's still broken."
)


class _Args(BaseModel):
    """Arguments for evaluate_progress."""

    message: str = Field(
        default="",
        description="Optional note about what you changed since last evaluation.",
    )


class EvaluateProgressTool(ForgeTool):
    """Run tests, coverage, and gap analysis. Returns score and remaining gaps.

    Call this after making changes to check your progress. Returns JSON with:
    - score (0.0-1.0): minimum across tests, LLR traces, and @traces
      (statement/MC-DC percentages are report-only and do not gate it)
    - gaps: list of remaining issues with fix hints
    - breakdown: per-dimension details (tests, traces, coverage)

    The tool runs pytest + coverage under the hood, so it takes 5-30 seconds.
    """

    name: str = _NAME
    description: str = _DESCRIPTION
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""
    _graph: Any = None

    def __init__(self, workspace: str, graph: Any) -> None:
        super().__init__(name=_NAME, description=_DESCRIPTION)
        object.__setattr__(self, "_workspace", workspace)
        object.__setattr__(self, "_graph", graph)

    def _execute(self, *args: Any, **kwargs: Any) -> str:
        """Dispatch entry point — forwards schema-validated args to :meth:`_evaluate`."""
        return self._evaluate(*args, **kwargs)

    def _evaluate(self, message: str = "") -> str:
        """Run the full workspace evaluation and return the JSON report."""
        report: str = run_async(self._async_evaluate(message), timeout=120)
        return report

    async def _async_evaluate(self, message: str) -> str:
        """Run the evaluation pipeline."""
        from backend.codegen.gap_finder import find_gaps
        from backend.codegen.mission_agent import (
            _score_breakdown,
            compute_value,
            format_gaps,
        )
        from backend.server.forge_logger import forge_logger
        from backend.workspace.scanner import (
            WorkspaceState,
            _run_tests_and_coverage,
            scan_files,
        )

        workspace = Path(self._workspace)
        forge_logger.emit("INFO", "EVAL ", f"Evaluating workspace: {workspace}")

        source_files, test_files = scan_files(workspace)
        test_results, lcov, error = _run_tests_and_coverage(workspace)

        # Emit coverage to WebSocket so the frontend header updates
        emit_kwargs: dict[str, str] = {}
        if lcov.line_pct is not None:
            emit_kwargs["line_coverage"] = str(round(lcov.line_pct, 1))
        if lcov.branch_pct is not None:
            emit_kwargs["branch_coverage"] = str(round(lcov.branch_pct, 1))
        if emit_kwargs:
            forge_logger.emit("INFO", "EVAL ", "Coverage updated", **emit_kwargs)

        ws_state = WorkspaceState(
            source_files=source_files,
            test_files=test_files,
            test_results=test_results,
            coverage_pct=lcov.line_pct,
            coverage_missing=lcov.missing,
            coverage_by_file=lcov.by_file,
            uncovered_lines=lcov.uncovered_lines,
            branch_coverage_pct=lcov.branch_pct,
            test_run_error=error,
        )
        score = compute_value(ws_state, self._graph)
        gaps = find_gaps(
            ws_state.source_files, ws_state.test_files,
            ws_state.test_results, self._graph,
            test_run_error=ws_state.test_run_error,
            coverage_by_file=ws_state.coverage_by_file,
            uncovered_lines=ws_state.uncovered_lines,
            branch_coverage_pct=ws_state.branch_coverage_pct,
        )

        result = {
            "score": round(score, 3),
            "score_pct": f"{score:.0%}",
            "gap_count": len(gaps),
            "all_gaps_closed": len(gaps) == 0,
            "breakdown": _score_breakdown(ws_state, self._graph),
            "gaps": format_gaps(gaps),
            "test_summary": {
                "total": len(ws_state.test_results),
                "passed": sum(1 for t in ws_state.test_results if t.status == "passed"),
                "failed": sum(1 for t in ws_state.test_results if t.status in ("failed", "error")),
            },
            "coverage": {
                "statement_pct": ws_state.coverage_pct,
                "branch_pct": ws_state.branch_coverage_pct,
            },
        }
        return json.dumps(result, indent=2)
