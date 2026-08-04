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

from backend.crew.semantic_duplicate_check import create_semantic_checker


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


async def _judge(content: Any) -> tuple[bool, MagicMock]:
    graph = _graph()
    check = create_semantic_checker(_llm(content), graph)
    deleted = await check("PARA-0001", "some requirement text", "[PARA-0002] other")
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

        check = create_semantic_checker(llm, graph)
        deleted = await check("PARA-0001", "text", "[PARA-0002] other")

        assert deleted is True
