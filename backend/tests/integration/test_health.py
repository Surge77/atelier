"""The /health endpoint and app wiring."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.config import get_settings


def _component(payload: dict, name: str) -> dict:
    return next(c for c in payload["components"] if c["name"] == name)


class TestHealth:
    def test_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_reports_all_three_subsystems(self, client: TestClient) -> None:
        names = {c["name"] for c in client.get("/health").json()["components"]}

        assert names == {"llm", "vector_store", "web_search"}

    def test_llm_reported_unconfigured_without_a_key(self, client: TestClient) -> None:
        llm = _component(client.get("/health").json(), "llm")

        assert llm["configured"] is False
        assert "no API key" in llm["detail"]

    def test_llm_reported_configured_with_a_key(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient
    ) -> None:
        monkeypatch.setenv("ATELIER_GROQ_API_KEY", "gsk_testvalue0123456789")
        get_settings.cache_clear()

        llm = _component(client.get("/health").json(), "llm")

        # Asserts the configured model is reported, not a particular vendor
        # model name, which changes as providers retire their catalogue.
        assert llm["configured"] is True
        assert get_settings().groq_model in llm["detail"]

    def test_local_defaults_need_no_credentials(self, client: TestClient) -> None:
        """Chroma and DuckDuckGo must be usable out of the box."""
        payload = client.get("/health").json()

        assert _component(payload, "vector_store")["configured"] is True
        assert _component(payload, "web_search")["configured"] is True


class TestAppWiring:
    def test_creates_the_data_directory_on_startup(self, client: TestClient) -> None:
        client.get("/health")
        settings = get_settings()

        assert settings.uploads_dir.exists()
        assert settings.artifacts_dir.exists()

    def test_unknown_route_returns_404(self, client: TestClient) -> None:
        assert client.get("/no-such-route").status_code == 404

    def test_openapi_schema_is_served(self, client: TestClient) -> None:
        assert client.get("/openapi.json").status_code == 200
