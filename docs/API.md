# API reference

Base URL: `http://localhost:8000`
Interactive docs: `http://localhost:8000/docs`

All `/api` routes accept an optional `X-API-Key` header. It is required only
when `ATELIER_API_KEY` is set.

## Error format

Every failure returns the same envelope. The message is always safe to show a
user; the detail stays in the server log.

```json
{
  "success": false,
  "error": { "code": "UPLOAD_REJECTED", "message": "Unsupported file type." },
  "timestamp": "2026-09-21T17:30:00+00:00"
}
```

| Code | Status |
|---|---|
| `VALIDATION_ERROR` | 400 / 422 |
| `UPLOAD_REJECTED` | 400 |
| `UNAUTHORIZED` | 401 |
| `NOT_FOUND` | 404 |
| `UNSUPPORTED_FORMAT` | 422 |
| `RATE_LIMITED` | 429 |
| `PROVIDER_ERROR` | 502 |
| `CONFIGURATION_ERROR`, `AGENT_ERROR`, `INTERNAL_ERROR` | 500 |

---

## `GET /health`

Reports which subsystems are usable. Never returns 503 for a missing API key —
the app is still running, and the UI uses this to tell the user what to set.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "components": [
    { "name": "llm",          "configured": true,  "detail": "groq:llama-3.3-70b-versatile" },
    { "name": "vector_store", "configured": true,  "detail": "chroma (local) at data/vectors" },
    { "name": "web_search",   "configured": true,  "detail": "duckduckgo (keyless)" }
  ]
}
```

---

## Uploads

### `POST /api/uploads`

Accept a template and analyse it. Multipart, field `file`. Optional
`conversation_id` query parameter.

Accepts `.docx .pptx .pdf .png .jpg .jpeg .webp .txt .md`, up to 25 MB.
Validated by extension **and** content signature; a DOCX cannot pose as a PPTX.

```bash
curl -X POST http://localhost:8000/api/uploads \
  -F "file=@samples/templates/Company_Proposal.docx"
```

```json
{
  "id": "0f3c…",
  "display_name": "Company_Proposal.docx",
  "kind": "docx",
  "size_bytes": 38542,
  "page_count": 1,
  "used_ocr": false,
  "summary": "5 headings, body style 'Normal', font Georgia, header and footer present",
  "profile": {
    "kind": "docx",
    "heading_styles": { "1": "Heading 1", "2": "Heading 2" },
    "body_style": "Normal",
    "bullet_style": "List Bullet",
    "default_font": "Georgia",
    "has_header": true,
    "outline": ["Northwind Analytics", "Executive Summary", "…"],
    "tone": { "formality": "…", "person": "third person", "sample_excerpts": ["…"] }
  }
}
```

A PPTX returns a `pptx` profile instead, with `layouts`, `layout_map`,
`aspect_ratio`, theme fonts and bullet rhythm.

### `GET /api/uploads` · `GET /api/uploads/{id}`

List uploads (optionally filtered by `conversation_id`), or fetch one with its
full profile.

---

## Chat

### `POST /api/chat`

Run one turn of the multi-agent conversation.

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Research the latest Generative AI trends and create a proposal and a 12-slide presentation using the same tone and style as the uploaded files.",
    "conversation_id": null
  }'
```

```json
{
  "conversation_id": "a1b2…",
  "reply": "Created Generative AI Adoption Proposal (document), Generative AI Trends (deck). Cited 6 source(s).",
  "intent": "create",
  "agents_involved": ["supervisor", "web_research", "document_generation",
                      "ppt_generation", "validation"],
  "artifacts": [
    {
      "id": "c3d4…",
      "kind": "document",
      "title": "Generative AI Adoption Proposal",
      "version": 1,
      "download_url": "/api/artifacts/c3d4…/versions/1/download",
      "validation_passed": true,
      "citation_coverage": 0.83
    }
  ],
  "citations": [
    { "id": "S1", "kind": "web", "title": "State of AI 2026",
      "url": "https://example.com/ai", "snippet": "…" }
  ],
  "trace": [
    { "kind": "run_started",   "agent": "supervisor", "message": "Research the latest…" },
    { "kind": "plan_selected", "message": "The user asked for a proposal and a deck",
      "data": { "intent": "create", "agents": ["supervisor", "web_research", "…"] } },
    { "kind": "agent_started", "agent": "web_research", "message": "Searching the web" },
    { "kind": "agent_finished","agent": "web_research", "duration_ms": 3412 },
    { "kind": "agent_skipped", "agent": "rag", "message": "Not needed for this request" },
    { "kind": "artifact_ready","message": "Generative AI Adoption Proposal v1" },
    { "kind": "run_finished",  "message": "2 artifact(s)" }
  ]
}
```

Pass the returned `conversation_id` on the next call to keep context. Follow-up
messages such as *"Add a competitive analysis section"* are routed as edits and
produce a new version of the existing artifact.

A turn runs several model calls in sequence; allow a generous client timeout.

### `GET /api/conversations/{id}/messages`

Replay a conversation, including each assistant turn's stored agent trace.

---

## Artifacts

### `GET /api/artifacts`

List artifacts, optionally filtered by `conversation_id`.

### `GET /api/artifacts/{id}`

One artifact with its full version history, current plan and citations.

```json
{
  "id": "c3d4…", "kind": "document", "title": "…", "current_version": 3,
  "versions": [
    { "version_no": 1, "summary": "Initial draft", "ops": [], "citation_count": 6,
      "validation": { "passed": true, "citation_coverage": 0.83 } },
    { "version_no": 2, "summary": "added section 'Competitive Analysis'",
      "ops": [ { "op": "insert_section", "position": 2 } ], "citation_count": 6 }
  ],
  "plan": { "kind": "document", "title": "…", "sections": [ … ] },
  "citations": [ … ]
}
```

### `GET /api/artifacts/{id}/versions/{n}/download`

Download **any** version as a real `.docx` or `.pptx`. The stored path is
re-resolved inside the artifacts directory rather than trusted, so a tampered
database row cannot read an arbitrary file.

### `POST /api/artifacts/{id}/convert`

Convert a document into a presentation, or a presentation into a document.
A pure transform of the content plan — no model call. Returns the new artifact.

---

## Enterprise knowledge base

### `POST /api/rag/documents`

Chunk, embed and index a document for the RAG agent. Multipart, field `file`.

```json
{ "document_id": "e5f6…", "display_name": "handbook.docx",
  "chunk_count": 24, "total_chunks_in_store": 88 }
```

### `GET /api/rag/documents` · `DELETE /api/rag/documents/{id}`

List indexed documents, or remove one and its chunks.

### `GET /api/rag/search?q=…&top_k=6`

Retrieval on its own, so the corpus can be inspected without a chat turn.

```json
[
  { "chunk_id": "e5f6…:3", "text": "Hotel spend is capped at 180 GBP…",
    "score": 0.81, "document_name": "handbook.docx",
    "heading": "Accommodation", "locator": "page 2" }
]
```
