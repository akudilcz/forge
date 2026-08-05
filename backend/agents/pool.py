"""AgentPool — warm compiled LangGraph agent graphs, one per role."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.definitions import AGENT_REGISTRY, GAP_AGENT_MAPPING
from backend.agents.factory import AgentFactory
from backend.analysis.gaps import GapType
from backend.config.models import ForgeConfig
from backend.pipeline.phase_context import phase_context

_log = structlog.get_logger(__name__)


class UnknownAgentError(Exception):
    """Requested agent_id not registered in pool."""


class AgentPool:
    """Holds one compiled LangGraph agent per role."""

    def __init__(self, broadcaster: Any = None) -> None:
        self._agents: dict[str, Any] = {}  # role.value → agent (for get())
        self._gap_agents: dict[str, Any] = {}  # gap_type.value → tool-whitelisted agent
        self._broadcaster = broadcaster

    async def initialise(self, factory: AgentFactory, config: ForgeConfig) -> None:  # noqa: ARG002
        """Compile and register all agents from the registry."""
        from backend.server.forge_logger import forge_logger  # noqa: PLC0415

        self._factory = factory
        checkpointer = phase_context.get_checkpointer()
        for role, definition in AGENT_REGISTRY.items():
            role_name = role.value
            self._agents[role_name] = factory.create_agent(
                definition,
                checkpointer=checkpointer,
            )
            _log.info("agent.pool.registered", role=role_name)
            forge_logger.emit(
                "INFO", "POOL ", f"registered agent {role_name}",
                agent_id=role_name,
            )

        for gap_type in GAP_AGENT_MAPPING:
            agent = factory.create_agent_for_gap(
                gap_type,
                checkpointer=checkpointer,
            )
            if agent is not None:
                self._gap_agents[gap_type.value] = agent
                _log.info("agent.pool.gap_agent_registered", gap=gap_type.value)

        forge_logger.emit(
            "INFO", "POOL ",
            f"agent pool initialised — {len(self._agents)} roles, "
            f"{len(self._gap_agents)} gap handlers",
            role_count=len(self._agents),
            gap_handler_count=len(self._gap_agents),
        )

    def rebuild(self, config: ForgeConfig | None = None) -> None:
        """Re-create all agents (e.g. after API key or LLM settings change)."""
        factory = getattr(self, "_factory", None)
        if factory is None:
            return
        if config is not None:
            factory._config = config
        phase_context.reset_all()
        checkpointer = phase_context.get_checkpointer()
        for role, definition in AGENT_REGISTRY.items():
            self._agents[role.value] = factory.create_agent(
                definition,
                checkpointer=checkpointer,
            )
        for gap_type in GAP_AGENT_MAPPING:
            agent = factory.create_agent_for_gap(
                gap_type,
                checkpointer=checkpointer,
            )
            if agent is not None:
                self._gap_agents[gap_type.value] = agent
        _log.info("agent.pool.rebuilt")

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_agent_for_gap(self, gap_type: GapType) -> Any | None:
        """Return the tool-whitelisted agent for the given gap type."""
        return self._gap_agents.get(gap_type.value)

    def get(self, role_name: str) -> Any:
        """Return the compiled agent graph for the given role name."""
        agent = self._agents.get(role_name)
        if agent is None:
            raise UnknownAgentError(f"No agent with role '{role_name}' in pool")
        return agent

    def all_ids(self) -> list[str]:
        """Return all registered agent role names."""
        return list(self._agents.keys())
