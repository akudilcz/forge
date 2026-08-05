"""AgentFactory — builds LangGraph react agents from agent definitions."""

from __future__ import annotations

import os
from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from backend.agents.definitions import AGENT_REGISTRY, GAP_AGENT_MAPPING, AgentDefinition, AgentRole
from backend.agents.gap_prompts import get_default_gap_prompt, has_default_gap_prompt
from backend.agents.throttle import llm_throttle
from backend.analysis.gaps import GapType
from backend.config.models import ForgeConfig
from backend.server.forge_logger import forge_logger
from backend.tools.registry import ToolRegistry

#: Global LLM call counter — incremented on every API call.
#: Integration tests set ``llm_call_limit`` to cap runaway loops.
llm_call_count: int = 0
llm_call_limit: int | None = None


class LLMCallLimitExceededError(RuntimeError):
    """Raised when the global LLM call counter exceeds the configured limit."""


class ThrottledChatOpenAI(ChatOpenAI):  # type: ignore[misc]
    """ChatOpenAI subclass that enforces a global minimum delay between API calls."""

    def _log_call(self, messages: list[Any]) -> None:
        """Log the outgoing LLM call with model name, context size, and prompt snippet.

        Runs inside a log_context(call_id=...) block (set by _astream/_agenerate)
        so the outbound '→ model' record correlates with the subsequent
        'stream-end' / 'response' records by call_id.
        """
        global llm_call_count  # noqa: PLW0603
        from backend.agents.llm_callback import _get_context_window

        llm_call_count += 1
        if llm_call_limit is not None and llm_call_count > llm_call_limit:
            raise LLMCallLimitExceededError(
                f"LLM call limit exceeded: {llm_call_count} > {llm_call_limit}"
            )

        model = self.model_name or "unknown"
        prompt_chars = sum(len(getattr(m, "content", "") or "") for m in messages)
        prompt_tokens = max(1, prompt_chars // 4)
        ctx_window = _get_context_window()
        ctx_kb = prompt_tokens // 250
        win_kb = ctx_window // 250
        # Obs #14: first 200 chars of the last user message so hung turns can
        # be correlated back to the prompt that started them.
        last_user = next(
            (getattr(m, "content", "") or "" for m in reversed(messages)
             if getattr(m, "type", "") == "human" or getattr(m, "role", "") == "user"),
            "",
        )
        snippet = (last_user if isinstance(last_user, str) else str(last_user))[:200].replace("\n", " ↵ ")
        forge_logger.emit(
            "INFO",
            "LLM  ",
            f"→ {model} ~{prompt_tokens}t [{ctx_kb}kb/{win_kb}kb] call#{llm_call_count}",
            model=model,
            prompt_tokens=str(prompt_tokens),
            context_window=str(ctx_window),
            prompt_snippet=snippet or None,
        )

    async def _astream(self, *args: Any, **kwargs: Any) -> Any:
        import time  # noqa: PLC0415

        from backend.observability import log_context, new_call_id  # noqa: PLC0415

        await llm_throttle.wait()
        call_id = new_call_id()
        t0 = time.monotonic()
        tool_call_count = 0
        prompt_tokens = 0
        completion_tokens = 0
        last_chunk: Any = None
        thinking_snippet: str | None = None
        try:
            # Obs #1: log_context entered BEFORE _log_call so the outbound
            # '→ model' record carries the same call_id as the subsequent
            # stream-end / response records.
            with log_context(call_id=call_id, model=self.model_name or None):
                if args:
                    self._log_call(args[0])
                async for chunk in super()._astream(*args, **kwargs):
                    last_chunk = chunk
                    usage = getattr(chunk, "usage_metadata", None)
                    if usage:
                        prompt_tokens = int(usage.get("input_tokens", 0) or 0) or prompt_tokens
                        completion_tokens = int(usage.get("output_tokens", 0) or 0) or completion_tokens
                    # Obs #8: capture thinking block if present.
                    thinking_snippet = thinking_snippet or _extract_thinking(chunk)
                    yield chunk
        except Exception as exc:  # noqa: BLE001
            forge_logger.llm_error(self.model_name or "unknown", f"{type(exc).__name__}: {exc}")
            raise
        finally:
            dur = int((time.monotonic() - t0) * 1000)
            # Obs #3: tool_call_count from the final coalesced AIMessage
            # (chunks carry tool_call_chunks deltas; LangChain's __add__ sums
            # them into .tool_calls on the final message).
            tool_call_count = len(getattr(last_chunk, "tool_calls", None) or [])
            # Obs #2: fallback to response_metadata.token_usage when
            # usage_metadata was never populated on any chunk (Poe's endpoint).
            if (prompt_tokens == 0 or completion_tokens == 0) and last_chunk is not None:
                rmeta = getattr(last_chunk, "response_metadata", None) or {}
                usage = rmeta.get("token_usage") or rmeta.get("usage") or {}
                if usage:
                    prompt_tokens = prompt_tokens or int(
                        usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                    )
                    completion_tokens = completion_tokens or int(
                        usage.get("completion_tokens") or usage.get("output_tokens") or 0
                    )
            forge_logger.emit(
                "INFO", "LLM  ",
                f"stream-end {self.model_name} {dur}ms "
                f"tool_calls={tool_call_count} "
                f"in={prompt_tokens}t out={completion_tokens}t",
                model=self.model_name or None,
                call_id=call_id,
                duration_ms=dur,
                tool_call_count=tool_call_count,
                prompt_tokens=prompt_tokens or None,
                completion_tokens=completion_tokens or None,
                thinking=thinking_snippet,
            )

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        import time  # noqa: PLC0415

        from backend.observability import log_context, new_call_id  # noqa: PLC0415

        await llm_throttle.wait()
        call_id = new_call_id()
        t0 = time.monotonic()
        try:
            with log_context(call_id=call_id, model=self.model_name or None):
                if args:
                    self._log_call(args[0])
                result = await super()._agenerate(*args, **kwargs)
            dur = int((time.monotonic() - t0) * 1000)
            gens = getattr(result, "generations", None) or []
            message = gens[0].message if gens else None
            completion = getattr(message, "content", "") or "" if message else ""
            tool_calls = getattr(message, "tool_calls", None) or [] if message else []
            # Obs #2: usage_metadata → response_metadata fallback
            usage = (getattr(message, "usage_metadata", None) or {}) if message else {}
            prompt_tokens = int(usage.get("input_tokens", 0) or 0)
            completion_tokens = int(usage.get("output_tokens", 0) or 0)
            if (prompt_tokens == 0 or completion_tokens == 0) and message is not None:
                rmeta = getattr(message, "response_metadata", None) or {}
                rusage = rmeta.get("token_usage") or rmeta.get("usage") or {}
                if rusage:
                    prompt_tokens = prompt_tokens or int(
                        rusage.get("prompt_tokens") or rusage.get("input_tokens") or 0
                    )
                    completion_tokens = completion_tokens or int(
                        rusage.get("completion_tokens") or rusage.get("output_tokens") or 0
                    )
            thinking_snippet = _extract_thinking(message) if message else None
            forge_logger.emit(
                "INFO", "LLM  ",
                f"response {self.model_name} {dur}ms "
                f"tool_calls={len(tool_calls)} content_len={len(completion)} "
                f"in={prompt_tokens}t out={completion_tokens}t",
                model=self.model_name or None,
                call_id=call_id,
                duration_ms=dur,
                tool_call_count=len(tool_calls),
                prompt_tokens=prompt_tokens or None,
                completion_tokens=completion_tokens or None,
                thinking=thinking_snippet,
            )
            return result
        except Exception as exc:
            forge_logger.llm_error(
                self.model_name or "unknown", f"{type(exc).__name__}: {exc}",
            )
            raise


def _extract_thinking(message_or_chunk: Any) -> str | None:
    """Return the first ~300 chars of any extended-thinking content block.

    Anthropic returns thinking as a content block of type 'thinking' alongside
    text/tool_use blocks. On LangChain AIMessage this surfaces as a list of
    dicts in ``content`` with ``{"type": "thinking", "thinking": "..."}``.
    If no thinking block is present, returns None.
    """
    if message_or_chunk is None:
        return None
    content = getattr(message_or_chunk, "content", None)
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            text = block.get("thinking") or block.get("text") or ""
            if text:
                return str(text)[:300].replace("\n", " ↵ ")
    return None


# ── Prompt stores (in-memory; per-process lifetime) ───────────────────────────
#
# Two-level hierarchy: gap-type override > role override > built-in default.
# Gap-type keys use GapType.value strings (e.g. "UNCOVERED_PARA").
# Role keys use AgentRole.value strings (e.g. "Requirements Engineer").

_ROLE_PROMPTS: dict[str, str] = {}  # keyed by role name
_GAP_PROMPTS: dict[str, str] = {}  # keyed by gap type value


_ROLE_TEMPLATE_MAP: dict[str, str] = {
    "Console": "roles/console.j2",
    "Document Specialist": "roles/document_specialist.j2",
    "Requirements Engineer": "roles/requirements_engineer.j2",
    "Design Architect": "roles/design_architect.j2",
    "Software Engineer": "roles/software_engineer.j2",
    "Test Engineer": "roles/test_engineer.j2",
    "Quality Auditor": "roles/quality_auditor.j2",
}


def _build_default_prompt(role_name: str) -> str:
    """Return the default system prompt for *role_name* from Jinja templates."""
    from backend.prompt_loader import render  # noqa: PLC0415

    template = _ROLE_TEMPLATE_MAP.get(role_name)
    if template:
        return render(template)
    return f"You are a {role_name}. Always use tools to complete tasks."


# ── Role-level helpers ────────────────────────────────────────────────────────


def get_prompt(role_name: str) -> str:
    """Return the effective role-level prompt (override or built-in default)."""
    return _ROLE_PROMPTS.get(role_name) or _build_default_prompt(role_name)


def set_prompt(role_name: str, prompt: str) -> None:
    """Store a custom role-level system prompt override."""
    _ROLE_PROMPTS[role_name] = prompt


def reset_prompt(role_name: str) -> None:
    """Remove any role-level override, restoring the built-in default."""
    _ROLE_PROMPTS.pop(role_name, None)


def is_default_prompt(role_name: str) -> bool:
    """Return True if no role-level override is set."""
    return role_name not in _ROLE_PROMPTS


# ── Gap-type-level helpers ────────────────────────────────────────────────────


def get_gap_prompt(gap_type_value: str, role_name: str) -> str:
    """Return the effective prompt for a gap type.

    Priority: user gap override > gap built-in default > user role override > role built-in default.
    """
    return (
        _GAP_PROMPTS.get(gap_type_value)
        or get_default_gap_prompt(gap_type_value)
        or get_prompt(role_name)
    )


def gap_inherits_from_role(gap_type_value: str) -> bool:
    """Return True when no user or built-in gap override exists and the prompt falls to the role."""
    return not _GAP_PROMPTS.get(gap_type_value) and not has_default_gap_prompt(gap_type_value)


def set_gap_prompt(gap_type_value: str, prompt: str) -> None:
    """Store a gap-type-specific system prompt override."""
    _GAP_PROMPTS[gap_type_value] = prompt


def reset_gap_prompt(gap_type_value: str) -> None:
    """Remove any gap-type override (falls back to role-level or default)."""
    _GAP_PROMPTS.pop(gap_type_value, None)


def is_default_gap_prompt(gap_type_value: str) -> bool:
    """Return True if no gap-type override is set."""
    return gap_type_value not in _GAP_PROMPTS


def build_llm(
    config: ForgeConfig,
    model: str | None = None,
    temperature: float | None = None,
) -> ThrottledChatOpenAI:
    """Create a ThrottledChatOpenAI instance from forge config.

    Single factory for all LLM construction in the backend.

    Args:
        config: Forge configuration (single source of truth for LLM settings).
        model: Explicit model name; falls back to Quality Auditor's configured model.
        temperature: Explicit temperature; falls back to config default.

    Raises:
        RuntimeError: when the endpoint is not explicitly keyless
            (``llm.keyless``) and the environment variable named by
            ``llm.api_key_env`` is unset or empty. A missing key must fail
            loudly at construction — never fall back to a placeholder that
            surfaces as swallowed mid-run 401s.
    """
    api_key = _resolve_api_key(config)
    model_name = model or config.llm.agents[AgentRole.QUALITY_AUDITOR.value]
    temp = temperature if temperature is not None else config.llm.options.temperature
    import httpx

    # Short connect timeout, long read timeout for streaming responses
    timeout = httpx.Timeout(
        connect=30.0,
        read=float(config.llm.request_timeout),
        write=30.0,
        pool=30.0,
    )
    return ThrottledChatOpenAI(
        model=model_name,
        base_url=config.llm.base_url,
        api_key=api_key,
        temperature=temp,
        timeout=timeout,
        # Direct llm.ainvoke callers (quality checks, dedup, trace audit) have
        # no retry loop of their own — one transient 429/5xx must not kill a
        # check, so the client retries transient transport failures itself.
        max_retries=2,
    )


def _resolve_api_key(config: ForgeConfig) -> str:
    """Resolve the API key for LLM construction, failing loudly if absent.

    An explicitly keyless local endpoint (``llm.keyless = true``, e.g. Ollama)
    uses a placeholder — the OpenAI client requires a non-empty string.
    """
    if config.llm.keyless:
        return "ollama"

    env_name = config.llm.api_key_env
    if not env_name:
        raise RuntimeError(
            "LLM configuration error: llm.api_key_env is empty and the "
            "endpoint is not marked keyless. Set llm.api_key_env to the "
            "environment variable holding your API key, or set "
            "llm.keyless = true for a local keyless endpoint."
        )
    api_key = os.environ.get(env_name)
    if not api_key:
        raise RuntimeError(
            f"LLM configuration error: environment variable {env_name!r} "
            f"(llm.api_key_env) is unset or empty. Export it, or set "
            f"llm.keyless = true for a local keyless endpoint."
        )
    return api_key


class AgentFactory:
    """
    Constructs compiled LangGraph react agent graphs from agent definitions.

    Each agent is a two-node StateGraph (llm_node → tool_node) backed by
    JSON function calling. The model emits structured tool_call objects —
    no text parsing, no hallucinated Final Answers.
    """

    def __init__(self, registry: ToolRegistry, config: ForgeConfig) -> None:
        self._registry = registry
        self._config = config

    def create_agent(
        self,
        definition: AgentDefinition,
        allowed_tools: list | None = None,
        prompt_override: str | None = None,
        checkpointer: Any | None = None,
    ) -> Any:
        """Build and compile a LangGraph react agent for the given definition.

        If *allowed_tools* is provided it is used directly; otherwise the full
        role-based permission set is used. *prompt_override* takes highest priority
        over the role-level store and the built-in default.

        When *checkpointer* is provided the agent supports conversation
        accumulation across invocations sharing the same ``thread_id``.

        A ``pre_model_hook`` is attached to trim oldest messages when the
        accumulated conversation exceeds the context window budget.
        """
        from backend.crew.phase_context import make_trim_hook

        tools = (
            allowed_tools
            if allowed_tools is not None
            else self._registry.get_tools_for_role(definition.role)
        )
        model_name = self._config.llm.agents[definition.role.value]
        llm = build_llm(self._config, model=model_name)
        prompt = prompt_override or get_prompt(definition.role.value)
        ctx_window = self._config.llm.context_window_for_model(model_name)
        return create_react_agent(
            model=llm,
            tools=tools,
            prompt=prompt,
            checkpointer=checkpointer,
            pre_model_hook=make_trim_hook(ctx_window),
        )

    def create_agent_for_gap(
        self,
        gap_type: GapType,
        checkpointer: Any | None = None,
    ) -> Any | None:
        """Build a tool-whitelisted ReAct agent for the given gap type."""
        role = GAP_AGENT_MAPPING.get(gap_type)
        if role is None:
            return None
        definition = AGENT_REGISTRY[role]
        tools = self._registry.get_tools_for_gap(gap_type)
        prompt = get_gap_prompt(gap_type.value, definition.role.value)
        return self.create_agent(
            definition,
            allowed_tools=tools,
            prompt_override=prompt,
            checkpointer=checkpointer,
        )
