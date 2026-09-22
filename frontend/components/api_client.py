"""HTTP client for the Atelier backend.

The UI owns no business logic; every decision is made by the backend and this
just moves JSON. That keeps the Streamlit layer disposable — the same API
serves a React frontend or a CLI without change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_BASE_URL = os.environ.get("ATELIER_API_URL", "http://127.0.0.1:8000")

# A create turn runs several model calls in sequence, so the timeout is
# generous; a research-plus-two-artifacts turn legitimately takes minutes.
CHAT_TIMEOUT = 600.0
UPLOAD_TIMEOUT = 180.0
QUICK_TIMEOUT = 20.0


class ApiError(RuntimeError):
    """A backend error, already reduced to something safe to display."""


@dataclass
class AtelierClient:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def _request(self, method: str, path: str, timeout: float, **kwargs: Any) -> Any:
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                timeout=timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise ApiError(f"Could not reach the backend at {self.base_url}: {exc}") from exc

        if response.status_code >= 400:
            raise ApiError(_message_of(response))

        return None if response.status_code == 204 else response.json()

    # --- Health ----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", QUICK_TIMEOUT)

    # --- Conversations ---------------------------------------------------

    def create_conversation(self) -> dict[str, Any]:
        return self._request("POST", "/api/conversations", QUICK_TIMEOUT)

    # --- Uploads ---------------------------------------------------------

    def upload_template(
        self, name: str, data: bytes, conversation_id: str | None
    ) -> dict[str, Any]:
        params = {"conversation_id": conversation_id} if conversation_id else {}
        return self._request(
            "POST",
            "/api/uploads",
            UPLOAD_TIMEOUT,
            files={"file": (name, data)},
            params=params,
        )

    def list_uploads(self, conversation_id: str | None) -> list[dict[str, Any]]:
        params = {"conversation_id": conversation_id} if conversation_id else {}
        return self._request("GET", "/api/uploads", QUICK_TIMEOUT, params=params)

    # --- Chat ------------------------------------------------------------

    def chat(self, message: str, conversation_id: str | None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/chat",
            CHAT_TIMEOUT,
            json={"message": message, "conversation_id": conversation_id},
        )

    # --- Artifacts -------------------------------------------------------

    def list_artifacts(self, conversation_id: str | None) -> list[dict[str, Any]]:
        params = {"conversation_id": conversation_id} if conversation_id else {}
        return self._request("GET", "/api/artifacts", QUICK_TIMEOUT, params=params)

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/artifacts/{artifact_id}", QUICK_TIMEOUT)

    def download(self, artifact_id: str, version_no: int) -> bytes:
        url = f"{self.base_url}/api/artifacts/{artifact_id}/versions/{version_no}/download"
        try:
            response = httpx.get(url, headers=self._headers(), timeout=UPLOAD_TIMEOUT)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ApiError(f"Download failed: {exc}") from exc
        return response.content

    def convert(self, artifact_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/api/artifacts/{artifact_id}/convert", UPLOAD_TIMEOUT
        )

    # --- Knowledge base --------------------------------------------------

    def ingest_knowledge(self, name: str, data: bytes) -> dict[str, Any]:
        return self._request(
            "POST", "/api/rag/documents", UPLOAD_TIMEOUT, files={"file": (name, data)}
        )

    def list_knowledge(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/rag/documents", QUICK_TIMEOUT)

    def delete_knowledge(self, document_id: str) -> None:
        self._request("DELETE", f"/api/rag/documents/{document_id}", QUICK_TIMEOUT)


def _message_of(response: httpx.Response) -> str:
    """Pull the backend's user-safe message out of its error envelope."""
    try:
        body = response.json()
        return body.get("error", {}).get("message") or f"Request failed ({response.status_code})"
    except ValueError:
        return f"Request failed ({response.status_code})"
