"""LLM-based combined quality checker — one call judges all nodes on all axes.

A single LLM prompt judges every candidate
node on the applicable axes and returns one line per node:

    HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS
    HLR-0002: ATOMIC=FAIL(two obligations) EARS=PASS MATCH=PASS SPECIFIC=PASS
    MODULE-0001: MATCH=PASS SPECIFIC=PASS

For HLR/LLR nodes four axes apply (ATOMIC, EARS, MATCH, SPECIFIC);
for other authored types only the two title axes apply (MATCH, SPECIFIC).

This trades N sequential calls for 1 batched call — decisive speed-up on
phase 3 (HLRs), phase 7 (LLRs), and every phase that authors a design artefact.

A missing verdict is never a pass. A node line the model garbled, or an axis
it omitted, used to default to ``(True, "")`` — a truncated batch response
scored every dropped node as clean. Unjudged nodes/axes are now re-asked in
exactly one follow-up call; anything still unjudged raises
``UnjudgedQualityError`` so the failure is loud instead of a vacuous sweep.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)


class UnjudgedQualityError(RuntimeError):
    """Raised when nodes/axes remain without a verdict after one retry."""

    def __init__(self, missing: dict[str, set[str]]) -> None:
        self.missing = missing
        detail = ", ".join(
            f"{nid}: {sorted(axes)}" for nid, axes in sorted(missing.items())
        )
        super().__init__(
            f"Quality batch left {len(missing)} node(s) unjudged after retry "
            f"— never defaulting to pass. Unjudged: {detail}"
        )


# Node types where atomicity + EARS axes apply (otherwise only title axes).
_REQUIREMENT_TYPES = frozenset({"HLR", "LLR"})

_TITLE_AXES = frozenset({"MATCH", "SPECIFIC"})
_REQUIREMENT_AXES = frozenset({"ATOMIC", "EARS", "MATCH", "SPECIFIC"})


_SYSTEM_PROMPT = """\
You are a quality auditor for a requirements+design graph. Given a list of
authored nodes, judge each node on the following axes.

For HLR and LLR nodes, judge FOUR axes:
  ATOMIC   — exactly ONE "shall" obligation? Multiple outcomes or properties → FAIL.
  EARS     — matches EARS pattern ("The system shall <action>",
             "When X, the system shall ...", "If X, the system shall ...",
             "While X, the system shall ...")? Negative phrasings
             ("shall not raise any exception") → FAIL.
  MATCH    — does the title accurately summarise ONLY the current content's
             scope? Broader-scope title with narrower content → FAIL.
  SPECIFIC — is the title a concrete 3-5 word noun phrase, NOT a generic
             label like "Handle Cases", "Misc Rules", "General Behavior"?

For every OTHER authored node type (ARCHITECTURE, MODULE, CONTRACT, DESIGN,
SUITE, CASE_HLR, CASE_LLR), judge ONLY the two title axes (MATCH, SPECIFIC).

OUTPUT FORMAT — one line per node in the INPUT order, with this exact shape:

  <NODE_ID>: <AXIS>=<PASS|FAIL(short reason)> <AXIS>=<PASS|FAIL(short reason)> ...

Rules:
- Use PASS when the axis is fine. Use FAIL(reason) otherwise — keep reason <80 chars.
- If an axis does not apply to a node type, OMIT it from that line.
- EVERY input node MUST receive a line with a verdict for EVERY applicable axis.
- Do NOT emit any other text — no preamble, no summary.

