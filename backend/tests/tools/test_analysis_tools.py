"""Tests for LLM analysis tools (with LLM mocked out)."""

from unittest.mock import MagicMock, patch

import pytest

from backend.tools.analysis import (
    CheckConsistencyTool,
    DeriveRequirementTool,
    _litellm_model,
    _resolve_model,
)


@pytest.mark.parametrize("model", ["llama3.2", "openai/gpt-5.4-mini", "GPT-OSS-120B-CS"])
def test_resolve_model_passes_model_through(model: str) -> None:
    """Model names from config are passed through verbatim — no prefix manipulation."""
    cfg = MagicMock()
    cfg.model_for_phase.return_value = model
    cfg.api_key_env = "TEST_KEY"
    cfg.base_url = "http://localhost"
    cfg.provider = "openai"
    resolved_model, _, _, _, _ = _resolve_model(cfg)
    assert resolved_model == model


def test_resolve_model_no_config() -> None:
    model, api_key, _, temperature, provider = _resolve_model(None)
    assert model
    assert api_key
    assert temperature == 0.2
    assert provider == ""


def test_resolve_model_reads_temperature_from_config() -> None:
    """Temperature from config.options flows through _resolve_model."""
    cfg = MagicMock()
    cfg.model_for_phase.return_value = "test-model"
    cfg.api_key_env = "TEST_KEY"
    cfg.base_url = "http://localhost"
    cfg.options.temperature = 0.42
    cfg.provider = "openai"
    _, _, _, temperature, _ = _resolve_model(cfg)
    assert temperature == 0.42


def test_resolve_model_returns_active_provider() -> None:
    """active_provider field is used (user's setting), not legacy provider field."""
    cfg = MagicMock()
    cfg.model_for_phase.return_value = "test-model"
    cfg.api_key_env = "TEST_KEY"
    cfg.base_url = "https://openrouter.ai/api/v1"
    cfg.options.temperature = 0.8
    cfg.active_provider = "openrouter"
    cfg.provider = "openai"  # legacy field — should be ignored
    _, _, _, _, provider = _resolve_model(cfg)
    assert provider == "openrouter"


# ── _litellm_model prefix logic ─────────────────────────────────────────────


def test_litellm_model_no_prefix_for_prefixed_model() -> None:
    """Models that already have a prefix are returned as-is."""
    assert _litellm_model("openai/gpt-4o", "openai", "http://x") == "openai/gpt-4o"
    assert _litellm_model("openrouter/openai/gpt-4o", "openrouter", None) == "openrouter/openai/gpt-4o"


def test_litellm_model_openrouter_prefix() -> None:
    """OpenRouter provider gets openrouter/ prefix."""
    assert _litellm_model("GPT-OSS-120B-CS", "openrouter", None) == "openrouter/GPT-OSS-120B-CS"


def test_litellm_model_openrouter_with_namespace() -> None:
    """OpenRouter models with namespace '/' (e.g. mistralai/mistral-small) still get prefixed."""
    assert _litellm_model("mistralai/mistral-small-2603", "openrouter", None) == "openrouter/mistralai/mistral-small-2603"
    assert _litellm_model("anthropic/claude-sonnet-4", "openrouter", None) == "openrouter/anthropic/claude-sonnet-4"
    assert _litellm_model("google/gemini-2.5-flash", "openrouter", None) == "openrouter/google/gemini-2.5-flash"


def test_litellm_model_detects_openrouter_from_base_url() -> None:
    """When provider is 'openai' but base_url is openrouter, detect and prefix correctly."""
    assert _litellm_model("mistralai/mistral-small-2603", "openai", "https://openrouter.ai/api/v1") == "openrouter/mistralai/mistral-small-2603"
    assert _litellm_model("openai/gpt-5.4-mini", "openai", "https://openrouter.ai/api/v1") == "openrouter/openai/gpt-5.4-mini"


def test_litellm_model_openai_prefix_for_custom_base_url() -> None:
    """Custom base_url with non-openrouter provider gets openai/ prefix."""
    assert _litellm_model("GPT-OSS-120B-CS", "openai", "https://api.poe.com/v1") == "openai/GPT-OSS-120B-CS"


