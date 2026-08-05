"""Verdict parsing for the semantic duplicate check.

Found during a live build. The trace showed:

    LLM response ... 126498ms tool_calls=0 content_len=0 out=334t
    DECIDE [semantic_dedup] UNIQUE —

The model spent its entire output budget on reasoning tokens and returned no
content. Because the parser was ``text.upper().startswith("DUPLICATE")`` and
``"".startswith(...)`` is ``False``, that failure was recorded as a confident
UNIQUE verdict — indistinguishable in the trace from the model genuinely judging
the node distinct.

The direction of the fallback was safe (keeping a node beats deleting a real
requirement), but conflating "the model said unique" with "the model said
nothing" hides a real failure mode: duplicates survive, the graph inflates, and
every downstream phase pays for them, with nothing in the logs to show why.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.quality.semantic_duplicate_check import create_semantic_checker


def _llm(content: Any) -> MagicMock:
    llm = MagicMock()
    response = MagicMock()
    response.content = content
    llm.ainvoke = AsyncMock(return_value=response)
    return llm


def _graph() -> MagicMock:
    graph = MagicMock()
    graph.delete_node = AsyncMock()
    return graph


def _llm_seq(*contents: str) -> MagicMock:
    """LLM mock returning one response per call, in order."""
    llm = MagicMock()
    responses = []
    for c in contents:
        r = MagicMock()
        r.content = c
        responses.append(r)
    llm.ainvoke = AsyncMock(side_effect=responses)
    return llm


async def _judge(content: Any) -> tuple[bool, MagicMock]:
    graph = _graph()
    check = create_semantic_checker(_llm(content), graph, {})
    deleted = await check("PARA-0001", "some requirement text", "[PARA-0002] related text")
    return deleted, graph


class TestGenuineVerdicts:
    async def test_duplicate_verdict_deletes_the_node(self) -> None:
        deleted, graph = await _judge("DUPLICATE - says the same as PARA-0002")
        assert deleted is True
        graph.delete_node.assert_awaited_once_with("PARA-0001")

    async def test_unique_verdict_keeps_the_node(self) -> None:
        deleted, graph = await _judge("UNIQUE - specifies a distinct obligation")
        assert deleted is False
        graph.delete_node.assert_not_awaited()

    @pytest.mark.parametrize("text", ["duplicate - lowercase", "  DUPLICATE - padded  "])
    async def test_verdict_parsing_is_case_and_whitespace_tolerant(self, text: str) -> None:
        deleted, graph = await _judge(text)
        assert deleted is True


class TestUnusableResponses:
    """The regression this module exists for: no answer is not a verdict."""

    @pytest.mark.parametrize(
        ("content", "label"),
        [
            ("", "empty string"),
            ("   \n  ", "whitespace only"),
            ("I need more context to decide.", "prose with no verdict"),
            ("The node PARA-0001 is interesting.", "prose mentioning the node"),
        ],
    )
    async def test_unusable_response_never_deletes(
        self, content: str, label: str
    ) -> None:
        """Deleting a requirement on no evidence is the unacceptable outcome."""
        deleted, graph = await _judge(content)

        assert deleted is False, f"deleted a node on an unusable verdict ({label})"
        graph.delete_node.assert_not_awaited()

    async def test_unusable_response_is_logged_distinctly_from_unique(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty response must not be recorded as a UNIQUE verdict.

        This is the actual defect: without a distinct label there is no way,
        from the trace alone, to tell a working dedup pass from one where every
        call silently failed.
        """
        decisions: list[tuple[str, str]] = []

        from backend.server import forge_logger as fl_module

        def capture(kind: str, label: str, detail: str = "", **kwargs: Any) -> None:
            decisions.append((kind, label))

        monkeypatch.setattr(fl_module.forge_logger, "decision", capture)

        await _judge("")

        assert decisions, "no decision was recorded at all"
        kinds = [k for k, _ in decisions]
        labels = [lbl for _, lbl in decisions]
        assert "semantic_dedup" in kinds
        assert "UNIQUE" not in labels, (
            "an empty LLM response was recorded as a UNIQUE verdict — the exact "
            "silent fallback this test guards against"
        )
        assert "UNPARSEABLE" in labels

    async def test_genuine_unique_still_labelled_unique(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The distinct label must not swallow real verdicts too."""
        decisions: list[str] = []

        from backend.server import forge_logger as fl_module

        def capture(kind: str, label: str, detail: str = "", **kwargs: Any) -> None:
            decisions.append(label)

        monkeypatch.setattr(fl_module.forge_logger, "decision", capture)

        await _judge("UNIQUE - genuinely distinct")

        assert "UNIQUE" in decisions
        assert "UNPARSEABLE" not in decisions


class TestResponseShapes:
    async def test_handles_a_response_without_a_content_attribute(self) -> None:
        """Some providers return a bare string rather than a message object."""
        graph = _graph()
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value="DUPLICATE - same thing")

        check = create_semantic_checker(llm, graph, {})
        deleted = await check("PARA-0001", "text", "[PARA-0002] related text")

        assert deleted is True


class TestDoubleConfirmation:
    """A single DUPLICATE verdict must never delete requirement text.

    Live-trace regression (merge_sort.log): PARA-0242 — "Do not delegate to
    the built-in list.sort or sorted" — was hard-deleted on one nondeterministic
    DUPLICATE verdict. Deletion now requires the same DUPLICATE verdict from
    two independent LLM calls.
    """

    async def test_confirmed_duplicate_deletes_after_two_calls(self) -> None:
        graph = _graph()
        llm = _llm_seq("DUPLICATE - same as PARA-0002", "DUPLICATE - same as PARA-0002")
        check = create_semantic_checker(llm, graph, {})

        deleted = await check("PARA-0001", "text", "[PARA-0002] related text")

        assert deleted is True
        assert llm.ainvoke.await_count == 2
        graph.delete_node.assert_awaited_once_with("PARA-0001")

    async def test_unconfirmed_duplicate_keeps_the_node(self) -> None:
        """First call says DUPLICATE, confirmation says UNIQUE → keep."""
        graph = _graph()
        llm = _llm_seq("DUPLICATE - same as PARA-0002", "UNIQUE - distinct obligation")
        check = create_semantic_checker(llm, graph, {})

        deleted = await check("PARA-0001", "text", "[PARA-0002] related text")

        assert deleted is False
        assert llm.ainvoke.await_count == 2
        graph.delete_node.assert_not_awaited()

    async def test_unparseable_confirmation_keeps_the_node(self) -> None:
        graph = _graph()
        llm = _llm_seq("DUPLICATE - same as PARA-0002", "")
        check = create_semantic_checker(llm, graph, {})

        deleted = await check("PARA-0001", "text", "[PARA-0002] related text")

        assert deleted is False
        graph.delete_node.assert_not_awaited()

    async def test_unique_verdict_makes_only_one_call(self) -> None:
        graph = _graph()
        llm = _llm_seq("UNIQUE - distinct obligation")
        check = create_semantic_checker(llm, graph, {})

        deleted = await check("PARA-0001", "text", "[PARA-0002] related text")

        assert deleted is False
        assert llm.ainvoke.await_count == 1


class TestDeterministicPrescreen:
    """Clearly-dissimilar pairs never reach the LLM judge (design §7.4).

    The prescreen is a cheap stdlib token-overlap check. It only reduces the
    candidate pairs that get judged — it never authorises deletion, and the
    two-call double confirmation still gates every actual deletion.
    """

    async def test_dissimilar_pair_skips_the_llm_and_keeps_the_node(self) -> None:
        graph = _graph()
        llm = _llm("DUPLICATE - should never be consulted")
        check = create_semantic_checker(llm, graph, {})

        deleted = await check(
            "PARA-0001",
            "Sort the integer list into ascending order.",
            "[PARA-0002] Render weather forecasts as coloured umbrella icons.",
        )

        assert deleted is False
        llm.ainvoke.assert_not_awaited()
        graph.delete_node.assert_not_awaited()

    async def test_similar_pair_still_reaches_the_judge(self) -> None:
        graph = _graph()
        llm = _llm("UNIQUE - distinct obligation")
        check = create_semantic_checker(llm, graph, {})

        deleted = await check(
            "PARA-0001",
            "The system shall sort the integer list ascending.",
            "[PARA-0002] The system shall sort the integer list descending.",
        )

        assert deleted is False
        assert llm.ainvoke.await_count == 1

    async def test_one_similar_peer_among_dissimilar_triggers_judgement(self) -> None:
        graph = _graph()
        llm = _llm("UNIQUE - distinct obligation")
        check = create_semantic_checker(llm, graph, {})

        siblings = (
            "  [PARA-0002] Render weather forecasts as coloured umbrella icons.\n"
            "  [PARA-0003] The system shall sort the integer list descending."
        )
        await check("PARA-0001", "The system shall sort the integer list ascending.", siblings)

        assert llm.ainvoke.await_count == 1

    async def test_double_confirmation_still_gates_deletion_after_prescreen(self) -> None:
        """The prescreen passing a pair through changes nothing about deletion
        safety: it still takes 2/2 DUPLICATE verdicts."""
        graph = _graph()
        llm = _llm_seq("DUPLICATE - same as PARA-0002", "DUPLICATE - same as PARA-0002")
        check = create_semantic_checker(llm, graph, {})

        deleted = await check(
            "PARA-0001",
            "The system shall sort the integer list ascending.",
            "[PARA-0002] The system shall sort the integer list ascending.",
        )

        assert deleted is True
        assert llm.ainvoke.await_count == 2
        graph.delete_node.assert_awaited_once_with("PARA-0001")

    async def test_unparseable_peers_text_is_conservatively_judged(self) -> None:
        """Peers text without [ID] blocks cannot be prescreened — send to the
        judge rather than silently skipping."""
        graph = _graph()
        llm = _llm("UNIQUE - distinct obligation")
        check = create_semantic_checker(llm, graph, {})

        await check("PARA-0001", "totally unrelated words here", "free-form peers blob")

        assert llm.ainvoke.await_count == 1

    def test_prescreen_function_scores_overlap(self) -> None:
        from backend.quality.semantic_duplicate_check import prescreen_similar_peers

        assert prescreen_similar_peers(
            "The system shall sort the list.",
            "[PARA-0002] The system shall sort the list quickly.",
        )
        assert not prescreen_similar_peers(
            "alpha beta gamma delta",
            "[PARA-0002] epsilon zeta eta theta",
        )

    def test_prescreen_empty_content_never_matches(self) -> None:
        from backend.quality.semantic_duplicate_check import prescreen_similar_peers

        assert not prescreen_similar_peers("", "[PARA-0002] some peer text")


class TestVerdictCache:
    """A prior UNIQUE verdict is sticky for unchanged content.

    The pipeline re-loops after any deletion (up to 12 cycles), re-judging
    survivors. PARA-0242 was judged UNIQUE at 22:38 and DUPLICATE for
    identical content at 22:57. With the cache, an unchanged node that was
    once judged UNIQUE is never re-litigated.
    """

    async def test_unique_verdict_is_sticky_for_unchanged_content(self) -> None:
        graph = _graph()
        cache: dict[tuple[str, str], str] = {}
        llm = _llm_seq("UNIQUE - distinct obligation")
        check = create_semantic_checker(llm, graph, cache)

        first = await check("PARA-0242", "Do not delegate to list.sort", "[PARA-0001] text delegate to list.sort")
        # Re-judging identical content must not call the LLM again.
        second = await check("PARA-0242", "Do not delegate to list.sort", "[PARA-0001] text delegate to list.sort")

        assert first is False
        assert second is False
        assert llm.ainvoke.await_count == 1
        graph.delete_node.assert_not_awaited()

    async def test_cache_survives_across_checker_instances(self) -> None:
        """The cache is flow-scoped: a fresh checker built for the next
        pipeline cycle shares the same cache dict."""
        graph = _graph()
        cache: dict[tuple[str, str], str] = {}
        llm1 = _llm_seq("UNIQUE - distinct obligation")
        await create_semantic_checker(llm1, graph, cache)(
            "PARA-0242", "unchanged text", "[PARA-0001] text delegate to list.sort"
        )

        llm2 = _llm_seq("DUPLICATE - flip-flop", "DUPLICATE - flip-flop")
        deleted = await create_semantic_checker(llm2, graph, cache)(
            "PARA-0242", "unchanged text", "[PARA-0001] text delegate to list.sort"
        )

        assert deleted is False
        llm2.ainvoke.assert_not_awaited()
        graph.delete_node.assert_not_awaited()

    async def test_changed_content_is_rejudged(self) -> None:
        graph = _graph()
        cache: dict[tuple[str, str], str] = {}
        llm = _llm_seq("UNIQUE - distinct obligation", "UNIQUE - still distinct")
        check = create_semantic_checker(llm, graph, cache)

        await check("PARA-0242", "original text", "[PARA-0001] text delegate to list.sort")
        await check("PARA-0242", "edited text", "[PARA-0001] text delegate to list.sort")

        assert llm.ainvoke.await_count == 2

    async def test_unparseable_verdict_is_not_cached(self) -> None:
        """No answer is not a verdict — the node may be re-judged later."""
        graph = _graph()
        cache: dict[tuple[str, str], str] = {}
        llm = _llm_seq("", "UNIQUE - distinct obligation")
        check = create_semantic_checker(llm, graph, cache)

        await check("PARA-0001", "text", "[PARA-0002] related text")
        await check("PARA-0001", "text", "[PARA-0002] related text")

        assert llm.ainvoke.await_count == 2

    async def test_unconfirmed_duplicate_becomes_sticky_unique(self) -> None:
        """A DUPLICATE/UNIQUE disagreement resolves to UNIQUE and sticks."""
        graph = _graph()
        cache: dict[tuple[str, str], str] = {}
        llm = _llm_seq("DUPLICATE - overlap", "UNIQUE - distinct")
        check = create_semantic_checker(llm, graph, cache)

        await check("PARA-0001", "text", "[PARA-0002] related text")
        deleted = await check("PARA-0001", "text", "[PARA-0002] related text")

        assert deleted is False
        assert llm.ainvoke.await_count == 2
        graph.delete_node.assert_not_awaited()
