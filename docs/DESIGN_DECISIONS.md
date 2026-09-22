# Design decisions

What was chosen, what was rejected, and why. Written so the reasoning survives
the person who made it.

---

## 1. The content plan is the source of truth

**Decision.** Generation produces a typed `ContentPlan`. A DOCX or PPTX file is
only ever a rendering of one.

**Rejected.** Generating the file directly, and re-generating it on every edit.

**Why.** The naive approach is why conversational editing normally destroys
formatting: each edit rebuilds the document from scratch and the styling drifts
a little further. Keeping a plan and re-rendering means the formatting was
never in the mutable thing to begin with.

Three requirements collapse into this one mechanism:

- editing without losing structure → patch the plan, re-render
- document ↔ presentation conversion → transform between plan shapes
- versioning and diffing → snapshot and compare plans

---

## 2. The uploaded template *is* the output file

**Decision.** The renderer opens the user's template, clears its body, and
writes new content through the template's own style names.

**Rejected.** Reading the template's fonts and colours and applying them to a
fresh document.

**Why.** A corporate template's identity lives almost entirely outside the
document body: the theme, the style table, numbering definitions, headers and
footers, page geometry, slide masters and layouts. Copying those faithfully is
a large amount of code that is wrong in a different way for every template.
Inheriting them is three lines and cannot drift.

Two details are easy to get wrong:

- **DOCX:** `w:sectPr` must be preserved when clearing the body. It carries
  page size, margins, orientation and the links to the header and footer parts.
  Deleting it silently resets the page and drops the running header.
- **PPTX:** removing a slide requires dropping the relationship *and* the
  `sldIdLst` entry. Doing only the latter orphans the slide part and leaves a
  file PowerPoint reports as corrupt.

---

## 3. No agent framework

**Decision.** A typed `ExecutionPlan`, a shared `RunState`, and ~200 lines of
readable orchestration.

**Rejected.** LangGraph.

**Why.** The supervisor's routing is a single structured call; the rest of the
turn is deterministic sequencing. A `StateGraph` over that is mostly ceremony
around linear edges, and it pulls in `langchain-core` and `langsmith` with
their release cadence.

The deciding factor was explainability. `orchestrator.py` can be read top to
bottom and the sequence you see is the sequence that executes. Every agent is a
plain function, unit-testable with no graph in the way.

**The tradeoff.** No free checkpointing or built-in streaming. Neither was
needed: versions are stored in SQLite regardless, and the event recorder gives
a trace that maps directly onto the UI.

---

## 4. The supervisor decides once per turn

**Decision.** One structured call returns the whole routing decision.

**Rejected.** A ReAct-style loop that re-plans after each step.

**Why.** Re-planning after every step multiplies cost and latency by the number
of steps, and makes the system's behaviour impossible to explain or test. One
auditable decision object is displayed in the UI, stored with the message, and
followed by code.

**The tradeoff.** The system cannot change course mid-turn if research comes
back empty. In practice the generation agents handle empty evidence correctly —
they are told to leave citations empty rather than invent them.

---

## 5. Everything external is a protocol

**Decision.** `LLMProvider`, `VectorStore`, `SearchProvider` and `Transcriber`
are abstractions with multiple implementations.

**Why.** The whole test suite runs with no network and no API key, and the
assignment's Pinecone requirement is satisfied without forcing an account on
anyone who clones the repository.

This is not speculative generality: every one of these has two or more real
implementations in the repository today.

---

## 6. Local-first defaults

**Decision.** Chroma with local `fastembed` embeddings, and keyless DuckDuckGo
search.

**Rejected.** Pinecone and a hosted embedding API as the default.

**Why.** A reviewer should be able to clone, `pip install`, add one LLM key and
have retrieval work. `fastembed` runs quantised ONNX on CPU with no torch
dependency; the model downloads once (~90 MB) and is cached.

Pinecone ships as an adapter behind one environment variable, so the
assignment's explicit requirement is met without the cost.

---

## 7. Structured output by schema validation, not vendor JSON mode

