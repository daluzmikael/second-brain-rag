# Second Brain RAG

Semantic search and question-answering over your own notes, built on a real
vector database — not keyword matching, not a structured-table lookup dressed
up as "RAG." Documents are chunked, embedded, and stored in Postgres via
`pgvector`; queries are answered by retrieving the most semantically similar
chunks and asking an LLM to synthesize a grounded, cited answer from them.

The demo corpus is a handful of README files pulled from my own past
projects, standing in for the kind of personal knowledge base (notes, docs,
project write-ups) this is meant to search — a "Notion-like" problem: given a
pile of your own unstructured documents, let natural-language questions find
the right passage and answer directly from it.

## Architecture

```
data/docs/*.md
      │
      ▼
  chunk_text()          token-aware sliding window (chunking.py)
      │
      ▼
  embed_texts()         OpenAI text-embedding-3-small (embeddings.py)
      │
      ▼
  Postgres + pgvector    chunks table, HNSW cosine index (db.py, ingest.py)
      │
      ▼  (query time)
  embed_query() → ORDER BY embedding <=> query_vector  (search.py)
      │
      ▼
  synthesize.answer()    gpt-4o-mini, cites [1] [2] ... by source (synthesize.py)
```

Interfaces on top of the same pipeline:
- **CLI** (`app/cli.py`) — `ingest`, `search`, `ask`
- **API** (`app/api.py`) — FastAPI, `POST /search`, `POST /ask`

## Stack

- **Postgres + [pgvector](https://github.com/pgvector/pgvector)** — vector storage and similarity search, run via Docker
- **OpenAI `text-embedding-3-small`** — embeddings (1536 dims)
- **OpenAI `gpt-4o-mini`** — answer synthesis with citations
- **psycopg3** — Postgres driver, with `pgvector`'s native adapter for vector columns
- **FastAPI** — HTTP interface

## Setup

Requires Docker and Python 3.11+.

```bash
git clone <this-repo>
cd second-brain-rag

# 1. Start Postgres with pgvector
docker compose up -d

# 2. Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# edit .env and set OPENAI_API_KEY

# 4. Ingest the sample corpus
python -m app.cli ingest data/docs

# 5. Ask a question
python -m app.cli ask "What tech stack does MediaLister use?"
```

## Usage

### CLI

```bash
# Re-run any time docs change — content hashing skips unchanged files
python -m app.cli ingest data/docs

# Raw retrieval, no LLM call — useful for sanity-checking similarity scores
python -m app.cli search "waitlist notifications" --top-k 5

# Full RAG: retrieve + synthesize a cited answer
python -m app.cli ask "How does the PickleBall waitlist app notify players?"
```

### API

```bash
uvicorn app.api:app --reload
```

```bash
curl -X POST localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "What does the 2dwalk project do?"}'
```

Interactive docs at `http://localhost:8000/docs`.

## Design notes

- **Chunking** is a token-aware sliding window (300 tokens, 50 overlap) using
  `tiktoken`, so retrieval doesn't hinge on documents happening to break
  cleanly on paragraph boundaries.
- **Re-ingestion is idempotent.** Each document's content hash is stored;
  unchanged files are skipped, and changed files are deleted and
  re-chunked/re-embedded rather than appended to, so stale chunks never
  linger.
- **Retrieval uses cosine distance** (`<=>` operator) against an HNSW index,
  pgvector's approximate-nearest-neighbor index for sub-linear search as the
  corpus grows.
- **Synthesis is grounded and citation-forced**: the system prompt requires
  the model to answer only from the retrieved excerpts and to say so plainly
  when the excerpts don't contain an answer, rather than falling back on
  parametric knowledge.

## Extending

- Swap `data/docs` for a real notes export (Obsidian vault, Notion export,
  class notes) — anything that lands as `.md`/`.txt` ingests as-is.
- Add a reranker (e.g. Cohere rerank) between retrieval and synthesis for
  higher-precision top-k on larger corpora.
- Add a lightweight web UI on top of the existing `/search` and `/ask`
  endpoints.
