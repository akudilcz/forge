"""Batched micro-repair prompts — one structured call fixes N small gaps.

Title-family gaps (vague/stale/sibling-duplicate titles) and wording-family
gaps (non-shall requirement wording) each need one small per-node edit, yet
the per-gap dispatch path re-sends the full system prompt and context for
every node. This module builds the single-call prompt (same
single-call-judges-all pattern as ``backend/quality/combined_check.py``)
and parses the per-node fixes back out. See design/01 §7.4.

The parser never invents a fix: a node line the model dropped, garbled, or
left empty is reported as *missing* so its gap stays open for the normal
per-gap dispatch path (the loud fallback lives in
``backend/quality/micro_repair.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.analysis.gaps import GapType

#: Gap types repaired by replacing the node's TITLE.
TITLE_FAMILY: frozenset[GapType] = frozenset(
    {
        GapType.VAGUE_TITLE,
        GapType.STALE_TITLE,
        GapType.SIBLING_TITLE_DUPLICATE,
    }
)

#: Gap types repaired by rewriting the node's CONTENT wording.
WORDING_FAMILY: frozenset[GapType] = frozenset(
    {
        GapType.MALFORMED_REQUIREMENT,
        GapType.NON_EARS_REQUIREMENT,
    }
)

#: All gap types the batched micro-repair path may handle.
BATCHABLE_REPAIR_TYPES: frozenset[GapType] = TITLE_FAMILY | WORDING_FAMILY

#: Minimum same-family gaps in a cycle before batching beats per-gap dispatch.
MIN_BATCH_SIZE = 3

#: Per-entry content cap (chars). Requirements are one-two sentences; titles
#: need only enough content to summarise. A hard per-entry cap keeps the
#: single batched call bounded regardless of batch size.
_CONTENT_CAP_CHARS = 1000


@dataclass(frozen=True)
class RepairEntry:
    """Everything the batch prompt needs about one node under repair."""

    node_id: str
    node_type: str
    title: str
    content: str
    violation: str
    sibling_titles: tuple[str, ...]


TITLE_REPAIR_SYSTEM_PROMPT = """\
You are a title editor for a requirements+design graph. Each node below
violates a title invariant (stated per node). For EACH node, write ONE
replacement title:

- a concrete 3-5 word noun phrase naming ONLY that node's current content
  scope (good: 'Return Empty List', 'Reject Boolean Values';
  bad: 'Handle Cases', 'Misc Rules', 'General Behavior');
- distinct from every listed sibling title (case-insensitive).

OUTPUT FORMAT — one line per node, in the INPUT order, exactly:

  <NODE_ID>: <replacement title>

Every input node MUST receive a line. Do NOT emit any other text —
no preamble, no summary, no quotes around the title.
"""

WORDING_REPAIR_SYSTEM_PROMPT = """\
You are a requirement-wording editor. Each requirement node below violates
the mandatory wording format (stated per node). For EACH node, rewrite its
content as a SINGLE atomic sentence that:

- starts with 'The system shall ' (exactly one 'shall');
- places any condition (when/if/while/where) AFTER the shall-clause;
- preserves the original requirement intent, testable and unambiguous;
- contains no bullet points and no sub-clauses.

OUTPUT FORMAT — one line per node, in the INPUT order, exactly:

  <NODE_ID>: <rewritten requirement sentence>

Every input node MUST receive a line. Do NOT emit any other text —
no preamble, no summary.
"""


def _capped(content: str) -> str:
    flat = content.replace("\n", " ").strip()
    if len(flat) <= _CONTENT_CAP_CHARS:
        return flat
    return flat[:_CONTENT_CAP_CHARS] + " …[capped]"


def build_title_repair_payload(entries: list[RepairEntry]) -> str:
    """Human-message payload for a title-family repair batch."""
    blocks: list[str] = []
    for e in entries:
        siblings = " | ".join(repr(t) for t in e.sibling_titles) or "(none)"
        blocks.append(
            f"[{e.node_id}] type={e.node_type}\n"
            f"  current_title: {e.title!r}\n"
            f"  violation: {e.violation}\n"
            f"  sibling_titles (must stay distinct from): {siblings}\n"
            f"  content: {_capped(e.content)!r}"
        )
    return "\n".join(blocks)


def build_wording_repair_payload(entries: list[RepairEntry]) -> str:
    """Human-message payload for a wording-family repair batch."""
    blocks: list[str] = []
    for e in entries:
        blocks.append(
            f"[{e.node_id}] type={e.node_type} title={e.title!r}\n"
            f"  violation: {e.violation}\n"
            f"  content: {_capped(e.content)!r}"
        )
    return "\n".join(blocks)


def parse_repair_response(
    text: str, expected_ids: list[str]
) -> tuple[dict[str, str], list[str]]:
    """Parse ``NODE-ID: <fix>`` lines.

    Returns ``(fixes, missing)`` where *missing* preserves the order of
    *expected_ids* for every node without a usable fix. A missing or empty
    line is never defaulted — the caller keeps that gap open.
    """
    expected = set(expected_ids)
    fixes: dict[str, str] = {}
    for raw in text.strip().splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        nid_part, value = line.split(":", 1)
        nid = nid_part.strip().strip("[]")
        if nid not in expected:
            continue
        cleaned = value.strip()
        if cleaned:
            fixes[nid] = cleaned
    missing = [nid for nid in expected_ids if nid not in fixes]
    return fixes, missing
