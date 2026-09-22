"""The whole turn, end to end, with a scripted model.

This exercises the assignment's own example workflow — analyse templates,
research, generate a document and a deck, validate, cite, then edit
conversationally — without any network call. The model is scripted so the
orchestration itself is what is under test, and the files it produces are
reopened to prove they are real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from docx import Document
from pptx import Presentation

from backend.app.config import get_settings
from backend.app.core.models.patch import PatchOp, PatchPlan
from backend.app.db.session import session_scope
from backend.app.ingestion.reader import read_document
from backend.app.llm.base import LLMProvider
from backend.app.search.provider import SearchResult
from backend.app.services.chat import handle_turn
from backend.app.services.uploads import ingest_upload

TEMPLATES = Path(__file__).resolve().parents[3] / "samples" / "templates"
PROPOSAL = TEMPLATES / "Company_Proposal.docx"
DECK = TEMPLATES / "Company_Template.pptx"

BODY = "Adoption of generative AI accelerated sharply across the enterprise this year."


def _normal_font(path: str) -> Any:
    """styles[...] is typed as BaseStyle; only ParagraphStyle exposes .font."""
    return cast(Any, Document(path).styles["Normal"]).font


class ScriptedProvider(LLMProvider):
    """Returns a canned object per requested schema, in order."""

    name = "scripted"

    def __init__(self, responses: dict[str, list[object]]) -> None:
        super().__init__("scripted-model", "scripted-vision")
        self._responses = {key: list(value) for key, value in responses.items()}
        self.schemas_requested: list[str] = []

    def _chat(self, messages, *, temperature, max_tokens, json_mode) -> str:  # type: ignore[no-untyped-def]
        raise AssertionError("complete_structured is overridden; _chat is never reached")

    def _vision(self, prompt: str, image_b64: str, media_type: str) -> str:
        return "# Scanned heading\nScanned body text."

    def complete_structured(self, messages, schema, *, temperature=None):  # type: ignore[no-untyped-def]
        name = schema.__name__
        self.schemas_requested.append(name)

        queue = self._responses.get(name)
        if not queue:
            raise AssertionError(f"No scripted response for {name}")
        return schema.model_validate(json.loads(json.dumps(queue.pop(0))))


class StubSearch:
    name = "stub"

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        return [
            SearchResult(
                title="State of Generative AI 2026",
                url="https://example.com/state-of-ai",
                content=BODY * 3,
                score=0.95,
            )
        ]


class StubStore:
    def add(self, chunks):  # type: ignore[no-untyped-def]
        return len(chunks)

    def search(self, query: str, top_k: int):  # type: ignore[no-untyped-def]
        return []

    def delete_document(self, document_id: str) -> int:
        return 0

    def count(self) -> int:
        return 0


def _document_plan() -> dict:
    return {
        "kind": "document",
        "title": "Generative AI Adoption Proposal",
        "sections": [
            {
                "heading": "Executive Summary",
                "level": 1,
                "blocks": [{"kind": "paragraph", "text": BODY * 4, "citation_ids": ["S1"]}],
            },
            {
                "heading": "Market Context",
                "level": 1,
                "blocks": [{"kind": "paragraph", "text": BODY * 4, "citation_ids": ["S1"]}],
            },
        ],
    }


def _deck_plan(slide_count: int = 3) -> dict:
    return {
        "kind": "deck",
        "title": "Generative AI Trends",
        "slides": [
            {
                "layout": "title_content",
                "title": f"Slide {i + 1}",
                "bullets": ["Model costs fell", "Tooling matured"],
                "speaker_notes": "Talk through the cost curve.",
                "citation_ids": ["S1"],
            }
            for i in range(slide_count)
        ],
    }


def _create_plan(**overrides) -> dict:  # type: ignore[no-untyped-def]
    base = {
        "intent": "create",
        "reasoning": "The user asked for a proposal and a deck.",
        "needs_web_research": True,
        "needs_enterprise_rag": False,
        "produce_document": True,
        "produce_deck": True,
        "document_brief": "Write a proposal on generative AI adoption.",
        "deck_brief": "Build a deck on generative AI trends.",
        "slide_count": 3,
    }
    base.update(overrides)
    return base


@pytest.fixture
def uploaded(client):  # type: ignore[no-untyped-def]
    """Both templates uploaded into one conversation."""
    if not PROPOSAL.exists():
        pytest.skip("Run scripts/make_samples.py first.")

    settings = get_settings()
    with session_scope() as session:
        from backend.app.db.models import Conversation

        conversation = Conversation(title="test")
        session.add(conversation)
        session.flush()
        conversation_id = conversation.id

        for path in (PROPOSAL, DECK):
            ingest_upload(
                path.read_bytes(), path.name, settings, session, conversation_id=conversation_id
            )

    return conversation_id


def _run(message: str, conversation_id: str, provider: ScriptedProvider):
    settings = get_settings()
    with session_scope() as session:
        return handle_turn(
            message=message,
            conversation_id=conversation_id,
            settings=settings,
            session=session,
            provider=provider,
            search_provider=StubSearch(),
            store=StubStore(),
        )


class TestCreateTurn:
    @pytest.fixture
    def outcome(self, uploaded: str):
        provider = ScriptedProvider(
            {
                "ExecutionPlan": [_create_plan()],
                "_QueryPlan": [{"queries": ["generative ai adoption 2026"]}],
                "DocumentPlan": [_document_plan()],
                "DeckPlan": [_deck_plan()],
                "_ToneJudgement": [
                    {"formality": "formal corporate", "person": "third person",
                     "vocabulary_notes": []}
                ],
            }
        )
        return _run(
            "Research generative AI trends and create a proposal and a 3-slide deck "
            "in the same style as the uploaded files.",
            uploaded,
            provider,
        )

    def test_produces_both_artifacts(self, outcome) -> None:
        assert len(outcome.result.produced) == 2
        assert {p.artifact.kind for p in outcome.result.produced} == {"document", "deck"}

    def test_reports_the_agents_it_used(self, outcome) -> None:
        agents = outcome.result.plan.agents_involved()

        assert "supervisor" in agents
        assert "web_research" in agents
        assert "document_generation" in agents
        assert "ppt_generation" in agents
        assert "validation" in agents

    def test_emits_a_trace_of_every_step(self, outcome) -> None:
        kinds = [event.kind for event in outcome.events]

        assert kinds[0] == "run_started"
        assert "plan_selected" in kinds
        assert kinds[-1] == "run_finished"
        assert kinds.count("agent_started") >= 4

    def test_records_which_agents_were_skipped(self, outcome) -> None:
        skipped = [e.agent for e in outcome.events if e.kind == "agent_skipped"]

        assert "rag" in skipped

    def test_every_artifact_starts_at_version_one(self, outcome) -> None:
        assert all(p.version.version_no == 1 for p in outcome.result.produced)

    def test_validation_passes(self, outcome) -> None:
        assert all(p.validation.passed for p in outcome.result.produced)

    def test_citations_are_attached_and_resolvable(self, outcome) -> None:
        version = outcome.result.produced[0].version

        assert version.citations
        assert version.citations[0]["url"] == "https://example.com/state-of-ai"

    def test_the_rendered_docx_keeps_the_template_font(self, outcome) -> None:
        document = next(p for p in outcome.result.produced if p.artifact.kind == "document")

        assert _normal_font(str(document.version.file_path)).name == "Georgia"

    def test_the_rendered_docx_contains_the_generated_content(self, outcome) -> None:
        document = next(p for p in outcome.result.produced if p.artifact.kind == "document")

        text = read_document(Path(document.version.file_path)).text

        assert "Executive Summary" in text
        assert "References" in text

    def test_the_rendered_pptx_has_the_requested_slide_count(self, outcome) -> None:
        deck = next(p for p in outcome.result.produced if p.artifact.kind == "deck")

        presentation = Presentation(str(deck.version.file_path))

        # Three content slides plus the appended Sources slide.
        assert len(presentation.slides) == 4


class TestEditTurn:
    def test_edit_creates_a_new_version_without_losing_formatting(self, uploaded: str) -> None:
        """The assignment's own follow-up: "Add an executive summary"."""
        create = ScriptedProvider(
            {
                "ExecutionPlan": [
                    _create_plan(produce_deck=False, needs_web_research=False)
                ],
                "DocumentPlan": [_document_plan()],
                "_ToneJudgement": [
                    {"formality": "formal", "person": "third person", "vocabulary_notes": []}
                ],
            }
        )
        first = _run("Write a proposal.", uploaded, create)
        artifact_id = first.result.produced[0].artifact.id

        edit = ScriptedProvider(
            {
                "ExecutionPlan": [
                    {
                        "intent": "edit",
                        "reasoning": "The user wants a new section.",
                        "edit_instruction": "Add a competitive analysis section.",
                        "target_artifact_id": artifact_id,
                    }
                ],
                "PatchPlan": [
                    PatchPlan(
                        ops=[
                            PatchOp(
                                op="insert_section",
                                position=0,
                                heading="Competitive Analysis",
                                blocks=[{"kind": "paragraph", "text": BODY * 3}],  # type: ignore[list-item]
                            )
                        ],
                        explanation="Added a competitive analysis section.",
                    ).model_dump()
                ],
            }
        )
        second = _run("Add a competitive analysis section.", uploaded, edit)

        assert second.result.produced
        version = second.result.produced[0].version
        assert version.version_no == 2
        assert version.ops

        assert _normal_font(str(version.file_path)).name == "Georgia"

        text = read_document(Path(version.file_path)).text
        assert "Competitive Analysis" in text
        assert "Executive Summary" in text  # the original section survived

    def test_editing_with_nothing_to_edit_explains_itself(self, client) -> None:
        provider = ScriptedProvider(
            {"ExecutionPlan": [{"intent": "edit", "edit_instruction": "Make it shorter."}]}
        )

        outcome = _run("Make it shorter.", "", provider)

        assert outcome.result.produced == []
        assert "nothing to edit" in outcome.result.reply.lower()


