"""Citations and evidence: the traceability spine.

Every factual claim in a generated artifact carries citation ids, every id
resolves to a ``Citation``, and every ``Citation`` names either a web page or
an enterprise document with its locator. That chain is what makes the
assignment's "source citations and traceability" checkable rather than
decorative — the validation agent fails a plan whose ids do not resolve.

Ids are short and human-shaped (``S1``, ``S2``) on purpose: the model has to
emit them inline while writing, and a UUID in a prompt is both expensive and
easy for a model to corrupt.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

CitationKind = Literal["web", "enterprise"]

MAX_SNIPPET_CHARS = 600


class Citation(BaseModel):
    id: str
    kind: CitationKind
    title: str
    # Set for web sources.
    url: str | None = None
    # Set for enterprise sources: the document the chunk came from.
    source_document: str | None = None
    # Where inside the source, e.g. "page 4" or "slide 2".
    locator: str | None = None
    snippet: str = ""
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def reference_line(self) -> str:
        """One rendered line for a References section or Sources slide."""
        if self.kind == "web":
            return f"[{self.id}] {self.title} — {self.url}"

        where = f", {self.locator}" if self.locator else ""
        return f"[{self.id}] {self.title} ({self.source_document}{where})"


class Evidence(BaseModel):
    """A retrieved passage plus the citation it must be attributed to."""

    citation_id: str
    content: str
    score: float = 0.0

    def as_prompt_block(self) -> str:
        return f"[{self.citation_id}] {self.content}"


class ResearchFindings(BaseModel):
    """What a research agent returns: passages to read and sources to cite."""

    evidence: list[Evidence] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    queries_used: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.evidence

    def as_prompt_block(self) -> str:
        if self.is_empty():
            return "No sources were retrieved."
        return "\n\n".join(item.as_prompt_block() for item in self.evidence)


class CitationAllocator:
    """Hands out short ids and keeps them unique across a whole run.

    Web research and RAG both produce sources in the same turn, so id
    allocation has to be shared or the two would collide on ``S1``.
    """

    def __init__(self, prefix: str = "S", start: int = 1) -> None:
        self._prefix = prefix
        self._next = start
        self.citations: list[Citation] = []

    def add(
        self,
        kind: CitationKind,
        title: str,
        snippet: str = "",
        url: str | None = None,
        source_document: str | None = None,
        locator: str | None = None,
    ) -> Citation:
        citation = Citation(
            id=f"{self._prefix}{self._next}",
            kind=kind,
            title=title.strip() or "Untitled source",
            url=url,
            source_document=source_document,
            locator=locator,
            snippet=snippet.strip()[:MAX_SNIPPET_CHARS],
        )
        self._next += 1
        self.citations.append(citation)
        return citation
