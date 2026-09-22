"""Selects the configured search provider."""

from __future__ import annotations

from backend.app.config import Settings, get_settings
from backend.app.core.errors import ConfigurationError
from backend.app.search.provider import SearchProvider

_provider: SearchProvider | None = None


def build_search_provider(settings: Settings | None = None) -> SearchProvider:
    settings = settings or get_settings()

    if settings.search_provider == "duckduckgo":
        from backend.app.search.duckduckgo import DuckDuckGoSearchProvider

        return DuckDuckGoSearchProvider(settings.search_timeout_seconds)

    if settings.search_provider == "tavily":
        from backend.app.search.tavily import TavilySearchProvider

        return TavilySearchProvider(settings.tavily_api_key, settings.search_timeout_seconds)

    raise ConfigurationError(f"Unknown search provider {settings.search_provider!r}.")


def get_search_provider() -> SearchProvider:
    global _provider
    if _provider is None:
        _provider = build_search_provider()
    return _provider


def set_search_provider(provider: SearchProvider | None) -> None:
    """Inject a provider. Used by tests to avoid real network calls."""
    global _provider
    _provider = provider
