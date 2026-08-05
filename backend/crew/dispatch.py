"""Agent dispatch — resolves a single Gap by invoking the appropriate LangGraph agent.

Extracted from ForgeFlow to keep flow.py focused on phase orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langchain_core.messages import HumanMessage

from backend.agents.definitions import GAP_AGENT_MAPPING
from backend.analysis.gaps import Gap, GapType
from backend.crew.phase_constraints import reset_phase_constraints, set_phase_constraints
from backend.crew.phase_context import phase_context
from backend.prompting.builder import build_context_for_gap, build_task_description, find_suite_id
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

_MAX_API_RETRIES = 5
_API_RETRY_BASE_SECS = 2


class DispatchQuotaError(Exception):
    """Raised when the LLM API quota is exhausted — callers should stop dispatching."""


def _is_transient_error(exc: Exception) -> bool:
    """Return True if exc is a transient API/network error worth retrying."""
    try:
        import openai

        if isinstance(exc, openai.APIError):
            code = getattr(exc, "status_code", None)
            return code is None or code >= 500
    except ImportError:
        pass
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def _is_quota_error(exc: Exception) -> bool:
    """Return True if exc is a fatal quota/auth error that won't resolve with retries."""
    try:
        import openai

        if isinstance(exc, openai.APIStatusError):
            code = getattr(exc, "status_code", None)
            if code in (401, 402, 403):
                return True
    except ImportError:
        pass
    return False


async def try_fast_trace(flow: Any, gap: Gap) -> bool:
    """Fast-path for UNDESIGNED gaps when a DESIGN already exists."""
    if gap.type != GapType.UNDESIGNED:
        return False
    llr = flow.graph.node_sync(gap.node_id)
    if llr is None or not llr.parent_id:
        return False
    module_ids = flow.graph.nodes_tracing_to(llr.parent_id, source_type="MODULE")
    if not module_ids:
        return False
    children = flow.graph.children_sync(module_ids[0])
    designs = [c for c in children if c.node_type == "DESIGN"]
    if not designs:
        return False
    design = designs[0]
    existing = design.trace_to or []
    if gap.node_id in existing:
        return True
    await flow.graph.update_node(
        design.node_id,
        content=None,
        properties=None,
        changed_by="fast-path",
        change_reason=f"link {gap.node_id}",
        trace_to=existing + [gap.node_id],
    )
    forge_logger.emit(
        "INFO", "FLOW ", f"Fast-path: added {gap.node_id} to {design.node_id}.trace_to"
    )
    return True


