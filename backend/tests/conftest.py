"""Shared pytest fixtures.

Every test runs against a throwaway data directory and a fresh SQLite file, so
nothing leaks between tests and the developer's real ``./data`` is never
touched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api import deps
from backend.app.config import Settings, get_settings
from backend.app.db import session as db_session
from backend.app.llm import factory as llm_factory
from backend.app.rag import factory as rag_factory
from backend.app.search import factory as search_factory

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point settings at a temp directory and clear all process-wide caches."""
    monkeypatch.setenv("ATELIER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ATELIER_LLM_PROVIDER", "groq")
    monkeypatch.setenv("ATELIER_VECTOR_BACKEND", "chroma")
    monkeypatch.setenv("ATELIER_SEARCH_PROVIDER", "duckduckgo")
    monkeypatch.setenv("ATELIER_API_KEY", "")
    # Env vars outrank .env in pydantic-settings, so a developer's real .env
    # cannot change a test outcome.

    _reset_singletons()
    yield
    _reset_singletons()


def _reset_singletons() -> None:
    """Every process-wide cache, cleared.

    The vector store in particular holds a path into the previous test's temp
    directory, so leaving it cached would leak a corpus between tests.
    """
    get_settings.cache_clear()
    db_session.reset_db()
    deps.reset_limiter()
    rag_factory.set_store(None)
    search_factory.set_search_provider(None)
    llm_factory.set_provider(None)


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """TestClient with the app's lifespan run, so the database exists."""
    from backend.app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
