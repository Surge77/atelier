"""Search providers, mocked at the HTTP boundary — no real network calls."""

from __future__ import annotations

import httpx
import pytest
import respx

from backend.app.core.errors import ConfigurationError, ProviderError
from backend.app.search.duckduckgo import (
    HTML_SEARCH_URL,
    INSTANT_ANSWER_URL,
    DuckDuckGoSearchProvider,
)
from backend.app.search.tavily import SEARCH_URL, TavilySearchProvider

HTML_PAGE = """
<html><body>
  <div class="result">
    <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Freal.example%2Fone">
      First Result
    </a>
    <div class="result__snippet">A snippet describing the first result.</div>
  </div>
  <div class="result">
    <a class="result__a" href="https://direct.example/two">Second Result</a>
    <div class="result__snippet">A snippet describing the second result.</div>
  </div>
  <div class="result"><div class="result__snippet">No anchor here.</div></div>
</body></html>
"""


class TestTavily:
    def test_requires_an_api_key(self) -> None:
        with pytest.raises(ConfigurationError, match="TAVILY"):
            TavilySearchProvider("")

    @respx.mock
    def test_maps_results(self) -> None:
        respx.post(SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Generative AI in 2026",
                            "url": "https://example.com/ai",
                            "content": "Adoption accelerated across the enterprise.",
                            "score": 0.92,
                            "published_date": "2026-03-01",
                        }
                    ]
                },
            )
        )

        results = TavilySearchProvider("tvly-test-key").search("ai trends", 5)

        assert len(results) == 1
        assert results[0].url == "https://example.com/ai"
        assert results[0].score == 0.92
        assert results[0].published == "2026-03-01"

    @respx.mock
    def test_skips_results_with_no_url(self) -> None:
        respx.post(SEARCH_URL).mock(
            return_value=httpx.Response(200, json={"results": [{"title": "No link"}]})
        )

        assert TavilySearchProvider("tvly-test-key").search("q", 5) == []

    @respx.mock
    def test_truncates_long_content(self) -> None:
        respx.post(SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                json={"results": [{"title": "T", "url": "https://e.com", "content": "x" * 9000}]},
            )
        )

        assert len(TavilySearchProvider("tvly-test-key").search("q", 5)[0].content) == 1500

    @respx.mock
    def test_an_http_error_becomes_a_provider_error(self) -> None:
        respx.post(SEARCH_URL).mock(return_value=httpx.Response(500))

        with pytest.raises(ProviderError, match="Tavily"):
            TavilySearchProvider("tvly-test-key").search("q", 5)

    @respx.mock
    def test_malformed_json_becomes_a_provider_error(self) -> None:
        respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, text="not json"))

        with pytest.raises(ProviderError):
            TavilySearchProvider("tvly-test-key").search("q", 5)


class TestDuckDuckGo:
    @respx.mock
    def test_uses_the_instant_answer_abstract(self) -> None:
        respx.get(INSTANT_ANSWER_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Heading": "Generative AI",
                    "AbstractText": "Generative AI produces new content.",
                    "AbstractURL": "https://en.wikipedia.org/wiki/Generative_AI",
                    "RelatedTopics": [],
                },
            )
        )
        respx.post(HTML_SEARCH_URL).mock(return_value=httpx.Response(200, text=HTML_PAGE))

        results = DuckDuckGoSearchProvider().search("generative ai", 1)

        assert results[0].title == "Generative AI"
        assert "wikipedia" in results[0].url

    @respx.mock
    def test_falls_back_to_html_results(self) -> None:
        respx.get(INSTANT_ANSWER_URL).mock(return_value=httpx.Response(200, json={}))
        respx.post(HTML_SEARCH_URL).mock(return_value=httpx.Response(200, text=HTML_PAGE))

        results = DuckDuckGoSearchProvider().search("anything", 5)

        assert len(results) == 2
        assert results[0].title == "First Result"

    @respx.mock
    def test_unwraps_the_duckduckgo_redirect(self) -> None:
        respx.get(INSTANT_ANSWER_URL).mock(return_value=httpx.Response(200, json={}))
        respx.post(HTML_SEARCH_URL).mock(return_value=httpx.Response(200, text=HTML_PAGE))

        results = DuckDuckGoSearchProvider().search("anything", 5)

        assert results[0].url == "https://real.example/one"

    @respx.mock
    def test_skips_a_result_with_no_anchor(self) -> None:
        respx.get(INSTANT_ANSWER_URL).mock(return_value=httpx.Response(200, json={}))
        respx.post(HTML_SEARCH_URL).mock(return_value=httpx.Response(200, text=HTML_PAGE))

        assert len(DuckDuckGoSearchProvider().search("anything", 10)) == 2

    @respx.mock
    def test_does_not_repeat_a_url_across_both_sources(self) -> None:
        respx.get(INSTANT_ANSWER_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Heading": "Second Result",
                    "AbstractText": "Already found via instant answers.",
                    "AbstractURL": "https://direct.example/two",
                    "RelatedTopics": [],
                },
            )
        )
        respx.post(HTML_SEARCH_URL).mock(return_value=httpx.Response(200, text=HTML_PAGE))

        urls = [r.url for r in DuckDuckGoSearchProvider().search("anything", 5)]

        assert len(urls) == len(set(urls))

    @respx.mock
    def test_both_sources_failing_returns_empty_rather_than_raising(self) -> None:
        """Keyless search is best-effort; a failure must not end the run."""
        respx.get(INSTANT_ANSWER_URL).mock(return_value=httpx.Response(503))
        respx.post(HTML_SEARCH_URL).mock(return_value=httpx.Response(503))

        assert DuckDuckGoSearchProvider().search("anything", 5) == []

    @respx.mock
    def test_respects_max_results(self) -> None:
        respx.get(INSTANT_ANSWER_URL).mock(return_value=httpx.Response(200, json={}))
        respx.post(HTML_SEARCH_URL).mock(return_value=httpx.Response(200, text=HTML_PAGE))

        assert len(DuckDuckGoSearchProvider().search("anything", 1)) == 1
