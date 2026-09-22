"""Local embeddings.

fastembed runs a quantised ONNX model on CPU with no torch dependency and no
API key, which is the point: a reviewer can clone this repository and the
retrieval layer works immediately. Swapping in a hosted embedding API would
mean implementing this one class.

The model downloads once (about 90 MB) on first use and is cached afterwards.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from backend.app.core.logging import get_logger

logger = get_logger(__name__)


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dimensions(self) -> int: ...


class FastEmbedEmbedder:
    """bge-small-en-v1.5 by default: 384 dimensions, strong for its size."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self.model_name = model_name
        self._model = None
        self._dimensions = 0

    def _ensure_model(self):  # type: ignore[no-untyped-def]
        # Loaded on first use rather than at import, so starting the app never
        # blocks on a model download.
        if self._model is None:
            from fastembed import TextEmbedding

            logger.info("Loading embedding model %s", self.model_name)
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors = [list(map(float, vector)) for vector in self._ensure_model().embed(texts)]
        if vectors:
            self._dimensions = len(vectors[0])
        return vectors

    @property
    def dimensions(self) -> int:
        if not self._dimensions:
            # One throwaway embedding is the only reliable way to learn the
            # width, and Pinecone needs it before an index can be created.
            self.embed(["dimension probe"])
        return self._dimensions


@lru_cache(maxsize=2)
def get_embedder(model_name: str = "BAAI/bge-small-en-v1.5") -> FastEmbedEmbedder:
    """Cached per model name; loading the model twice wastes time and memory."""
    return FastEmbedEmbedder(model_name)
