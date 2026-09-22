"""Atelier — the chat interface.

Run with:  streamlit run frontend/streamlit_app.py

The UI holds no business logic. Every turn is one POST to /api/chat; what comes
back is the reply, the artifacts, the citations and the full agent trace, all
of which are rendered here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Streamlit runs this file as a script, so the project root is not on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frontend.components.api_client import ApiError, AtelierClient
from frontend.components.artifacts_view import (
    render_artifacts_panel,
    render_inline_artifacts,
)
from frontend.components.sidebar import render_sidebar
from frontend.components.trace_view import render_citations, render_trace

EXAMPLE_PROMPTS = [
    "Research the latest Generative AI trends and create a proposal and a "
    "12-slide presentation using the same tone and style as the uploaded files.",
    "Add an executive summary.",
    "Make the presentation more concise.",
    "Add a competitive analysis section.",
]


def main() -> None:
    st.set_page_config(
        page_title="Atelier",
        page_icon="📄",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _init_state()
    client = AtelierClient()

    if not _ensure_conversation(client):
        render_sidebar(client)
        return

    render_sidebar(client)

    chat_column, artifacts_column = st.columns([3, 2], gap="large")

    with chat_column:
        _render_intro()
        _render_history()
        _render_pending_turn(client)

    with artifacts_column:
        render_artifacts_panel(client)

    # Outside the columns, so Streamlit pins it to the bottom of the viewport
    # rather than dropping it inline at the end of the left column.
    _capture_prompt()


def _init_state() -> None:
    defaults = {
        "conversation_id": None,
        "messages": [],
        "uploaded_signatures": set(),
        "uploader_generation": 0,
        "pending_prompt": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _ensure_conversation(client: AtelierClient) -> bool:
    """Claim a conversation before any upload can happen.

    Uploading first and letting the opening turn adopt the file left a window
    where a template belonged to no conversation, and a second conversation
    could strand it — generating with no template at all, silently.
    """
    if st.session_state.conversation_id:
        return True

    try:
        st.session_state.conversation_id = client.create_conversation()["id"]
    except ApiError as exc:
        st.error(f"Cannot reach the backend. {exc}")
        st.caption("Start it with: `uvicorn backend.app.main:app --reload`")
        return False

    return True


def _render_intro() -> None:
    """Only shown before the first turn; the brand lives in the sidebar."""
    if st.session_state.messages:
        return

    st.subheader("Start a document or a deck")
    st.caption(
        "Upload a template in the sidebar, then ask for what you need. "
        "Formatting is inherited from your template, not reconstructed."
    )

    for index, prompt in enumerate(EXAMPLE_PROMPTS):
        if st.button(
            prompt,
            key=f"example_{index}",
            help="Use this prompt",
            use_container_width=True,
        ):
            st.session_state.pending_prompt = prompt
            st.rerun()


def _render_history() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

            if message["role"] == "assistant":
                render_inline_artifacts(message.get("artifacts", []))
                render_citations(message.get("citations", []))
                render_trace(message.get("trace", []))


def _capture_prompt() -> None:
    """Queue a typed prompt and re-render, so history stays the single source."""
    if typed := st.chat_input("Ask for a document, a deck, or an edit…"):
        st.session_state.pending_prompt = typed
        st.rerun()


def _render_pending_turn(client: AtelierClient) -> None:
    prompt = st.session_state.pending_prompt
    if not prompt:
        return

    st.session_state.pending_prompt = None
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    _run_turn(client, prompt)
    st.rerun()


def _run_turn(client: AtelierClient, prompt: str) -> None:
    # A turn is several sequential model calls. A status block says what is
    # happening and roughly how long it takes, which a bare spinner does not.
    with st.chat_message("assistant"), st.status("Agents working…", expanded=True) as status:
        st.write("Supervisor is routing the request…")
        st.caption(
            "Research and generation run in sequence. "
            "A full document-plus-deck turn usually takes one to three minutes."
        )
        try:
            response = client.chat(prompt, st.session_state.conversation_id)
        except ApiError as exc:
            status.update(label="The turn failed", state="error")
            st.session_state.messages.append(
                {"role": "assistant", "content": _failure_message(exc)}
            )
            return

        status.update(
            label=f"Done — {len(response.get('artifacts', []))} artifact(s)",
            state="complete",
            expanded=False,
        )

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": response["reply"],
            "artifacts": response.get("artifacts", []),
            "citations": response.get("citations", []),
            "trace": response.get("trace", []),
        }
    )


def _failure_message(exc: ApiError) -> str:
    """One message in the transcript, naming a way to recover."""
    return (
        f"**That didn't work.** {exc}\n\n"
        "Check the system panel in the sidebar, then try again or rephrase."
    )


if __name__ == "__main__":
    main()