class TestAnswerTurn:
    def test_a_question_produces_no_artifact(self, client) -> None:
        provider = ScriptedProvider(
            {
                "ExecutionPlan": [
                    {"intent": "answer", "reply": "I can generate documents and decks."}
                ]
            }
        )

        outcome = _run("What can you do?", "", provider)

        assert outcome.result.produced == []
        assert "generate documents" in outcome.result.reply


class TestValidationRepair:
    def test_a_failed_validation_triggers_one_regeneration(self, uploaded: str) -> None:
        """First draft has the wrong slide count; the second is correct."""
        provider = ScriptedProvider(
            {
                "ExecutionPlan": [
                    _create_plan(produce_document=False, needs_web_research=False, slide_count=3)
                ],
                "DeckPlan": [_deck_plan(slide_count=2), _deck_plan(slide_count=3)],
                "_ToneJudgement": [
                    {"formality": "formal", "person": "third person", "vocabulary_notes": []}
                ],
            }
        )

        outcome = _run("Build a 3-slide deck.", uploaded, provider)

        assert provider.schemas_requested.count("DeckPlan") == 2
        assert outcome.result.produced[0].validation.passed

    def test_repair_is_attempted_only_once(self, uploaded: str) -> None:
        provider = ScriptedProvider(
            {
                "ExecutionPlan": [
                    _create_plan(produce_document=False, needs_web_research=False, slide_count=3)
                ],
                "DeckPlan": [_deck_plan(slide_count=2), _deck_plan(slide_count=2)],
                "_ToneJudgement": [
                    {"formality": "formal", "person": "third person", "vocabulary_notes": []}
                ],
            }
        )

        outcome = _run("Build a 3-slide deck.", uploaded, provider)

        assert provider.schemas_requested.count("DeckPlan") == 2
        assert not outcome.result.produced[0].validation.passed
