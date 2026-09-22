"""Keyless web search, so the demo runs with no search credentials.

Two sources are combined. The Instant Answer API gives clean, structured
abstracts but covers few queries; the HTML endpoint covers everything but has
to be parsed. Trying the API first and falling back keeps quality high where it
can and coverage complete everywhere else.

This is a fallback, not a match for a paid search API. ATELIER_SEARCH_PROVIDER
=tavily gives noticeably better research results.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from backend.app.core.logging import get_logger
from backend.app.search.provider import SearchResult

logger = get_logger(__name__)

INSTANT_ANSWER_URL = "https://api.duckduckgo.com/"
HTML_SEARCH_URL = "https://html.duckduckgo.com/html/"

# DuckDuckGo's HTML endpoint returns an empty page to an unrecognised agent.
USER_AGENT = "Mozilla/5.0 (compatible; Atelier/0.1; +https://github.com/)"


class DuckDuckGoSearchProvider:
    name = "duckduckgo"

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout = timeout_seconds

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        results = self._instant_answers(query, max_results)

        if len(results) < max_results:
            seen = {result.url for result in results}
            for result in self._html_results(query, max_results):
                if result.url not in seen:
                    results.append(result)
                    seen.add(result.url)
                if len(results) >= max_results:
                    break

        return [result.truncated() for result in results[:max_results]]

    def _instant_answers(self, query: str, max_results: int) -> list[SearchResult]:
        try:
            response = httpx.get(
                INSTANT_ANSWER_URL,
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("DuckDuckGo instant answers unavailable: %s", exc)
            return []

        results: list[SearchResult] = []

        abstract = payload.get("AbstractText") or ""
        if abstract and payload.get("AbstractURL"):
            results.append(
                SearchResult(
                    title=payload.get("Heading") or query,
                    url=payload["AbstractURL"],
                    content=abstract,
                    score=1.0,
                )
            )

        for topic in payload.get("RelatedTopics", []):
            if len(results) >= max_results:
                break
            if topic.get("FirstURL") and topic.get("Text"):
                results.append(
                    SearchResult(
                        title=topic["Text"].split(" - ")[0][:200],
                        url=topic["FirstURL"],
                        content=topic["Text"],
                        score=0.6,
                    )
                )

        return results

    def _html_results(self, query: str, max_results: int) -> list[SearchResult]:
        try:
            response = httpx.post(
                HTML_SEARCH_URL,
                data={"q": query},
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("DuckDuckGo HTML search failed: %s", exc)
            return []

        return _parse_html_results(response.text, max_results)


def _parse_html_results(html: str, max_results: int) -> list[SearchResult]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []

    for node in soup.select(".result")[: max_results * 2]:
        anchor = node.select_one(".result__a")
        if anchor is None:
            continue

        url = _clean_redirect(str(anchor.get("href") or ""))
        if not url:
            continue

        snippet = node.select_one(".result__snippet")
        results.append(
            SearchResult(
                title=anchor.get_text(strip=True)[:200],
                url=url,
                content=snippet.get_text(" ", strip=True) if snippet else "",
                score=0.5,
            )
        )

        if len(results) >= max_results:
            break

    return results


def _clean_redirect(href: str) -> str:
    """DuckDuckGo wraps results in /l/?uddg=<encoded target>."""
    if not href.startswith(("http://", "https://", "//")):
        return ""

    normalised = f"https:{href}" if href.startswith("//") else href
    parsed = urlparse(normalised)

    if parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [])
        return target[0] if target else ""

    return normalised
