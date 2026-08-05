"""check_trace_quality — per-function semantic trace validation tool.

The mission agent calls this after structural gaps are closed to get a
per-function assessment of whether each traced function actually implements
the LLR it claims. Returns plain text verdicts the agent can act on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.async_utils import run_async
from backend.tools.base import ForgeTool


class _Args(BaseModel):
    """Arguments for check_trace_quality."""

    file_path: str = Field(
        description="Relative path to the source file to check (e.g. 'src/planner.py').",
    )


class CheckTraceQualityTool(ForgeTool):
    """Evaluate whether each traced function genuinely implements its claimed LLR.

    Call this on a source file after all tests pass and coverage is 100%.
    Returns per-function verdicts: PASS, WEAK, or SCOPE_CREEP with rationale.
    Use the verdicts to decide what to remove or refactor.
    """

    name: str = "check_trace_quality"
    description: str = (
        "Semantic trace quality check on a source file. Evaluates each "
        "@traces-annotated function against the LLR requirement text. "
        "Returns PASS/WEAK/SCOPE_CREEP verdicts per function. Call this "
        "after achieving 100% coverage to identify unrequired code."
    )
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""
    _graph: Any = None
    _config: Any = None

    def __init__(self, workspace: str, graph: Any, config: Any) -> None:
        # name/description are supplied as field defaults on this subclass;
        # mypy models the pydantic base __init__ as requiring them.
        super().__init__()
        object.__setattr__(self, "_workspace", workspace)
        object.__setattr__(self, "_graph", graph)
        object.__setattr__(self, "_config", config)

    def _execute(self, file_path: str = "") -> str:  # type: ignore[override]
        verdict: str = run_async(self._async_check(file_path), timeout=120)
        return verdict

    async def _async_check(self, file_path: str) -> str:
        """Build the prompt, call the LLM, return the raw text verdict."""
        from backend.agents.factory import build_llm
        from backend.crew.trace_parser import analyse_traces
        from backend.server.forge_logger import forge_logger

        workspace = Path(self._workspace)
        full_path = workspace / file_path
        if not full_path.exists():
            return f"TOOL_ERROR: file not found: {file_path}"

        code = full_path.read_text(encoding="utf-8")
        analysis = analyse_traces(code)
        if not analysis.traces:
            return "No traced functions found in this file."

        llr_texts = _gather_llr_texts(self._graph)
        if not llr_texts:
            return "No LLR nodes found in the project graph."

        prompt = _build_prompt(code, analysis.traces, llr_texts)
        forge_logger.emit(
            "INFO", "QUAL ",
            f"Checking {file_path} ({len(analysis.traces)} functions, "
            f"{len(llr_texts)} LLRs)",
        )

        llm = build_llm(
            self._config, model=self._config.llm.model_for_phase(12), cacheable=True
        )
        from langchain_core.messages import HumanMessage, SystemMessage
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])

        text = response.content if hasattr(response, "content") else str(response)
        if not isinstance(text, str):
            msg = f"LLM returned non-text content: {type(text).__name__}"
            raise TypeError(msg)
        forge_logger.emit("INFO", "QUAL ", f"Quality check complete for {file_path}")
        return text


_SYSTEM_PROMPT = """\
You are a DO-178C traceability auditor. For each function, return one verdict:

- PASS: function implements behaviour described in its traced LLR(s)
- WEAK: function references a valid LLR but is tangential to it
- SCOPE_CREEP: function implements behaviour no LLR asks for

Format each verdict as: VERDICT: function_name — rationale

Check each function against ALL requirements, not just the ones it traces to.
A function that satisfies one LLR but violates another is SCOPE_CREEP.
"""


def _gather_llr_texts(graph: Any) -> dict[str, str]:
    """Build {LLR-ID: requirement text} from graph nodes."""
    return {
        n.node_id: (n.content or n.title or "").strip()
        for n in graph.all_nodes()
        if n.node_type == "LLR"
    }


def _build_prompt(
    code: str,
    traces: list[Any],
    llr_texts: dict[str, str],
) -> str:
    """Build the user message listing all requirements and traced functions."""
    llr_section = "\n".join(
        f"- {lid}: {text}" for lid, text in sorted(llr_texts.items())
    )
    func_section = "\n".join(
        f"- `{t.symbol}` (lines {t.start}-{t.end}), traces to: {', '.join(t.llr_ids)}"
        for t in traces if t.symbol
    )
    return (
        f"## Requirements\n{llr_section}\n\n"
        f"## Source Code\n```python\n{code}\n```\n\n"
        f"## Functions to Evaluate\n{func_section}"
    )
