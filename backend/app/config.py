"""Application settings.

Every value is read from the environment (prefix ``ATELIER_``) or ``.env``.
Nothing is hard-coded and no secret ever has a default, so a missing key fails
loudly at the point of use rather than silently falling back to someone else's
credentials.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LlmProviderName = Literal["groq", "openai", "anthropic"]
VectorBackendName = Literal["chroma", "pinecone"]
SearchProviderName = Literal["duckduckgo", "tavily"]

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Only formats the ingestion layer can actually parse.
ALLOWED_UPLOAD_SUFFIXES = frozenset(
    {".docx", ".pdf", ".pptx", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".md"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATELIER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM -------------------------------------------------------------
    llm_provider: LlmProviderName = "groq"

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    # Groq's catalogue carries no vision model at present, so scanned-PDF
    # and image OCR needs ATELIER_LLM_PROVIDER=openai or anthropic.
    groq_vision_model: str = "openai/gpt-oss-120b"

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_vision_model: str = "gpt-4.1-mini"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"
    anthropic_vision_model: str = "claude-haiku-4-5-20251001"

    llm_temperature: float = 0.3
    llm_max_tokens: int = 8000
    llm_timeout_seconds: float = 90.0
    llm_max_retries: int = 3

    # --- Retrieval -------------------------------------------------------
    vector_backend: VectorBackendName = "chroma"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    rag_top_k: int = 6
    chunk_tokens: int = 320
    chunk_overlap_tokens: int = 60

    pinecone_api_key: str = ""
    pinecone_index: str = "atelier"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"

    # --- Web research ----------------------------------------------------
    search_provider: SearchProviderName = "duckduckgo"
    tavily_api_key: str = ""
    search_max_results: int = 6
    search_timeout_seconds: float = 20.0

    # --- Server ----------------------------------------------------------
    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    api_key: str = ""
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:8501", "http://127.0.0.1:8501"]
    )
    rate_limit_per_minute: int = 60

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept ``a,b,c`` from the environment as well as a real list."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    # --- Derived paths ---------------------------------------------------
    # Kept as properties rather than fields so that overriding data_dir in a
    # test moves every subdirectory with it.

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def vector_dir(self) -> Path:
        return self.data_dir / "vectors"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'atelier.db').as_posix()}"

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.uploads_dir, self.artifacts_dir, self.vector_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton. Cleared by tests via ``cache_clear()``."""
    return Settings()
