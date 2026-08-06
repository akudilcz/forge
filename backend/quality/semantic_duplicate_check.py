"""Semantic duplicate checker — plain text LLM judgment, no structured output.

Asks the LLM whether a node is a semantic duplicate of its siblings.
Parses DUPLICATE/UNIQUE from the text response.

Deletion is guarded two ways (a false DUPLICATE destroys requirement text
that cannot be auto-recovered — live-trace proven on PARA-0242):

1. **Double confirmation** — a node is only deleted when two independent
   LLM calls both return DUPLICATE. A single nondeterministic verdict is
   never enough.
2. **Sticky UNIQUE verdicts** — a UNIQUE verdict is cached per
   ``(node_id, content-hash)`` in a caller-supplied cache dict (scoped to
   the owning flow, no global state). The pipeline's deletion-triggered
   re-loop therefore cannot re-litigate an unchanged node in a later cycle.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.server.forge_logger import forge_logger

_SYSTEM_PROMPT = """\
You are a deduplication judge. Given the SIBLINGS context followed by a
TARGET node, decide whether the target is a TRUE semantic duplicate of
any sibling.

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

_STICKY_UNIQUE = "UNIQUE"

# ── Deterministic prescreen (design/01 §7.4) ─────────────────────────────────
#
# Clearly-dissimilar pairs never reach the LLM judge. The threshold is
# conservative: true semantic duplicates share far more than 20% of the
# smaller node's vocabulary ("The system shall …" boilerplate alone usually
# clears it), so only pairs with essentially disjoint wording are skipped.
# The prescreen only reduces candidate pairs — it never authorises deletion;
# the two-call double confirmation below still gates every actual deletion.

_PRESCREEN_MIN_OVERLAP = 0.2

#: One peer block per line: "  [NODE-ID] content…" (build_all_peers_context).
_PEER_LINE_RE = re.compile(r"^\s*\[(?P<pid>[^\]\s]+)\]\s*(?P<body>.*)$")


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _overlap(a: set[str], b: set[str]) -> float:
    """Token-set overlap coefficient: |A∩B| / min(|A|, |B|)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _peer_bodies(siblings_text: str) -> list[str]:
    """Split the peers context into per-peer content blocks."""
    bodies: list[str] = []
    for raw in siblings_text.splitlines():
        m = _PEER_LINE_RE.match(raw)
        if m:
            bodies.append(m.group("body"))
        elif bodies:
            # Continuation line of the previous peer block.
            bodies[-1] = f"{bodies[-1]} {raw.strip()}"
    return bodies


def prescreen_similar_peers(node_content: str, siblings_text: str) -> bool:
    """Return True when at least one peer is lexically similar enough to be
    worth an LLM judgment.

    Conservative on the judge side: peers text that cannot be parsed into
    ``[ID]`` blocks is sent to the judge rather than silently skipped.
    """
    bodies = _peer_bodies(siblings_text)
    if not bodies:
        return True
    target = _token_set(node_content)
    return any(
        _overlap(target, _token_set(body)) >= _PRESCREEN_MIN_OVERLAP
        for body in bodies
    )


def _cache_key(node_id: str, node_content: str) -> tuple[str, str]:
    """Cache key for a verdict: the node plus a hash of its exact content."""
    digest = hashlib.sha256(node_content.encode("utf-8")).hexdigest()
    return (node_id, digest)


def create_semantic_checker(
    llm: Any,
    graph: Any,
    verdict_cache: dict[tuple[str, str], str],
) -> Any:
    """Return an async callable that judges one node and deletes it if duplicate.

    *verdict_cache* maps ``(node_id, content-hash)`` to a sticky verdict.
    It must be owned by the flow so it persists across pipeline cycles
    (each cycle builds a fresh checker) but never leaks between projects.
    """

    async def _judge_once(
        node_id: str, node_content: str, siblings_text: str
    ) -> tuple[bool, str]:
        """One independent LLM judgment. Returns (is_duplicate, label)."""
        t0 = time.monotonic()
        # Prompt-cache alignment (design/01 §7.4): [system + SIBLINGS] is the
        # static prefix — shared across targets under one parent and across
        # the byte-identical double-confirmation call — and the TARGET is the
        # dynamic suffix. Provider-side KV/prompt caching reuses the prefix
        # only; sampling of the two verdicts remains independent.
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=(
                f"SIBLINGS:\n{siblings_text}\n\n"
                f"TARGET REQUIREMENT ({node_id}):\n{node_content}"
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
        return is_duplicate, label

    async def check(node_id: str, node_content: str, siblings_text: str) -> bool:
        """Judge node_id against siblings_text; delete only on a confirmed
        duplicate verdict. Returns True if deleted."""
        key = _cache_key(node_id, node_content)
        if key in verdict_cache:
            forge_logger.emit(
                "INFO", "SEMA ",
                f"Skip {node_id} — sticky {verdict_cache[key]} verdict for unchanged content",
                node_id=node_id,
            )
            return False

        # Deterministic prescreen: a target sharing essentially no vocabulary
        # with any peer cannot be a semantic duplicate — skip the LLM judge.
        # Reduces candidate pairs only; never authorises deletion.
        if not prescreen_similar_peers(node_content, siblings_text):
            forge_logger.emit(
                "INFO", "SEMA ",
                f"Prescreen skip {node_id} — no lexically similar peer "
                f"(overlap < {_PRESCREEN_MIN_OVERLAP})",
                node_id=node_id,
            )
            return False

        forge_logger.emit(
            "INFO", "SEMA ",
            f"Judging {node_id}",
            f"content={node_content[:80].replace(chr(10), ' ')!r}",
            node_id=node_id,
            sibling_count=siblings_text.count("[") if siblings_text else 0,
        )

        is_duplicate, label = await _judge_once(node_id, node_content, siblings_text)
        if not is_duplicate:
            if label == "UNIQUE":
                # Sticky: an unchanged node judged UNIQUE is never re-litigated.
                verdict_cache[key] = _STICKY_UNIQUE
            return False

        # One DUPLICATE verdict is not enough to destroy requirement text —
        # require the same verdict from a second, independent call.
        confirmed, confirm_label = await _judge_once(node_id, node_content, siblings_text)
        if not confirmed:
            forge_logger.emit(
                "WARN", "SEMA ",
                f"Unconfirmed duplicate {node_id} — first call said DUPLICATE, "
                f"confirmation said {confirm_label}; keeping node",
                node_id=node_id,
            )
            # Disagreement defaults to UNIQUE, and sticks: without this the
            # pipeline re-loop gets up to 12 fresh chances to flip the verdict.
            verdict_cache[key] = _STICKY_UNIQUE
            return False

        forge_logger.emit(
            "INFO", "SEMA ",
            f"Deleting confirmed duplicate {node_id} (2/2 DUPLICATE verdicts)",
            node_id=node_id,
        )
        await graph.delete_node(node_id)
        return True

    return check
