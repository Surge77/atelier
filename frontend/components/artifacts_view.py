"""Artifact cards: download any version, inspect history, convert."""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.components.api_client import ApiError, AtelierClient

EXTENSIONS = {"document": "docx", "deck": "pptx"}
MEDIA_TYPES = {
    "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "deck": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
KIND_LABELS = {"document": "Document", "deck": "Presentation"}
KIND_ICONS = {"document": "📄", "deck": "📊"}


def render_artifacts_panel(client: AtelierClient) -> None:
    """Every artifact in the conversation, newest first."""
    st.subheader("Artifacts")

    conversation_id = st.session_state.conversation_id
    if not conversation_id:
        _render_empty_state()
        return

    try:
        artifacts = client.list_artifacts(conversation_id)
    except ApiError as exc:
        st.error(str(exc))
        return

    if not artifacts:
        _render_empty_state()
        return

    for artifact in artifacts:
        _render_card(client, artifact)


def _render_empty_state() -> None:
    """A blank panel tells the user nothing about what belongs here."""
    with st.container(border=True):
        st.markdown("**Nothing generated yet**")
        st.caption(
            "Documents and presentations will appear here once you ask for one. "
            "Every edit adds a version, and every version stays downloadable."
        )


def _render_card(client: AtelierClient, artifact: dict[str, Any]) -> None:
    kind = artifact["kind"]
    icon = KIND_ICONS.get(kind, "📁")
    label = KIND_LABELS.get(kind, kind)

    with st.container(border=True):
        st.markdown(f"{icon} **{artifact['title']}**")
        st.caption(f"{label} · version {artifact['current_version']}")

        _current_download(client, artifact)

        with st.expander("Version history"):
            _render_history(client, artifact)


def _current_download(client: AtelierClient, artifact: dict[str, Any]) -> None:
    """Fetch only the newest version, which is the one people actually want."""
    version_no = artifact["current_version"]
    extension = EXTENSIONS.get(artifact["kind"], "bin")

    try:
        payload = client.download(artifact["id"], version_no)
    except ApiError:
        st.caption("This version's file is unavailable.")
        return

    st.download_button(
        f"Download v{version_no}",
        data=payload,
        file_name=f"{_slug(artifact['title'])}_v{version_no}.{extension}",
        mime=MEDIA_TYPES.get(artifact["kind"], "application/octet-stream"),
        key=f"dl_{artifact['id']}_{version_no}",
        type="primary",
        use_container_width=True,
    )


def _render_history(client: AtelierClient, artifact: dict[str, Any]) -> None:
    try:
        detail = client.get_artifact(artifact["id"])
    except ApiError as exc:
        st.error(str(exc))
        return

    for version in reversed(detail.get("versions", [])):
        row, action = st.columns([4, 1], vertical_alignment="center")

        summary = version["summary"] or "Initial draft"
        row.markdown(f"**v{version['version_no']}** — {summary}")
        row.caption(" · ".join(_version_details(version)))

        # A link, not a download_button: Streamlit renders expander contents
        # even while collapsed, so eagerly fetching every version's bytes on
        # every rerun would download the whole history repeatedly.
        action.link_button(
            "Open",
            url=(
                f"{client.base_url}/api/artifacts/{artifact['id']}"
                f"/versions/{version['version_no']}/download"
            ),
            use_container_width=True,
        )

    _convert_button(client, artifact)


def _version_details(version: dict[str, Any]) -> list[str]:
    details = [f"{version['citation_count']} source(s)"]

    if validation := version.get("validation"):
        status = "validation passed" if validation.get("passed") else "validation has issues"
        details.append(status)
        details.append(f"{validation.get('citation_coverage', 0):.0%} cited")

    if version.get("ops"):
        details.append(f"{len(version['ops'])} edit(s)")

    return details


def _convert_button(client: AtelierClient, artifact: dict[str, Any]) -> None:
    target = "presentation" if artifact["kind"] == "document" else "document"

    if st.button(
        f"Convert to {target}",
        key=f"convert_{artifact['id']}",
        help=f"Create a new {target} from this artifact's content",
        use_container_width=True,
    ):
        with st.spinner(f"Converting to {target}…"):
            try:
                client.convert(artifact["id"])
            except ApiError as exc:
                st.error(str(exc))
                return
        st.rerun()


def render_inline_artifacts(artifacts: list[dict[str, Any]]) -> None:
    """The compact result strip shown directly under an assistant reply."""
    if not artifacts:
        return

    for artifact in artifacts:
        # Status is carried by the word as well as the glyph, so it does not
        # depend on colour or icon recognition alone.
        status = "validated" if artifact["validation_passed"] else "needs review"
        icon = KIND_ICONS.get(artifact["kind"], "📁")
        label = KIND_LABELS.get(artifact["kind"], artifact["kind"])

        st.markdown(
            f"{icon} **{artifact['title']}** — {label} v{artifact['version']} · "
            f"{status} · {artifact['citation_coverage']:.0%} of content cited"
        )


def _slug(text: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in text).strip("_")
    return (cleaned[:60] or "artifact").lower()