def test_litellm_model_no_prefix_without_base_url() -> None:
    """No prefix added when there's no base_url and no openrouter provider."""
    assert _litellm_model("GPT-OSS-120B-CS", "", None) == "GPT-OSS-120B-CS"


# ── DeriveRequirementTool ─────────────────────────────────────────────────────

def test_derive_requirement_with_mocked_llm_and_fallback() -> None:
    tool = DeriveRequirementTool()
    mock_resp = '{"req_text": "The system shall handle users.", "verification_method": "test", "derived": false, "derived_rationale": ""}'
    with patch("backend.tools.analysis._litellm_call", return_value=mock_resp):
        result = tool._execute("The system must handle 100 users.", level="hlr")
    assert result["req_text"] == "The system shall handle users."

    with patch("backend.tools.analysis._litellm_call", side_effect=Exception("LLM offline")):
        result = tool._execute("The system handles errors.", level="hlr")
    # Should return an error string, NOT a garbage fallback like "The system shall ..."
    assert isinstance(result, str), "LLM failure should return error string"
    assert "ERROR" in result or "TOOL_ERROR" in result


# ── CheckConsistencyTool ──────────────────────────────────────────────────────

def test_check_consistency_no_content_returns_guidance() -> None:
    result = CheckConsistencyTool()._execute("req.1", child_content="", parent_content="")
    assert result["consistent"] is None
    assert "graph_read" in result["issues"][0]


def test_check_consistency_with_mocked_llm_and_fallback() -> None:
    tool = CheckConsistencyTool()
    mock_resp = '{"consistent": false, "issues": ["Mismatch found"], "suggested_content": "Fix it"}'
    with patch("backend.tools.analysis._litellm_call", return_value=mock_resp):
        result = tool._execute("req.1", child_content="child text", parent_content="parent text")
    assert result["consistent"] is False
    assert len(result["issues"]) == 1

    # An LLM failure is "cannot judge", not a pass. Returning True here meant an
    # offline model silently certified every requirement as consistent.
    with patch("backend.tools.analysis._litellm_call", side_effect=Exception("offline")):
        result = tool._execute("req.1", child_content="text", parent_content="parent")
    assert result["consistent"] is None
    assert result["issues"], "the agent was given no explanation of why the check failed"

    # A reply the model produced but that carries no JSON verdict is equally unusable.
    with patch("backend.tools.analysis._litellm_call", return_value="I am not sure."):
        result = tool._execute("req.1", child_content="text", parent_content="parent")
    assert result["consistent"] is None


def test_check_atomicity_llm_failure_is_not_a_pass() -> None:
    from backend.tools.analysis import CheckAtomicityTool

    tool = CheckAtomicityTool()
    with patch("backend.tools.analysis._litellm_call", side_effect=Exception("offline")):
        result = tool._execute(requirement_content="The system shall do a thing.")
    assert result["atomic"] is None, (
        "an offline model silently certified the requirement as atomic"
    )

    with patch("backend.tools.analysis._litellm_call", return_value="no json here"):
        result = tool._execute(requirement_content="The system shall do a thing.")
    assert result["atomic"] is None


def test_derive_tool_output_keys_match_persisted_property_names() -> None:
    """U4 round-trip contract: the keys the derive tool emits are exactly the
    property names the authoring prompts instruct agents to persist and the
    write-time shape checks validate (specs/13)."""
    from backend.analysis.requirement_marking import (
        DERIVED_KEY,
        DERIVED_RATIONALE_KEY,
        VERIFICATION_METHOD_KEY,
        VERIFICATION_METHODS,
    )
    from backend.tools.analysis import _DERIVE_PROMPT

    for key in (VERIFICATION_METHOD_KEY, DERIVED_KEY, DERIVED_RATIONALE_KEY):
        assert key in _DERIVE_PROMPT, key
    # The prompt constrains the method to the four standard values.
    for method in VERIFICATION_METHODS:
        assert method in _DERIVE_PROMPT, method
