# Atelier

A multi-agent AI chatbot that reads your document and presentation templates,
researches a topic on the live web and in your own knowledge base, and writes
**editable DOCX and PPTX files in your template's own style** — then keeps
editing them in conversation without losing their formatting.

```
You:  Research the latest Generative AI trends and create a proposal and a
      12-slide presentation using the same tone and style as the uploaded files.

      → Supervisor routes the turn
      → Web Research finds 6 sources
      → Document Generation writes the proposal
      → PPT Generation builds 12 slides
      → Validation checks every citation resolves
      → proposal.docx + deck.pptx, in your fonts, with a References section

You:  Add a competitive analysis section.

      → v2, with the new section and everything else untouched
```

---

## The one idea worth knowing

**Every artifact is a content plan. The DOCX or PPTX file is only ever a
rendering of it.**

```
  your template ──► style profile  (styles, layouts, theme, tone)
                          │
  research ─────────► evidence + citations
                          │
                          ▼
                    ContentPlan           ◄── the source of truth
                   /            \
        DocxRenderer          PptxRenderer
              │                      │
           .docx                  .pptx      ← real files, editable in Office
```

Almost every requirement falls out of that single decision:

| Requirement | How it works |
|---|---|
| **Keeps your formatting** | The renderer opens *your uploaded template* as the output file, clears its body, and writes through the template's own style names. Fonts, theme colours, headers, footers and slide masters are inherited, never reconstructed — so they cannot drift. |
| **Conversational editing** | Edits produce operations against the **plan**, never the binary. The file is re-rendered through the same template. Formatting loss is structurally impossible, because the formatting was never in the plan to lose. |
| **Document ↔ presentation** | A pure transform between two plan shapes. No model call, no cost. |
| **Versioning & traceability** | A version is a plan snapshot plus the operations that produced it. Diffing two versions is diffing two plans. |

---

## Setup

**Requires:** Python 3.11 or 3.12.

```bash
git clone <your-repo-url>
cd atelier

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt

cp env.example .env
```

Open `.env` and set one key:

```ini
ATELIER_GROQ_API_KEY=gsk_...
```