Example:
HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS
HLR-0002: ATOMIC=FAIL(covers sort AND validate) EARS=PASS MATCH=PASS SPECIFIC=PASS
MODULE-0001: MATCH=PASS SPECIFIC=PASS
SUITE-0001: MATCH=PASS SPECIFIC=FAIL(title "Tests" is a vague label)
"""

def quality_pass_key(node_id: str, title: str, content: str) -> tuple[str, str]:
    """Cache key for a sticky PASS verdict: node plus a hash of exactly what
    was judged. Title participates because two of the four axes (MATCH,
    SPECIFIC) judge the title, so a retitle must rotate the key."""
    digest = hashlib.sha256(f"{title}\x00{content}".encode()).hexdigest()
    return (node_id, digest)


_RETRY_NOTE = (
    "The nodes below received NO verdict (or an incomplete one) in your "
    "previous answer. Judge EVERY node below on EVERY applicable axis, one "
    "line per node, exactly in the output format.\n\n"
)


def _payload(items: list[tuple[str, str, str, str]]) -> str:
    lines: list[str] = []
    for node_id, node_type, title, content in items:
        snippet = content.replace("\n", " ").strip()[:320]
        lines.append(
            f"[{node_id}] type={node_type} title={title!r}\n"
            f"  content={snippet!r}"
        )
    return "\n".join(lines)


def create_combined_quality_checker(llm: Any) -> Any:
    """Return an async callable that judges a batch of nodes in one LLM call
    (plus at most one retry call for unjudged nodes/axes)."""

    async def _invoke(items: list[tuple[str, str, str, str]], prefix: str) -> str:
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=prefix + _payload(items)),
        ])
        text: str = response.content if hasattr(response, "content") else str(response)
        return text

    async def check(
        items: list[tuple[str, str, str, str]],
    ) -> list[Gap]:
        """items: list of (node_id, node_type, title, content). Returns list of Gaps.

        Raises UnjudgedQualityError when any node/axis is still without a
        verdict after the single retry.
        """
        if not items:
            return []

        forge_logger.emit(
            "INFO", "XQUAL",
            f"Combined quality check — {len(items)} node(s)",
        )
        gaps, missing = _parse_verdicts(items, await _invoke(items, ""))
        if not missing:
            return gaps

        forge_logger.emit(
            "WARN", "XQUAL",
            f"{len(missing)}/{len(items)} node(s) missing verdicts — "
            f"re-asking once",
            f"unjudged={sorted(missing)[:10]}",
        )
        retry_items = [it for it in items if it[0] in missing]
        retry_gaps, retry_missing = _parse_verdicts(
            retry_items, await _invoke(retry_items, _RETRY_NOTE)
        )
        # Round-one verdicts stand: accept retry gaps only for axes that
        # were actually missing, so a re-judged axis cannot double-report.
        for gap in retry_gaps:
            if _GAP_TYPE_TO_AXIS[gap.type] in missing[gap.node_id]:
                gaps.append(gap)

        still_missing: dict[str, set[str]] = {}
        for nid, axes in missing.items():
            if nid in retry_missing:
                still = axes & retry_missing[nid]
                if still:
                    still_missing[nid] = still
        if still_missing:
            raise UnjudgedQualityError(still_missing)
        return gaps

    return check


# A single axis verdict line of the form:  AXIS=PASS  or  AXIS=FAIL(reason)
_AXIS_RE = re.compile(
    r"(?P<axis>ATOMIC|EARS|MATCH|SPECIFIC)\s*=\s*"
    r"(?P<verdict>PASS|FAIL)"
    r"(?:\((?P<reason>[^)]*)\))?",
    re.IGNORECASE,
)


def _expected_axes(node_type: str) -> frozenset[str]:
    """Axes the model is required to judge for a node type."""
    if node_type in _REQUIREMENT_TYPES:
        return _REQUIREMENT_AXES
    return _TITLE_AXES


def _parse_verdicts(
    items: list[tuple[str, str, str, str]],
    text: str,
) -> tuple[list[Gap], dict[str, set[str]]]:
    """Parse the batch verdict text.

    Returns ``(gaps, missing)`` where *missing* maps node_id → the applicable
    axes for which no verdict was returned. A dropped node line appears with
    all its applicable axes missing. Missing is never treated as a pass.
    """
    by_id: dict[str, tuple[str, str, str]] = {
        nid: (ntype, title, content) for nid, ntype, title, content in items
    }

    # Parse each output line; first token up to ':' is the node_id.
    verdicts_by_node: dict[str, dict[str, tuple[bool, str]]] = {}
    unknown_ids: set[str] = set()
    for raw in text.strip().splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        nid_part, rest = line.split(":", 1)
        nid = nid_part.strip()
        if nid not in by_id:
            # A verdict-shaped line for an id outside the candidate set is a
            # hallucinated id — dropped from accounting, but never silently
            # (design/01 §7.4). Prose lines without axis verdicts stay quiet.
            if _AXIS_RE.search(rest):
                unknown_ids.add(nid)
            continue
        node_verdicts = verdicts_by_node.setdefault(nid, {})
        for m in _AXIS_RE.finditer(rest):
            axis = m.group("axis").upper()
            passed = m.group("verdict").upper() == "PASS"
            reason = (m.group("reason") or "").strip()
            node_verdicts[axis] = (passed, reason)
    if unknown_ids:
        forge_logger.emit(
            "WARN", "XQUAL",
            f"Ignoring verdict line(s) for {len(unknown_ids)} unknown node "
            f"id(s) not in the candidate set — judge hallucinated ids",
            f"unknown={sorted(unknown_ids)[:10]}",
        )

    gaps: list[Gap] = []
    missing: dict[str, set[str]] = {}
    for nid, ntype, title, content in items:
        expected = _expected_axes(ntype)
        node_verdicts = verdicts_by_node.setdefault(nid, {})
        for axis in ("ATOMIC", "EARS", "MATCH", "SPECIFIC"):
            if axis not in expected or axis not in node_verdicts:
                continue
            passed, reason = node_verdicts[axis]
            if not passed:
                gaps.append(_axis_gap(nid, ntype, title, content, axis, reason))
        unjudged = set(expected - set(node_verdicts))
        if unjudged:
            missing[nid] = unjudged
    return gaps, missing


# Axis → (gap type, log label). Descriptions are built in _axis_gap.
_AXIS_TO_GAP_TYPE: dict[str, tuple[GapType, str]] = {
    "ATOMIC": (GapType.NON_ATOMIC_REQUIREMENT, "NON-ATOMIC"),
    "EARS": (GapType.NON_EARS_REQUIREMENT, "NON-EARS"),
    "MATCH": (GapType.STALE_TITLE, "STALE_TITLE"),
    "SPECIFIC": (GapType.VAGUE_TITLE, "VAGUE_TITLE"),
}

_GAP_TYPE_TO_AXIS: dict[GapType, str] = {
    gap_type: axis for axis, (gap_type, _) in _AXIS_TO_GAP_TYPE.items()
}


def _axis_gap(
    nid: str, ntype: str, title: str, content: str, axis: str, reason: str
) -> Gap:
    """Build the Gap for one failed axis verdict."""
    gap_type, label = _AXIS_TO_GAP_TYPE[axis]
    forge_logger.emit("INFO", "XQUAL", f"{nid} → {label}", reason[:120])
    if axis == "ATOMIC":
        description = f"{ntype} {nid} is non-atomic: {content[:120]!r}"
        context = {"reasoning": reason}
    elif axis == "EARS":
        description = f"{ntype} {nid} not EARS-form: {content[:120]!r}"
        context = {"reasoning": reason}
    elif axis == "MATCH":
        description = (
            f"{ntype} {nid} title {title!r} no longer matches content: "
            f"{content[:100]!r}"
        )
        context = {"current_title": title, "reasoning": reason}
    else:  # SPECIFIC
        description = f"{ntype} {nid} has a vague/generic title {title!r}."
        context = {"current_title": title, "reasoning": reason}
    return Gap(
        type=gap_type,
        priority=GapPriority.MAINTENANCE,
        node_id=nid,
        description=description,
        context=context,
    )
