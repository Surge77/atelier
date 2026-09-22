"""Changing an artifact that already exists: editing and conversion.

Both operate on a stored ``ContentPlan`` and re-render it, which is why neither
can lose the template's formatting — the formatting was never in the plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from backend.app.agents.editing_agent import EDITING_AGENT, plan_edit
from backend.app.agents.supervisor import ExecutionPlan
from backend.app.agents.validation_agent import validate_plan
from backend.app.core.models.citation import Citation
from backend.app.core.models.content import ContentPlan, DocumentPlan, plan_from_dict
from backend.app.core.models.template import DocxTemplateProfile, PptxTemplateProfile
from backend.app.db.models import Artifact
from backend.app.orchestration.events import EventRecorder
from backend.app.orchestration.results import ProducedArtifact, TurnResult, announce
from backend.app.orchestration.run_state import RunState
from backend.app.services.conversion import deck_to_document, document_to_deck
from backend.app.services.patching import apply_patch
from backend.app.services.versioning import add_version, create_artifact, get_version

DECK_WORDS = ("slide", "deck", "presentation", "ppt")
DOCUMENT_WORDS = ("document", "report", "proposal", "section", "paragraph")


class EditTarget(NamedTuple):
    artifact: Artifact
    plan: ContentPlan
    profile: DocxTemplateProfile | PptxTemplateProfile
    template_path: Path | None


def run_edit(
    state: RunState, execution: ExecutionPlan, recorder: EventRecorder
) -> TurnResult:
    target = resolve_target(state, execution)
    if target is None:
        return TurnResult(
            reply="There is nothing to edit yet. Ask me to create a document or deck first.",
            plan=execution,
        )

    artifact, current_plan, profile, template_path = target
    instruction = execution.edit_instruction or state.user_message

    with recorder.step(EDITING_AGENT, instruction[:120]):
        patch = plan_edit(
            instruction, current_plan, state.provider, state.findings, profile.tone
        )

    if patch.is_empty():
        return TurnResult(reply="I could not find anything to change for that.", plan=execution)

    updated, applied = apply_patch(current_plan, patch.ops)
    recorder.note(patch.summary(), EDITING_AGENT, operations=len(applied))

    # Citations from previous versions stay valid, so they are carried forward
    # alongside anything this turn retrieved.
    citations = merge_citations(artifact, state)

    version = add_version(
        state.session,
        state.settings,
        artifact,
        updated,
        profile,
        citations,
        template_path,
        ops=[op.model_dump(mode="json") for op in applied],
        summary=patch.summary()[:400],
    )
    announce(recorder, artifact, version)

    report = validate_plan(updated, citations)
    return TurnResult(
        reply=patch.explanation or f"Updated {artifact.title} to v{version.version_no}.",
        plan=execution,
        produced=[ProducedArtifact(artifact, version, report)],
    )


def run_convert(
    state: RunState, execution: ExecutionPlan, recorder: EventRecorder
) -> TurnResult:
    target = resolve_target(state, execution)
    if target is None:
        return TurnResult(reply="There is no artifact to convert yet.", plan=execution)

    artifact, current_plan = target.artifact, target.plan

    with recorder.step("conversion", f"Converting {artifact.kind}"):
        if isinstance(current_plan, DocumentPlan):
            converted: ContentPlan = document_to_deck(current_plan)
            kind, profile, template_path = "deck", state.deck_profile(), state.deck_template_path()
        else:
            converted = deck_to_document(current_plan)
            kind = "document"
            profile = state.document_profile()
            template_path = state.document_template_path()

    new_artifact = create_artifact(
        state.session, state.conversation_id, kind, converted.title
    )
    citations = merge_citations(artifact, state)
    version = add_version(
        state.session,
        state.settings,
        new_artifact,
        converted,
        profile,
        citations,
        template_path,
        summary=f"Converted from {artifact.kind} {artifact.title!r}",
    )
    announce(recorder, new_artifact, version)

    report = validate_plan(converted, citations)
    return TurnResult(
        reply=f"Converted {artifact.title!r} into a {kind}.",
        plan=execution,
        produced=[ProducedArtifact(new_artifact, version, report)],
    )


def resolve_target(state: RunState, execution: ExecutionPlan) -> EditTarget | None:
    """The artifact an edit or conversion applies to, with its plan and profile."""
    artifact = next(
        (a for a in state.existing_artifacts if a.id == execution.target_artifact_id),
        None,
    ) or infer_target(state, execution)

    if artifact is None:
        return None

    version = get_version(artifact, None)
    plan = plan_from_dict(version.plan)

    if artifact.kind == "deck":
        return EditTarget(artifact, plan, state.deck_profile(), state.deck_template_path())
    return EditTarget(
        artifact, plan, state.document_profile(), state.document_template_path()
    )


def infer_target(state: RunState, execution: ExecutionPlan) -> Artifact | None:
    """Guess which artifact an unaddressed edit means, from its wording.

    Without this, "add an executive summary" always lands on whichever artifact
    was created last - usually the deck - even when the user clearly means the
    document.
    """
    if not state.existing_artifacts:
        return None

    said = f"{execution.edit_instruction} {state.user_message}".lower()
    wants_deck = any(word in said for word in DECK_WORDS)
    wants_document = any(word in said for word in DOCUMENT_WORDS)

    if wants_deck and not wants_document:
        kind = "deck"
    elif wants_document and not wants_deck:
        kind = "document"
    else:
        return state.existing_artifacts[-1]

    matching = [a for a in state.existing_artifacts if a.kind == kind]
    return matching[-1] if matching else state.existing_artifacts[-1]


def merge_citations(artifact: Artifact, state: RunState) -> list[Citation]:
    """Earlier citations plus anything retrieved this turn, de-duplicated."""
    previous = [Citation.model_validate(row) for row in get_version(artifact, None).citations]
    seen = {citation.id for citation in previous}
    return previous + [c for c in state.allocator.citations if c.id not in seen]
