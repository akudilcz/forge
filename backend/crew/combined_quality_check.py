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
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)


# Node types where atomicity + EARS axes apply (otherwise only title axes).
_REQUIREMENT_TYPES = frozenset({"HLR", "LLR"})


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
- Do NOT emit any other text — no preamble, no summary.

Example:
HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS
HLR-0002: ATOMIC=FAIL(covers sort AND validate) EARS=PASS MATCH=PASS SPECIFIC=PASS
MODULE-0001: MATCH=PASS SPECIFIC=PASS
SUITE-0001: MATCH=PASS SPECIFIC=FAIL(title "Tests" is a vague label)
"""


def create_combined_quality_checker(llm: Any) -> Any:
    """Return an async callable that judges a batch of nodes in one LLM call."""

    async def check(
        items: list[tuple[str, str, str, str]],
    ) -> list[Gap]:
        """items: list of (node_id, node_type, title, content). Returns list of Gaps."""
        if not items:
            return []

        lines: list[str] = []
        for node_id, node_type, title, content in items:
            snippet = content.replace("\n", " ").strip()[:320]
            lines.append(
                f"[{node_id}] type={node_type} title={title!r}\n"
                f"  content={snippet!r}"
            )
        payload = "\n".join(lines)

        forge_logger.emit(
            "INFO", "XQUAL",
            f"Combined quality check — {len(items)} node(s)",
        )

        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=payload),
        ])
        text = response.content if hasattr(response, "content") else str(response)
        return _parse_verdicts(items, text)

    return check


# A single axis verdict line of the form:  AXIS=PASS  or  AXIS=FAIL(reason)
_AXIS_RE = re.compile(
    r"(?P<axis>ATOMIC|EARS|MATCH|SPECIFIC)\s*=\s*"
    r"(?P<verdict>PASS|FAIL)"
    r"(?:\((?P<reason>[^)]*)\))?",
    re.IGNORECASE,
)


def _parse_verdicts(
    items: list[tuple[str, str, str, str]],
    text: str,
) -> list[Gap]:
    """Parse the batch verdict text into Gaps."""
    by_id: dict[str, tuple[str, str, str]] = {
        nid: (ntype, title, content) for nid, ntype, title, content in items
    }

    # Parse each output line; first token up to ':' is the node_id.
    #
    # A node the model omits, or an axis it forgets, used to be indistinguishable
    # from a PASS: the lookups below defaulted to (True, "") and an unmentioned
    # node was simply never examined. That made a truncated or malformed batch
    # response look like a clean quality sweep. Unjudged nodes and axes are now
    # counted and reported at WARN — no gap is invented on no evidence, but the
    # silence is visible.
    judged: dict[str, set[str]] = {}
    gaps: list[Gap] = []
    for raw in text.strip().splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        nid_part, rest = line.split(":", 1)
        nid = nid_part.strip()
        if nid not in by_id:
            continue
        ntype, title, content = by_id[nid]

        verdicts: dict[str, tuple[bool, str]] = {}
        for m in _AXIS_RE.finditer(rest):
            axis = m.group("axis").upper()
            passed = m.group("verdict").upper() == "PASS"
            reason = (m.group("reason") or "").strip()
            verdicts[axis] = (passed, reason)

        if not verdicts:
            continue
        judged[nid] = set(verdicts)

        is_requirement = ntype in _REQUIREMENT_TYPES

        if is_requirement:
            ok, reason = verdicts.get("ATOMIC", (True, ""))
            if not ok:
                forge_logger.emit("INFO", "XQUAL", f"{nid} → NON-ATOMIC", reason[:120])
                gaps.append(Gap(
                    type=GapType.NON_ATOMIC_REQUIREMENT,
                    priority=GapPriority.MAINTENANCE,
                    node_id=nid,
                    description=f"{ntype} {nid} is non-atomic: {content[:120]!r}",
                    context={"reasoning": reason},
                ))
            ok, reason = verdicts.get("EARS", (True, ""))
            if not ok:
                forge_logger.emit("INFO", "XQUAL", f"{nid} → NON-EARS", reason[:120])
                gaps.append(Gap(
                    type=GapType.NON_EARS_REQUIREMENT,
                    priority=GapPriority.MAINTENANCE,
                    node_id=nid,
                    description=f"{ntype} {nid} not EARS-form: {content[:120]!r}",
                    context={"reasoning": reason},
                ))

        # Title axes apply to every authored node.
        ok, reason = verdicts.get("MATCH", (True, ""))
        if not ok:
            forge_logger.emit("INFO", "XQUAL", f"{nid} → STALE_TITLE", reason[:120])
            gaps.append(Gap(
                type=GapType.STALE_TITLE,
                priority=GapPriority.MAINTENANCE,
                node_id=nid,
                description=(
                    f"{ntype} {nid} title {title!r} no longer matches content: "
                    f"{content[:100]!r}"
                ),
                context={"current_title": title, "reasoning": reason},
            ))
        ok, reason = verdicts.get("SPECIFIC", (True, ""))
        if not ok:
            forge_logger.emit("INFO", "XQUAL", f"{nid} → VAGUE_TITLE", reason[:120])
            gaps.append(Gap(
                type=GapType.VAGUE_TITLE,
                priority=GapPriority.MAINTENANCE,
                node_id=nid,
                description=(
                    f"{ntype} {nid} has a vague/generic title {title!r}."
                ),
                context={"current_title": title, "reasoning": reason},
            ))

    _report_unjudged(by_id, judged)
    return gaps


def _report_unjudged(
    by_id: dict[str, tuple[str, str, str]],
    judged: dict[str, set[str]],
) -> None:
    """Warn about nodes and axes the model never returned a verdict for.

    Silence is not a pass. Without this, a response that dropped half the batch
    scored every dropped node as clean.
    """
    unjudged = sorted(set(by_id) - set(judged))
    if unjudged:
        forge_logger.emit(
            "WARN",
            "XQUAL",
            f"{len(unjudged)}/{len(by_id)} node(s) received no quality verdict "
            f"— NOT treated as passing",
            f"unjudged={unjudged[:10]}",
        )

    for nid, axes in sorted(judged.items()):
        ntype = by_id[nid][0]
        expected = {"MATCH", "SPECIFIC"}
        if ntype in _REQUIREMENT_TYPES:
            expected |= {"ATOMIC", "EARS"}
        missing = sorted(expected - axes)
        if missing:
            forge_logger.emit(
                "WARN",
                "XQUAL",
                f"{nid} judged on only {sorted(axes)} — missing {missing}",
                node_id=nid,
            )
