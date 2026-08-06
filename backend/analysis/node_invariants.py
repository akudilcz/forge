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

# ── EARS classification (Mavin et al., "EARS" RE'09) ─────────────────────────

#: Property key under which the engine stamps the classification on HLR/LLR.
EARS_PATTERN_KEY = "ears_pattern"

#: EARS pattern name → concrete rewrite template quoted in rejections.
EARS_TEMPLATES: dict[str, str] = {
    "ubiquitous": "The system shall <response>",
    "state_driven": "While <state>, the system shall <response>",
    "event_driven": "When <trigger>, the system shall <response>",
    "optional_feature": "Where <feature>, the system shall <response>",
    "unwanted_behaviour": "If <condition>, then the system shall <response>",
}

# Clause grammar, in Mavin's mandated order: an optional While-clause first,
# then any When / Where / If…then clauses, then the mandatory shall-clause
# ("the <system> shall <response>"). One leading clause names the simple
# pattern; two or more make the requirement Complex.
_WHILE_CLAUSE = re.compile(r"while\s+[^,]+,\s*", re.IGNORECASE)
_KEYWORD_CLAUSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("event_driven", re.compile(r"when\s+[^,]+,\s*", re.IGNORECASE)),
    ("optional_feature", re.compile(r"where\s+[^,]+,\s*", re.IGNORECASE)),
    ("unwanted_behaviour", re.compile(r"if\s+.+?,\s*then\s+", re.IGNORECASE)),
)
_SHALL_CLAUSE = re.compile(
    r"the\s+(?P<system>\S.*?)\s+shall\s+(?P<response>\S.*)",
    re.IGNORECASE,
)
# EARS keywords are structural: a condition trailing the shall-clause is a
# clause-order violation, not a response.
_KEYWORD_IN_RESPONSE = re.compile(r"\b(?:while|when|where|if)\b", re.IGNORECASE)


def classify_ears(text: str) -> str | None:
    """Classify requirement text into its EARS pattern, or ``None``.

    Returns one of ``ubiquitous`` / ``state_driven`` / ``event_driven`` /
    ``optional_feature`` / ``unwanted_behaviour`` / ``complex``. ``None``
    means the text matches no pattern (e.g. missing 'shall', an If-clause
    without 'then', or a condition placed after the shall-clause).
    """
    flat = " ".join(text.strip().split())
    if not flat:
        return None
    clauses: list[str] = []
    pos = 0
    lead = _WHILE_CLAUSE.match(flat, pos)
    if lead:
        clauses.append("state_driven")
        pos = lead.end()
    progressed = True
    while progressed:
        progressed = False
        for name, pattern in _KEYWORD_CLAUSES:
            m = pattern.match(flat, pos)
            if m:
                clauses.append(name)
                pos = m.end()
                progressed = True
                break
    shall = _SHALL_CLAUSE.fullmatch(flat, pos)
    if shall is None or _KEYWORD_IN_RESPONSE.search(shall.group("response")):
        return None
    if not clauses:
        return "ubiquitous"
    if len(clauses) == 1:
        return clauses[0]
    return "complex"


