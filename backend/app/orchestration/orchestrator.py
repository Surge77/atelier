"""Runs one conversational turn across the nine agents.

Reading this file top to bottom is the fastest way to understand the whole
system: the supervisor decides, research runs if asked for, producers produce,
validation gates, and versions are written. No framework, no hidden control
flow — the sequence you can see is the sequence that executes.

The two branches that change an artifact rather than creating one live in
``revisions``, and the generate-validate-render cycle lives in ``producers``.
"""

from __future__ import annotations

from backend.app.agents.research_agents import (
    RAG_AGENT,
    WEB_RESEARCH_AGENT,
    merge_findings,
    research_web,
    retrieve_enterprise_knowledge,
)
from backend.app.agents.supervisor import (
    SUPERVISOR_AGENT,
    ExecutionPlan,
    describe_context,
    plan_turn,
)
from backend.app.core.logging import get_logger
from backend.app.orchestration.events import AgentEvent, EventRecorder
from backend.app.orchestration.producers import produce_deck, produce_document
from backend.app.orchestration.results import ProducedArtifact, TurnResult
from backend.app.orchestration.revisions import run_convert, run_edit
from backend.app.orchestration.run_state import RunState

logger = get_logger(__name__)

__all__ = ["ProducedArtifact", "TurnResult", "run_turn"]


def run_turn(state: RunState, recorder: EventRecorder) -> TurnResult:
    """Plan the turn, execute it, and return what was produced."""
    recorder.emit(AgentEvent(kind="run_started", message=state.user_message[:200]))

    execution = _route(state, recorder)
    recorder.emit(
        AgentEvent(
            kind="plan_selected",
            message=execution.reasoning or execution.intent,
            data={
                "intent": execution.intent,
                "agents": execution.agents_involved(),
                "web_research": execution.needs_web_research,
                "enterprise_rag": execution.needs_enterprise_rag,
            },
        )
    )

    _gather_knowledge(state, execution, recorder)

    if execution.intent == "edit":
        result = run_edit(state, execution, recorder)
    elif execution.intent == "convert":
        result = run_convert(state, execution, recorder)
    elif execution.produces_anything():
        result = _run_create(state, execution, recorder)
    else:
        result = TurnResult(reply=execution.reply or _default_reply(state), plan=execution)

    recorder.emit(
        AgentEvent(kind="run_finished", message=f"{len(result.produced)} artifact(s)")
    )
    return result


def _route(state: RunState, recorder: EventRecorder) -> ExecutionPlan:
    with recorder.step(SUPERVISOR_AGENT, "Deciding what this turn needs"):
        return plan_turn(
            state.user_message,
            state.provider,
            describe_context(state.template_names(), state.artifact_titles()),
        )


def _gather_knowledge(
    state: RunState, execution: ExecutionPlan, recorder: EventRecorder
) -> None:
    """Run whichever research agents the supervisor asked for."""
    groups = []

    if execution.needs_web_research:
        with recorder.step(WEB_RESEARCH_AGENT, "Searching the web"):
            findings = research_web(
                state.user_message,
                state.search_provider,
                state.allocator,
                state.provider,
                state.settings.search_max_results,
            )
        recorder.note(
            f"{len(findings.citations)} web source(s)",
            WEB_RESEARCH_AGENT,
            queries=findings.queries_used,
        )
        groups.append(findings)
    else:
        recorder.skipped(WEB_RESEARCH_AGENT, "Not needed for this request")

    if execution.needs_enterprise_rag:
        with recorder.step(RAG_AGENT, "Searching the enterprise knowledge base"):
            findings = retrieve_enterprise_knowledge(
                state.user_message, state.store, state.allocator, state.settings.rag_top_k
            )
        recorder.note(f"{len(findings.citations)} enterprise chunk(s)", RAG_AGENT)
        groups.append(findings)
    else:
        recorder.skipped(RAG_AGENT, "Not needed for this request")

    state.findings = merge_findings(*groups)


def _run_create(
    state: RunState, execution: ExecutionPlan, recorder: EventRecorder
) -> TurnResult:
    produced: list[ProducedArtifact] = []

    if execution.produce_document:
        produced.append(produce_document(state, execution, recorder))

    if execution.produce_deck:
        produced.append(produce_deck(state, execution, recorder))

    return TurnResult(reply=_describe(produced, state), plan=execution, produced=produced)


def _describe(produced: list[ProducedArtifact], state: RunState) -> str:
    if not produced:
        return "I did not produce anything for that request."

    names = ", ".join(f"{p.artifact.title} ({p.artifact.kind})" for p in produced)
    sources = len(state.allocator.citations)
    trailer = f" Cited {sources} source(s)." if sources else ""
    return f"Created {names}.{trailer}"


def _default_reply(state: RunState) -> str:
    return (
        "I can analyse an uploaded template, research a topic, and produce an "
        "editable document or deck in that template's style. "
        f"You have {len(state.template_names())} template(s) uploaded."
    )
