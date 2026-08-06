"""Provenance-hash staleness primitives (specs/12-artifact-model-and-traceability.md §2.6).

Every child node carries ``properties.derived_from_hash`` — the SHA-256 of
the parent *content* it was authored against. The graph engine stamps it
automatically on create/update/reparent; agents never supply it. The Gap
Analyser emits ``STALE_NODE`` iff the stored stamp differs from the hash
of the parent's current content, so metadata/trace/title touches of a
parent can never cascade staleness onto its children.
"""

from __future__ import annotations

import hashlib

#: Property key holding the provenance stamp on a child node.
DERIVED_FROM_HASH = "derived_from_hash"


def provenance_hash(content: str) -> str:
    """SHA-256 hex digest of the parent content a child was authored against."""
    return hashlib.sha256(content.encode()).hexdigest()
