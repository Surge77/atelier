"""Agents 4 and 5: real-time web research and enterprise RAG.

Both return ``ResearchFindings`` — passages to read plus the citations they
must be attributed to — so the generation agents consume one shape regardless
of where knowledge came from, and the validation agent can check attribution
uniformly.

The web agent expands one request into several queries before searching. A
single verbatim query ("research the latest Generative AI trends and write a
proposal") retrieves poorly; three focused queries retrieve well. That
expansion is the only model call either agent makes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.app.core.errors import ProviderError
from backend.app.core.logging import get_logger
from backend.app.core.models.citation import CitationAllocator, Evidence, ResearchFindings
from backend.app.llm.base import LLMProvider, Message
from backend.app.rag.store import VectorStore
from backend.app.search.provider import SearchProvider

logger = get_logger(__name__)

WEB_RESEARCH_AGENT = "web_research"
RAG_AGENT = "rag"

MAX_QUERIES = 3
MIN_USEFUL_CONTENT_CHARS = 40


class _QueryPlan(BaseModel):
    queries: list[str] = Field(
        description="2-3 focused web search queries, each under 12 words",
    )


def research_web(
    request: str,
    search_provider: SearchProvider,
    allocator: CitationAllocator,
    provider: LLMProvider | None = None,
    max_results: int = 6,
) -> ResearchFindings:
    """Agent 4. Searches the live web and returns cited passages."""
    queries = _plan_queries(request, provider)
    findings = ResearchFindings(queries_used=queries)
    seen_urls: set[str] = set()

    for query in queries:
        for result in _search_safely(search_provider, query, max_results):
            if result.url in seen_urls or len(result.content) < MIN_USEFUL_CONTENT_CHARS:
                continue
            seen_urls.add(result.url)

            citation = allocator.add(
                kind="web",
                title=result.title,
                url=result.url,
                snippet=result.content,
            )
            findings.citations.append(citation)
            findings.evidence.append(
                Evidence(
                    citation_id=citation.id, content=result.content, score=result.score
                )
            )

    logger.info(
        "Web research: %s queries, %s sources", len(queries), len(findings.citations)
    )
    return findings


def _search_safely(provider: SearchProvider, query: str, max_results: int):
    """One failed query must not sink the whole research step."""
    try:
        return provider.search(query, max_results)
    except ProviderError as exc:
        logger.warning("Search for %r failed: %s", query, exc)
        return []


def _plan_queries(request: str, provider: LLMProvider | None) -> list[str]:
    """Turn a request into focused search queries."""
    if provider is None:
        return [request[:200]]

    try:
        plan = provider.complete_structured(
            [
                Message(
                    role="system",
                    content=(
                        "You turn a document request into web search queries. "
                        "Produce 2-3 short, specific queries covering the distinct "
                        "things that need researching. No boolean operators."
                    ),
                ),
                Message(role="user", content=request),
            ],
            _QueryPlan,
            temperature=0.0,
        )
    except ProviderError as exc:
        logger.warning("Query planning failed, searching the request verbatim: %s", exc)
        return [request[:200]]

    queries = [query.strip() for query in plan.queries if query.strip()]
    return queries[:MAX_QUERIES] or [request[:200]]


def retrieve_enterprise_knowledge(
    request: str,
    store: VectorStore,
    allocator: CitationAllocator,
    top_k: int = 6,
) -> ResearchFindings:
    """Agent 5. Retrieves from the indexed enterprise corpus.

    No model call: the request is embedded and matched directly. Rewriting the
    query through an LLM first was tried and mostly added latency, because the
    corpus is small and the request already carries the right vocabulary.
    """
    findings = ResearchFindings(queries_used=[request[:200]])

    try:
        hits = store.search(request, top_k)
    except ProviderError as exc:
        logger.warning("Enterprise retrieval failed: %s", exc)
        return findings

    for hit in hits:
        if len(hit.text) < MIN_USEFUL_CONTENT_CHARS:
            continue

        citation = allocator.add(
            kind="enterprise",
            title=hit.heading or hit.document_name,
            source_document=hit.document_name,
            locator=hit.locator or None,
            snippet=hit.text,
        )
        findings.citations.append(citation)
        findings.evidence.append(
            Evidence(citation_id=citation.id, content=hit.text, score=hit.score)
        )

    logger.info("Enterprise retrieval: %s chunks", len(findings.citations))
    return findings


def merge_findings(*groups: ResearchFindings) -> ResearchFindings:
    """Combine findings from several agents, best-scoring evidence first."""
    merged = ResearchFindings()

    for group in groups:
        merged.evidence.extend(group.evidence)
        merged.citations.extend(group.citations)
        merged.queries_used.extend(group.queries_used)

    merged.evidence.sort(key=lambda item: item.score, reverse=True)
    return merged
