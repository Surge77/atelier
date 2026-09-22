"""Producing a new document or deck, and gating it on validation.

Split out of the orchestrator so that file stays a readable spine. The
generate-validate-repair-render cycle is the same shape for both formats; only
the agent and the profile differ.
"""

from __future__ import annotations

from collections.abc import Callable

from backend.app.agents.generation_agents import (
    DOCUMENT_GENERATION_AGENT,
    PPT_GENERATION_AGENT,
    generate_deck,
    generate_document,
)
from backend.app.agents.supervisor import ExecutionPlan
from backend.app.agents.validation_agent import (
    VALIDATION_AGENT,
    ValidationReport,
    validate_plan,
)
from backend.app.core.models.content import ContentPlan, DeckPlan
from backend.app.orchestration.events import EventRecorder
from backend.app.orchestration.results import ProducedArtifact, announce
from backend.app.orchestration.run_state import RunState
from backend.app.services.versioning import add_version, create_artifact

# One repair attempt. A model that failed the same structural check twice will
# usually fail it a third time, and the user is waiting.
MAX_REPAIR_ATTEMPTS = 1


def produce_document(
    state: RunState, execution: ExecutionPlan, recorder: EventRecorder
) -> ProducedArtifact:
    profile = state.document_profile()
    brief = execution.document_brief or state.user_message

    with recorder.step(DOCUMENT_GENERATION_AGENT, "Writing the document"):
        plan = generate_document(brief, profile, state.findings, state.provider)

    plan, report = validate_and_repair(
        plan,
        state,
        recorder,
        lambda instruction: generate_document(
            f"{brief}\n\n{instruction}", profile, state.findings, state.provider
        ),
    )

    artifact = create_artifact(
        state.session,
        state.conversation_id,
        "document",
        plan.title,
        state.docx_template.upload_id if state.docx_template else None,
    )
    version = add_version(
        state.session,
        state.settings,
        artifact,
        plan,
        profile,
        state.allocator.citations,
        state.document_template_path(),
        validation=report.model_dump(mode="json"),
        summary="Initial draft",
    )
    announce(recorder, artifact, version)
    return ProducedArtifact(artifact, version, report)


def produce_deck(
    state: RunState, execution: ExecutionPlan, recorder: EventRecorder
) -> ProducedArtifact:
    profile = state.deck_profile()
    brief = execution.deck_brief or state.user_message
    slide_count = execution.slide_count

    with recorder.step(PPT_GENERATION_AGENT, f"Building {slide_count} slides"):
        plan = generate_deck(brief, profile, state.findings, state.provider, slide_count)

    plan, report = validate_and_repair(
        plan,
        state,
        recorder,
        lambda instruction: generate_deck(
            f"{brief}\n\n{instruction}", profile, state.findings, state.provider, slide_count
        ),
        expected_slide_count=slide_count,
    )

    artifact = create_artifact(
        state.session,
        state.conversation_id,
        "deck",
        plan.title,
        state.pptx_template.upload_id if state.pptx_template else None,
    )
    version = add_version(
        state.session,
        state.settings,
        artifact,
        plan,
        profile,
        state.allocator.citations,
        state.deck_template_path(),
        validation=report.model_dump(mode="json"),
        summary="Initial draft",
    )
    announce(recorder, artifact, version)
    return ProducedArtifact(artifact, version, report)


def validate_and_repair(
    plan: ContentPlan,
    state: RunState,
    recorder: EventRecorder,
    regenerate: Callable[[str], ContentPlan],
    expected_slide_count: int | None = None,
) -> tuple[ContentPlan, ValidationReport]:
    """Validate, and give generation one chance to fix a hard failure."""

    def check(candidate: ContentPlan) -> ValidationReport:
        with recorder.step(VALIDATION_AGENT, "Checking structure and citations"):
            return validate_plan(
                candidate,
                state.allocator.citations,
                expected_slide_count,
                state.deck_profile() if isinstance(candidate, DeckPlan) else None,
                had_sources=bool(state.allocator.citations),
            )

    report = check(plan)
    recorder.note(report.summary(), VALIDATION_AGENT, coverage=report.citation_coverage)

    for _ in range(MAX_REPAIR_ATTEMPTS):
        if report.passed:
            break

        recorder.note("Regenerating to fix validation errors", VALIDATION_AGENT)
        plan = regenerate(report.repair_instruction())
        report = check(plan)
        recorder.note(report.summary(), VALIDATION_AGENT, coverage=report.citation_coverage)

    return plan, report
