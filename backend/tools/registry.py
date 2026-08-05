"""ToolRegistry — permission matrix and instance management.

Holds live tool instances and enforces per-role and per-gap-type
permission checks. Tool classes are instantiated in lifespan.py;
this module only manages access control.
"""

from __future__ import annotations

from backend.agents.definitions import AgentRole
from backend.analysis.gaps import GapType
from backend.tools.base import ForgeTool

# All graph-write tool names, used for permission sets.
_GRAPH_WRITE_TOOLS = frozenset(
    {
        "graph_add_node",
        "graph_update_node",
        "graph_delete_node",
        "graph_reparent_node",
        "graph_update_trace",
        "graph_add_traces",
        "graph_remove_traces",
        "graph_add_edge",
        "graph_remove_edge",
        "graph_refresh_provenance",
        # Batch writer — accepts an array of ops and runs them in one tool call.
        # Exposing it alongside the single-op tools lets capable models (Opus,
        # Sonnet) emit all decisions in one turn instead of N sequential ReAct
        # round-trips. See backend/tools/multi_graph_write.py.
        "multi_graph_write",
    }
)


class ToolRegistry:
    """Manages live tool instances and enforces per-role and per-gap tool access.

    Attributes:
        _tools_instances: Pre-built live ForgeTool instances shared across roles.
        _role_permissions: Set of allowed tool names per AgentRole.
    """

    def __init__(self, tools: list[ForgeTool] | None = None):
        self._tools_instances = tools or []
        self._role_permissions: dict[AgentRole, set[str]] = {
            AgentRole.DOCUMENT_SPECIALIST: {"graph_read"} | _GRAPH_WRITE_TOOLS,
            AgentRole.REQUIREMENTS_ENGINEER: {
                "graph_read",
                "derive_requirement",
                "check_atomicity",
            }
            | _GRAPH_WRITE_TOOLS,
            AgentRole.DESIGN_ARCHITECT: {"graph_read"} | _GRAPH_WRITE_TOOLS,
            AgentRole.SOFTWARE_ENGINEER: {
                "graph_read",
                "list_dir",
                "list_files",
                "file_read",
            }
            | _GRAPH_WRITE_TOOLS,
            AgentRole.TEST_ENGINEER: {
                "graph_read",
                "list_dir",
                "list_files",
                "file_read",
            }
            | _GRAPH_WRITE_TOOLS,
            AgentRole.QUALITY_AUDITOR: {
                "graph_read",
                "check_consistency",
                "file_read",
            }
            | _GRAPH_WRITE_TOOLS,
            AgentRole.CONSOLE: {
                "graph_read",
                "list_dir",
                "list_files",
                "file_read",
                "file_write",
                "file_patch",
                "graph_search",
                "graph_grep",
                "graph_stats",
                "graph_trace",
                "code_search",
                "graph_regex_replace",
                "graph_bulk_delete",
                # Operator tools — phase lifecycle actions
                "run_phase",
                "stop_build",
                "stop_all_agents",
                "scan_gaps",
                "scan_quality",
                "qual_check",
                "purge_derived",
                "ingest_document",
            }
            | _GRAPH_WRITE_TOOLS,
        }

    # Per-gap tool whitelist — only the tools each gap actually needs.
    #
    # ``multi_graph_write`` is included on every entry that can write to the
    # graph so capable models can emit N operations in a single tool call
    # instead of N sequential ReAct turns.
    _GAP_TOOL_WHITELIST: dict[GapType, frozenset[str]] = {
        GapType.UNCHUNKED_DOCUMENT: frozenset(
            {
                "graph_read",
                "graph_add_node",
                "multi_graph_write",
            }
        ),
        GapType.UNCOVERED_PARA: frozenset(
            {
                "graph_read",
                "derive_requirement",
                "graph_add_node",
                "graph_reparent_node",
                "multi_graph_write",
            }
        ),
        GapType.UNARCHITECTED: frozenset(
            {
                "graph_read",
                "graph_add_node",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.UNMODULARISED: frozenset(
            {
                "graph_read",
                "graph_add_node",
                "graph_add_traces",
                "multi_graph_write",
            }
        ),
        GapType.UNCONTRACTED: frozenset(
            {
                "graph_read",
                "graph_add_node",
                "multi_graph_write",
            }
        ),
        GapType.UNREFINED_HLR: frozenset(
            {
                "graph_read",
                "derive_requirement",
                "graph_add_node",
                "graph_reparent_node",
                "multi_graph_write",
            }
        ),
        GapType.UNDESIGNED: frozenset(
            {
                "graph_read",
                "graph_add_node",
                "graph_add_traces",
                "multi_graph_write",
            }
        ),
        GapType.UNSUITED: frozenset(
            {
                "graph_read",
                "graph_add_node",
                "multi_graph_write",
            }
        ),
        GapType.UNTESTED_HLR: frozenset(
            {
                "graph_read",
                "graph_add_node",
                "graph_add_traces",
                "multi_graph_write",
            }
        ),
        GapType.UNTESTED_LLR: frozenset(
            {
                "graph_read",
                "graph_add_node",
                "graph_add_traces",
                "multi_graph_write",
            }
        ),
        # UNSYNCED_DESIGN / UNSYNCED_TEST: handled by workspace_sync step (no agent)
        GapType.STALE_NODE: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "graph_delete_node",
                # Deterministic 'reviewed, no change needed' closure.
                "graph_refresh_provenance",
                "multi_graph_write",
            }
        ),
        GapType.ORPHAN_NODE: frozenset(
            {
                "graph_read",
                "graph_reparent_node",
                "graph_delete_node",
                "multi_graph_write",
            }
        ),
        GapType.EMPTY_CONTENT: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.STALE_TRACE_TO: frozenset(
            {
                "graph_read",
                "graph_add_traces",
                "graph_remove_traces",
                "graph_update_trace",
                "multi_graph_write",
            }
        ),
        # The six entries below were absent while their gap types were still
        # mapped to an agent in GAP_AGENT_MAPPING. `get_tools_for_gap` returns
        # `frozenset()` for an unlisted type, so those agents were dispatched
        # with **no tools at all** — they could observe the gap and had no way
        # to act on it, so the gap could never close and the loop simply retried
        # until it gave up. `test_gap_tool_whitelist.py` now fails if an agent
        # is ever mapped without a matching tool set.
        GapType.EMPTY_TRACE: frozenset(
            {
                "graph_read",
                "graph_trace",
                "graph_add_traces",
                "graph_update_trace",
                "multi_graph_write",
            }
        ),
        GapType.CIRCULAR_TRACE: frozenset(
            {
                "graph_read",
                "graph_trace",
                "graph_remove_traces",
                "graph_update_trace",
                "multi_graph_write",
            }
        ),
        GapType.STALE_ARCHITECTURE: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "graph_add_node",
                "multi_graph_write",
            }
        ),
        GapType.STALE_SUITE: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "graph_add_node",
                "multi_graph_write",
            }
        ),
        # The two workspace-facing gaps additionally need file tools: the
        # remedy is to (re)write the source file the DESIGN points at, not just
        # to edit the graph.
        GapType.MISSING_CODE: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "file_read",
                "file_write",
                "list_files",
                "multi_graph_write",
            }
        ),
        GapType.STALE_CODE: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "file_read",
                "file_write",
                "file_patch",
                "code_search",
                "list_files",
                "multi_graph_write",
            }
        ),
        GapType.INCONSISTENT_CONTENT: frozenset(
            {
                "graph_read",
                "check_consistency",
                "graph_update_node",
                "graph_delete_node",
                "multi_graph_write",
            }
        ),
        GapType.MALFORMED_REQUIREMENT: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.NON_ATOMIC_REQUIREMENT: frozenset(
            {
                "graph_read",
                "check_atomicity",
                "graph_update_node",
                "graph_add_node",
                "multi_graph_write",
            }
        ),
        GapType.NON_EARS_REQUIREMENT: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.UNTITLED_NODE: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.TITLE_COLLIDES_WITH_PARENT: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.SIBLING_TITLE_DUPLICATE: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.STALE_TITLE: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.VAGUE_TITLE: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.DUPLICATE_NODE: frozenset(
            {
                "graph_read",
                "graph_delete_node",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.INADEQUATE_CONTENT: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.VAGUE_REQUIREMENT: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.UNTESTABLE_REQUIREMENT: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.CONTRADICTORY_REQUIREMENTS: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "graph_delete_node",
                "multi_graph_write",
            }
        ),
        GapType.INCOMPLETE_DECOMPOSITION: frozenset(
            {
                "graph_read",
                "graph_add_node",
                "multi_graph_write",
            }
        ),
        GapType.CONTRACT_VIOLATION: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
        GapType.CROSS_MODULE_COUPLING: frozenset(
            {
                "graph_read",
                "graph_update_node",
                "multi_graph_write",
            }
        ),
    }

    def add_tools(self, tools: list[ForgeTool]) -> None:
        """Register additional live tool instances (e.g. operator tools added post-init)."""
        self._tools_instances.extend(tools)

    def get_tools_for_role(self, role: AgentRole) -> list[ForgeTool]:
        """Return the subset of live tool instances that the given role is permitted to use."""
        allowed_names = self._role_permissions.get(role, set())
        instance_map: dict[str, ForgeTool] = {t.name: t for t in self._tools_instances}
        return [instance_map[name] for name in allowed_names if name in instance_map]

    def get_tools_for_gap(self, gap_type: GapType) -> list[ForgeTool]:
        """Return the subset of live tool instances whitelisted for resolving the given gap type."""
        allowed_names = self._GAP_TOOL_WHITELIST.get(gap_type, frozenset())
        instance_map: dict[str, ForgeTool] = {t.name: t for t in self._tools_instances}
        return [instance_map[name] for name in allowed_names if name in instance_map]

    def update_llm_config(self, llm_config: object) -> None:
        """Push new LLM config to all analysis tool instances that cache it."""
        for tool in self._tools_instances:
            if hasattr(tool, "_llm_config"):
                object.__setattr__(tool, "_llm_config", llm_config)