async def dispatch(flow: Any, gap: Gap, attempt: int = 1) -> str:
    """Dispatch the gap to the appropriate LangGraph agent."""
    from backend.observability import log_context  # noqa: PLC0415

    gap_id = f"{gap.type.value}:{gap.node_id}:{attempt}"
    with log_context(
        gap_type=gap.type.value, node_id=gap.node_id, gap_id=gap_id,
        attempt=attempt,
    ):
        if await try_fast_trace(flow, gap):
            forge_logger.decision(
                "dispatch", "fast_path", "existing DESIGN linked",
            )
            return "fast-path trace"

        agent = flow.pool.get_agent_for_gap(gap.type)
        if agent is None:
            logger.warning("forge.flow.no_agent_for_gap gap=%s", gap.type)
            forge_logger.no_agent_for_gap(gap.type.value)
            return ""

        role_def = GAP_AGENT_MAPPING.get(gap.type)
        role_name = role_def.value if role_def else str(gap.type)
        forge_logger.decision(
            "dispatch", "agent_dispatch", f"role={role_name}", attempt=attempt,
        )
        forge_logger.agent_dispatch(role_name, gap.type.value, gap.node_id)

        t0 = time.monotonic()
        crew_output = ""
        token = set_phase_constraints(gap.type)
        pre_count = flow._graph_state_count()
        dispatch_outcome = "ok"  # obs #13
        try:
            for api_attempt in range(_MAX_API_RETRIES):
                try:
                    crew_output = await run_agent_task(
                        flow,
                        agent,
                        gap,
                        attempt=attempt,
                        model=_get_model(flow),
                    )
                    forge_logger.agent_done(
                        role_name, (time.monotonic() - t0) * 1000,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    if _is_transient_error(exc) and api_attempt < _MAX_API_RETRIES - 1:
                        # If graph already changed (partial work done),
                        # don't retry — the outer loop will re-evaluate.
                        if flow._graph_state_count() > pre_count:
                            forge_logger.emit(
                                "INFO",
                                "FLOW",
                                f"Transient error but graph changed — skipping retry ({exc})",
                                error_type=type(exc).__name__,
                            )
                            break
                        wait = _API_RETRY_BASE_SECS**api_attempt
                        logger.warning(
                            "forge.flow.transient_api_error gap=%s attempt=%d wait=%.0fs: %s",
                            gap.type, api_attempt + 1, wait, exc,
                        )
                        forge_logger.emit(
                            "WARN", "FLOW",
                            f"Transient API error, retrying in {wait:.0f}s… ({exc})",
                            error_type=type(exc).__name__,
                            retry_wait_s=wait,
                            api_attempt=api_attempt + 1,
                        )
                        await asyncio.sleep(wait)
                    else:
                        import traceback  # noqa: PLC0415

                        logger.warning(
                            "forge.flow.dispatch_error gap=%s error=%s\n%s",
                            gap.type, exc, traceback.format_exc(),
                        )
                        forge_logger.agent_error(
                            role_name, f"{type(exc).__name__}: {exc}",
                        )
                        if api_attempt >= _MAX_API_RETRIES - 1:
                            forge_logger.emit(
                                "ERROR", "FLOW",
                                f"retries exhausted after {_MAX_API_RETRIES}",
                                error_type=type(exc).__name__,
                                api_attempt=api_attempt + 1,
                            )
                        if _is_quota_error(exc):
                            dispatch_outcome = "quota"
                            raise DispatchQuotaError(str(exc)) from exc
                        dispatch_outcome = (
                            "timeout"
                            if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                            else "exception"
                        )
                        break
        finally:
            reset_phase_constraints(token)
            # Obs #13: final one-line outcome marker per dispatch.
            forge_logger.emit(
                "INFO", "FLOW",
                f"dispatch_outcome={dispatch_outcome} "
                f"{gap.type.value}:{gap.node_id} attempt={attempt}",
                dispatch_outcome=dispatch_outcome,
            )
        return crew_output


def _get_model(flow: Any) -> str:
    """Extract the current LLM model name from flow config."""
    try:
        phase = getattr(flow, "_current_phase", None) or 0
        # flow is duck-typed (Any); LLMConfig.model_for_phase returns str.
        model: str = flow.config.llm.model_for_phase(phase)
        return model
    except Exception:  # noqa: BLE001
        return ""


async def run_agent_task(
    flow: Any,
    agent: Any,
    gap: Gap,
    attempt: int = 1,
    model: str = "",
) -> str:
    """Invoke a LangGraph agent with phase-scoped conversation context."""
    ancestor_context = build_context_for_gap(flow.graph, gap)
    suite_id = find_suite_id(flow.graph) if flow.graph else ""

    _log_dispatch_diagnostics(flow, gap, ancestor_context)

    description, _ = build_task_description(
        gap,
        ancestor_context,
        attempt=attempt,
        suite_id=suite_id,
    )
    forge_logger.emit(
        "INFO",
        "CREW ",
        f"Task for {gap.type.value}:{gap.node_id} (attempt {attempt})",
        description.replace("\n", " ↵ "),
    )

    phase = getattr(flow.state, "current_phase", 0)

    thread_id = phase_context.get_thread_id(phase, gap.type)

    config: dict[str, Any] = {
        "recursion_limit": 500,
        "configurable": {"thread_id": thread_id},
    }

    raw = ""
    tool_call_count = 0
    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=description)]},
        version="v2",
        config=config,
    ):
        if event.get("event") != "on_chat_model_end":
            continue
        if model:
            forge_logger.emit(
                "INFO",
                "CREW ",
                f"LLM → {gap.type.value}:{gap.node_id}",
                model=model,
            )
        msg = event.get("data", {}).get("output")
        if msg is None:
            continue
        if hasattr(msg, "message"):
            msg = msg.message
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                forge_logger.crew_tool_call(str(name), str(args))
                tool_call_count += 1
        else:
            raw = getattr(msg, "content", "") or ""
    if tool_call_count == 0 and raw:
        forge_logger.emit(
            "WARN",
            "CREW ",
            f"No tool calls from agent — possible hallucination ({gap.type.value}:{gap.node_id})",
            f"Text-only response ({len(raw)} chars): {raw[:120].replace(chr(10), ' ')!r}",
        )
    forge_logger.crew_finish(raw)
    return raw


def _log_dispatch_diagnostics(flow: Any, gap: Gap, ancestor_context: str) -> None:
    """Log context preview and node reachability diagnostics."""
    ctx_preview = ancestor_context[:200].replace("\n", " ↵ ") if ancestor_context else "(empty)"
    forge_logger.emit(
        "DEBUG",
        "CREW ",
        f"Context for {gap.type.value}:{gap.node_id}: "
        f"{len(ancestor_context)} chars — {ctx_preview}",
    )
    _diag_node = flow.graph.node_sync(gap.node_id) if flow.graph else None
    if _diag_node:
        _diag_parent = _diag_node.parent_id or "(none)"
        _diag_content = (_diag_node.content or "")[:80].replace("\n", " ")
        forge_logger.emit(
            "DEBUG",
            "CREW ",
            f"  node_sync({gap.node_id}): parent={_diag_parent} content={_diag_content!r}",
        )
    else:
        forge_logger.emit(
            "WARN", "CREW ", f"  node_sync({gap.node_id}) returned None — node not in memory!"
        )
