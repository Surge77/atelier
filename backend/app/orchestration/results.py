"""What a turn produces.

Lives apart from the orchestrator so the producer and revision modules can
return these without importing the module that calls them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.agents.supervisor import ExecutionPlan
from backend.app.agents.validation_agent import ValidationReport
from backend.app.db.models import Artifact, ArtifactVersion
from backend.app.orchestration.events import AgentEvent, EventRecorder


@dataclass
class ProducedArtifact:
    artifact: Artifact
    version: ArtifactVersion
    validation: ValidationReport


@dataclass
class TurnResult:
    reply: str
    plan: ExecutionPlan
    produced: list[ProducedArtifact] = field(default_factory=list)


def announce(recorder: EventRecorder, artifact: Artifact, version: ArtifactVersion) -> None:
    """Tell the trace that a file is ready, so the UI can link to it."""
    recorder.emit(
        AgentEvent(
            kind="artifact_ready",
            message=f"{artifact.title} v{version.version_no}",
            data={
                "artifact_id": artifact.id,
                "version": version.version_no,
                "kind": artifact.kind,
            },
        )
    )
