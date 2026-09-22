"""Tavily web search.

Called over plain HTTP rather than through the tavily-python SDK: the request
is one POST, and going direct keeps the dependency optional and the retry and
timeout behaviour consistent with the rest of the app.
"""

from __future__ import annotations

import httpx

from backend.app.core.errors import ConfigurationError, ProviderError
from backend.app.core.logging import get_logger
from backend.app.search.provider import SearchResult

logger = get_logger(__name__)

SEARCH_URL = "https://api.tavily.com/search"


class TavilySearchProvider:
    name = "tavily"

    def __init__(self, api_key: str, timeout_seconds: float = 20.0) -> None:
        if not api_key:
            raise ConfigurationError(
                "ATELIER_TAVILY_API_KEY is not set.",
                public_message=(
                    "Tavily is selected but no API key is configured. "
                    "Set ATELIER_TAVILY_API_KEY or use ATELIER_SEARCH_PROVIDER=duckduckgo."
                ),
            )
        self._api_key = api_key
        self._timeout = timeout_seconds

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        try:
            response = httpx.post(
                SEARCH_URL,
                json={
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "advanced",
                    "include_answer": False,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Tavily search failed: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(f"Tavily returned malformed JSON: {exc}") from exc

        return [
            SearchResult(
                title=str(item.get("title") or query)[:200],
                url=str(item.get("url") or ""),
                content=str(item.get("content") or ""),
                score=float(item.get("score") or 0.0),
                published=item.get("published_date"),
            ).truncated()
            for item in payload.get("results", [])
            if item.get("url")
        ]
