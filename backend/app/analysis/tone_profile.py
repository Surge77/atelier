"""Derives how an uploaded document *sounds*.

Split deliberately in two. Everything measurable — sentence length, paragraph
length, heading case, whether bullets are fragments — is computed from the text
and is therefore free, deterministic and testable. Only the genuinely
subjective part (formality, voice, vocabulary) goes to the model, and the
verbatim excerpts do most of the work at generation time anyway: showing the
model four real sentences beats telling it "formal".
"""

from __future__ import annotations

import re
from statistics import mean

from pydantic import BaseModel, Field

from backend.app.core.logging import get_logger
from backend.app.core.models.template import ToneProfile
from backend.app.ingestion.base import ExtractedDocument
from backend.app.llm.base import LLMProvider, Message

logger = get_logger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_FIRST_PERSON = re.compile(r"\b(we|our|us|i|my)\b", re.IGNORECASE)
_SECOND_PERSON = re.compile(r"\b(you|your)\b", re.IGNORECASE)

MAX_EXCERPTS = 4
MIN_EXCERPT_WORDS = 8
MAX_EXCERPT_CHARS = 280
# Below this there is not enough prose for the statistics to mean anything.
MIN_PARAGRAPHS_FOR_STATS = 2


class _ToneJudgement(BaseModel):
    """The subjective half, asked of the model."""

    formality: str = Field(description="e.g. 'formal corporate', 'plain technical'")
    person: str = Field(description="e.g. 'first person plural', 'third person'")
    vocabulary_notes: list[str] = Field(
        default_factory=list,
        description="3-5 short notes on recurring word choices, jargon or conventions",
    )


def build_tone_profile(
    extracted: ExtractedDocument, provider: LLMProvider | None = None
) -> ToneProfile:
    """Measure what can be measured, then ask the model for the rest."""
    profile = _measured_profile(extracted)

    if provider is None:
        return profile

    try:
        judgement = _ask_model(extracted, provider)
    except Exception as exc:
        logger.warning("Tone judgement failed, keeping measured profile only: %s", exc)
        return profile

    return profile.model_copy(
        update={
            "formality": judgement.formality,
            "person": judgement.person,
            "vocabulary_notes": judgement.vocabulary_notes,
        }
    )


def _measured_profile(extracted: ExtractedDocument) -> ToneProfile:
    paragraphs = [b.text for b in extracted.blocks_of("paragraph") if b.text.strip()]
    bullets = [b.text for b in extracted.blocks_of("bullet") if b.text.strip()]
    headings = extracted.outline()

    return ToneProfile(
        formality="neutral professional",
        person=_detect_person(paragraphs),
        heading_case=_detect_heading_case(headings),
        bullet_style=_describe_bullets(bullets),
        avg_sentence_words=_avg_sentence_words(paragraphs),
        avg_paragraph_sentences=_avg_paragraph_sentences(paragraphs),
        sample_excerpts=_pick_excerpts(paragraphs),
    )


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _avg_sentence_words(paragraphs: list[str]) -> float:
    lengths = [
        len(sentence.split())
        for paragraph in paragraphs
        for sentence in _sentences(paragraph)
    ]
    return round(mean(lengths), 1) if lengths else 18.0


def _avg_paragraph_sentences(paragraphs: list[str]) -> float:
    if len(paragraphs) < MIN_PARAGRAPHS_FOR_STATS:
        return 3.0
    return round(mean(len(_sentences(p)) for p in paragraphs), 1)


def _detect_person(paragraphs: list[str]) -> str:
    joined = " ".join(paragraphs)
    if not joined:
        return "third person"

    first = len(_FIRST_PERSON.findall(joined))
    second = len(_SECOND_PERSON.findall(joined))

    if first > second and first > 2:
        return "first person plural"
    if second > first and second > 2:
        return "second person"
    return "third person"


def _detect_heading_case(headings: list[str]) -> str:
    candidates = [h for h in headings if len(h.split()) > 1]
    if not candidates:
        return "Title Case"

    if all(h.isupper() for h in candidates):
        return "UPPER CASE"
    if sum(h.istitle() for h in candidates) > len(candidates) / 2:
        return "Title Case"
    return "Sentence case"


def _describe_bullets(bullets: list[str]) -> str:
    if not bullets:
        return "short noun phrases"

    ends_with_period = sum(b.rstrip().endswith(".") for b in bullets)
    average_words = mean(len(b.split()) for b in bullets)

    length = "short" if average_words < 8 else "medium-length"
    punctuation = (
        "ending in a full stop"
        if ends_with_period > len(bullets) / 2
        else "with no terminal punctuation"
    )
    return f"{length} phrases, {punctuation}"


def _pick_excerpts(paragraphs: list[str]) -> list[str]:
    """Real sentences from the source, used as few-shot anchors."""
    excerpts = [
        paragraph[:MAX_EXCERPT_CHARS].strip()
        for paragraph in paragraphs
        if len(paragraph.split()) >= MIN_EXCERPT_WORDS
    ]
    return excerpts[:MAX_EXCERPTS]


def _ask_model(extracted: ExtractedDocument, provider: LLMProvider) -> _ToneJudgement:
    sample = extracted.text[:4000]
    return provider.complete_structured(
        [
            Message(
                role="system",
                content=(
                    "You analyse the writing style of business documents. "
                    "Describe only what is present in the sample. Be concrete."
                ),
            ),
            Message(
                role="user",
                content=f"Describe the formality, voice and vocabulary of:\n\n{sample}",
            ),
        ],
        _ToneJudgement,
        temperature=0.0,
    )
