"""Renders the agent trace as a timeline.

This is the part of the UI that makes "multi-agent" visible rather than
claimed: the reviewer watches the supervisor route the turn, sees which
specialists ran, which were skipped and why, and how long each took.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

AGENT_LABELS = {
    "supervisor": "Supervisor",
    "document_analysis": "Document Analysis",
    "ppt_analysis": "PPT Analysis",
    "web_research": "Web Research",
    "rag": "Enterprise RAG",
    "document_generation": "Document Generation",
    "ppt_generation": "PPT Generation",
    "validation": "Validation",
    "editing": "Editing",
    "conversion": "Conversion",
}

KIND_ICONS = {
    "run_started": "▶",
    "plan_selected": "🧭",
    "agent_started": "⏳",
    "agent_finished": "✅",
    "agent_failed": "❌",
    "agent_skipped": "⏭",
    "note": "•",
    "artifact_ready": "📄",
    "run_finished": "■",
}


def render_trace(trace: list[dict[str, Any]], expanded: bool = False) -> None:
    if not trace:
        return

    agents = _agents_in(trace)
    title = f"Agent trace — {len(agents)} agent(s), {len(trace)} step(s)"

    with st.expander(title, expanded=expanded):
        for event in trace:
            _render_event(event)


def _render_event(event: dict[str, Any]) -> None:
    kind = event.get("kind", "note")
    icon = KIND_ICONS.get(kind, "•")
    agent = AGENT_LABELS.get(event.get("agent", ""), event.get("agent", ""))
    message = event.get("message", "")
    duration = event.get("duration_ms")

    if kind == "agent_finished":
        st.markdown(f"{icon} **{agent}** finished in {duration} ms")
        return

    if kind == "agent_skipped":
        st.markdown(f"{icon} **{agent}** skipped — {message}")
        return

    if kind == "plan_selected":
        data = event.get("data", {})
        st.markdown(f"{icon} **Routing:** {message}")
        st.caption(
            f"intent: `{data.get('intent')}` · "
            f"agents: {', '.join(data.get('agents', []))}"
        )
        return

    if kind == "note" and event.get("data"):
        st.markdown(f"{icon} **{agent}** — {message}")
        _render_note_data(event["data"])
        return

    st.markdown(f"{icon} **{agent}** {message}".rstrip())


def _render_note_data(data: dict[str, Any]) -> None:
    if queries := data.get("queries"):
        st.caption("Searched: " + " · ".join(f"`{q}`" for q in queries))
    if (coverage := data.get("coverage")) is not None:
        st.caption(f"Citation coverage: {coverage:.0%}")
    if (operations := data.get("operations")) is not None:
        st.caption(f"{operations} edit operation(s) applied")


def _agents_in(trace: list[dict[str, Any]]) -> set[str]:
    return {
        event.get("agent", "")
        for event in trace
        if event.get("kind") in ("agent_started", "agent_skipped")
    }


def render_citations(citations: list[dict[str, Any]]) -> None:
    """The source list behind a reply."""
    if not citations:
        return

    with st.expander(f"Sources ({len(citations)})", expanded=False):
        for citation in citations:
            identifier = citation.get("id", "?")
            title = citation.get("title", "Untitled")

            if citation.get("kind") == "web":
                url = citation.get("url", "")
                st.markdown(f"**[{identifier}]** [{title}]({url})")
            else:
                document = citation.get("source_document", "enterprise document")
                locator = citation.get("locator") or ""
                suffix = f", {locator}" if locator else ""
                st.markdown(f"**[{identifier}]** {title} — _{document}{suffix}_")

            if snippet := citation.get("snippet"):
                st.caption(snippet[:240] + ("…" if len(snippet) > 240 else ""))
