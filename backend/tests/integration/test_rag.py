"""Enterprise RAG end to end: real embeddings, a real Chroma index.

Marked ``integration`` because the embedding model downloads once (about
90 MB). Everything else in the suite runs offline; this is the test that
proves retrieval genuinely works rather than that the fakes agree with
each other.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

CORPUS = {
    "travel_policy.md": (
        "# Travel Policy\n\n"
        "Employees must book economy class for flights under six hours. "
        "Business class requires director approval and a documented reason.\n\n"
        "# Accommodation\n\n"
        "Hotel spend is capped at 180 GBP per night in London and 120 GBP elsewhere.\n"
    ),
    "security_policy.md": (
        "# Access Control\n\n"
        "Production database access is granted through the platform team only. "
        "Standing access is not permitted; every grant expires after eight hours.\n\n"
        "# Incident Response\n\n"
        "A suspected breach must be reported to the security channel within fifteen minutes.\n"
    ),
}


@pytest.fixture
def corpus_files(tmp_path: Path) -> dict[str, Path]:
    paths = {}
    for name, body in CORPUS.items():
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        paths[name] = path
    return paths


def _ingest(client: TestClient, path: Path):
    return client.post(
        "/api/rag/documents", files={"file": (path.name, path.read_bytes())}
    )


class TestIngestion:
    def test_indexes_a_document_into_chunks(
        self, client: TestClient, corpus_files: dict[str, Path]
    ) -> None:
        response = _ingest(client, corpus_files["travel_policy.md"])

        assert response.status_code == 201
        body = response.json()
        assert body["chunk_count"] == 2  # one per heading
        assert body["document_id"]

    def test_store_total_grows_across_documents(
        self, client: TestClient, corpus_files: dict[str, Path]
    ) -> None:
        _ingest(client, corpus_files["travel_policy.md"])
        second = _ingest(client, corpus_files["security_policy.md"]).json()

        assert second["total_chunks_in_store"] == 4

    def test_lists_indexed_documents(
        self, client: TestClient, corpus_files: dict[str, Path]
    ) -> None:
        _ingest(client, corpus_files["travel_policy.md"])

        listing = client.get("/api/rag/documents").json()

        assert len(listing) == 1
        assert listing[0]["display_name"] == "travel_policy.md"
        assert listing[0]["chunk_count"] == 2


class TestRetrieval:
    def test_finds_the_semantically_relevant_chunk(
        self, client: TestClient, corpus_files: dict[str, Path]
    ) -> None:
        """The query shares no keywords with the passage it should find."""
        for path in corpus_files.values():
            _ingest(client, path)

        hits = client.get("/api/rag/search", params={"q": "how much can I spend on a hotel"}).json()

        assert hits
        assert "180 GBP" in hits[0]["text"]

    def test_separates_the_two_documents(
        self, client: TestClient, corpus_files: dict[str, Path]
    ) -> None:
        for path in corpus_files.values():
            _ingest(client, path)

        hits = client.get(
            "/api/rag/search", params={"q": "who can reach the production database"}
        ).json()

        assert hits[0]["document_name"] == "security_policy.md"

    def test_hits_carry_citation_metadata(
        self, client: TestClient, corpus_files: dict[str, Path]
    ) -> None:
        _ingest(client, corpus_files["travel_policy.md"])

        hit = client.get("/api/rag/search", params={"q": "flight booking class"}).json()[0]

        assert hit["document_name"] == "travel_policy.md"
        assert hit["heading"]
        assert 0.0 <= hit["score"] <= 1.0

    def test_respects_top_k(self, client: TestClient, corpus_files: dict[str, Path]) -> None:
        for path in corpus_files.values():
            _ingest(client, path)

        hits = client.get("/api/rag/search", params={"q": "policy", "top_k": 2}).json()

        assert len(hits) == 2

    def test_empty_corpus_returns_no_hits(self, client: TestClient) -> None:
        assert client.get("/api/rag/search", params={"q": "anything at all"}).json() == []

    def test_rejects_a_too_short_query(self, client: TestClient) -> None:
        assert client.get("/api/rag/search", params={"q": "a"}).status_code == 422


class TestDeletion:
    def test_removes_a_document_and_its_chunks(
        self, client: TestClient, corpus_files: dict[str, Path]
    ) -> None:
        document_id = _ingest(client, corpus_files["travel_policy.md"]).json()["document_id"]

        assert client.delete(f"/api/rag/documents/{document_id}").status_code == 204
        assert client.get("/api/rag/documents").json() == []
        assert client.get("/api/rag/search", params={"q": "hotel spend cap"}).json() == []

    def test_deleting_an_unknown_document_is_a_404(self, client: TestClient) -> None:
        assert client.delete("/api/rag/documents/nope").status_code == 404


class TestAgentAttribution:
    def test_rag_agent_produces_resolvable_citations(
        self, client: TestClient, corpus_files: dict[str, Path]
    ) -> None:
        """The agent path, not just the endpoint."""
        from backend.app.agents.research_agents import retrieve_enterprise_knowledge
        from backend.app.core.models.citation import CitationAllocator
        from backend.app.rag.factory import get_store

        _ingest(client, corpus_files["security_policy.md"])

        allocator = CitationAllocator()
        findings = retrieve_enterprise_knowledge(
            "how quickly must a breach be reported", get_store(), allocator
        )

        assert findings.evidence
        known = {c.id for c in allocator.citations}
        assert all(e.citation_id in known for e in findings.evidence)
        assert all(c.source_document == "security_policy.md" for c in findings.citations)
