"""The vector store contract.

Two backends implement it: Chroma (local, embedded, no account) and Pinecone
(managed, named explicitly in the assignment). Neither leaks into the agents,
which depend only on this protocol, so switching is one environment variable.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from backend.app.rag.chunking import Chunk


class SearchHit(BaseModel):
    chunk_id: str
    text: str
    score: float
    metadata: dict[str, str]

    @property
    def document_name(self) -> str:
        return self.metadata.get("document_name", "enterprise document")

    @property
    def heading(self) -> str:
        return self.metadata.get("heading", "")

    @property
    def locator(self) -> str:
        location = self.metadata.get("location", "")
        if not location:
            return ""
        unit = "slide" if self.metadata.get("source_kind") == "pptx" else "page"
        return f"{unit} {location}"


class VectorStore(Protocol):
    """What the retrieval layer needs. Deliberately small."""

    def add(self, chunks: list[Chunk]) -> int:
        """Index the chunks. Returns how many were written."""
        ...

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        """Nearest chunks to the query, best first."""
        ...

    def delete_document(self, document_id: str) -> int:
        """Remove every chunk belonging to a document."""
        ...

    def count(self) -> int:
        """Total indexed chunks."""
        ...
