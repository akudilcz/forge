"""Shared authoring-invariant checks (specs/12-artifact-model-and-traceability.md §3.6).

Single source of truth for the deterministic invariants that graph-write
tools enforce at write time (rejecting the write with an actionable tool
ERROR) and that the Gap Analyser re-checks as a backstop for graphs
authored before enforcement existed. Both layers call these pure
functions, so the two can never diverge.

Every check returns ``None`` when the invariant holds, or a message that
tells the agent exactly how to fix the violation in the same turn.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping

from backend.graph.models import GraphNode, NodeType

# ── Constants (mirrored by the Gap Analyser) ─────────────────────────────────

#: Maximum words allowed in an authored title.
TITLE_MAX_WORDS = 7

#: Node types that do not require an authored title (and are excluded from
#: sibling-title comparison).
TITLE_EXEMPT_TYPES: frozenset[str] = frozenset(
    {
        NodeType.PROJECT.value,
        NodeType.DOCUMENT.value,
        NodeType.RESULT.value,
        NodeType.RECORD.value,
    }
)

#: Requirement node types whose wording is constrained.
REQUIREMENT_TYPES: frozenset[str] = frozenset(
    {NodeType.HLR.value, NodeType.LLR.value}
)

#: Minimum content length (chars) for non-container, non-requirement nodes.
MIN_CONTENT_LENGTH = 50

#: Node types the minimum-content-length rule applies to.
MIN_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        NodeType.ARCHITECTURE.value,
        NodeType.MODULE.value,
        NodeType.CONTRACT.value,
        NodeType.DESIGN.value,
        NodeType.SUITE.value,
        NodeType.CASE_HLR.value,
        NodeType.CASE_LLR.value,
    }
)

#: CASE node type → the only node type its trace_to may reference.
CASE_TRACE_TARGET: dict[str, str] = {
    NodeType.CASE_HLR.value: NodeType.HLR.value,
    NodeType.CASE_LLR.value: NodeType.LLR.value,
}

_PARA_PLACEHOLDER = re.compile(r"\bPARA-\d{4}\b")
_REQUIRED_PREFIX = "the system shall "


# ── Normalisation shared by write tools and analyser ─────────────────────────


def normalise_title(title: str) -> str:
    """Canonical form used for sibling-title comparison."""
    return title.strip().lower()


def normalise_content(content: str) -> str:
    """Canonical form used for sibling-content comparison."""
    return content.strip().lower()


# ── Per-node checks ──────────────────────────────────────────────────────────


def check_title(node_type: str, title: str) -> str | None:
    """Authored nodes need a short human-readable title (≤7 words)."""
    if node_type in TITLE_EXEMPT_TYPES:
        return None
    stripped = title.strip()
    if not stripped:
        return (
            f"{node_type} nodes require a short human-readable title "
            f"(3-5 words). Provide title='...' and retry."
        )
    word_count = len(stripped.split())
    if word_count > TITLE_MAX_WORDS:
        return (
            f"title is too long ({word_count} words): {stripped!r}. "
            f"Shorten it to 3-5 words and retry."
        )
    return None


def check_requirement_wording(node_type: str, content: str) -> str | None:
    """HLR/LLR content must be a real 'The system shall …' statement."""
    if node_type not in REQUIREMENT_TYPES:
        return None
    stripped = content.strip()
    if not stripped:
        return None  # EMPTY_CONTENT is a separate concern
    placeholder = _PARA_PLACEHOLDER.search(stripped)
    if placeholder:
        return (
            f"{node_type} content references a raw PARA node ID "
            f"({placeholder.group(0)}): {stripped[:80]!r}. Replace the "
            f"placeholder with the actual requirement text and retry."
        )
    if not stripped.lower().startswith(_REQUIRED_PREFIX):
        return (
            f"{node_type} content must start with 'The system shall ': "
            f"got {stripped[:80]!r}. Rewrite the requirement wording and retry."
        )
    return None


def check_min_content_length(node_type: str, content: str) -> str | None:
    """Non-empty content on substantive node types must be actionable."""
    if node_type not in MIN_CONTENT_TYPES:
        return None
    stripped = content.strip()
    if not stripped:
        return None  # EMPTY_CONTENT is a separate concern
    if len(stripped) < MIN_CONTENT_LENGTH:
        return (
            f"{node_type} content is only {len(stripped)} chars — minimum "
            f"{MIN_CONTENT_LENGTH} required. Provide substantive, actionable "
            f"content and retry."
        )
    return None


# ── CONTRACT public_api shape (specs/13 Structured Public API Surface) ──────

#: Allowed `kind` values for a CONTRACT public_api entry.
CONTRACT_API_KINDS: frozenset[str] = frozenset({"function", "class", "method"})

#: Required keys of every public_api entry.
CONTRACT_API_KEYS: tuple[str, ...] = ("module", "symbol", "kind", "signature")


def check_contract_public_api(
    node_type: str, properties: Mapping[str, object],
) -> str | None:
    """CONTRACT nodes must declare a valid, non-empty ``public_api``.

    Live trace (merge_sort e2e, oracle 1/24): prose-only contracts let
    codegen ship a workspace exposing none of the whitepaper's required
    symbols. The structured surface makes the API machine-checkable by
    the phase-12 API-surface gate (specs/03).
    """
    if node_type != "CONTRACT":
        return None
    fix = "Supply properties.public_api per specs/13 and retry."
    api = properties.get("public_api")
    if not isinstance(api, list) or not api:
        return (
            "CONTRACT must declare a non-empty properties.public_api list "
            f"of {{module, symbol, kind, signature}} entries. {fix}"
        )
    for i, entry in enumerate(api):
        if not isinstance(entry, dict):
            return f"public_api[{i}] is not an object. {fix}"
        for key in CONTRACT_API_KEYS:
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                return (
                    f"public_api[{i}] is missing a non-empty string "
                    f"{key!r}. {fix}"
                )
        if entry["kind"] not in CONTRACT_API_KINDS:
            return (
                f"public_api[{i}] has kind {entry['kind']!r} — must be one "
                f"of {sorted(CONTRACT_API_KINDS)}. {fix}"
            )
    return None


def check_contract_prohibited(
    node_type: str, properties: Mapping[str, object],
) -> str | None:
    """Shape-check ``prohibited_constructs`` when a CONTRACT declares it.

    Optional, unlike ``public_api``: most whitepapers state no
    implementation prohibitions. When present, each entry must carry a
    non-empty ``construct`` (dotted callable/module name) and
    ``rationale`` so the phase-12 gate can quote WHY a hit is banned.
    (Live trace: expression_evaluator's generated code delegated to
    ``compile()`` despite the whitepaper's explicit §12 ban.)
    """
    if node_type != "CONTRACT":
        return None
    if "prohibited_constructs" not in properties:
        return None
    fix = "Fix properties.prohibited_constructs per specs/13 and retry."
    banned = properties["prohibited_constructs"]
    if not isinstance(banned, list):
        return (
            "prohibited_constructs must be a list of "
            f"{{construct, rationale}} objects. {fix}"
        )
    for i, entry in enumerate(banned):
        if not isinstance(entry, dict):
            return f"prohibited_constructs[{i}] is not an object. {fix}"
        for key in ("construct", "rationale"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                return (
                    f"prohibited_constructs[{i}] is missing a non-empty "
                    f"string {key!r}. {fix}"
                )
    return None


def check_title_distinct_from_parent(
    node_type: str,
    title: str,
    parent: GraphNode | None,
) -> str | None:
    """A child's title must not duplicate its parent's title.

    An identical title (case/whitespace-insensitive) means the child has
    not narrowed scope — the drift pattern the analyser reports as
    ``TITLE_COLLIDES_WITH_PARENT``. Shared by the write tools and the
    analyser so the two can never diverge.
    """
    if node_type in TITLE_EXEMPT_TYPES or parent is None:
        return None
    child_key = normalise_title(title)
    parent_key = normalise_title(parent.title or "")
    if not child_key or not parent_key or child_key != parent_key:
        return None
    return (
        f"title {title.strip()!r} is identical to parent {parent.node_id}'s "
        f"({parent.node_type}) title. Choose a title reflecting this node's "
        f"narrower scope and retry."
    )


# ── Sibling uniqueness checks ────────────────────────────────────────────────


def check_sibling_title_unique(
    node_type: str,
    title: str,
    node_id: str,
    siblings: Iterable[GraphNode],
) -> str | None:
    """Titles among siblings under one parent must be distinct."""
    if node_type in TITLE_EXEMPT_TYPES:
        return None
    key = normalise_title(title)
    if not key:
        return None  # absence is check_title's concern
    for sib in siblings:
        if sib.node_id == node_id or sib.node_type in TITLE_EXEMPT_TYPES:
            continue
        if normalise_title(sib.title) == key:
            return (
                f"title {title.strip()!r} duplicates sibling {sib.node_id}'s "
                f"title under the same parent. Sibling titles must be "
                f"distinct — choose a more specific title and retry."
            )
    return None


def check_sibling_content_unique(
    node_type: str,
    content: str,
    node_id: str,
    siblings: Iterable[GraphNode],
) -> str | None:
    """Same-type siblings must not carry identical (normalised) content.

    PARA nodes are exempt (specs/12 §3.5/§3.6): they mirror the source
    document, whose identity is position + title — a whitepaper may
    legitimately repeat a sentence in two sections, and heading PARAs are
    empty by design.
    """
    if node_type == NodeType.PARA.value:
        return None
    key = normalise_content(content)
    if not key:
        return None  # EMPTY_CONTENT is a separate concern
    for sib in siblings:
        if sib.node_id == node_id or sib.node_type != node_type:
            continue
        if normalise_content(sib.content) == key:
            return (
                f"content is identical to sibling {sib.node_id}'s (after "
                f"whitespace/case normalisation). Author distinct content, "
                f"or update the existing node instead of duplicating it."
            )
    return None


# ── CASE trace_to membership ─────────────────────────────────────────────────


def check_case_trace_targets(
    node_type: str,
    trace_to: list[str],
    resolve: Callable[[str], GraphNode | None],
) -> str | None:
    """CASE_HLR must trace only to HLRs; CASE_LLR only to LLRs.

    Unresolvable references are not a *type* violation (the analyser's
    STALE_TRACE_TO check owns dangling refs), so only refs that resolve
    are type-checked — matching the analyser's semantics exactly.
    """
    if node_type not in CASE_TRACE_TARGET:
        return None
    expected = CASE_TRACE_TARGET[node_type]
    if not trace_to:
        return (
            f"{node_type} must trace to at least one {expected} node — "
            f'supply trace_to=\'["{expected}-nnnn"]\' with the requirement '
            f"ID(s) this case verifies."
        )
    wrong = [
        ref
        for ref in trace_to
        if (target := resolve(ref)) is not None and target.node_type != expected
    ]
    if wrong:
        return (
            f"{node_type} trace_to contains non-{expected} node(s): "
            f"{', '.join(wrong)}. Remove them — trace_to must contain only "
            f"{expected} node IDs."
        )
    return None
