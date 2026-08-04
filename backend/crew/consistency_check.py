"""LLM-based consistency checkers — contradictions, decomposition, conformance.

Plain text LLM calls (no structured output). Each factory returns an async
callable that parses a two-line verdict and produces Gap objects.

Three checkers cover four gap types:
  - requirement_consistency  → CONTRADICTORY_REQUIREMENTS
  - decomposition            → INCOMPLETE_DECOMPOSITION
  - architecture_conformance → CONTRACT_VIOLATION + CROSS_MODULE_COUPLING
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

# ── Prompts ──────────────────────────────────────────────────────────────────

_CONTRADICTION_PROMPT = """\
You are a requirements consistency auditor. You receive one requirement and
all its sibling requirements (same parent). Check whether the target
requirement semantically contradicts any sibling.

Respond with exactly one line:
CONTRADICTION: PASS or FAIL

If FAIL, add a brief reason after a dash.
Example:
CONTRADICTION: FAIL - conflicts with sibling REQ-003 which mandates CSV-only
"""

_DECOMPOSITION_PROMPT = """\
You are a requirements decomposition auditor. You receive a high-level
requirement (HLR), its low-level requirement (LLR) children, and the
MODULE's CONTRACT context.

Check whether the LLRs fully cover the HLR given the CONTRACT context.
A simple HLR with one LLR is fine. But if the CONTRACT specifies multiple
interfaces, the LLRs should decompose the HLR into testable parts covering
each interface.

Respond with exactly one line:
DECOMPOSITION: PASS or FAIL

If FAIL, add a brief reason after a dash.
Example:
DECOMPOSITION: FAIL - CONTRACT defines 3 endpoints but LLRs only cover 2
"""

_CONFORMANCE_PROMPT = """\
You are an architectural conformance auditor. You receive a DESIGN, its
MODULE's CONTRACT, and summaries of all other MODULEs.

Check two things:
1. CONTRACT_VIOLATION: Does the DESIGN implement against the CONTRACT's
   public interface? If it ignores or contradicts the CONTRACT, it fails.
2. CROSS_MODULE_COUPLING: Does the DESIGN reference internals of another
   MODULE? Cross-module dependencies must go through CONTRACTs only.

Respond with exactly two lines:
CONTRACT: PASS or FAIL
COUPLING: PASS or FAIL

