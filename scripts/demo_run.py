"""Drives the assignment's example workflow against a running backend.

Uploads both sample templates, asks for a proposal and a deck, runs the four
follow-up edits, then downloads every artifact into ``samples/outputs`` and
reopens each one to verify the template's styling survived.

Start the backend first:

    uvicorn backend.app.main:app

Then:

    python scripts/demo_run.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import httpx
from docx import Document
from pptx import Presentation

# Model output contains characters cp1252 cannot encode, which would crash
# printing on a default Windows console.
if hasattr(sys.stdout, "reconfigure"):
    cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "samples" / "templates"
OUTPUTS = ROOT / "samples" / "outputs"
BASE_URL = "http://127.0.0.1:8000"

CREATE_PROMPT = (
    "Research the latest Generative AI trends and create a proposal and a "
    "6-slide presentation using the same tone and style as the uploaded files."
)
FOLLOW_UPS = [
    "Add an executive summary.",
    "Make the presentation more concise.",
    "Add a competitive analysis section.",
]

CHAT_TIMEOUT = 900.0


def main() -> int:
    OUTPUTS.mkdir(parents=True, exist_ok=True)

    client = httpx.Client(base_url=BASE_URL, timeout=CHAT_TIMEOUT)

    _banner("Health")
    if not _health(client):
        return 1

    _banner("Uploading templates")
    conversation_id = _upload_templates(client)

    _banner("Turn 1 - create")
    conversation_id = _turn(client, CREATE_PROMPT, conversation_id)

    for index, prompt in enumerate(FOLLOW_UPS, start=2):
        _banner(f"Turn {index} - edit")
        _turn(client, prompt, conversation_id)

    _banner("Artifacts and versions")
    _download_all(client, conversation_id)

    _banner("Verifying template preservation")
    _verify_outputs()

    print("\nDone. Generated files are in samples/outputs/")
    return 0


def _banner(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


def _health(client: httpx.Client) -> bool:
    response = client.get("/health")
    response.raise_for_status()

    ok = True
    for component in response.json()["components"]:
        mark = "OK " if component["configured"] else "XX "
        print(f"  {mark} {component['name']}: {component['detail']}")
        if component["name"] == "llm" and not component["configured"]:
            ok = False

    if not ok:
        print("\n  No LLM key configured. Set one in .env and restart the backend.")
    return ok


def _upload_templates(client: httpx.Client) -> str | None:
    conversation_id: str | None = None

    for name in ("Company_Proposal.docx", "Company_Template.pptx"):
        path = TEMPLATES / name
        if not path.exists():
            print(f"  missing {name} — run scripts/make_samples.py first")
            sys.exit(1)

        params = {"conversation_id": conversation_id} if conversation_id else {}
        response = client.post(
            "/api/uploads", files={"file": (name, path.read_bytes())}, params=params
        )
        response.raise_for_status()
        body = response.json()

        print(f"  {body['display_name']}  [{body['kind']}]")
        print(f"     {body['summary']}")

    return conversation_id


def _turn(client: httpx.Client, message: str, conversation_id: str | None) -> str:
    print(f'  > "{message}"\n')

    response = client.post(
        "/api/chat", json={"message": message, "conversation_id": conversation_id}
    )
    if response.status_code >= 400:
        print(f"  FAILED {response.status_code}: {response.text[:400]}")
        sys.exit(1)

    body = response.json()
    _print_trace(body["trace"])

    print(f"\n  intent: {body['intent']}")
    print(f"  reply : {body['reply']}")

    for artifact in body["artifacts"]:
        status = "pass" if artifact["validation_passed"] else "ISSUES"
        print(
            f"  -> {artifact['title']} [{artifact['kind']}] v{artifact['version']} "
            f"| validation {status} | {artifact['citation_coverage']:.0%} cited"
        )

    if body["citations"]:
        print(f"  sources ({len(body['citations'])}):")
        for citation in body["citations"][:5]:
            where = citation.get("url") or citation.get("source_document", "")
            print(f"     [{citation['id']}] {citation['title'][:60]} — {where[:60]}")

    return body["conversation_id"]


def _print_trace(trace: list[dict]) -> None:
    icons = {
        "run_started": ">",
        "plan_selected": "*",
        "agent_started": " ",
        "agent_finished": "+",
        "agent_failed": "!",
        "agent_skipped": "-",
        "note": ".",
        "artifact_ready": "#",
        "run_finished": "<",
    }

    for event in trace:
        icon = icons.get(event["kind"], " ")
        duration = f" ({event['duration_ms']} ms)" if event.get("duration_ms") else ""
        message = event.get("message", "")[:70]
        print(f"   {icon} {event['agent']:<22} {message}{duration}")


def _download_all(client: httpx.Client, conversation_id: str | None) -> None:
    artifacts = client.get(
        "/api/artifacts", params={"conversation_id": conversation_id}
    ).json()

    for artifact in artifacts:
        detail = client.get(f"/api/artifacts/{artifact['id']}").json()
        extension = "pptx" if artifact["kind"] == "deck" else "docx"

        print(f"\n  {artifact['title']} [{artifact['kind']}]")

        for version in detail["versions"]:
            number = version["version_no"]
            payload = client.get(
                f"/api/artifacts/{artifact['id']}/versions/{number}/download"
            ).content

            destination = OUTPUTS / f"{_slug(artifact['title'])}_v{number}.{extension}"
            destination.write_bytes(payload)

            summary = version["summary"] or "initial draft"
            print(
                f"     v{number}: {summary[:60]} "
                f"({version['citation_count']} sources, {len(payload):,} bytes)"
            )


def _verify_outputs() -> None:
    for path in sorted(OUTPUTS.glob("*.docx")):
        document = Document(str(path))
        # python-docx types styles[...] as BaseStyle and Paragraph.style as
        # optional; both are concrete here.
        font = cast(Any, document.styles["Normal"]).font.name
        header = document.sections[0].header.paragraphs[0].text.strip()
        headings = [
            p.text
            for p in document.paragraphs
            if p.style is not None and (p.style.name or "").startswith("Heading")
        ]
        print(f"  {path.name}")
        print(f"     body font : {font}   (template uses Georgia)")
        print(f"     header    : {header or '(none)'}")
        print(f"     headings  : {', '.join(headings[:6])}")

    for path in sorted(OUTPUTS.glob("*.pptx")):
        presentation = Presentation(str(path))
        master = cast(Any, presentation.slide_master.placeholders[0])
        master_font = master.text_frame.paragraphs[0].font
        titles = [
            slide.shapes.title.text
            for slide in presentation.slides
            if slide.shapes.title is not None
        ]
        print(f"  {path.name}")
        print(f"     slides     : {len(presentation.slides)}")
        print(f"     master font: {master_font.name}   (template uses Trebuchet MS)")
        print(f"     titles     : {', '.join(titles[:6])}")


def _slug(text: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in text).strip("_")
    return (cleaned[:50] or "artifact").lower()


if __name__ == "__main__":
    sys.exit(main())
