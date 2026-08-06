"""ForgeConfig Pydantic model and related configuration types."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProjectConfig(BaseModel):
    """Project-level configuration: name, forge.md path, and workspace directory."""

    model_config = ConfigDict(strict=True)

    name: str = "my-project"
    forgemd: str = "forge.md"
    workspace_dir: str = Field(
        default_factory=lambda: __import__("os").environ.get("FORGE_WORKSPACE", "/store/workspace"),
    )


class ServerConfig(BaseModel):
    """HTTP/WebSocket server configuration: host, port, and auth settings."""

    model_config = ConfigDict(strict=True)

    host: str = "localhost"
    port: int = 7340
    auth_token: str = ""


class LLMOptionsConfig(BaseModel):
    """Ollama inference options passed verbatim to /api/chat."""

    model_config = ConfigDict(extra="allow")

    temperature: float = 0.8
    top_p: float = 0.9
    repeat_penalty: float = 1.1
    num_thread: int = 8


class ProviderConfig(BaseModel):
    """Per-provider LLM settings stored when switching providers."""

    model_config = ConfigDict(strict=False)

    base_url: str = ""
    api_key_env: str = ""
    # Explicit opt-in for local endpoints that require no API key (e.g.
    # Ollama). Never inferred — a missing key with keyless=False is a
    # loud configuration error at LLM construction.
    keyless: bool = False
    agents: dict[str, str] = Field(default_factory=dict)
    phase_models: dict[str, str] = Field(default_factory=dict)
    model_context_windows: dict[str, int] = Field(default_factory=dict)


# Default provider presets
_ALL_ROLES = [
    "Document Specialist", "Requirements Engineer", "Design Architect",
    "Software Engineer", "Test Engineer", "Quality Auditor", "Console",
]

_POE_DEFAULTS = ProviderConfig(
    base_url="https://api.poe.com/v1",
    api_key_env="POE_API_KEY",
    agents=dict.fromkeys(_ALL_ROLES, "GPT-OSS-120B-CS"),
    phase_models={str(i): "GPT-OSS-120B-CS" for i in range(1, 12)} | {"12": "claude-sonnet-4-6"},
    model_context_windows={"GPT-OSS-120B-CS": 128000, "claude-sonnet-4-6": 1000000},
)

_OPENROUTER_DEFAULTS = ProviderConfig(
    base_url="https://openrouter.ai/api/v1",
    api_key_env="OPENROUTER_API_KEY",
    agents=dict.fromkeys(_ALL_ROLES, "openai/gpt-5.4-mini"),
    phase_models={str(i): "openai/gpt-5.4-mini" for i in range(1, 13)},
    model_context_windows={"openai/gpt-5.4-mini": 128000},
)


class LLMConfig(BaseModel):
    """LLM configuration — provider-agnostic via LiteLLM."""

    model_config = ConfigDict(strict=False)

    # Active provider key (matches a key in `providers`)
    active_provider: str = "poe"
    provider: str = "openai"
    base_url: str = "https://api.poe.com/v1"
    api_key_env: str = "POE_API_KEY"
    # Explicit opt-in for local endpoints that require no API key (e.g.
    # Ollama). Never inferred — a missing key with keyless=False is a
    # loud configuration error at LLM construction.
    keyless: bool = False
    request_timeout: int = 600
    call_delay_ms: int = 400
    # Local SQLite response cache for non-streaming LLM calls. This flag is
    # the global kill switch; per-construction-site participation is explicit
    # via build_llm(cacheable=...) — see specs/12-artifact-model-and-traceability.md §7.4.
    cache_enabled: bool = True
    # Directory holding llm_cache.db; created on first use. A relative path
    # resolves against the repo root (never the process cwd), so integration
    # tests running in throwaway workspaces share the warm repo-level cache.
    cache_dir: str = ".cache"
    # Full request/response trace of every LLM call: one JSON record per call
    # appended to <trace_dir>/trace.<pid>.jsonl. A relative trace_dir resolves
    # against the repo root (same rule as cache_dir). See
    # specs/11-observability.md §"LLM call trace".
    trace_enabled: bool = True
    trace_dir: str = ".forge/llm_trace"
    num_ctx: int = 128000
    context_window_default: int = 128000
    # Hard cap (tokens, exact tiktoken count) on the accumulated conversation
    # history re-sent per agent dispatch. Task-scaled rather than a fraction
    # of the model window — a per-gap repair never legitimately needs a full
    # model window of history. Enforced by the pre_model_hook built in
    # backend/pipeline/phase_context.py::make_trim_hook.
    dispatch_token_budget: int = 24000
    # Hard cap (tokens, exact tiktoken count) on the Phase 12 mission
    # thread's conversation per LLM call. The mission agent needs more
    # context than a single-gap dispatch (full graph context + a long
    # working session) but must be bounded: measured live, the unbounded
    # thread grew 52k→250k tokens over 140-250 calls. Enforced by the
    # pre_model_hook built in backend/codegen/mission_history.py::
    # make_mission_trim_hook — see specs/03 §History Compaction.
    mission_token_budget: int = 60000
    # Temperature bump applied to a Phase 12 mission pass that regenerates
    # a persistently failing slice from scratch (U10: after
    # REPAIR_DEPTH_CAP repair passes, diverse regeneration beats deeper
    # repair — Olausson et al.). Added to options.temperature for that
    # pass's model; normal passes use the configured default.
    regeneration_temperature_bump: float = 0.3
    # Maximum nodes judged per combined-quality LLM call. The judge's verdict
    # block scales with node count, and one call over the whole phase truncates
    # at the provider output-token limit on large phases (live evidence:
    # 81 HLRs → 62 unjudged after retry). run_combined_quality_check chunks
    # candidates to this size — see specs/12-artifact-model-and-traceability.md §7.4.
    quality_judge_batch_size: int = 25
    # Maximum items authored per batch-phase LLM call. Batch authoring output
    # scales with item count, and one call over the whole phase truncates at
    # the provider output-token limit on large documents (live evidence:
    # 46+ PARAs → 123 HLRs; the last PARAs never received HLRs and phase 3
    # halted awaiting_approval). Batch steps chunk their item lists to this
    # size with per-chunk retry — see specs/13-quality-and-convergence-guarantees.md
    # §"Batch prompts".
    batch_author_chunk_size: int = 20
    options: LLMOptionsConfig = Field(default_factory=LLMOptionsConfig)
    # Per-agent model overrides; key = AgentRole.value, value = model name
    agents: dict[str, str] = Field(
        default_factory=lambda: {
            "Document Specialist": "GPT-OSS-120B-CS",
            "Requirements Engineer": "GPT-OSS-120B-CS",
            "Design Architect": "GPT-OSS-120B-CS",
            "Software Engineer": "GPT-OSS-120B-CS",
            "Test Engineer": "GPT-OSS-120B-CS",
            "Quality Auditor": "GPT-OSS-120B-CS",
            "Console": "GPT-OSS-120B-CS",
        }
    )
    # Per-phase model config; key = phase number (str), value = model name
    phase_models: dict[str, str] = Field(
        default_factory=lambda: {
            "1": "GPT-OSS-120B-CS",
            "2": "GPT-OSS-120B-CS",
            "3": "GPT-OSS-120B-CS",
            "4": "GPT-OSS-120B-CS",
            "5": "GPT-OSS-120B-CS",
            "6": "GPT-OSS-120B-CS",
            "7": "GPT-OSS-120B-CS",
            "8": "GPT-OSS-120B-CS",
            "9": "GPT-OSS-120B-CS",
            "10": "GPT-OSS-120B-CS",
            "11": "GPT-OSS-120B-CS",
            "12": "claude-sonnet-4-6",
        }
    )
    # Per-model context window sizes (tokens); key = model name
    model_context_windows: dict[str, int] = Field(
        default_factory=lambda: {
            "GPT-OSS-120B-CS": 128000,
            "claude-sonnet-4-6": 200000,
        }
    )
    # Saved per-provider configs for quick switching
    providers: dict[str, ProviderConfig] = Field(
        default_factory=lambda: {
            "poe": _POE_DEFAULTS.model_copy(),
            "openrouter": _OPENROUTER_DEFAULTS.model_copy(),
        }
    )

    def model_for_phase(self, phase: int) -> str:
        """Return the model configured for a given phase."""
        fallback = self.phase_models.get("1", "GPT-OSS-120B-CS")
        return self.phase_models.get(str(phase), fallback)

    def context_window_for_model(self, model: str) -> int:
        """Return the context window size (tokens) for the given model."""
        return self.model_context_windows.get(model, 128000)


class ComplianceGatesConfig(BaseModel):
    """Per-phase compliance gate flags."""

    model_config = ConfigDict(strict=True)

    block_phase4_without_complete_hlrs: bool = True
    block_phase5_without_complete_llrs: bool = True
    block_phase8_without_full_coverage: bool = True
    block_phase9_without_compliance: bool = True


class ComplianceConfig(BaseModel):
    """DO-178C compliance configuration.

    Controls whether phase gate checks are enforced and at which Design
    Assurance Level (DAL).
    """

    model_config = ConfigDict(strict=True)

    enabled: bool = False
    standard: str = "DO178C"
    dal: str = "B"
    project_prefix: str = "PROJ"
    gates: ComplianceGatesConfig = Field(default_factory=ComplianceGatesConfig)


class GitConfig(BaseModel):
    """Git integration configuration."""

    model_config = ConfigDict(strict=True)

    auto_commit: bool = True
    commit_prefix: str = "[forge]"
    remote_enabled: bool = False
    remote_url: str = ""
    remote_token_env: str = "GIT_TOKEN"


class NotificationsSlackConfig(BaseModel):
    """Slack notification channel config."""

    model_config = ConfigDict(strict=True)

    webhook_url: str = ""
    events: list[str] = Field(
        default_factory=lambda: ["phase_complete", "blocker_raised", "compliance_gap"]
    )


class NotificationsConfig(BaseModel):
    """Notification delivery configuration."""

    model_config = ConfigDict(strict=True)

    enabled: bool = False
    slack: NotificationsSlackConfig = Field(default_factory=NotificationsSlackConfig)


class MemoryConfig(BaseModel):
    """Memory backend configuration."""

    model_config = ConfigDict(strict=True)

    backend: str = "chroma"
    embedding_model: str = "ollama/nomic-embed-text"
    persist_dir: str = ".forge/memory"


class ToolsConfig(BaseModel):
    """Tool permission configuration."""

    model_config = ConfigDict(strict=True)

    shell_exec_allowlist: list[str] = Field(
        default_factory=lambda: [
            "bazel *",
            "ruff check*",
            "ruff format*",
            "mypy*",
            "npm run*",
            "pnpm run*",
            "docker build*",
            "docker-compose*",
            "ollama*",
        ]
    )
    web_fetch_allowlist: list[str] = Field(
        default_factory=lambda: [
            "docs.python.org",
            "fastapi.tiangolo.com",
            "pydantic-docs.helpmanual.io",
            "react.dev",
            "npmjs.com",
            "pypi.org",
        ]
    )


class ContractsConfig(BaseModel):
    """Contract system configuration."""

    model_config = ConfigDict(strict=True)

    max_negotiation_rounds: int = 6
    require_contract_tests: bool = True


class ForgeConfig(BaseModel):
    """Top-level FORGE configuration model.

    Represents the contents of forge.toml.
    """

    model_config = ConfigDict(strict=False)

    project: ProjectConfig = Field(default_factory=ProjectConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    contracts: ContractsConfig = Field(default_factory=ContractsConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
