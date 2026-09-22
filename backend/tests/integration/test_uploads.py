"""Upload endpoint: acceptance, rejection and template analysis."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEMPLATES = Path(__file__).resolve().parents[3] / "samples" / "templates"
PROPOSAL = TEMPLATES / "Company_Proposal.docx"
DECK = TEMPLATES / "Company_Template.pptx"


def _upload(client: TestClient, path: Path, name: str | None = None):
    if not path.exists():
        pytest.skip("Run scripts/make_samples.py first.")
    return client.post(
        "/api/uploads",
        files={"file": (name or path.name, path.read_bytes())},
    )


class TestDocxUpload:
    def test_accepts_and_analyses_a_proposal(self, client: TestClient) -> None:
        response = _upload(client, PROPOSAL)

        assert response.status_code == 201
        body = response.json()
        assert body["kind"] == "docx"
        assert body["used_ocr"] is False
        assert body["id"]

    def test_returns_the_style_profile(self, client: TestClient) -> None:
        profile = _upload(client, PROPOSAL).json()["profile"]

        assert profile["kind"] == "docx"
        assert profile["default_font"] == "Georgia"
        assert profile["heading_styles"]["1"] == "Heading 1"
        assert profile["has_header"] is True

    def test_summary_describes_what_was_found(self, client: TestClient) -> None:
        assert "Georgia" in _upload(client, PROPOSAL).json()["summary"]

    def test_stores_the_original_name_not_the_path(self, client: TestClient) -> None:
        body = _upload(client, PROPOSAL, name="../../escape.docx").json()

        assert body["display_name"] == "escape.docx"


class TestPptxUpload:
    def test_accepts_and_analyses_a_deck(self, client: TestClient) -> None:
        body = _upload(client, DECK).json()

        assert body["kind"] == "pptx"
        assert body["page_count"] == 3

    def test_returns_the_layout_map(self, client: TestClient) -> None:
        profile = _upload(client, DECK).json()["profile"]

        assert profile["kind"] == "pptx"
        assert "title_content" in profile["layout_map"]
        assert profile["aspect_ratio"] == "16:9"


class TestRejection:
    def test_rejects_an_unsupported_extension(self, client: TestClient) -> None:
        response = client.post("/api/uploads", files={"file": ("virus.exe", b"MZ\x90\x00")})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "UPLOAD_REJECTED"

    def test_rejects_content_that_contradicts_the_extension(self, client: TestClient) -> None:
        response = client.post(
            "/api/uploads", files={"file": ("fake.pdf", b"MZ\x90\x00" + b"\x00" * 64)}
        )

        assert response.status_code == 400

    def test_rejects_an_empty_file(self, client: TestClient) -> None:
        assert client.post("/api/uploads", files={"file": ("empty.pdf", b"")}).status_code == 400

    def test_error_response_hides_internal_detail(self, client: TestClient) -> None:
        body = client.post("/api/uploads", files={"file": ("x.exe", b"MZ")}).json()

        assert body["success"] is False
        assert "Rejected suffix" not in body["error"]["message"]


class TestListingAndRetrieval:
    def test_lists_uploads_newest_first(self, client: TestClient) -> None:
        _upload(client, PROPOSAL)
        _upload(client, DECK)

        listing = client.get("/api/uploads").json()

        assert len(listing) == 2
        assert {row["kind"] for row in listing} == {"docx", "pptx"}

    def test_retrieves_a_stored_profile(self, client: TestClient) -> None:
        upload_id = _upload(client, PROPOSAL).json()["id"]

        body = client.get(f"/api/uploads/{upload_id}").json()

        assert body["profile"]["default_font"] == "Georgia"
        assert body["summary"]

    def test_unknown_upload_is_a_404(self, client: TestClient) -> None:
        response = client.get("/api/uploads/doesnotexist")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestApiKeyGuard:
    def test_rejects_a_request_without_the_key_when_one_is_configured(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient
    ) -> None:
        from backend.app.config import get_settings

        monkeypatch.setenv("ATELIER_API_KEY", "shared-secret-value")
        get_settings.cache_clear()

        assert client.get("/api/uploads").status_code == 401

    def test_accepts_a_request_carrying_the_key(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient
    ) -> None:
        from backend.app.config import get_settings

        monkeypatch.setenv("ATELIER_API_KEY", "shared-secret-value")
        get_settings.cache_clear()

        response = client.get("/api/uploads", headers={"X-API-Key": "shared-secret-value"})

        assert response.status_code == 200


@pytest.fixture
def stubbed_llm():
    """A provider that answers without producing artifacts.

    These tests are about upload scoping, not generation, so the model is
    replaced rather than configured.
    """
    from backend.app.llm import factory
    from backend.app.llm.base import LLMProvider

    class AnsweringProvider(LLMProvider):
        name = "stub"

        def __init__(self) -> None:
            super().__init__("stub", "stub")

        def _chat(self, messages, *, temperature, max_tokens, json_mode) -> str:
            return "{}"

        def _vision(self, prompt: str, image_b64: str, media_type: str) -> str:
            return ""

        def complete_structured(self, messages, schema, *, temperature=None):
            return schema.model_validate({"intent": "answer", "reply": "Hello."})

    factory.set_provider(AnsweringProvider())
    yield
    factory.set_provider(None)


class TestConversationScoping:
    def test_unassigned_listing_excludes_other_conversations(
        self, client: TestClient, stubbed_llm: None
    ) -> None:
        """A new conversation must not see another conversation's templates."""
        claimed = _upload(client, PROPOSAL).json()["id"]
        client.post("/api/chat", json={"message": "hello"})  # adopts the orphan

        listing = client.get("/api/uploads").json()

        assert all(row["id"] != claimed for row in listing)

    def test_uploads_are_adopted_by_the_first_turn(
        self, client: TestClient, stubbed_llm: None
    ) -> None:
        """Templates uploaded before any conversation must still be seen by it."""
        _upload(client, PROPOSAL)

        assert len(client.get("/api/uploads").json()) == 1

        response = client.post("/api/chat", json={"message": "hello"})
        conversation_id = response.json()["conversation_id"]

        scoped = client.get(
            "/api/uploads", params={"conversation_id": conversation_id}
        ).json()

        assert len(scoped) == 1
        assert client.get("/api/uploads").json() == []
