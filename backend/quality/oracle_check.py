"""Independent CASE-oracle validation (U9, specs/13 §Oracle validation).

The dominant failure mode of LLM-authored test cases is a *wrong oracle*:
the case exercises the right topic but asserts an outcome the requirement
never states (or contradicts), so a wrong implementation passes and the
error silently steers code generation. An independent judge therefore
validates every CASE against its traced requirement text and the owning
module's CONTRACT record, on three axes:

  OUTCOME       — the expected outcome actually follows from the requirement;
  CONTRACT      — contracted exception/return semantics are encoded where the
                  record states them;
  DISCRIMINATES — the case names a real discriminating input (the wrong
                  implementation it kills), not boilerplate.

A missing verdict is never a pass: unjudged cases are re-asked exactly once,
then :class:`UnjudgedQualityError` propagates — oracle quality GATES phase 10
completion. A failed axis becomes one ``INCONSISTENT_CONTENT`` repair gap on
the CASE (existing case-quality taxonomy; the gap context carries the axis
findings for the repair prompt).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.quality.combined_check import UnjudgedQualityError
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

#: The three oracle axes, in judging order.
ORACLE_AXES: tuple[str, str, str] = ("OUTCOME", "CONTRACT", "DISCRIMINATES")

_CASE_TYPES = ("CASE_HLR", "CASE_LLR")

_NO_CONTRACT_BLOCK = "(no contract record for the owning module)"


_SYSTEM_PROMPT = """\
You are an independent test-oracle auditor. For each TEST CASE you receive
its traced REQUIREMENT text and the owning module's CONTRACT record. Judge
each case on THREE axes:

  OUTCOME       — does the case's expected outcome actually FOLLOW from the
                  traced requirement text? A plausible-but-wrong oracle —
                  asserting behaviour the requirement never states, or
                  contradicting it — is the failure this axis exists to
                  catch. Judge against the requirement TEXT, not against
                  what a typical implementation would do.
  CONTRACT      — where the CONTRACT record states exception or return
                  semantics for the symbol under test, does the case encode
                  them EXACTLY (exception class AND base class, exact return
                  values — None vs empty collection matters)? If the record
                  states no obligation that applies to this case → PASS.
  DISCRIMINATES — does the case name a concrete DISCRIMINATING input: real
                  data for which a specific wrong implementation would fail?
                  Boilerplate ("use discriminating inputs", "assert correct
                  behaviour") with no concrete input named → FAIL(name the
                  missing input).

OUTPUT FORMAT — one line per case in the INPUT order, with this exact shape:

  <CASE_ID>: OUTCOME=<PASS|FAIL(short reason)> CONTRACT=<PASS|FAIL(short reason)> DISCRIMINATES=<PASS|FAIL(short reason)>

Rules:
- Keep every FAIL reason under 80 chars.
- EVERY input case MUST receive a line with a verdict for EVERY axis.
- Do NOT emit any other text — no preamble, no summary.

