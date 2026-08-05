"""Token-aware context packing — priority ordered, no mid-string truncation.

Replaces the hard 40k-char tail chop in ``task_builder.py`` with a packer that
drops whole low-priority sections when the budget is exceeded. Full node
content is always preserved in any section that is kept — truncation is never
applied mid-string.

Token counting uses ``tiktoken`` (already a project dep). We use the
``cl100k_base`` encoding which is a reasonable approximation for Claude
models (Claude does not publish its tokenizer, but cl100k is within 10–15%
of Claude's actual token counts for English text). Budget defaults assume
a Claude 200k-token context window with headroom reserved for the system
prompt, agent scratchpad, and response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

logger = logging.getLogger(__name__)


# Claude 200k window minus generous headroom for system prompt + response +
# tool schema + scratchpad. Tune via ``pack(..., budget_tokens=...)``.
DEFAULT_BUDGET_TOKENS = 120_000


@dataclass(frozen=True)
class Section:
    """One priority-tagged slice of context.

    priority: higher is more important; kept preferentially when over budget
    name:     short identifier for logging which sections were dropped
    text:     full content — NEVER truncated mid-string by the packer
    """

    priority: int
    name: str
    text: str


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Return the tiktoken-estimated token count for a string."""
    if not text:
        return 0
    return len(_encoding().encode(text))


def pack(
    sections: list[Section],
    *,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    separator: str = "\n\n---\n\n",
) -> str:
    """Concatenate ``sections`` into a single string, dropping lowest-priority
    whole sections if the total exceeds ``budget_tokens``. Never truncates
    mid-string.

    Sections are emitted in their original order in the output — priority
    affects only the drop decision, not the presentation order.
    """
    if not sections:
        return ""

    sep_tokens = count_tokens(separator)
    sized = [(s, count_tokens(s.text)) for s in sections]
    total = sum(t for _, t in sized) + sep_tokens * max(0, len(sized) - 1)

    dropped: list[tuple[Section, int]] = []
    # Drop from lowest priority first, preserving original ordering among kept.
    while total > budget_tokens and sized:
        # Find index of lowest-priority section (stable: first with min priority).
        min_pri = min(s.priority for s, _ in sized)
        drop_idx = next(i for i, (s, _) in enumerate(sized) if s.priority == min_pri)
        dropped_section, dropped_tokens = sized.pop(drop_idx)
        dropped.append((dropped_section, dropped_tokens))
        total -= dropped_tokens
        if sized:
            total -= sep_tokens

    if dropped:
        dropped.sort(key=lambda x: x[0].priority)
        summary = ", ".join(f"{s.name}(p={s.priority}, t={t})" for s, t in dropped)
        logger.info(
            "context_budget: dropped %d section(s) to fit %d tokens — %s",
            len(dropped),
            budget_tokens,
            summary,
        )
        try:  # noqa: SIM105
            from backend.server.forge_logger import forge_logger  # noqa: PLC0415

            forge_logger.emit(
                "WARN", "CTX  ",
                f"dropped {len(dropped)} section(s) to fit {budget_tokens} tokens",
                detail=summary,
                budget_tokens=budget_tokens,
                dropped_count=len(dropped),
                dropped_sections=[s.name for s, _ in dropped],
            )
        except Exception:  # noqa: BLE001
            pass

    kept_texts = [s.text for s, _ in sized if s.text]
    return separator.join(kept_texts)


# Priority constants — use these to keep priorities comparable across builders.
P_TARGET = 100  # the node the gap is about
P_TARGET_PARENT = 95  # direct parent of the target
P_TRACE_TO = 90  # nodes the target traces to (requirements it implements)
P_PEER_ARTEFACT = 80  # CONTRACT for DESIGN, DESIGN for CASE, SUITE for CASE
P_SIBLING_FOR_DEDUP = 70  # same-type siblings when deciding duplicates
P_ANCESTOR_CHAIN = 60  # ancestor walk beyond direct parent
P_EXISTING_PEERS = 50  # existing LLRs / CASEs / DESIGNs listing
P_LANDSCAPE = 40  # all-HLRs / all-MODULEs for global view
P_WHITEPAPER_DIGEST = 30  # rationale + constraint + non_functional PARAs
P_BACKGROUND = 10  # anything else
