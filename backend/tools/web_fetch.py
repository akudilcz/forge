"""web_fetch — fetch a URL and return its text content."""

from __future__ import annotations

import re

import httpx
from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool, ToolPermissionError


def _extract_text(html: str) -> str:
    """Very lightweight HTML → plain text extraction."""
    # Remove script and style blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s{3,}", "\n\n", text)
    return text.strip()


class _Args(BaseModel):
    url: str = Field(description="URL to fetch.")
    max_chars: int = Field(default=8000, description="Maximum characters to return.")


class WebFetchTool(ForgeTool):
    """Fetch a URL and return its plain-text content, restricted to a configured domain allowlist.

    HTML responses are stripped of tags before being returned.
    """

    name: str = "web_fetch"
    description: str = (
        "Fetch a URL and return its text content. "
        "Only domains in the configured allowlist are permitted. "
        "Returns up to max_chars of plain text."
    )
    args_schema: type[BaseModel] = _Args

    _allowlist: list[str] = []

    def __init__(self, allowlist: list[str]) -> None:
        """Args:
            allowlist: Domain substrings that are permitted; empty list allows all domains.
        """
        super().__init__()
        object.__setattr__(self, "_allowlist", allowlist)

    def _is_allowed(self, url: str) -> bool:
        """Return True if url contains any domain from the allowlist (or allowlist is empty)."""
        if not self._allowlist:
            return True
        for domain in self._allowlist:
            if domain in url:
                return True
        return False

    def _execute(self, url: str, max_chars: int = 8000) -> str:
        """Fetch url and return up to max_chars of plain text.

        Raises:
            ToolPermissionError: If url's domain is not in the allowlist.
        """
        if not self._is_allowed(url):
            raise ToolPermissionError(
                f"Domain not in web_fetch allowlist. "
                f"Allowed: {self._allowlist}"
            )
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                response = client.get(url, headers={"User-Agent": "FORGE/1.0"})
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "html" in content_type:
                    text = _extract_text(response.text)
                else:
                    text = response.text
                return text[:max_chars]
        except httpx.HTTPStatusError as exc:
            return f"HTTP ERROR {exc.response.status_code}: {url}"
        except httpx.TimeoutException:
            return f"ERROR: Request timed out: {url}"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR fetching {url}: {exc}"