Example:
CASE_HLR-0001: OUTCOME=PASS CONTRACT=PASS DISCRIMINATES=PASS
CASE_LLR-0002: OUTCOME=FAIL(asserts sorted output the LLR never states) CONTRACT=PASS DISCRIMINATES=FAIL(no concrete input named)
"""


@dataclass(frozen=True)
class OracleItem:
    """One CASE plus the evidence the judge validates it against."""

    node_id: str
    case_content: str
    requirement_block: str
    contract_block: str


def oracle_pass_key(item: OracleItem) -> tuple[str, str]:
    """Sticky-PASS cache key: node plus a hash of exactly what was judged.

    The requirement and contract blocks participate because the verdict is
    relative to them — an edited requirement or contract must rotate the key
    and force a re-judgement, exactly like a rewritten case.
    """
    digest = hashlib.sha256(
        "\x00".join(
            (item.case_content, item.requirement_block, item.contract_block)
        ).encode()
    ).hexdigest()
    return (item.node_id, digest)


# ── Item collection ──────────────────────────────────────────────────────────


def collect_oracle_items(graph: Any) -> list[OracleItem]:
    """Assemble one :class:`OracleItem` per CASE with a resolvable trace.

    The requirement block inlines every traced requirement's text; the
    contract block carries the owning MODULE's structured ``public_api``
    (CASE_HLR: MODULE whose ``trace_to`` owns the HLR; CASE_LLR: via the
    LLR's parent HLR). A CASE whose traces resolve to no requirement is
    skipped with a loud warning — trace validity is ``case_trace_check``'s
    job, and judging an oracle against nothing proves nothing.
    """
    all_nodes = graph.all_nodes()
    hlr_to_module: dict[str, Any] = {}
    for n in all_nodes:
        if n.node_type == "MODULE":
            for hlr_id in n.trace_to or []:
                hlr_to_module[hlr_id] = n
    contracts_by_module: dict[str, list[Any]] = {}
    for n in all_nodes:
        if n.node_type == "CONTRACT" and n.parent_id:
            contracts_by_module.setdefault(n.parent_id, []).append(n)

    items: list[OracleItem] = []
    for case in all_nodes:
        if case.node_type not in _CASE_TYPES:
            continue
        reqs = [r for r in (graph.node_sync(rid) for rid in case.trace_to or []) if r]
        if not reqs:
            forge_logger.emit(
                "WARN", "ORCL ",
                f"{case.node_id}: no resolvable traced requirement — skipping "
                f"oracle validation (trace validity is case_trace_check's job)",
            )
            continue
        items.append(
            OracleItem(
                node_id=case.node_id,
                case_content=(case.content or "").strip(),
                requirement_block="\n".join(
                    f"[{r.node_id}] {(r.content or '').strip()}" for r in reqs
                ),
                contract_block=_contract_block_for(
                    reqs, hlr_to_module, contracts_by_module
                ),
            )
        )
    return items


def _contract_block_for(
    reqs: list[Any],
    hlr_to_module: dict[str, Any],
    contracts_by_module: dict[str, list[Any]],
) -> str:
    """Render the CONTRACT record(s) of the module(s) owning the traced reqs."""
    module_ids: list[str] = []
    for req in reqs:
        hlr_id = req.node_id if req.node_type == "HLR" else req.parent_id
        module = hlr_to_module.get(hlr_id) if hlr_id else None
        if module and module.node_id not in module_ids:
            module_ids.append(module.node_id)

    blocks: list[str] = []
    for module_id in module_ids:
        for contract in contracts_by_module.get(module_id, []):
            public_api = (contract.properties or {}).get("public_api")
            if not public_api:
                continue
            blocks.append(
                f"[{contract.node_id}] module={module_id}\n"
                f"{json.dumps(public_api, indent=2)}"
            )
    return "\n\n".join(blocks) if blocks else _NO_CONTRACT_BLOCK


# ── The judge ────────────────────────────────────────────────────────────────


_RETRY_NOTE = (
    "The cases below received NO verdict (or an incomplete one) in your "
    "previous answer. Judge EVERY case below on EVERY axis, one line per "
    "case, exactly in the output format.\n\n"
)


def _payload(items: list[OracleItem]) -> str:
    blocks: list[str] = []
    for item in items:
        blocks.append(
            f"TEST CASE [{item.node_id}]:\n{item.case_content}\n"
            f"TRACED REQUIREMENT(S):\n{item.requirement_block}\n"
            f"CONTRACT RECORD:\n{item.contract_block}"
        )
    return "\n\n---\n\n".join(blocks)


def create_oracle_checker(llm: Any) -> Any:
    """Return an async callable judging a batch of :class:`OracleItem`s in
    one LLM call (plus at most one retry call for unjudged cases).

    The callable returns one ``INCONSISTENT_CONTENT`` :class:`Gap` per CASE
    that failed any axis, and raises :class:`UnjudgedQualityError` when any
    case is still without a full verdict after the retry.
    """

    async def _invoke(items: list[OracleItem], prefix: str) -> str:
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=prefix + _payload(items)),
        ])
        text: str = response.content if hasattr(response, "content") else str(response)
        return text

    async def check(items: list[OracleItem]) -> list[Gap]:
        if not items:
            return []
        forge_logger.emit(
            "INFO", "ORCL ", f"Oracle validation — {len(items)} case(s)"
        )
        gaps, missing = _parse_verdicts(items, await _invoke(items, ""))
        if not missing:
            return gaps

        forge_logger.emit(
            "WARN", "ORCL ",
            f"{len(missing)}/{len(items)} case(s) missing oracle verdicts — "
            f"re-asking once",
            f"unjudged={sorted(missing)[:10]}",
        )
        # Cases with any missing axis are re-judged wholly in the retry, so
        # their round-one (partial) gap is dropped — one gap per case, never
        # a double report.
        gaps = [g for g in gaps if g.node_id not in missing]
        retry_items = [it for it in items if it.node_id in missing]
        retry_gaps, retry_missing = _parse_verdicts(
            retry_items, await _invoke(retry_items, _RETRY_NOTE)
        )
        gaps.extend(retry_gaps)
        if retry_missing:
            raise UnjudgedQualityError(retry_missing)
        return gaps

    return check


_AXIS_RE = re.compile(
    r"(?P<axis>OUTCOME|CONTRACT|DISCRIMINATES)\s*=\s*"
    r"(?P<verdict>PASS|FAIL)"
    r"(?:\((?P<reason>[^)]*)\))?",
    re.IGNORECASE,
)


def _parse_verdicts(
    items: list[OracleItem],
    text: str,
) -> tuple[list[Gap], dict[str, set[str]]]:
    """Parse the batch verdict text into repair gaps and missing axes.

    Returns ``(gaps, missing)``: one merged gap per failing case, and
    ``missing`` mapping case id → axes without a verdict (a dropped line
    appears with all three axes missing — silence is never a pass).
    Verdict-shaped lines for hallucinated case ids are dropped loudly.
    """
    known = {item.node_id for item in items}
    verdicts_by_case: dict[str, dict[str, tuple[bool, str]]] = {}
    unknown_ids: set[str] = set()
    for raw in text.strip().splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        nid_part, rest = line.split(":", 1)
        nid = nid_part.strip()
        if nid not in known:
            if _AXIS_RE.search(rest):
                unknown_ids.add(nid)
            continue
        case_verdicts = verdicts_by_case.setdefault(nid, {})
        for m in _AXIS_RE.finditer(rest):
            case_verdicts[m.group("axis").upper()] = (
                m.group("verdict").upper() == "PASS",
                (m.group("reason") or "").strip(),
            )
    if unknown_ids:
        forge_logger.emit(
            "WARN", "ORCL ",
            f"Ignoring verdict line(s) for {len(unknown_ids)} unknown case "
            f"id(s) not in the candidate set — judge hallucinated ids",
            f"unknown={sorted(unknown_ids)[:10]}",
        )

    gaps: list[Gap] = []
    missing: dict[str, set[str]] = {}
    for item in items:
        case_verdicts = verdicts_by_case.setdefault(item.node_id, {})
        failures = [
            {"axis": axis, "reason": case_verdicts[axis][1]}
            for axis in ORACLE_AXES
            if axis in case_verdicts and not case_verdicts[axis][0]
        ]
        if failures:
            gaps.append(_oracle_gap(item, failures))
        unjudged = {axis for axis in ORACLE_AXES if axis not in case_verdicts}
        if unjudged:
            missing[item.node_id] = unjudged
    return gaps, missing


def _oracle_gap(item: OracleItem, failures: list[dict[str, str]]) -> Gap:
    """One merged INCONSISTENT_CONTENT repair gap per failing CASE."""
    summary = "; ".join(f"{f['axis']}: {f['reason']}" for f in failures)
    forge_logger.emit(
        "INFO", "ORCL ", f"{item.node_id} → ORACLE FAIL", summary[:160]
    )
    return Gap(
        type=GapType.INCONSISTENT_CONTENT,
        priority=GapPriority.MAINTENANCE,
        node_id=item.node_id,
        description=(
            f"CASE {item.node_id} failed independent oracle validation — "
            f"{summary}"
        ),
        context={"oracle_failures": failures},
    )
