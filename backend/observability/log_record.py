"""Canonical structured log record for FORGE observability.

Every `emit` call in the pipeline becomes one LogRecord. Fields are chosen
so that the most common debug queries (all events for gap X, all LLM
calls in phase 7 cycle 2, etc.) can be answered with an indexed WHERE
rather than full-text grep.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class LogCategory(str, Enum):
    """Fixed set of log categories. Using an enum (rather than free-form
    strings) makes category filters reliable — a typo surfaces as an
    import/enum error, not a silently-empty query result.

    Values are short (5-char) identifiers chosen to line up visually in
    human-readable log tails. No trailing whitespace — padding is the
    formatter's job, not the record's.
    """

    SYS = "SYS"        # Startup, shutdown, retention, broadcaster lifecycle
    LOOP = "LOOP"      # Build loop start/stop/error/cancelled
    PHASE = "PHASE"    # Per-phase start/complete/no-gaps
    PIPE = "PIPE"      # Phase pipeline step routing
    FLOW = "FLOW"      # ForgeFlow orchestration
    BATCH = "BATCH"    # Batch step prompts + outcomes (phases 3/5/7/8)

    GAP = "GAP"        # Gap dispatch, resolved, no-progress
    GAPF = "GAPF"      # Gap finder (code_gen gap scan)
    AGENT = "AGENT"    # Agent dispatch / done / error
    POOL = "POOL"      # Agent pool initialise / registration
    CREW = "CREW"      # Crew step events — prompts, tool calls, finish
    LLM = "LLM"        # LLM call / response / error / prompt / content
    TOOL = "TOOL"      # Tool invocation (covers all ForgeTool subclasses)
    THROT = "THROT"    # Global LLM throttle waits

    DECIDE = "DECIDE"  # Explicit "why" decisions (fast-path, dedup, merge)

    GRAPH = "GRAPH"    # Graph mutations (add/update/delete/reparent)
    STORE = "STORE"    # baseline record events
    SYNC = "SYNC"      # workspace_sync file creates/refreshes/missing

    QUAL = "QUAL"      # Quality semantic-dedup orchestration
    SEMA = "SEMA"      # Semantic-dedup per-node judging
    RQUAL = "RQUAL"    # Requirement atomicity / EARS / vagueness check
    TQUAL = "TQUAL"    # Title quality: title↔content match + specificity check
    XQUAL = "XQUAL"    # Combined batched quality check (req + title axes in one call)
    CONS = "CONS"      # Design consolidation merges
    CONSIST = "CONSIST"  # Contradictory / consistency checks
    DECOMP = "DECOMP"  # Incomplete decomposition check
    COV = "COV"        # Coverage calculations
    CTRC = "CTRC"      # CASE trace coverage judge
    CTX = "CTX"        # Context budget drops

    CGEN = "CGEN"      # Phase 12 code generation
    SCAN = "SCAN"      # Workspace scanner (bazel / pytest runs)
    BZEL = "BZEL"      # Bazel workspace init / build
    EVAL = "EVAL"      # Evaluate-progress tool
    AUDIT = "AUDIT"    # Phase auditor verdicts
    QUEUE = "QUEUE"    # Work-queue add/remove/promote
    DLVR = "DLVR"      # Deliverables pack build

    USER = "USER"      # User actions from frontend
    AUTH = "AUTH"      # Auth login / check / failure
    HTTP = "HTTP"      # HTTP request/response middleware
    WS = "WS"          # WebSocket connect / disconnect

    AGNT = "AGNT"      # Agents infra (factory, llm_callback)
    TEST = "TEST"      # Test runner artefacts
    CAT = "CAT"        # Legacy / test-only
    DASH = "DASH"      # Dashboard stats
    CONFORM = "CONFORM"  # Architecture conformance (consistency_check)


_CATEGORY_BY_CANONICAL: dict[str, LogCategory] = {
    c.value: c for c in LogCategory
}


def normalise_category(cat: str | LogCategory) -> str:
    """Return the canonical string form of a category.

    Accepts :class:`LogCategory` members, or strings with optional
    trailing whitespace (a legacy padding artefact). Unknown strings
    are returned upper-cased and stripped with no enum validation —
    callers that want validation should use :func:`validate_category`.
    """
    if isinstance(cat, LogCategory):
        return cat.value
    return cat.strip().upper() if isinstance(cat, str) else str(cat)


def validate_category(cat: str | LogCategory) -> LogCategory:
    """Return the :class:`LogCategory` member matching ``cat``.

    Raises :class:`ValueError` when ``cat`` does not match any known
    category — useful in tests and strict-mode deployments to catch
    typos at the emit site.
    """
    normalised = normalise_category(cat)
    if normalised in _CATEGORY_BY_CANONICAL:
        return _CATEGORY_BY_CANONICAL[normalised]
    raise ValueError(
        f"Unknown LogCategory {cat!r} — known: "
        f"{sorted(_CATEGORY_BY_CANONICAL)}"
    )


@dataclass(frozen=True, slots=True)
class LogRecord:
    """A single structured log entry.

    ``ts_ms`` is epoch milliseconds. ``level`` is one of
    ``DEBUG|INFO|WARN|ERROR``. ``category`` is a short stable tag used as
    a coarse filter in the query UI (PHASE / CREW / AGENT / LLM / TOOL /
    GRAPH / SYNC / SEMA / QUAL / CTRC / PIPE / FLOW / BATCH / CGEN /
    DECIDE / SYS).
    """

    ts_ms: int
    level: str
    category: str
    msg: str
    detail: str | None = None

    # Correlation identifiers
    run_id: str | None = None
    phase: int | None = None
    cycle: int | None = None
    gap_type: str | None = None
    gap_id: str | None = None
    node_id: str | None = None
    agent_id: str | None = None
    call_id: str | None = None

    # LLM-specific
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tool_call_count: int | None = None

    # Tool-specific
    tool_name: str | None = None

    # Metrics / errors
    duration_ms: int | None = None
    error_type: str | None = None

    # Freeform extras — anything that doesn't warrant its own column.
    # Serialised as JSON at the sink layer.
    extras: dict[str, Any] | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict view (safe for JSON serialisation)."""
        return asdict(self)


# The canonical column order used by SQLiteLogSink and query.py. Keep in
# sync with log_schema.CREATE_LOGS_TABLE.
COLUMNS: tuple[str, ...] = (
    "ts_ms",
    "level",
    "category",
    "msg",
    "detail",
    "run_id",
    "phase",
    "cycle",
    "gap_type",
    "gap_id",
    "node_id",
    "agent_id",
    "call_id",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "tool_call_count",
    "tool_name",
    "duration_ms",
    "error_type",
    "extras",
)

# Meta-keys the caller can pass via `emit(**meta)` that we promote to
# first-class columns. Anything else goes into extras.
PROMOTED_META_KEYS: frozenset[str] = frozenset(
    {
        "run_id", "phase", "cycle", "gap_type", "gap_id", "node_id",
        "agent_id", "call_id",
        "model", "prompt_tokens", "completion_tokens", "tool_call_count",
        "tool_name", "duration_ms", "error_type",
    }
)
