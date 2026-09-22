"""Liveness and configuration-readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.config import Settings, get_settings

router = APIRouter(tags=["health"])


class ComponentStatus(BaseModel):
    name: str
    configured: bool
    detail: str


class HealthResponse(BaseModel):
    status: str
    version: str
    components: list[ComponentStatus]


def _llm_status(settings: Settings) -> ComponentStatus:
    key_field = f"{settings.llm_provider}_api_key"
    model_field = f"{settings.llm_provider}_model"
    configured = bool(getattr(settings, key_field, ""))
    model = getattr(settings, model_field, "unknown")
    return ComponentStatus(
        name="llm",
        configured=configured,
        detail=(
            f"{settings.llm_provider}:{model}"
            if configured
            else f"{settings.llm_provider} selected but no API key set"
        ),
    )


def _vector_status(settings: Settings) -> ComponentStatus:
    if settings.vector_backend == "pinecone":
        configured = bool(settings.pinecone_api_key)
        detail = f"pinecone:{settings.pinecone_index}" if configured else "pinecone key missing"
    else:
        configured = True
        detail = f"chroma (local) at {settings.vector_dir}"
    return ComponentStatus(name="vector_store", configured=configured, detail=detail)


def _search_status(settings: Settings) -> ComponentStatus:
    if settings.search_provider == "tavily":
        configured = bool(settings.tavily_api_key)
        detail = "tavily" if configured else "tavily key missing"
    else:
        configured = True
        detail = "duckduckgo (keyless)"
    return ComponentStatus(name="web_search", configured=configured, detail=detail)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report which subsystems are usable.

    Deliberately never returns 503 for a missing API key: the app is still
    running and the UI uses this to tell the user exactly what to configure.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version="0.1.0",
        components=[
            _llm_status(settings),
            _vector_status(settings),
            _search_status(settings),
        ],
    )
