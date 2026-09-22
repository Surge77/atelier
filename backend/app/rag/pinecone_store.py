"""Pinecone-backed vector store.

The assignment names Pinecone explicitly, so this adapter ships. It is not the
default, because requiring an account would mean the repository does not run
for anyone who clones it. Set ATELIER_VECTOR_BACKEND=pinecone to use it.

Pinecone metadata has to carry the chunk text, since the API returns vectors
and metadata but not the original document body.
"""

from __future__ import annotations

from typing import Any

from backend.app.core.errors import ConfigurationError, ProviderError
from backend.app.core.logging import get_logger
from backend.app.rag.chunking import Chunk
from backend.app.rag.embeddings import Embedder
from backend.app.rag.store import SearchHit

logger = get_logger(__name__)

NAMESPACE = "atelier"
UPSERT_BATCH = 100


class PineconeVectorStore:
    def __init__(
        self,
        api_key: str,
        index_name: str,
        embedder: Embedder,
        cloud: str = "aws",
        region: str = "us-east-1",
    ) -> None:
        if not api_key:
            raise ConfigurationError(
                "ATELIER_PINECONE_API_KEY is not set.",
                public_message=(
                    "Pinecone is selected but no API key is configured. "
                    "Set ATELIER_PINECONE_API_KEY or use ATELIER_VECTOR_BACKEND=chroma."
                ),
            )

        self._api_key = api_key
        self._index_name = index_name
        self._embedder = embedder
        self._cloud = cloud
        self._region = region
        self._index: Any = None

    def _ensure_index(self) -> Any:
        if self._index is not None:
            return self._index

        try:
            # Optional extra: absent unless `pip install pinecone` was run.
            from pinecone import Pinecone, ServerlessSpec  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConfigurationError(
                "The pinecone package is not installed.",
                public_message="Install the Pinecone extra: pip install pinecone",
            ) from exc

        client = Pinecone(api_key=self._api_key)
        existing = {index["name"] for index in client.list_indexes()}

        if self._index_name not in existing:
            logger.info("Creating Pinecone index %s", self._index_name)
            client.create_index(
                name=self._index_name,
                dimension=self._embedder.dimensions,
                metric="cosine",
                spec=ServerlessSpec(cloud=self._cloud, region=self._region),
            )

        self._index = client.Index(self._index_name)
        return self._index

    def add(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0

        index = self._ensure_index()
        vectors = self._embedder.embed([chunk.text for chunk in chunks])

        payload = [
            {
                "id": chunk.id,
                "values": vectors[position],
                # Pinecone returns metadata but not documents, so the text
                # rides along here.
                "metadata": {**chunk.metadata, "text": chunk.text},
            }
            for position, chunk in enumerate(chunks)
        ]

        for start in range(0, len(payload), UPSERT_BATCH):
            index.upsert(vectors=payload[start : start + UPSERT_BATCH], namespace=NAMESPACE)

        return len(payload)

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        index = self._ensure_index()

        try:
            response = index.query(
                vector=self._embedder.embed([query])[0],
                top_k=top_k,
                namespace=NAMESPACE,
                include_metadata=True,
            )
        except Exception as exc:
            raise ProviderError(f"Pinecone query failed: {exc}") from exc

        return [
            SearchHit(
                chunk_id=str(match["id"]),
                text=str(match.get("metadata", {}).get("text", "")),
                score=round(float(match.get("score", 0.0)), 4),
                metadata={
                    str(k): str(v)
                    for k, v in (match.get("metadata") or {}).items()
                    if k != "text"
                },
            )
            for match in response.get("matches", [])
        ]

    def delete_document(self, document_id: str) -> int:
        index = self._ensure_index()
        index.delete(filter={"document_id": document_id}, namespace=NAMESPACE)
        # Pinecone deletes asynchronously and reports no count.
        return 0

    def count(self) -> int:
        stats = self._ensure_index().describe_index_stats()
        namespaces = stats.get("namespaces", {})
        return int(namespaces.get(NAMESPACE, {}).get("vector_count", 0))
