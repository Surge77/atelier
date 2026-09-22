"""Web research and enterprise RAG agents.

Both are driven by fakes: no network, no embedding model, no API key. What is
under test is attribution — that every passage the generator will read carries
a citation id that resolves.
"""

from __future__ import annotations

import pytest

from backend.app.agents.research_agents import (
    merge_findings,
    research_web,
    retrieve_enterprise_knowledge,
)
from backend.app.core.errors import ProviderError
from backend.app.core.models.citation import CitationAllocator
from backend.app.rag.store import SearchHit
from backend.app.search.provider import SearchResult


class FakeSearchProvider:
    name = "fake"

    def __init__(self, results: list[SearchResult] | None = None, fail: bool = False) -> None:
        self._results = results or []
        self._fail = fail
        self.queries: list[str] = []

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        self.queries.append(query)
        if self._fail:
            raise ProviderError("upstream is down")
        return self._results[:max_results]


class FakeVectorStore:
    def __init__(self, hits: list[SearchHit] | None = None, fail: bool = False) -> None:
        self._hits = hits or []
        self._fail = fail

    def add(self, chunks):  # type: ignore[no-untyped-def]
        return len(chunks)

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        if self._fail:
            raise ProviderError("index unavailable")
        return self._hits[:top_k]

    def delete_document(self, document_id: str) -> int:
        return 0

    def count(self) -> int:
        return len(self._hits)


def _result(url: str, content: str = "A" * 200) -> SearchResult:
    return SearchResult(title=f"Page at {url}", url=url, content=content, score=0.9)


def _hit(chunk_id: str, text: str = "B" * 200, **metadata: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        text=text,
        score=0.8,
        metadata={"document_name": "handbook.docx", **metadata},
    )


class TestWebResearch:
    def test_returns_evidence_paired_with_citations(self) -> None:
        provider = FakeSearchProvider([_result("https://a.example"), _result("https://b.example")])
        allocator = CitationAllocator()

        findings = research_web("latest AI trends", provider, allocator)

        assert len(findings.evidence) == 2
        assert {e.citation_id for e in findings.evidence} == {c.id for c in findings.citations}

    def test_every_evidence_id_resolves_to_a_citation(self) -> None:
        provider = FakeSearchProvider([_result("https://a.example")])
        allocator = CitationAllocator()

        findings = research_web("query", provider, allocator)
        known = {c.id for c in allocator.citations}

        assert all(e.citation_id in known for e in findings.evidence)

    def test_deduplicates_the_same_url_across_queries(self) -> None:
        provider = FakeSearchProvider([_result("https://same.example")])
        allocator = CitationAllocator()

        findings = research_web("query", provider, allocator)

        assert len(findings.citations) == 1

    def test_drops_results_with_no_usable_content(self) -> None:
        provider = FakeSearchProvider([_result("https://thin.example", content="short")])
        allocator = CitationAllocator()

        assert research_web("query", provider, allocator).is_empty()

    def test_searches_the_request_verbatim_without_a_model(self) -> None:
        provider = FakeSearchProvider([_result("https://a.example")])

        research_web("generative AI trends", provider, CitationAllocator())

        assert provider.queries == ["generative AI trends"]

    def test_a_failing_search_degrades_instead_of_raising(self) -> None:
        provider = FakeSearchProvider(fail=True)

        findings = research_web("query", provider, CitationAllocator())

        assert findings.is_empty()

    def test_web_citations_render_with_their_url(self) -> None:
        provider = FakeSearchProvider([_result("https://a.example")])
        allocator = CitationAllocator()

        research_web("query", provider, allocator)

        assert "https://a.example" in allocator.citations[0].reference_line()


class TestEnterpriseRetrieval:
    def test_returns_cited_chunks(self) -> None:
        store = FakeVectorStore([_hit("c1"), _hit("c2")])
        allocator = CitationAllocator()

        findings = retrieve_enterprise_knowledge("expenses policy", store, allocator)

        assert len(findings.evidence) == 2
        assert all(c.kind == "enterprise" for c in findings.citations)

    def test_citation_records_the_document_and_locator(self) -> None:
        store = FakeVectorStore([_hit("c1", heading="Travel", location="4")])
        allocator = CitationAllocator()

        retrieve_enterprise_knowledge("travel", store, allocator)
        citation = allocator.citations[0]

        assert citation.source_document == "handbook.docx"
        assert citation.locator == "page 4"
        assert citation.title == "Travel"

    def test_slide_locator_for_a_deck_source(self) -> None:
        store = FakeVectorStore([_hit("c1", location="2", source_kind="pptx")])
        allocator = CitationAllocator()

        retrieve_enterprise_knowledge("q", store, allocator)

        assert allocator.citations[0].locator == "slide 2"

    def test_respects_top_k(self) -> None:
        store = FakeVectorStore([_hit(f"c{i}") for i in range(10)])

        findings = retrieve_enterprise_knowledge("q", store, CitationAllocator(), top_k=3)

        assert len(findings.evidence) == 3

    def test_an_unavailable_index_degrades_instead_of_raising(self) -> None:
        store = FakeVectorStore(fail=True)

        assert retrieve_enterprise_knowledge("q", store, CitationAllocator()).is_empty()

    def test_empty_corpus_returns_no_findings(self) -> None:
        assert retrieve_enterprise_knowledge("q", FakeVectorStore(), CitationAllocator()).is_empty()


class TestSharedAllocation:
    def test_web_and_enterprise_ids_never_collide(self) -> None:
        """Both agents run in one turn, so ids must come from one allocator."""
        allocator = CitationAllocator()
        web = research_web("q", FakeSearchProvider([_result("https://a.example")]), allocator)
        enterprise = retrieve_enterprise_knowledge("q", FakeVectorStore([_hit("c1")]), allocator)

        ids = [c.id for c in web.citations + enterprise.citations]

        assert ids == ["S1", "S2"]
        assert len(set(ids)) == 2


class TestMergeFindings:
    def test_orders_evidence_by_score(self) -> None:
        allocator = CitationAllocator()
        low = retrieve_enterprise_knowledge(
            "q", FakeVectorStore([_hit("c1")]), allocator
        )
        high = research_web("q", FakeSearchProvider([_result("https://a.example")]), allocator)

        merged = merge_findings(low, high)

        assert merged.evidence[0].score >= merged.evidence[-1].score

    def test_keeps_every_citation(self) -> None:
        allocator = CitationAllocator()
        web = research_web("q", FakeSearchProvider([_result("https://a.example")]), allocator)
        enterprise = retrieve_enterprise_knowledge("q", FakeVectorStore([_hit("c1")]), allocator)

        assert len(merge_findings(web, enterprise).citations) == 2

    def test_merging_nothing_is_empty(self) -> None:
        assert merge_findings().is_empty()


class TestPromptRendering:
    def test_evidence_block_labels_each_passage_with_its_id(self) -> None:
        allocator = CitationAllocator()
        findings = research_web(
            "q", FakeSearchProvider([_result("https://a.example")]), allocator
        )

        assert "[S1]" in findings.as_prompt_block()

    def test_empty_findings_say_so_rather_than_render_blank(self) -> None:
        """A blank block would read to the model as "no constraint"."""
        assert "No sources" in merge_findings().as_prompt_block()


@pytest.mark.parametrize("bad_query", ["", "   "])
def test_allocator_titles_never_end_up_blank(bad_query: str) -> None:
    allocator = CitationAllocator()

    citation = allocator.add(kind="web", title=bad_query, url="https://a.example")

    assert citation.title == "Untitled source"
