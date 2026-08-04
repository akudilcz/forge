"""Graph node and edge Pydantic models for the FORGE Project Graph Engine.

The graph engine is described in docs/DESIGN_GRAPH.md. This module
defines the data-model layer only: enumerations, Pydantic models, and
lightweight value objects. No business logic lives here.

Key design decisions (see DESIGN_GRAPH.md):
  * 16 node types cover the complete software lifecycle across 10 layers.
  * 7 edge types express every traceability relationship.
  * Structural containment is expressed via parent_id on the child.
  * The lifecycle follows DRAFT → ACTIVE → STALE / LOCKED / SUPERSEDED.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ── Lifecycle ──────────────────────────────────────────────────────────────────


class LifecycleState(str, Enum):
    """Valid lifecycle states for any node in the graph.

    State meanings:
      DRAFT  – being authored; not yet ready for downstream use.
      ACTIVE – approved and authoritative.
      STALE  – a dependency changed; needs review before downstream use.
      LOCKED – frozen in a baseline; cannot change without a CR.
      DEPRECATED - superseded by a newer version.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    STALE = "stale"
    LOCKED = "locked"
    DEPRECATED = "deprecated"


# ── Node types ─────────────────────────────────────────────────────────────────


class NodeType(str, Enum):
    """Node types covering the complete software lifecycle.

    V4 workspace-synced layers:
      0 – Project root          (PROJECT)
      1 – Document layer        (DOCUMENT, PARA)
      2 – Requirement layer     (HLR, LLR)
      3 – Architecture layer    (ARCHITECTURE)
      4 – Module/Contract layer (MODULE, CONTRACT)
      5 – Design layer          (DESIGN, CODE)
      6 – Test layer            (SUITE, CASE_HLR, CASE_LLR, TEST)
      7 – Evidence layer        (RESULT — reserved for future CI/CD)
      8 – Record layer          (RECORD)
    """

    # Layer 0
    PROJECT = "PROJECT"
    # Layer 1 — Document layer
    DOCUMENT = "DOCUMENT"
    PARA = "PARA"
    # Layer 2 — Requirement layer (v3)
    HLR = "HLR"
    LLR = "LLR"
    # Layer 3 — Architecture layer (v3)
    ARCHITECTURE = "ARCHITECTURE"
    # Layer 4 — Module/Contract layer
    MODULE = "MODULE"
    CONTRACT = "CONTRACT"
    # Layer 5 — Design layer (v4)
    DESIGN = "DESIGN"   # class API spec (name, signatures, responsibilities)
    CODE = "CODE"       # workspace source file reference
    # Layer 6 — Test layer
    SUITE = "SUITE"
    CASE_HLR = "CASE_HLR"  # HLR-level test case (traces to HLR)
    CASE_LLR = "CASE_LLR"  # LLR-level test case (traces to LLR)
    TEST = "TEST"           # workspace test file reference
    # Layer 7 — Evidence layer (reserved for future CI/CD)
    RESULT = "RESULT"
    # Layer 8 — Record layer
    RECORD = "RECORD"


# Map each node type to its immutable layer number.
NODE_TYPE_LAYER: dict[NodeType, int] = {
    # V4 types
    NodeType.PROJECT: 0,
    NodeType.DOCUMENT: 1,
    NodeType.PARA: 1,
    NodeType.HLR: 2,
    NodeType.LLR: 2,
    NodeType.ARCHITECTURE: 3,
    NodeType.MODULE: 4,
    NodeType.CONTRACT: 4,
    NodeType.DESIGN: 5,
    NodeType.CODE: 5,
    NodeType.SUITE: 6,
    NodeType.CASE_HLR: 6,
    NodeType.CASE_LLR: 6,
    NodeType.TEST: 6,
    NodeType.RESULT: 7,
    NodeType.RECORD: 8,
}


# ── Edge types ─────────────────────────────────────────────────────────────────


