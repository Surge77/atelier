"""Sidebar: system status, template uploads, and the knowledge base."""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.components.api_client import ApiError, AtelierClient

TEMPLATE_TYPES = ["docx", "pptx", "pdf", "png", "jpg", "jpeg", "webp", "txt", "md"]
KNOWLEDGE_TYPES = ["docx", "pptx", "pdf", "txt", "md"]


def render_sidebar(client: AtelierClient) -> None:
    with st.sidebar:
        st.title("Atelier")
        st.caption("Multi-agent document and presentation studio")

        _render_status(client)
        st.divider()
        _render_templates(client)
        st.divider()
        _render_knowledge_base(client)
        st.divider()
        _render_reset()


def _render_status(client: AtelierClient) -> None:
    st.subheader("System")

    try:
        health = client.health()
    except ApiError as exc:
        st.error(str(exc))
        st.caption("Start the backend: `uvicorn backend.app.main:app --reload`")
        return

    for component in health.get("components", []):
        icon = "🟢" if component["configured"] else "🔴"
        st.markdown(f"{icon} **{component['name']}** — {component['detail']}")

    if not all(c["configured"] for c in health.get("components", [])):
        st.caption("Set the missing key in `.env`, then restart the backend.")


def _render_templates(client: AtelierClient) -> None:
    st.subheader("Templates")
    st.caption("Uploads define the style your artifacts inherit.")

    # The assignment's own workflow uploads a DOCX and a PPTX together, so a
    # single-file uploader would reject one of them.
    uploaded = st.file_uploader(
        "Upload templates",
        type=TEMPLATE_TYPES,
        accept_multiple_files=True,
        key=f"template_uploader_{st.session_state.uploader_generation}",
        help="DOCX, PPTX, PDF or a scanned image. Styles and tone are reused.",
    )

    for item in uploaded or []:
        _ingest_template(client, item)

    _list_templates(client)


def _ingest_template(client: AtelierClient, uploaded: Any) -> None:
    signature = (uploaded.name, uploaded.size)
    if signature in st.session_state.uploaded_signatures:
        return

    with st.spinner(f"Analysing {uploaded.name}…"):
        try:
            result = client.upload_template(
                uploaded.name, uploaded.getvalue(), st.session_state.conversation_id
            )
        except ApiError as exc:
            st.error(str(exc))
            return

    st.session_state.uploaded_signatures.add(signature)
    st.success(f"{result['display_name']}: {result['summary']}")
    if result.get("used_ocr"):
        st.caption("Read with vision OCR — the file had no text layer.")


def _list_templates(client: AtelierClient) -> None:
    try:
        uploads = client.list_uploads(st.session_state.conversation_id)
    except ApiError:
        return

    if not uploads:
        st.caption("No templates yet.")
        return

    for upload in uploads:
        st.markdown(f"`{upload['kind']}` {upload['display_name']}")


def _render_knowledge_base(client: AtelierClient) -> None:
    st.subheader("Enterprise knowledge")
    st.caption("Indexed documents the RAG agent can retrieve and cite.")

    uploaded = st.file_uploader(
        "Add documents",
        type=KNOWLEDGE_TYPES,
        accept_multiple_files=True,
        key=f"knowledge_uploader_{st.session_state.uploader_generation}",
        help="Indexed for retrieval. The RAG agent cites these by document and page.",
    )

    for item in uploaded or []:
        _ingest_knowledge(client, item)

    _list_knowledge(client)


def _ingest_knowledge(client: AtelierClient, uploaded: Any) -> None:
    signature = ("kb", uploaded.name, uploaded.size)
    if signature in st.session_state.uploaded_signatures:
        return

    with st.spinner(f"Indexing {uploaded.name}…"):
        try:
            result = client.ingest_knowledge(uploaded.name, uploaded.getvalue())
        except ApiError as exc:
            st.error(str(exc))
            return

    st.session_state.uploaded_signatures.add(signature)
    st.success(f"Indexed {result['chunk_count']} chunk(s)")


def _list_knowledge(client: AtelierClient) -> None:
    try:
        documents = client.list_knowledge()
    except ApiError:
        return

    if not documents:
        st.caption("Knowledge base is empty.")
        return

    for document in documents:
        columns = st.columns([4, 1])
        columns[0].markdown(
            f"{document['display_name']} · {document['chunk_count']} chunk(s)"
        )
        if columns[1].button(
            "Remove",
            key=f"del_{document['id']}",
            help=f"Remove {document['display_name']} from the knowledge base",
        ):
            client.delete_knowledge(document["id"])
            st.rerun()


def _render_reset() -> None:
    if st.button("New conversation", use_container_width=True):
        st.session_state.conversation_id = None
        st.session_state.messages = []
        st.session_state.uploaded_signatures = set()
        # Changing the key resets the file_uploader widgets, which otherwise
        # keep showing the previous conversation's files.
        st.session_state.uploader_generation += 1
        st.rerun()