Get a free one at [console.groq.com/keys](https://console.groq.com/keys).
Prefer another provider? Set `ATELIER_LLM_PROVIDER` to `openai` or `anthropic`
and fill in that key instead — nothing else changes.

**Retrieval and web search need no keys at all.** They default to a local
Chroma index with local embeddings, and to keyless DuckDuckGo search.

> **Scanned PDFs and images** are read by a vision model. Groq's catalogue
> currently exposes none, so OCR needs `ATELIER_LLM_PROVIDER=openai` or
> `anthropic`. Everything else works on Groq. A PDF that carries its own text
> layer never touches the vision path.

### Generate the sample templates

```bash
python scripts/make_samples.py
```

This writes `samples/templates/Company_Proposal.docx` and
`Company_Template.pptx` — branded with a distinctive body font, heading
colours, a running header and footer, and styled slide masters, so you can see
for yourself that the styling is being preserved rather than invented.

### Run it

Two terminals:

```bash
# Terminal 1 — API
uvicorn backend.app.main:app --reload

# Terminal 2 — chat UI
streamlit run frontend/streamlit_app.py
```

Then open **http://localhost:8501**.

API docs are served at **http://localhost:8000/docs**.

---

## Usage

1. **Upload a template** in the sidebar — DOCX, PPTX, PDF, or a scanned image.
   The analysis agents read its structure, its style names and its tone, and
   the sidebar tells you what they found.

2. **Optionally add enterprise documents** under *Enterprise knowledge*. These
   are chunked, embedded locally and indexed; the RAG agent retrieves and cites
   them.

3. **Ask for something.** For example:

   > Research the latest Generative AI trends and create a proposal and a
   > 12-slide presentation using the same tone and style as the uploaded files.

4. **Watch the agent trace** under the reply — which agents ran, which were
   skipped and why, what was searched, and how much of the output is cited.

5. **Download** either artifact and open it in Word or PowerPoint. The
   template's fonts, colours, header, footer and slide master are all there.

6. **Keep editing in conversation:**

   > Add an executive summary.
   > Make the presentation more concise.
   > Add a competitive analysis section.
   > Update the report using the latest web information.

   Each produces a new version. Every version stays downloadable from the
   artifact card.

7. **Convert** a document into a presentation, or the reverse, from the
   artifact's version history.

---

## The nine agents

| # | Agent | What it does |
|---|---|---|
| 1 | **Supervisor** | Reads the request and decides the turn: which specialists to invoke, what to produce, and the brief each producer receives |
| 2 | **Document Analysis** | Reads DOCX, PDF or images (with vision OCR for scans) into a style and tone profile |
| 3 | **PPT Analysis** | Reads PPTX layouts, theme fonts, slide size and bullet rhythm |
| 4 | **Web Research** | Expands the request into focused queries, searches live, returns cited passages |
| 5 | **Enterprise RAG** | Retrieves from your indexed corpus with document and page citations |
| 6 | **Document Generation** | Writes a `DocumentPlan` in the template's voice |
| 7 | **PPT Generation** | Builds a `DeckPlan` matching the template's rhythm |
| 8 | **Validation** | Checks citations resolve, structure holds, budgets are respected — one repair pass on failure |
| 9 | **Editing** | Turns "add a competitive analysis section" into patch operations on the plan |

See [docs/AGENTS.md](docs/AGENTS.md) for each agent's inputs, outputs and
failure behaviour.

---

## Configuration

Everything is environment-driven. Full list in `env.example`.

| Variable | Default | Notes |
|---|---|---|
| `ATELIER_LLM_PROVIDER` | `groq` | `groq`, `openai` or `anthropic` |
| `ATELIER_VECTOR_BACKEND` | `chroma` | `chroma` (local) or `pinecone` |
| `ATELIER_SEARCH_PROVIDER` | `duckduckgo` | `duckduckgo` (keyless) or `tavily` |
| `ATELIER_API_KEY` | *(empty)* | Optional shared secret; when set, every `/api` call must send `X-API-Key` |
| `ATELIER_DATA_DIR` | `./data` | Uploads, artifacts, vector index and SQLite all live here |

### Using Pinecone

The assignment names Pinecone, so an adapter ships. It is not the default,
because requiring an account would mean this repository does not run for
anyone who clones it.

```bash
pip install pinecone
```

```ini
ATELIER_VECTOR_BACKEND=pinecone
ATELIER_PINECONE_API_KEY=pcsk_...
ATELIER_PINECONE_INDEX=atelier
```

The index is created on first use with the right dimensions. No other code
changes — both backends implement the same `VectorStore` protocol.

---

## Seeing it work without clicking

`scripts/demo_run.py` drives the assignment's own workflow against a running
backend: it uploads both templates, asks for a proposal and a deck, runs the
follow-up edits, downloads every version into `samples/outputs/`, and reopens
each file to check the template's styling survived.

```bash
uvicorn backend.app.main:app          # in one terminal
python scripts/demo_run.py            # in another
```

It prints the live agent trace per turn, then a verification block:

```
generative_ai_trends_proposal_v1.docx
   body font : Georgia      (template uses Georgia)
   header    : Northwind Analytics  |  Client Proposal
   headings  : Executive Summary, Trend Analysis, Recommendations, …

generative_ai_trends_v4.pptx
   slides     : 11
   master font: Trebuchet MS   (template uses Trebuchet MS)
```

`samples/outputs/` holds a committed run, so the generated files can be opened
in Word and PowerPoint without running anything.

## Tests

```bash
pytest                      # the whole suite
pytest -m "not integration" # skip the slower ones
pytest --cov                # with coverage
```

368 tests, 92% coverage. No unit test makes a network call or needs an API
key — the model, the search provider and the vector store are all injected, and
the HTTP boundary is mocked with `respx`.

The renderer tests are the ones worth reading: they render a file, **reopen it
from disk**, and assert that the template's font, heading colour, header,
footer, page geometry and slide master all survived.

---

## Project layout

```
atelier/
├── backend/app/
│   ├── agents/          the nine agents, one concern per file
│   ├── orchestration/   supervisor sequencing and the event trace
│   ├── core/models/     ContentPlan, TemplateProfile, Citation, PatchOp
│   ├── ingestion/       DOCX, PPTX, PDF, image readers + vision OCR
│   ├── analysis/        style and tone profiling
│   ├── rendering/       template-preserving DOCX and PPTX writers
│   ├── rag/             chunking, embeddings, Chroma and Pinecone
│   ├── search/          Tavily and keyless DuckDuckGo
│   ├── llm/             provider abstraction + Groq/OpenAI/Anthropic
│   ├── services/        storage, versioning, patching, conversion
│   └── api/routes/      chat, uploads, artifacts, rag, health
├── frontend/            Streamlit chat UI
├── samples/             template generator output and example artifacts
└── docs/                architecture, agents, API, design decisions
```

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how a turn flows through the system
- [docs/AGENTS.md](docs/AGENTS.md) — each agent in detail
- [docs/API.md](docs/API.md) — every endpoint with examples
- [docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md) — what was chosen, what was rejected, and why

## Licence

MIT.