class EdgeType(str, Enum):
    """Seven edge types that express every meaningful traceability relationship.

    All traceability edges point upward (more concrete → more abstract).
    Impact propagation travels in the reverse (downward) direction.
    """

    # Derivation: more concrete → more abstract
    DERIVES_FROM = "DERIVES_FROM"
    # Implementation: code/design → requirement/design
    IMPLEMENTS = "IMPLEMENTS"
    # Realisation: contract → interface
    REALISES = "REALISES"
    # Conformance: code → contract
    CONFORMS_TO = "CONFORMS_TO"
    # Verification: test case → requirement
    VERIFIES = "VERIFIES"
    # Structural coverage: test case → code function
    EXERCISES = "EXERCISES"
    # Assurance records: record → any node
    APPLIES_TO = "APPLIES_TO"


# ── Core models ────────────────────────────────────────────────────────────────


class GraphNode(BaseModel):
    """A single node in the project graph.

    Attributes:
        node_id: Permanent, human-readable identity.  Never reused.
        node_type: One of the 18 defined NodeType values.
        layer: Integer 0-9 derived from node_type.  Immutable.
        title: Short human-readable name for UI and logs (3-5 words).
        content: Primary text payload.  Hashed to detect changes.
        content_hash: SHA-256 of ``content``.  Recomputed on every mutation.
        version: Monotonically incrementing integer.  Starts at 1.
        parent_id: Structural parent.  Null only for the PROJECT node.
        trace_to: Cross-branch traceability references (e.g. CASE→HLR, MODULE→HLR).
        lifecycle: Current lifecycle state.
        created_by: Agent ID or "engineer".
        created_at: Timestamp of creation.
        updated_at: Timestamp of most recent mutation.
        properties: JSON bag of type-specific properties.
    """

    node_id: str
    node_type: str
    layer: int = 0
    title: str = ""
    content: str = ""
    content_hash: str = ""
    version: int = 1
    parent_id: str | None = None
    trace_to: list[str] = Field(default_factory=list)
    para_type: str = ""
    lifecycle: LifecycleState = LifecycleState.DRAFT
    created_by: str = "system"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    properties: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Compute content_hash and layer on construction if not supplied."""
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(self.content.encode()).hexdigest()
        # Derive layer from node_type if not explicitly set
        if self.layer == 0 and self.node_type:
            try:
                nt = NodeType(self.node_type)
                self.layer = NODE_TYPE_LAYER.get(nt, 0)
            except ValueError:
                pass
        # Restore para_type from properties (where engine persists it as "sub_type")
        if not self.para_type and self.properties:
            self.para_type = self.properties.get("sub_type", "")

    def __hash__(self) -> int:
        return hash(self.node_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GraphNode):
            return NotImplemented
        return self.node_id == other.node_id


class GraphEdge(BaseModel):
    """A directed traceability edge between two nodes.

    Edges store provenance (created_by, rationale) and a confidence score.

    Attributes:
        edge_id: Stable UUID for this edge record.
        edge_type: One of the 7 EdgeType values.
        source_id: The more-concrete node (edge points upward toward abstract).
        target_id: The more-abstract node.
        rationale: Why this edge was asserted.
        confidence: 0.0–1.0.  Parser-inferred edges use 0.85.
        created_by: Agent ID or "engineer".
        created_at: Timestamp of creation.
    """

    edge_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    edge_type: str
    source_id: str
    target_id: str
    rationale: str | None = None
    confidence: float = 1.0
    created_by: str = "system"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Graph query result models ───────────────────────────────────────────────────


class ImpactSet(BaseModel):
    """Result of an impact propagation query: nodes that become stale when root_node_id changes."""

    root_node_id: str
    stale_nodes: list[str] = Field(default_factory=list)
    stale_count: int = 0


class TraceabilityChain(BaseModel):
    """Upward ancestry chain for a node, from immediate parent to PROJECT root."""

    node_id: str
    ancestors: list[dict[str, Any]] = Field(default_factory=list)


class TraceabilityGaps(BaseModel):
    """Summary of traceability gaps: unimplemented requirements, uncovered tests, and untested code."""

    unimplemented_requirements: list[str] = Field(default_factory=list)
    uncovered_requirements: list[str] = Field(default_factory=list)
    untested_code: list[str] = Field(default_factory=list)
