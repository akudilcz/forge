"""Analysis tools: LLM-powered semantic analysis for FORGE agents.

Each tool wraps an LLM call and returns structured JSON output.
Falls back to deterministic defaults if LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool

_log = logging.getLogger(__name__)

# Suppress litellm's verbose "Provider List" stderr spam when it can't
# auto-detect the provider from an unknown model name.
logging.getLogger("LiteLLM").setLevel(logging.WARNING)


def _is_openrouter(provider: str, base_url: str | None) -> bool:
    """Detect OpenRouter from provider name OR base_url."""
    if provider == "openrouter":
        return True
    if base_url and "openrouter.ai" in base_url:
        return True
    return False


def _litellm_model(model: str, provider: str, base_url: str | None) -> str:
    """Add the litellm provider prefix so litellm routes correctly.

    LiteLLM needs a prefix to know how to route the call:
    - ``openrouter/`` for OpenRouter (handles base_url internally)
    - ``openai/`` for other OpenAI-compatible endpoints (Poe, vLLM, etc.)

    OpenRouter model names contain "/" (e.g. ``mistralai/mistral-small-2603``)
    which is a namespace separator, NOT a litellm provider prefix.
    """
    # OpenRouter: always prepend, even if model contains "/" (that's a namespace)
    if _is_openrouter(provider, base_url):
        if model.startswith("openrouter/"):
            return model
        return f"openrouter/{model}"
    # Already has a known litellm provider prefix
    if "/" in model:
        return model
    # Other OpenAI-compatible endpoints
    if base_url:
        return f"openai/{model}"
    return model


def _litellm_call(
    model: str,
    prompt: str,
    api_key: str = "",
    base_url: str | None = None,
    temperature: float = 0.2,
    provider: str = "",
) -> str:
    """Synchronous LLM call via litellm. Returns raw text response."""
    import litellm

    # Drop unsupported params automatically (e.g. temperature for GPT-5)
    litellm.drop_params = True

    effective_model = _litellm_model(model, provider, base_url)

    kwargs: dict[str, Any] = {
        "model": effective_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    if api_key:
        kwargs["api_key"] = api_key
    # OpenRouter: litellm handles base_url via OPENROUTER_API_BASE env var;
    # for other providers we pass base_url directly.
    if base_url and provider != "openrouter":
        kwargs["base_url"] = base_url

    response = litellm.completion(**kwargs)
    return response.choices[0].message.content or ""


def _resolve_model(llm_config: Any) -> tuple[str, str, str | None, float, str]:
    """Resolve LLM connection parameters from a config object or environment variables.

    Returns:
        Tuple of (model_string, api_key, base_url, temperature, provider).
        base_url may be None for hosted providers that don't require it.
    """
    base_url: str | None
    if llm_config is None:
        model = os.environ.get("FORGE_LLM_MODEL", "ollama/llama3.2")
        api_key = os.environ.get("FORGE_API_KEY", "ollama")
        base_url = os.environ.get("FORGE_LLM_BASE_URL", "http://localhost:11434")
        return model, api_key, base_url, 0.2, ""

    model = llm_config.model_for_phase(1)
    api_key_env = getattr(llm_config, "api_key_env", "FORGE_API_KEY")
    api_key = os.environ.get(api_key_env, "") or "ollama"
    base_url = getattr(llm_config, "base_url", None)
    options = getattr(llm_config, "options", None)
    temperature = getattr(options, "temperature", 0.2) if options else 0.2
    # Use active_provider (the user's setting) not the legacy provider field
    provider = getattr(llm_config, "active_provider", "") or getattr(llm_config, "provider", "")
    return model, api_key, base_url, temperature, provider


# ── DeriveRequirementTool ─────────────────────────────────────────────────────

_DERIVE_PROMPT = """\
Derive a formal software requirement from the source text below.
Level: {level} (hlr=high-level requirement, llr=low-level requirement)

Return ONLY valid JSON:
{{"req_text": "The system shall ...", "verification_method": "test|analysis|inspection|demonstration", "derived": false, "derived_rationale": ""}}