If FAIL, add a brief reason after a dash.
Example:
CONTRACT: FAIL - DESIGN uses private helper not in CONTRACT
COUPLING: PASS
"""


# ── Factories ────────────────────────────────────────────────────────────────


def create_requirement_consistency_checker(llm: Any) -> Any:
    """Return async callable checking CONTRADICTORY_REQUIREMENTS."""

    async def check(
        node_id: str,
        content: str,
        siblings_content: str,
    ) -> list[Gap]:
        if not content.strip():
            return []

        forge_logger.emit(
            "INFO",
            "CONSIST",
            f"Checking contradictions for {node_id}",
            f"content={content[:80].replace(chr(10), ' ')!r}",
            node_id=node_id,
        )

        response = await llm.ainvoke(
            [
                SystemMessage(content=_CONTRADICTION_PROMPT),
                HumanMessage(
                    content=(
                        f"TARGET REQUIREMENT ({node_id}):\n{content}\n\n"
                        f"SIBLING REQUIREMENTS:\n{siblings_content}"
                    )
                ),
            ]
        )

        text = _extract_text(response)
        gaps = _parse_contradiction(node_id, content, text)
        forge_logger.emit(
            "INFO" if not gaps else "WARN", "CONSIST",
            f"{node_id}: {len(gaps)} contradiction(s)",
            node_id=node_id,
            gap_count=len(gaps),
        )
        return gaps

    return check


def create_decomposition_checker(llm: Any) -> Any:
    """Return async callable checking INCOMPLETE_DECOMPOSITION."""

    async def check(
        hlr_id: str,
        hlr_content: str,
        llrs_content: str,
        contract_content: str,
    ) -> list[Gap]:
        if not hlr_content.strip():
            return []

        forge_logger.emit(
            "INFO",
            "DECOMP",
            f"Checking decomposition for {hlr_id}",
            f"hlr={hlr_content[:80].replace(chr(10), ' ')!r}",
        )

        response = await llm.ainvoke(
            [
                SystemMessage(content=_DECOMPOSITION_PROMPT),
                HumanMessage(
                    content=(
                        f"HLR ({hlr_id}):\n{hlr_content}\n\n"
                        f"LLR CHILDREN:\n{llrs_content}\n\n"
                        f"CONTRACT CONTEXT:\n{contract_content}"
                    )
                ),
            ]
        )

        text = _extract_text(response)
        return _parse_decomposition(hlr_id, hlr_content, text)

    return check


def create_architecture_conformance_checker(llm: Any) -> Any:
    """Return async callable checking CONTRACT_VIOLATION + CROSS_MODULE_COUPLING."""

    async def check(
        design_id: str,
        design_content: str,
        contract_content: str,
        all_modules_content: str,
    ) -> list[Gap]:
        if not design_content.strip():
            return []

        forge_logger.emit(
            "INFO",
            "CONFORM",
            f"Checking conformance for {design_id}",
            f"design={design_content[:80].replace(chr(10), ' ')!r}",
        )

        response = await llm.ainvoke(
            [
                SystemMessage(content=_CONFORMANCE_PROMPT),
                HumanMessage(
                    content=(
                        f"DESIGN ({design_id}):\n{design_content}\n\n"
                        f"CONTRACT:\n{contract_content}\n\n"
                        f"ALL MODULES:\n{all_modules_content}"
                    )
                ),
            ]
        )

        text = _extract_text(response)
        return _parse_conformance(design_id, design_content, text)

    return check


# ── Parsing helpers ──────────────────────────────────────────────────────────


def _extract_text(response: Any) -> str:
    return response.content if hasattr(response, "content") else str(response)


def _parse_contradiction(node_id: str, content: str, text: str) -> list[Gap]:
    """Parse CONTRADICTION verdict into gaps."""
    gaps: list[Gap] = []
    for line in text.strip().upper().splitlines():
        stripped = line.strip()
        if stripped.startswith("CONTRADICTION:") and "FAIL" in stripped:
            reasoning = _reason_after_dash(text, "CONTRADICTION:")
            forge_logger.emit("INFO", "CONSIST", f"{node_id} → CONTRADICTION", reasoning[:120])
            gaps.append(
                Gap(
                    type=GapType.CONTRADICTORY_REQUIREMENTS,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node_id,
                    description=f"{node_id} contradicts a sibling: {content[:120]!r}",
                    context={"reasoning": reasoning},
                )
            )
            break
    return gaps


def _parse_decomposition(hlr_id: str, content: str, text: str) -> list[Gap]:
    """Parse DECOMPOSITION verdict into gaps."""
    gaps: list[Gap] = []
    for line in text.strip().upper().splitlines():
        stripped = line.strip()
        if stripped.startswith("DECOMPOSITION:") and "FAIL" in stripped:
            reasoning = _reason_after_dash(text, "DECOMPOSITION:")
            forge_logger.emit("INFO", "DECOMP", f"{hlr_id} → INCOMPLETE", reasoning[:120])
            gaps.append(
                Gap(
                    type=GapType.INCOMPLETE_DECOMPOSITION,
                    priority=GapPriority.MAINTENANCE,
                    node_id=hlr_id,
                    description=f"{hlr_id} LLRs incompletely decompose HLR: {content[:120]!r}",
                    context={"reasoning": reasoning},
                )
            )
            break
    return gaps


def _parse_conformance(design_id: str, content: str, text: str) -> list[Gap]:
    """Parse CONTRACT + COUPLING verdicts into gaps."""
    gaps: list[Gap] = []
    upper_lines = text.strip().upper().splitlines()

    for line in upper_lines:
        stripped = line.strip()
        if stripped.startswith("CONTRACT:") and "FAIL" in stripped:
            reasoning = _reason_after_dash(text, "CONTRACT:")
            forge_logger.emit(
                "INFO", "CONFORM", f"{design_id} → CONTRACT_VIOLATION", reasoning[:120]
            )
            gaps.append(
                Gap(
                    type=GapType.CONTRACT_VIOLATION,
                    priority=GapPriority.MAINTENANCE,
                    node_id=design_id,
                    description=f"{design_id} violates MODULE CONTRACT: {content[:120]!r}",
                    context={"reasoning": reasoning},
                )
            )
        elif stripped.startswith("COUPLING:") and "FAIL" in stripped:
            reasoning = _reason_after_dash(text, "COUPLING:")
            forge_logger.emit(
                "INFO", "CONFORM", f"{design_id} → CROSS_MODULE_COUPLING", reasoning[:120]
            )
            gaps.append(
                Gap(
                    type=GapType.CROSS_MODULE_COUPLING,
                    priority=GapPriority.MAINTENANCE,
                    node_id=design_id,
                    description=f"{design_id} has cross-module coupling: {content[:120]!r}",
                    context={"reasoning": reasoning},
                )
            )
    return gaps


def _reason_after_dash(raw_text: str, prefix: str) -> str:
    """Extract the reason string after '-' on the line starting with prefix."""
    for line in raw_text.strip().splitlines():
        if line.strip().upper().startswith(prefix):
            if "-" in line:
                return line.split("-", 1)[1].strip()
            return ""
    return ""
