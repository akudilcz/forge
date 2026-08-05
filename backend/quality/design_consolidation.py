"""Design consolidation — merges DESIGN sprawl within a MODULE.

Plain text LLM call (no structured output). Asks the LLM which DESIGNs
to merge and what the merged content should be. Parses KEEP/MERGE
directives from the response.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a software design reviewer. Given a MODULE with multiple DESIGN
nodes, determine which DESIGNs are redundant and should be merged.

For each group that should be merged, output:
KEEP: <node_id to keep>
MERGE: <node_id to merge into the kept one>
MERGED_CONTENT: <the combined design content>
REASON: <why these should be merged>

If no merging is needed, respond with:
NO_MERGE_NEEDED

Separate groups with a blank line.
"""


def create_design_consolidator(llm: Any, graph: Any) -> Any:
    """Return an async callable that consolidates DESIGNs under one MODULE."""

    async def consolidate(
        module_id: str,
        module_content: str,
        contract_content: str,
        designs: list[dict[str, Any]],
    ) -> int:
        """Evaluate DESIGNs under module_id and merge where appropriate."""
        if len(designs) <= 1:
            return 0

        designs_text = "\n\n".join(
            f"[DESIGN {d['node_id']}] (trace_to: {d['trace_to']})\n{d['content']}"
            for d in designs
        )

        forge_logger.emit(
            "INFO", "CONS ",
            f"Consolidating {len(designs)} DESIGN(s) under {module_id}",
        )

        try:
            response = await llm.ainvoke([
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=(
                    f"MODULE ({module_id}):\n{module_content}\n\n"
                    f"CONTRACT:\n{contract_content or '(none)'}\n\n"
                    f"EXISTING DESIGNS ({len(designs)}):\n{designs_text}"
                )),
            ])
            text = (response.content if hasattr(response, "content") else str(response)).strip()
        except Exception as exc:  # noqa: BLE001
            forge_logger.emit("WARN", "CONS ", f"LLM failed for {module_id}: {exc}")
            return 0

        if "NO_MERGE_NEEDED" in text.upper():
            forge_logger.emit("INFO", "CONS ", f"{module_id} — no merging needed")
            return 0

        return await _execute_merges(graph, module_id, text, designs)

    return consolidate


#: Line prefixes that terminate a MERGED_CONTENT block.
_BLOCK_MARKERS = ("KEEP:", "MERGE:", "MERGED_CONTENT:", "REASON:")


async def _execute_merges(
    graph: Any, module_id: str, text: str, designs: list[dict[str, Any]],
) -> int:
    """Parse merge directives from LLM response and execute them."""
    design_ids = {d["node_id"] for d in designs}
    deleted = 0

    # Parse KEEP/MERGE blocks
    blocks = text.strip().split("\n\n")
    for block in blocks:
        lines = block.strip().splitlines()
        keep_id = ""
        merge_ids: list[str] = []
        merged_content = ""
        reasoning = ""

        # MERGED_CONTENT is a block, not a line. Taking only the text after the
        # marker truncated every realistic multi-line design body to its first
        # line — and the merged nodes were deleted regardless, so the detail was
        # unrecoverable. Accumulate until the next known marker instead.
        content_lines: list[str] = []
        collecting = False
        for line in lines:
            upper = line.strip().upper()
            if collecting and not upper.startswith(_BLOCK_MARKERS):
                content_lines.append(line)
                continue
            collecting = False
            if upper.startswith("KEEP:"):
                keep_id = line.split(":", 1)[1].strip()
            elif upper.startswith("MERGE:"):
                merge_ids.append(line.split(":", 1)[1].strip())
            elif upper.startswith("MERGED_CONTENT:"):
                first = line.split(":", 1)[1].strip()
                content_lines = [first] if first else []
                collecting = True
            elif upper.startswith("REASON:"):
                reasoning = line.split(":", 1)[1].strip()
        merged_content = "\n".join(content_lines).strip()

        if not keep_id or not merge_ids:
            continue
        if keep_id not in design_ids:
            forge_logger.emit("WARN", "CONS ", f"Skip — {keep_id} not in designs")
            continue

        forge_logger.decision(
            "design_merge",
            f"{merge_ids}->{keep_id}",
            reasoning[:200],
            node_id=keep_id,
            merge_ids=merge_ids,
        )

        # Union trace_to from all merged DESIGNs
        keep = graph.node_sync(keep_id)
        if keep is None:
            continue

        all_traces: list[str] = list(keep.trace_to or [])
        for mid in merge_ids:
            merged = graph.node_sync(mid)
            if merged:
                all_traces.extend(merged.trace_to or [])
        unique_traces = list(dict.fromkeys(all_traces))

        if not merged_content:
            # Without parsed content there is nothing to merge INTO the keeper,
            # so deleting the others would destroy requirement coverage that was
            # never transferred. Previously the union was skipped here and the
            # deletes ran anyway.
            forge_logger.emit(
                "WARN",
                "CONS ",
                f"Skipping merge into {keep_id} — no MERGED_CONTENT parsed",
                f"merge_ids={merge_ids}",
                node_id=keep_id,
            )
            continue

        await graph.update_node(
            node_id=keep_id,
            content=merged_content,
            properties=None,
            changed_by="design_consolidation",
            change_reason=f"Merged {len(merge_ids)} DESIGN(s)",
            trace_to=unique_traces,
        )

        for mid in merge_ids:
            if graph.node_sync(mid) is not None:
                await graph.delete_node(mid)
                forge_logger.emit("INFO", "CONS ", f"Deleted merged DESIGN {mid}")
                deleted += 1

    forge_logger.emit("INFO", "CONS ", f"{module_id} consolidation done — {deleted} merged")
    return deleted