SOURCE:
---
{content}
---
"""


_DERIVE_NAME = "derive_requirement"
_DERIVE_DESCRIPTION = "Derive a formal requirement from source text using LLM analysis."


class DeriveRequirementInput(BaseModel):
    parent_content: str = Field(..., description="Source text to derive from")
    level: str = Field(..., description="Target level: 'hlr' or 'llr'")


class DeriveRequirementTool(ForgeTool):
    """Derive a formal, verifiable software requirement from source paragraph text using an LLM.

    Falls back to a minimal "The system shall …" stub when the LLM is unavailable.
    """

    name: str = _DERIVE_NAME
    description: str = _DERIVE_DESCRIPTION
    args_schema: type[BaseModel] = DeriveRequirementInput

    _llm_config: Any = None

    def __init__(self, llm_config: Any = None) -> None:
        """Args:
        llm_config: Optional LLM configuration object; falls back to env vars when None.
        """
        super().__init__(name=_DERIVE_NAME, description=_DERIVE_DESCRIPTION)
        object.__setattr__(self, "_llm_config", llm_config)

    def _execute(self, *args: Any, **kwargs: Any) -> Any:
        """Dispatch entry point — forwards schema-validated args to :meth:`_derive`.

        Returns the payload of :meth:`_derive` unchanged; unlike most tools this
        one hands the agent a dict rather than the ``str`` declared by
        :meth:`ForgeTool._execute`.
        """
        return self._derive(*args, **kwargs)

    def _derive(self, parent_content: str, level: str = "hlr") -> dict[str, Any] | str | None:
        """Derive a requirement dict from parent_content at the specified level.

        Returns:
            Dict with keys ``req_text``, ``verification_method``, ``derived``,
            ``derived_rationale`` when the LLM returns parseable JSON, a
            ``TOOL_ERROR`` string when the call fails, and ``None`` when the
            response contains no JSON object at all.
        """
        try:
            model, api_key, base_url, temperature, provider = _resolve_model(self._llm_config)
            prompt = _DERIVE_PROMPT.format(content=parent_content[:4000], level=level)
            raw = _litellm_call(model, prompt, api_key, base_url, temperature, provider)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                derived: dict[str, Any] = json.loads(raw[start:end])
                return derived
        except Exception as exc:  # noqa: BLE001
            _log.warning("derive_requirement.llm_failed: %s", exc)
            return f"TOOL_ERROR: LLM call failed — cannot derive requirement: {exc}"
        return None


# ── CheckConsistencyTool ──────────────────────────────────────────────────────

_CONSISTENCY_PROMPT = """\
You are a software quality auditor. Determine whether the child node is \
justified and consistent with its parent node.

A child node is INCONSISTENT (consistent=false) if ANY of these apply:
- The parent's plan or inventory does not mention or anticipate this child \
  (e.g. parent says "single module: core" but this child is a different module)
- The parent explicitly contradicts this child's existence or scope
- This child duplicates the responsibility of another sibling implied by the parent

A child node is CONSISTENT (consistent=true) only if the parent's content \
explicitly plans for or logically requires a node with this child's role.

Parent content:
---
{parent_content}
---

Child content:
---
{child_content}
---

Return ONLY valid JSON, no prose:
{{"consistent": <true|false>, "issues": ["reason if inconsistent"], "suggested_content": null}}
"""


_CONSISTENCY_NAME = "check_consistency"
_CONSISTENCY_DESCRIPTION = "Check if a node is consistent with its parent using LLM analysis."


class CheckConsistencyInput(BaseModel):
    node_id: str = Field(..., description="ID of the node to check consistency for")
    child_content: str = Field(
        default="",
        description="Full content of the child node. REQUIRED — fetch via graph_read before calling.",
    )
    parent_content: str = Field(
        default="",
        description="Full content of the parent node. REQUIRED — fetch via graph_read before calling.",
    )


class CheckConsistencyTool(ForgeTool):
    """Verify that a child graph node is semantically consistent with its parent using an LLM.

    Caller must supply pre-fetched child_content and parent_content strings;
    the tool does not perform graph reads internally.
    """

    name: str = _CONSISTENCY_NAME
    description: str = _CONSISTENCY_DESCRIPTION
    args_schema: type[BaseModel] = CheckConsistencyInput

    _llm_config: Any = None

    def __init__(self, llm_config: Any = None) -> None:
        """Args:
        llm_config: Optional LLM configuration object; falls back to env vars when None.
        """
        super().__init__(name=_CONSISTENCY_NAME, description=_CONSISTENCY_DESCRIPTION)
        object.__setattr__(self, "_llm_config", llm_config)

    def _execute(self, *args: Any, **kwargs: Any) -> Any:
        """Dispatch entry point — forwards schema-validated args to :meth:`_check`.

        Returns the dict payload of :meth:`_check` rather than the ``str``
        declared by :meth:`ForgeTool._execute`.
        """
        return self._check(*args, **kwargs)

    def _check(
        self,
        node_id: str,
        child_content: str = "",
        parent_content: str = "",
    ) -> dict[str, Any]:
        """Check consistency between child and parent content.

        Returns:
            Dict with keys ``consistent`` (bool|None), ``issues`` (list), ``suggested_content`` (str|None).
            Returns consistent=None with an instruction message when required content is missing.
        """
        if not child_content or not parent_content:
            return {
                "consistent": None,
                "issues": [
                    "child_content and parent_content are required. "
                    "Use graph_read to fetch the node and its parent, then call "
                    "check_consistency again with their content fields."
                ],
                "suggested_content": None,
            }

        try:
            model, api_key, base_url, temperature, provider = _resolve_model(self._llm_config)
            prompt = _CONSISTENCY_PROMPT.format(
                parent_content=parent_content[:2000],
                child_content=child_content[:2000],
            )
            raw = _litellm_call(model, prompt, api_key, base_url, temperature, provider)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                verdict: dict[str, Any] = json.loads(raw[start:end])
                return verdict
        except Exception as exc:  # noqa: BLE001
            _log.warning("check_consistency.llm_failed: %s", exc)
            return _unknown_consistency(f"the consistency check failed: {exc}")

        # Reached when the model replied but the reply contained no JSON object.
        # This used to return `consistent: True` — a confident PASS produced by a
        # call that never delivered a verdict, indistinguishable to the agent
        # from a genuine pass. `None` reuses the "cannot judge" contract already
        # defined above for missing content.
        _log.warning("check_consistency.unparseable_response")
        return _unknown_consistency(
            "the consistency check returned no parseable verdict"
        )


def _unknown_consistency(reason: str) -> dict[str, Any]:
    """A verdict of "cannot judge" — never a silent PASS.

    ``consistent=None`` is the contract already used when required content is
    missing: the agent is told the check did not run rather than being handed a
    clean bill of health nobody issued.
    """
    return {
        "consistent": None,
        "issues": [f"{reason}. Retry, or escalate if it keeps failing."],
        "suggested_content": None,
    }


def _unknown_atomicity(reason: str) -> dict[str, Any]:
    """Atomicity equivalent of :func:`_unknown_consistency`."""
    return {
        "atomic": None,
        "obligations": [],
        "reason": f"{reason}. Retry, or escalate if it keeps failing.",
    }


# ── CheckAtomicityTool ───────────────────────────────────────────────────────

_ATOMICITY_PROMPT = """\
You are a requirements quality auditor. Determine whether the following \
requirement is ATOMIC — i.e. it expresses exactly ONE testable obligation.

