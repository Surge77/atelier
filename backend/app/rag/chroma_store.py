"""Chroma-backed vector store. The default: local, embedded, no account.

Embeddings are computed here and passed in explicitly rather than letting
Chroma pick its own embedding function, so that both backends index with the
same model and a corpus stays comparable across a backend switch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.core.errors import ProviderError
from backend.app.core.logging import get_logger
from backend.app.rag.chunking import Chunk
from backend.app.rag.embeddings import Embedder
from backend.app.rag.store import SearchHit

logger = get_logger(__name__)

COLLECTION_NAME = "atelier_enterprise"


class ChromaVectorStore:
    def __init__(self, persist_dir: Path, embedder: Embedder) -> None:
        self._persist_dir = persist_dir
        self._embedder = embedder
        self._collection: Any = None

    def _ensure_collection(self) -> Any:
        if self._collection is None:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._persist_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(self._persist_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def add(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0

        collection = self._ensure_collection()
        collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            embeddings=self._embedder.embed([chunk.text for chunk in chunks]),
            metadatas=[chunk.metadata for chunk in chunks],
        )
        return len(chunks)

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        collection = self._ensure_collection()
        if collection.count() == 0:
            return []

        try:
            result = collection.query(
                query_embeddings=self._embedder.embed([query]),
                n_results=min(top_k, collection.count()),
            )
        except Exception as exc:
            raise ProviderError(f"Chroma query failed: {exc}") from exc

        return _to_hits(result)

    def delete_document(self, document_id: str) -> int:
        collection = self._ensure_collection()
        before = collection.count()
        collection.delete(where={"document_id": document_id})
        return before - collection.count()

    def count(self) -> int:
        return int(self._ensure_collection().count())


def _to_hits(result: dict[str, Any]) -> list[SearchHit]:
    """Chroma returns parallel lists nested one level per query."""
    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]

    hits: list[SearchHit] = []
    for index, chunk_id in enumerate(ids):
        distance = distances[index] if index < len(distances) else 1.0
        metadata = metadatas[index] if index < len(metadatas) else {}
        hits.append(
            SearchHit(
                chunk_id=chunk_id,
                text=documents[index] if index < len(documents) else "",
                # Cosine distance, so similarity is 1 - distance.
                score=round(1.0 - float(distance), 4),
                metadata={str(k): str(v) for k, v in (metadata or {}).items()},
            )
        )
    return hits
