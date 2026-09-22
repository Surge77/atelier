# The nine agents

Each agent is a plain function with an explicit signature, not a class behind a
uniform `run(state)` interface. A supervisor calling nine narrow functions
reads better than nine classes pretending to share a shape they do not, and
every agent can be unit-tested on its own with no orchestration in the way.

---

## 1. Supervisor — `agents/supervisor.py`

**In:** the user's message, plus a description of what already exists
(uploaded templates, artifacts generated earlier in the conversation).
**Out:** an `ExecutionPlan`.

```python
ExecutionPlan(
    intent="create" | "edit" | "convert" | "answer",
    reasoning="...",                 # one sentence, shown in the UI
    needs_web_research=bool,
    needs_enterprise_rag=bool,
    produce_document=bool,
    produce_deck=bool,
    document_brief="...",            # self-contained; the writer sees only this
    deck_brief="...",
    slide_count=12,
    edit_instruction="...",
    target_artifact_id="...",
)
```

The conversation context is what lets it tell *"make it shorter"* (an edit of
an existing artifact) from *"write a summary"* (a new one).

**On failure:** falls back to keyword routing so the system keeps answering.
Crude, but only reached after the provider's own retries are exhausted.

---

## 2. Document Analysis — `agents/analysis_agents.py`

**In:** a DOCX, PDF, image or text file.
**Out:** `AnalysisResult(extracted, DocxTemplateProfile)`.

Reads the file, then profiles it:

- **Style names that actually exist in the file.** python-docx raises
  `KeyError` for a style a document does not define, and corporate templates
  routinely rename or delete the built-ins — so guessing `"Heading 1"` crashes
  on exactly the documents this feature exists to serve.
- **Tone**, split in two: everything measurable (sentence length, paragraph
  length, heading case, whether bullets end in a full stop) is computed
  directly; only the subjective half goes to the model.
- **Verbatim excerpts**, which do most of the work at generation time. Showing
  a model four real sentences from the client's document beats telling it
  "formal".

A PDF or scan has no Word style table, so it yields safe built-in style names
while keeping its measured tone — which is what lets a user upload a scanned
proposal and still get output that reads like it.

**Scanned input:** the PDF reader extracts the text layer first and only treats
a page as a scan when that text is too sparse to be genuine. Pages are then
rasterised with `pypdfium2` and transcribed by the vision model.

---

## 3. PPT Analysis — `agents/analysis_agents.py`

**In:** a PPTX. **Out:** `AnalysisResult(extracted, PptxTemplateProfile)`.

- **Layouts classified structurally**, by which placeholders they carry, with
  the layout *name* used only as a tiebreaker. Structure is the reliable
  signal: a template localised into another language, or one whose layouts
  were renamed in-house, still has a title placeholder and two body
  placeholders where a two-content layout should be.
- **Theme fonts** read from the theme package part, which python-pptx does not
  expose directly.
- **Rhythm** — bullets per slide, words per bullet — measured from the
  template's own slides and used as generation limits.

---

## 4. Web Research — `agents/research_agents.py`

**In:** the request. **Out:** `ResearchFindings(evidence, citations, queries_used)`.

Expands the request into 2–3 focused queries before searching. A single
verbatim query (*"research generative AI trends and write a proposal"*)
retrieves poorly; focused queries retrieve well. That expansion is the only
model call the agent makes.

Results are de-duplicated by URL and results with too little content are
dropped. A failed query is logged and skipped — one bad query must not sink
the research step.

**Providers:** Tavily (needs a key) or DuckDuckGo (keyless default, combining
the Instant Answer API with parsed HTML results).

---

## 5. Enterprise RAG — `agents/research_agents.py`

**In:** the request. **Out:** `ResearchFindings`.

No model call: the request is embedded and matched directly. Rewriting the
query through an LLM first was tried and mostly added latency — the corpus is
small and the request already carries the right vocabulary.

Chunking follows the document's own headings rather than a character window,
so a chunk can cite *"page 4"* instead of *"characters 3200–3800"*.

**Backends:** Chroma (local, default) or Pinecone, behind one protocol.

---

## Shared: citation allocation

Web research and RAG both produce sources in the same turn, so id allocation
is shared through a single `CitationAllocator`. Otherwise both would start at
`S1` and collide.

```
S1 → https://example.com/state-of-ai
S2 → handbook.docx, page 4
```

---

## 6. Document Generation — `agents/generation_agents.py`

**In:** brief, `DocxTemplateProfile`, `ResearchFindings`.
**Out:** a `DocumentPlan`.

The prompt carries the tone profile with its verbatim excerpts, the template's
own outline as structure to mirror, the evidence, and the **exact set of valid
citation ids**.

Any id the model invents is **stripped before rendering**. A hallucinated id
would render into the References section as a source that does not exist,
which is worse than no citation at all.

---

## 7. PPT Generation — `agents/generation_agents.py`

**In:** brief, `PptxTemplateProfile`, `ResearchFindings`, slide count.
**Out:** a `DeckPlan`.

Also chooses a layout role per slide (`title`, `section_header`,
`title_content`, `two_content`) and writes speaker notes.

Bullets over the template's budget are trimmed **here, not in the renderer**,
so the stored plan matches what the file contains — otherwise a later edit
would operate on bullets the user never saw.

---

## 8. Validation — `agents/validation_agent.py`

**In:** a plan, the citations, the expected slide count. **Out:** a
`ValidationReport`.

Almost every check is deterministic, because those are the failures that
actually happen and they need no model call to find:

| Check | Severity |
|---|---|
| Citation id does not resolve | **error** |
| Document has no title | **error** |
| Fewer than two sections | **error** |
| A section has no content | **error** |
| Slide count differs from the request | **error** |
| A slide has no title | **error** |
| Section is very thin | warning |
| Slide exceeds the template's bullet budget | warning |
| A bullet runs to paragraph length | warning |
| Research ran but little content is cited | warning |

An **error** triggers exactly one repair pass: the specific failures are fed
back to the generation agent as an instruction. A model that failed the same
structural check twice will usually fail it a third time, and the user is
waiting.

A **warning** is reported but never blocks.

---

## 9. Editing — `agents/editing_agent.py`

**In:** an instruction, the current plan, optional fresh research.
**Out:** a `PatchPlan` — operations plus a one-sentence explanation.

The agent sees an **outline** of the plan (ids, headings, sizes), not its full
text. That keeps the call cheap and discourages the model from quietly
rewriting sections the user did not mention.

```
Document ops:  set_title · insert_section · replace_section ·
               update_section_heading · append_blocks · delete_section ·
               move_section

Deck ops:      set_title · insert_slide · replace_slide ·
               update_slide_title · update_slide_bullets · delete_slide ·
               move_slide
```

Fresh research threads through the same way it does for generation, which is
what makes *"update the report using the latest web information"* work: the
supervisor requests web research, and the new sources arrive here as citable
evidence.

---

## The trace

Every step emits an event, so the UI shows the orchestration happening:

```
▶  run_started      "Research the latest Generative AI trends…"
🧭 plan_selected    intent: create · agents: supervisor, web_research, …
⏳ agent_started    Supervisor — Deciding what this turn needs
✅ agent_finished   Supervisor — 1240 ms
⏭  agent_skipped    Enterprise RAG — Not needed for this request
•  note             Web Research — 6 web source(s)
✅ agent_finished   Validation — 3 ms
📄 artifact_ready   Generative AI Adoption Proposal v1
■  run_finished     2 artifact(s)
```

The trace is stored on the assistant message, so a past turn can be replayed.
This is what makes "multi-agent" something a reviewer can watch rather than
something the README claims.
