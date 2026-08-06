"""Derived-requirement and verification-method markings on HLR/LLR nodes.

U4 (specs/13 "Derived requirements and verification methods"): what
derivation decides is persisted, never discarded. A requirement with no
direct parent-text provenance — one that emerges from design necessity —
carries ``properties.derived: true`` plus a mandatory
``derived_rationale`` (the DO-178C derived-requirement concept), and every
requirement may state how it will be verified via
``properties.verification_method`` (one of the four standard methods,
IEEE 29148).

Pure shape checks, shared by the graph-write tools (rejection at write
time) and the Gap Analyser (backstop for legacy graphs) — same
two-layer pattern as ``node_invariants``, so enforcement and detection
can never diverge. Every check returns ``None`` when the invariant
holds, or an actionable fix-it message.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.analysis.node_invariants import REQUIREMENT_TYPES

#: The four standard verification methods (IEEE 29148 / DO-178C).
VERIFICATION_METHODS: tuple[str, ...] = (
    "test", "analysis", "inspection", "demonstration",
)

#: Property keys persisted on HLR/LLR nodes — the exact keys the
#: ``derive_requirement`` tool emits, so tool output round-trips into
#: node properties unchanged.
DERIVED_KEY = "derived"
DERIVED_RATIONALE_KEY = "derived_rationale"
VERIFICATION_METHOD_KEY = "verification_method"


def is_marked_derived(properties: Mapping[str, object]) -> bool:
    """True when the node carries an explicit ``derived: true`` flag."""
    return DERIVED_KEY in properties and properties[DERIVED_KEY] is True


def check_derived_marking(
    node_type: str,
    properties: Mapping[str, object],
) -> str | None:
    """Derived marking must be HLR/LLR-only, boolean, and justified.

    ``derived: true`` requires a non-empty ``derived_rationale`` — the
    exemption it buys (see the analyser's staleness check) is never
    granted on an unexplained flag.
    """
    flagged = DERIVED_KEY in properties
    has_rationale = DERIVED_RATIONALE_KEY in properties
    if not flagged and not has_rationale:
        return None
    if node_type not in REQUIREMENT_TYPES:
        return (
            f"derived marking applies only to HLR/LLR requirement nodes, "
            f"not {node_type}. Remove properties.derived / "
            f"derived_rationale and retry."
        )
    if flagged and not isinstance(properties[DERIVED_KEY], bool):
        return (
            "properties.derived must be a JSON boolean (true). "
            "Fix the value and retry."
        )
    if not is_marked_derived(properties):
        if has_rationale:
            return (
                "properties.derived_rationale is set but derived is not "
                "true. Set derived: true alongside the rationale (or "
                "remove both) and retry."
            )
        return None
    rationale = properties[DERIVED_RATIONALE_KEY] if has_rationale else None
    if not isinstance(rationale, str) or not rationale.strip():
        return (
            "derived: true requires a non-empty string "
            "properties.derived_rationale explaining the design necessity "
            "this requirement emerged from (DO-178C derived requirement). "
            "Supply the rationale and retry."
        )
    return None


def check_verification_method(
    node_type: str,
    properties: Mapping[str, object],
) -> str | None:
    """``verification_method``, when present, names one of the four methods.

    Optional — legacy graphs carry none — but a stated method must be
    ``test``, ``analysis``, ``inspection``, or ``demonstration``
    (case-insensitive), and only requirement nodes may state one.
    """
    if VERIFICATION_METHOD_KEY not in properties:
        return None
    methods = ", ".join(VERIFICATION_METHODS)
    if node_type not in REQUIREMENT_TYPES:
        return (
            f"verification_method applies only to HLR/LLR requirement "
            f"nodes, not {node_type}. Remove "
            f"properties.verification_method and retry."
        )
    value = properties[VERIFICATION_METHOD_KEY]
    if not isinstance(value, str) or value.strip().lower() not in VERIFICATION_METHODS:
        return (
            f"verification_method {value!r} is not one of the four "
            f"standard methods: {methods}. Fix the value and retry."
        )
    return None