**Decision.** Ask for JSON, validate against a Pydantic model, and on failure
hand the **validation error itself** back as a follow-up turn.

**Rejected.** Each vendor's native structured-output feature.

**Why.** The three supported providers differ here and change over time. One
mechanism works everywhere.

Feeding the specific error back repairs far more failures than a blind retry,
because the model is told exactly what was wrong. Budget: two repairs.

---

## 8. Validation is deterministic

**Decision.** Citation resolution, structure, counts and budgets are checked in
code. Only tone is a model judgement, and it never blocks.

**Rejected.** An LLM-as-judge quality gate.

**Why.** The failures that actually occur are structural: an invented citation
id, a deck with 10 slides when 12 were asked for, an empty section. Code finds
those instantly, for free, deterministically, and can be unit-tested. An LLM
judge would be slower, cost more, and be non-deterministic about exactly the
things that are easiest to check exactly.

---

## 9. One repair attempt

**Decision.** `MAX_REPAIR_ATTEMPTS = 1`.

**Why.** A model that failed the same structural check twice usually fails it a
third time, and each attempt is a full generation call while the user waits. If
the second attempt still fails, the artifact is produced anyway with its issues
reported rather than withheld — a flawed draft the user can edit beats an error
message.

---

## 10. Synchronous route handlers

**Decision.** `def`, not `async def`.

**Why.** FastAPI runs sync handlers on a worker thread pool. The workload is
CPU-bound — rendering Office files, computing embeddings — and the provider
SDKs are used synchronously. Async would add plumbing without adding
concurrency, and would risk blocking the event loop on CPU work.

This deviates from a blanket "always `async def`" rule, but it satisfies that
rule's actual purpose (never block the event loop) more directly.

---

## 11. `pypdf` + `pypdfium2`, not PyMuPDF

**Decision.** `pypdf` (BSD) reads text; `pypdfium2` (BSD/Apache) rasterises
pages for vision OCR.

**Rejected.** PyMuPDF, which is the more capable library.

**Why.** PyMuPDF is **AGPL-3.0**. For a repository that will be published, that
is a real licensing obligation rather than a footnote. `pdf2image` was also
rejected: it needs the poppler binary, which is awkward on Windows.

Every dependency in this project is MIT, BSD or Apache.

---

## 12. Vision OCR, not Tesseract

**Decision.** Rasterise the page and transcribe with the configured vision
model.

**Why.** No native binary to install, and markedly better handling of real
layout — tables, columns, headings — than classical OCR. The prompt asks for
structural markers (`# ` for headings, `- ` for list items), so the transcript
arrives already segmented rather than as a wall of text.

Only used when the PDF's own text layer is too sparse to be genuine, so digital
PDFs stay on the fast, free path.

---

## 13. Skipped edits are not fatal

**Decision.** A patch operation targeting a section or slide that no longer
exists is logged and skipped.

**Why.** The editing agent works from an outline and will occasionally name a
stale id. Losing one operation is a far better outcome than failing the user's
whole turn. The version record lists the operations that **actually applied**,
so the history stays accurate.

---

## 14. Streamlit, not React

**Decision.** A Streamlit chat UI.

**Why.** The value of this project is the multi-agent backend. Streamlit puts a
working chat interface — file upload, live agent trace, artifact cards, version
history, citation panel — in about 500 readable lines of the same language as
the rest of the codebase.

The UI holds no business logic, so it is disposable: the same API serves a
React frontend or a CLI without a backend change.

**The tradeoff.** Less visual polish than a bespoke frontend.

---

## What would change for production

| Now | Then | Why |
|---|---|---|
| SQLite | Postgres | Concurrent writers. No SQLite-specific SQL is used, so this is a URL change. |
| Inline generation | Job queue + polling | A 12-slide research turn takes minutes; a queue survives a dropped connection. |
| In-process rate limit | Redis | Correct across multiple workers. |
| Local filesystem | S3 or equivalent | Artifacts must outlive the container. |
| Optional shared secret | Real authentication | Per-user artifact isolation. |
| Whole-file re-render | Incremental render | Only matters for very large documents. |
