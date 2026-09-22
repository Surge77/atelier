"""The web search contract."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

MAX_CONTENT_CHARS = 1500


class SearchResult(BaseModel):
    title: str
    url: str
    # The passage the generator reads. Providers differ in how much they give.
    content: str = ""
    score: float = 0.0
    published: str | None = None

    def truncated(self) -> SearchResult:
        return self.model_copy(update={"content": self.content[:MAX_CONTENT_CHARS]})


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        """Results for a query, best first. Returns empty rather than raising
        when the query simply finds nothing."""
        ...