def _nearest_ears_template(text: str) -> str:
    """The template the mis-worded text is closest to, by leading keyword."""
    lowered = text.lstrip().lower()
    for keyword, pattern in (
        ("if", "unwanted_behaviour"),
        ("when", "event_driven"),
        ("while", "state_driven"),
        ("where", "optional_feature"),
    ):
        if lowered.startswith(keyword):
            return EARS_TEMPLATES[pattern]
    return EARS_TEMPLATES["ubiquitous"]


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
    """HLR/LLR content must match one of the five EARS patterns (or Complex)."""
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
    if classify_ears(stripped) is None:
        return (
            f"{node_type} content must match an EARS pattern (Ubiquitous, "
            f"State-driven, Event-driven, Optional-feature, "
            f"Unwanted-behaviour, or a Complex combination — conditions "
            f"BEFORE the shall-clause): got {stripped[:80]!r}. Nearest "
            f"template: '{_nearest_ears_template(stripped)}'. Rewrite the "
            f"requirement wording and retry."
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

#: Required keys of every entry in an optional per-symbol ``raises`` list.
CONTRACT_RAISES_KEYS: tuple[str, ...] = ("cls", "base", "when")

#: Optional per-symbol obligation lists of plain strings.
CONTRACT_OBLIGATION_LIST_KEYS: tuple[str, ...] = (
    "preconditions", "postconditions", "invariants",
)


def _check_api_entry_obligations(i: int, entry: Mapping[str, object]) -> str | None:
    """Shape-check optional obligation fields on one public_api entry.

    ``raises`` (list of {cls, base, when}) and the plain-string lists
    (``preconditions`` / ``postconditions`` / ``invariants``) are optional
    — most symbols state none — but malformed declarations are rejected
    with the exact key that needs fixing (specs/13 CONTRACT records).
    """
    fix = "Fix the entry per specs/13 and retry."
    if "raises" in entry:
        raises = entry["raises"]
        if not isinstance(raises, list):
            return (
                f"public_api[{i}].raises must be a list of "
                f"{{cls, base, when}} objects. {fix}"
            )
        for j, rec in enumerate(raises):
            if not isinstance(rec, dict):
                return f"public_api[{i}].raises[{j}] is not an object. {fix}"
            for key in CONTRACT_RAISES_KEYS:
                value = rec.get(key)
                if not isinstance(value, str) or not value.strip():
                    return (
                        f"public_api[{i}].raises[{j}] is missing a "
                        f"non-empty string {key!r}. {fix}"
                    )
    for key in CONTRACT_OBLIGATION_LIST_KEYS:
        if key not in entry:
            continue
        items = entry[key]
        if not isinstance(items, list):
            return f"public_api[{i}].{key} must be a list of strings. {fix}"
        for j, item in enumerate(items):
            if not isinstance(item, str) or not item.strip():
                return (
                    f"public_api[{i}].{key}[{j}] must be a non-empty "
                    f"string. {fix}"
                )
    return None


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
        obligation_msg = _check_api_entry_obligations(i, entry)
        if obligation_msg is not None:
            return obligation_msg
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


# ── PARA non-normative marking (specs/03 Phase 3 cover-or-classify) ──────────

#: Documented reason kinds for marking a PARA non-normative. The duplicate
#: kind carries the restated sibling's id as a suffix (duplicate-of-PARA-0012).
NON_NORMATIVE_REASON_KINDS: tuple[str, ...] = (
    "background/context",
    "duplicate-of-<PARA-id>",
    "example/illustration",
    "meta/document-structure",
)

_DUPLICATE_OF_PREFIX = "duplicate-of-"
_EXACT_REASON_KINDS: frozenset[str] = frozenset(
    kind for kind in NON_NORMATIVE_REASON_KINDS if kind != "duplicate-of-<PARA-id>"
)


def is_marked_non_normative(properties: Mapping[str, object]) -> bool:
    """True when the node carries an explicit ``non_normative: true`` flag."""
    return "non_normative" in properties and properties["non_normative"] is True


def _check_non_normative_rationale(rationale: object) -> str | None:
    """Rationale must name one of the documented reason kinds."""
    kinds = ", ".join(NON_NORMATIVE_REASON_KINDS)
    if not isinstance(rationale, str) or not rationale.strip():
        return (
            "non_normative: true requires a non-empty string "
            f"properties.non_normative_rationale naming one of: {kinds}. "
            "Supply the rationale and retry."
        )
    value = rationale.strip()
    if value in _EXACT_REASON_KINDS:
        return None
    if value.startswith(_DUPLICATE_OF_PREFIX) and len(value) > len(_DUPLICATE_OF_PREFIX):
        return None
    return (
        f"non_normative_rationale {value!r} is not a documented reason kind "
        f"— use one of: {kinds}. Fix the rationale and retry."
    )


def check_non_normative_marking(
    node_type: str,
    properties: Mapping[str, object],
) -> str | None:
    """PARA-only non-normative marking must carry a documented rationale.

    Phase 3 is 'cover or classify' (specs/03): a PARA that is not a
    requirement source is exempted from HLR coverage only by an explicit
    ``non_normative: true`` plus a rationale naming a documented reason
    kind — never silently.
    """
    flagged = "non_normative" in properties
    has_rationale = "non_normative_rationale" in properties
    if not flagged and not has_rationale:
        return None
    if node_type != NodeType.PARA.value:
        return (
            f"non_normative marking applies only to PARA nodes, not "
            f"{node_type}. Remove properties.non_normative / "
            f"non_normative_rationale and retry."
        )
    if flagged and not isinstance(properties["non_normative"], bool):
        return (
            "properties.non_normative must be a JSON boolean (true). "
            "Fix the value and retry."
        )
    if not is_marked_non_normative(properties):
        if has_rationale:
            return (
                "properties.non_normative_rationale is set but "
                "non_normative is not true. Set non_normative: true "
                "alongside the rationale (or remove both) and retry."
            )
        return None
    rationale = (
        properties["non_normative_rationale"] if has_rationale else None
    )
    return _check_non_normative_rationale(rationale)
