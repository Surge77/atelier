"""The agent trace.

Every agent step emits an event. The Streamlit UI renders these as a live
timeline, and they are stored on the assistant message so a past turn can be
replayed. Making the orchestration observable is what turns "multi-agent" from
a claim into something a reviewer can watch happen.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.core.logging import get_logger

logger = get_logger(__name__)

EventKind = Literal[
    "run_started",
    "plan_selected",
    "agent_started",
    "agent_finished",
    "agent_failed",
    "agent_skipped",
    "note",
    "artifact_ready",
    "run_finished",
]


class AgentEvent(BaseModel):
    kind: EventKind
    agent: str = "supervisor"
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# A sink is just a callable. No registry, no base class - a list's `append`
# and an SSE queue's `put` both satisfy it as they are.
EventSink = Callable[[AgentEvent], None]


class EventRecorder:
    """Collects events in order and forwards them to an optional live sink."""

    def __init__(self, sink: EventSink | None = None) -> None:
        self.events: list[AgentEvent] = []
        self._sink = sink

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)
        logger.info("[%s] %s %s", event.agent, event.kind, event.message)
        if self._sink is not None:
            self._sink(event)

    def note(self, message: str, agent: str = "supervisor", **data: Any) -> None:
        self.emit(AgentEvent(kind="note", agent=agent, message=message, data=data))

    def skipped(self, agent: str, reason: str) -> None:
        self.emit(AgentEvent(kind="agent_skipped", agent=agent, message=reason))

    @contextmanager
    def step(self, agent: str, message: str = ""):
        """Wrap one agent step so it is timed and its failure is recorded.

        The exception is re-raised: the recorder observes, it does not decide
        whether a failure is fatal. That call belongs to the orchestrator.
        """
        self.emit(AgentEvent(kind="agent_started", agent=agent, message=message))
        started = perf_counter()

        try:
            yield
        except Exception as exc:
            self.emit(
                AgentEvent(
                    kind="agent_failed",
                    agent=agent,
                    message=str(exc)[:300],
                    duration_ms=_elapsed_ms(started),
                )
            )
            raise

        self.emit(
            AgentEvent(kind="agent_finished", agent=agent, duration_ms=_elapsed_ms(started))
        )

    def as_dicts(self) -> list[dict[str, Any]]:
        return [event.model_dump(mode="json") for event in self.events]


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