A requirement is NON-ATOMIC if it bundles multiple distinct behaviours, \
constraints, or qualities that could each be verified independently. \
Look for:
- Multiple obligations joined by 'and', 'while', semicolons
- Comma-separated clauses that each impose a separate constraint
- A single sentence that covers both a functional behaviour AND a quality/performance attribute

Requirement:
---
{requirement}
---

Return ONLY valid JSON, no prose:
{{"atomic": <true|false>, "obligations": ["obligation 1", "obligation 2", ...], \
"reason": "brief explanation"}}

If atomic, obligations should contain a single entry with the requirement restated.
If non-atomic, list each distinct obligation as a separate entry.
"""


_ATOMICITY_NAME = "check_atomicity"
_ATOMICITY_DESCRIPTION = (
    "Check if a requirement is atomic (single obligation) using LLM analysis."
)


class CheckAtomicityInput(BaseModel):
    requirement_content: str = Field(
        ..., description="The full text of the requirement to evaluate for atomicity."
    )


class CheckAtomicityTool(ForgeTool):
    """Determine whether a requirement text expresses exactly one testable obligation using an LLM.

    Falls back to reporting atomic=True when the LLM is unavailable.
    """

    name: str = _ATOMICITY_NAME
    description: str = _ATOMICITY_DESCRIPTION
    args_schema: type[BaseModel] = CheckAtomicityInput

    _llm_config: Any = None

    def __init__(self, llm_config: Any = None) -> None:
        """Args:
        llm_config: Optional LLM configuration object; falls back to env vars when None.
        """
        super().__init__(name=_ATOMICITY_NAME, description=_ATOMICITY_DESCRIPTION)
        object.__setattr__(self, "_llm_config", llm_config)

    def _execute(self, *args: Any, **kwargs: Any) -> Any:
        """Dispatch entry point — forwards schema-validated args to :meth:`_check_atomic`.

        Returns the dict payload of :meth:`_check_atomic` rather than the ``str``
        declared by :meth:`ForgeTool._execute`.
        """
        return self._check_atomic(*args, **kwargs)

    def _check_atomic(self, requirement_content: str = "") -> dict[str, Any]:
        """Evaluate atomicity of requirement_content.

        Returns:
            Dict with keys ``atomic`` (bool), ``obligations`` (list[str]), ``reason`` (str).
        """
        if not requirement_content.strip():
            return {"atomic": True, "obligations": [], "reason": "empty content"}

        try:
            model, api_key, base_url, temperature, provider = _resolve_model(self._llm_config)
            prompt = _ATOMICITY_PROMPT.format(requirement=requirement_content[:2000])
            raw = _litellm_call(model, prompt, api_key, base_url, temperature, provider)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                verdict: dict[str, Any] = json.loads(raw[start:end])
                return verdict
        except Exception as exc:  # noqa: BLE001
            _log.warning("check_atomicity.llm_failed: %s", exc)
            return _unknown_atomicity(f"the atomicity check failed: {exc}")

        # See the note in _check_consistency: an unusable reply is not a PASS.
        _log.warning("check_atomicity.unparseable_response")
        return _unknown_atomicity("the atomicity check returned no parseable verdict")
