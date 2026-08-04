"""Semantic duplicate checker — plain text LLM judgment, no structured output.

Asks the LLM whether a node is a semantic duplicate of its siblings.
Parses DUPLICATE/UNIQUE from the text response.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a deduplication judge. Given a TARGET node and its SIBLINGS,
decide whether the target is a TRUE semantic duplicate of any sibling.

Two nodes are DUPLICATE only if they specify THE SAME OBLIGATION —
i.e. a passing test written against one would also satisfy the other,
and deleting either leaves no gap in coverage.

Two nodes are UNIQUE when any of the following holds:
- They describe different behaviours, even if they share a subject
  (e.g. "shall sort the list" vs "shall not modify the input" — same
  function, distinct obligations).
- One is a main behaviour and the other is an edge case or error case
  (e.g. "shall sort" vs "shall return [] for empty input" vs
  "shall raise TypeError on non-integers").
- They are bullet points from the same list describing different
  input conditions or outcomes.
- They share vocabulary (function names, domain terms) but specify
  different invariants, pre-conditions, or post-conditions.
- They live at different levels (one is a heading or overview, the
  other is a concrete requirement inside that section).

DEFAULT TO UNIQUE WHEN IN DOUBT. A false DUPLICATE deletes real
coverage and cannot be auto-recovered. Do NOT call two nodes
duplicate merely because they are in the same section, mention the
same function, or look structurally similar.

Respond with exactly one line:
DUPLICATE - <sibling id and the exact obligation that overlaps>
or
UNIQUE - <the distinct obligation this node specifies>
"""


def create_semantic_checker(llm: Any, graph: Any) -> Any:
    """Return an async callable that judges one node and deletes it if duplicate."""

    async def check(node_id: str, node_content: str, siblings_text: str) -> bool:
        """Judge node_id against siblings_text; delete if duplicate. Returns True if deleted."""
        import time  # noqa: PLC0415

        forge_logger.emit(
            "INFO", "SEMA ",
            f"Judging {node_id}",
            f"content={node_content[:80].replace(chr(10), ' ')!r}",
            node_id=node_id,
            sibling_count=siblings_text.count("[") if siblings_text else 0,
        )

        t0 = time.monotonic()
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=(
                f"TARGET REQUIREMENT ({node_id}):\n{node_content}\n\n"
                f"SIBLINGS:\n{siblings_text}"
            )),
        ])
        duration_ms = int((time.monotonic() - t0) * 1000)

        text = (response.content if hasattr(response, "content") else str(response)).strip()
        upper = text.upper()

        # An unusable answer is NOT a verdict. `"".startswith("DUPLICATE")` is
        # False, so an empty or truncated response used to be recorded as a
        # confident UNIQUE — indistinguishable in the trace from the model
        # actually judging the node distinct. Live runs do produce these: a
        # reasoning model can spend its whole output budget on reasoning tokens
        # and return content_len=0.
        #
        # The prompt's "default to UNIQUE when in doubt" instructs the *model*
        # how to judge; it does not license the *parser* to invent a verdict
        # when no answer came back. The node is still kept (deleting on no
        # evidence would be far worse), but the outcome is labelled distinctly
        # and logged at WARN so these are visible and countable rather than
        # silently inflating the graph with real duplicates.
        if upper.startswith("DUPLICATE"):
            is_duplicate, label = True, "DUPLICATE"
        elif upper.startswith("UNIQUE"):
            is_duplicate, label = False, "UNIQUE"
        else:
            is_duplicate, label = False, "UNPARSEABLE"
            forge_logger.emit(
                "WARN", "SEMA ",
                f"Unusable dedup verdict for {node_id} — keeping node",
                f"raw={text[:200]!r}",
                node_id=node_id,
                duration_ms=duration_ms,
            )

        forge_logger.decision(
            "semantic_dedup", label, text[:120],
            node_id=node_id,
            duration_ms=duration_ms,
        )

        if is_duplicate:
            forge_logger.emit(
                "INFO", "SEMA ",
                f"Deleting confirmed duplicate {node_id}",
                node_id=node_id,
            )
            await graph.delete_node(node_id)

        return is_duplicate

    return check
