"""Tests for WebFetchTool with mocked httpx (tools/web_fetch.py)."""

from unittest.mock import MagicMock, patch

import pytest

from backend.tools.base import ToolPermissionError
from backend.tools.web_fetch import WebFetchTool, _extract_text

# ── _extract_text helper ──────────────────────────────────────────────────────

def test_extract_text_removes_script_and_style_tags() -> None:
    assert "alert" not in _extract_text("<script>alert('x')</script><p>Hello</p>")
    assert "color" not in _extract_text("<style>.foo { color: red; }</style><p>Content</p>")


def test_extract_text_strips_tags_and_collapses_whitespace() -> None:
    text = _extract_text("<div><p>Plain <b>text</b></p></div>")
    assert "<" not in text
    assert "Plain" in text

    text = _extract_text("<p>Line 1</p>\n\n\n\n<p>Line 2</p>")
    assert "\n\n\n" not in text


# ── WebFetchTool — allowlist ──────────────────────────────────────────────────

@pytest.mark.parametrize(("allowlist", "url", "expected"), [
    ([], "https://example.com", True),
    (["example.com"], "https://example.com/path", True),
    (["example.com"], "https://evil.com", False),
])
def test_allowlist_rules(allowlist: list[str], url: str, expected: bool) -> None:
    assert WebFetchTool(allowlist=allowlist)._is_allowed(url) is expected


def test_execute_raises_on_blocked_domain() -> None:
    with pytest.raises(ToolPermissionError):
        WebFetchTool(allowlist=["allowed.com"])._execute(url="https://evil.com")


# ── WebFetchTool — successful fetch ──────────────────────────────────────────

def _mock_client(text: str, content_type: str = "text/html") -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.headers = {"content-type": content_type}
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get.return_value = resp
    return client


def test_execute_fetches_html_and_raw_text() -> None:
    tool = WebFetchTool(allowlist=[])
    with patch("backend.tools.web_fetch.httpx.Client", return_value=_mock_client("<p>Hello from the web</p>")):
        assert "Hello from the web" in tool._execute(url="https://example.com")

    with patch("backend.tools.web_fetch.httpx.Client", return_value=_mock_client("plain text", content_type="text/plain")):
        assert "plain text" in tool._execute(url="https://example.com/data.txt")


def test_execute_respects_max_chars() -> None:
    tool = WebFetchTool(allowlist=[])
    with patch("backend.tools.web_fetch.httpx.Client", return_value=_mock_client("<p>" + "X" * 10000 + "</p>")):
        assert len(tool._execute(url="https://example.com", max_chars=100)) <= 100


# ── WebFetchTool — error paths ────────────────────────────────────────────────

def test_execute_handles_http_errors() -> None:
    import httpx
    tool = WebFetchTool(allowlist=[])

    mock_response = MagicMock(status_code=404)
    client = _mock_client("")
    client.get.side_effect = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response)
    with patch("backend.tools.web_fetch.httpx.Client", return_value=client):
        result = tool._execute(url="https://example.com/missing")
    assert "404" in result or "HTTP ERROR" in result

    client2 = _mock_client("")
    client2.get.side_effect = httpx.TimeoutException("timeout")
    with patch("backend.tools.web_fetch.httpx.Client", return_value=client2):
        result = tool._execute(url="https://example.com")
    assert "timed out" in result.lower() or "timeout" in result.lower()

    client3 = _mock_client("")
    client3.get.side_effect = RuntimeError("network error")
    with patch("backend.tools.web_fetch.httpx.Client", return_value=client3):
        assert "ERROR" in tool._execute(url="https://example.com")
