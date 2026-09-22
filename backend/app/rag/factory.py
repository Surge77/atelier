"""Selects the configured vector store."""

from __future__ import annotations

from backend.app.config import Settings, get_settings
from backend.app.core.errors import ConfigurationError
from backend.app.rag.embeddings import get_embedder
from backend.app.rag.store import VectorStore

_store: VectorStore | None = None


def build_store(settings: Settings | None = None) -> VectorStore:
    settings = settings or get_settings()
    embedder = get_embedder(settings.embedding_model)

    if settings.vector_backend == "chroma":
        from backend.app.rag.chroma_store import ChromaVectorStore

        return ChromaVectorStore(settings.vector_dir, embedder)

    if settings.vector_backend == "pinecone":
        from backend.app.rag.pinecone_store import PineconeVectorStore

        return PineconeVectorStore(
            api_key=settings.pinecone_api_key,
            index_name=settings.pinecone_index,
            embedder=embedder,
            cloud=settings.pinecone_cloud,
            region=settings.pinecone_region,
        )

    raise ConfigurationError(f"Unknown vector backend {settings.vector_backend!r}.")


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = build_store()
    return _store


def set_store(store: VectorStore | None) -> None:
    """Inject a store. Used by tests to avoid loading an embedding model."""
    global _store
    _store = store
