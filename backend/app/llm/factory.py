"""Selects the configured LLM provider.

The provider is resolved once per process and cached. Agents depend on the
``LLMProvider`` abstract base, never on a vendor SDK, which is what makes the
whole system swappable with one environment variable.
"""

from __future__ import annotations

from backend.app.config import Settings, get_settings
from backend.app.core.errors import ConfigurationError
from backend.app.llm.base import LLMProvider

_provider: LLMProvider | None = None


def build_provider(settings: Settings | None = None) -> LLMProvider:
    """Construct the provider named by ``ATELIER_LLM_PROVIDER``."""
    settings = settings or get_settings()
    shared = {
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "max_retries": settings.llm_max_retries,
    }

    if settings.llm_provider == "groq":
        from backend.app.llm.groq_provider import GroqProvider

        return GroqProvider(
            settings.groq_api_key, settings.groq_model, settings.groq_vision_model, **shared
        )

    if settings.llm_provider == "openai":
        from backend.app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            settings.openai_api_key, settings.openai_model, settings.openai_vision_model, **shared
        )

    if settings.llm_provider == "anthropic":
        from backend.app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            settings.anthropic_api_key,
            settings.anthropic_model,
            settings.anthropic_vision_model,
            **shared,
        )

    raise ConfigurationError(f"Unknown LLM provider {settings.llm_provider!r}.")


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = build_provider()
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    """Inject a provider. Used by tests to avoid any network call."""
    global _provider
    _provider = provider
