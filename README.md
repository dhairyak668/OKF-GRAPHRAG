# OKF-GraphRAG

A graph-aware Retrieval Augmented Generation (RAG) system built on
[Open Knowledge Format (OKF)](https://github.com/GoogleCloudPlatform/open-knowledge-format).

## What it does

Plain RAG loses the relationships between documents.  OKF-GraphRAG keeps them:
documents are stored as OKF markdown files with explicit inter-document links,
forming a knowledge graph.  Retrieval follows those links to pull in
connected context that keyword or vector search alone would miss.

### Architecture

Two pipelines:

**Ingestion** — OKF markdown → parser → SQLite graph (nodes, edges, chunks, vectors)

**Retrieval** — question → metadata filter → hybrid search (vector + BM25) →
graph expansion (1-2 hops) → rerank → context builder → LLM-grounded answer

### Two-tier design

| Tier | Contents | Purpose |
|------|----------|---------|
| **Knowledge bundle** (`knowledge/`) | Structured OKF summaries with cross-links | Search & routing index |
| **Source docs** (`source_docs/`) | Full original documentation | Ground truth for LLM answering |

## Quick start

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Set up your OpenAI API key (see Configuration below)
cp .env.example .env   # then edit .env with your key

# 3. Ingest a knowledge bundle and generate embeddings
python -m okf_graphrag ingest knowledge/sample/
python -m okf_graphrag embed

# 4. Ask a question
python -m okf_graphrag ask "How do I configure rate limiting?" -v
```

## Asking questions

`ask` is the primary way to use OKF-GraphRAG. It takes your question, retrieves
relevant concepts via graph-aware search, loads the original source documents as
ground truth, and sends everything to GPT-4.1 with a strict grounding prompt.
The answer cites only what the documents actually say.

```bash
# Basic usage
python -m okf_graphrag ask "How does authentication work?"

# With verbose output to see which concepts and source docs were used
python -m okf_graphrag ask "How does authentication work?" -v

# Filter by document type or tags
python -m okf_graphrag ask "How do I deploy?" --type Process --hops 2

# Control graph expansion depth (0 = search only, 1-2 = follow links)
python -m okf_graphrag ask "What is the event-driven architecture?" --hops 2
```

The `-v` flag is useful for understanding what happened under the hood: how many
concepts were retrieved, how many came from search vs graph expansion, and which
source documents were loaded.

**Note:** `ask` requires the `source_docs/` folder to be present — it reads the
full original documentation as ground truth for the LLM. Without it, retrieval
still works but the LLM has no source material to answer from. If you only have
the knowledge bundle, use `retrieve` instead to get graph-aware context without
the LLM answering step.

## Additional commands

Beyond `ask`, two lower-level commands let you inspect what the retrieval
pipeline is doing before it reaches the LLM.

### `search` — flat chunk lookup

Finds the top-k text chunks matching your query. No graph awareness — purely
keyword and/or vector similarity against the knowledge bundle.

```bash
# Hybrid search (70% vector + 30% BM25, default)
python -m okf_graphrag search "api gateway routing"

# Keyword-only (no embeddings needed)
python -m okf_graphrag search "authentication tokens" --mode bm25

# Vector-only
python -m okf_graphrag search "rate limiting" --mode vector --top-k 5
```

### `retrieve` — graph-aware retrieval

Starts with the same hybrid search, then expands outward through the knowledge
graph (1-2 hops) to pull in connected concepts that search alone would miss.
Results are reranked with source bonuses (search +0.3, 1-hop +0.15, 2-hop +0.05).

```bash
# Basic retrieval with 2-hop expansion
python -m okf_graphrag retrieve "How does authentication work?" --hops 2

# Compare what graph expansion adds over plain search
python -m okf_graphrag retrieve "notification delivery" --compare

# Print the full context that would be sent to the LLM
python -m okf_graphrag retrieve "event-driven pipeline" --context
```

The `--compare` flag is particularly useful — it shows you exactly which concepts
were discovered through graph links that search alone would have missed.

## All CLI commands

| Command | Description |
|---------|-------------|
| `ask <query>` | Ask a question — grounded answer from source documents |
| `search <query>` | Hybrid/vector/BM25 chunk search |
| `retrieve <query>` | Graph-aware retrieval with expansion |
| `ingest <bundle>` | Parse OKF bundle into SQLite graph |
| `embed` | Generate embeddings for unembedded chunks |
| `show <concept_id>` | Show a concept and its neighbors |
| `list` | List all concepts |
| `convert <raw_docs>` | Convert raw docs to OKF format |
| `compare` | Context efficiency comparison (naive vs L3 vs L4) |

## Configuration

Copy `.env.example` to `.env` and add your OpenAI API key:

```
OPENAI_API_KEY=your-key-here
```

## Tech stack

- Python 3.12+
- SQLite (graph store + vector index)
- OpenAI text-embedding-3-small
- GPT-4.1 for grounded answering and document conversion
- numpy for cosine similarity
- PyYAML + python-frontmatter for OKF parsing

## Project structure

```
OKF-GraphRAG/
├── okf_graphrag/          # Main package
│   ├── cli.py             # CLI entry point
│   ├── parsing/           # OKF markdown parser
│   ├── graph/             # SQLite knowledge graph store
│   ├── search/            # Embedding, vector search, BM25, hybrid
│   ├── retrieval/         # Graph-aware retrieval engine
│   ├── generation/        # Source resolution + LLM answering
│   └── ingestion/         # Bundle ingestion + LLM converter
├── knowledge/             # OKF knowledge bundles
│   └── sample/            # Sample bundle
├── source_docs/           # Original documentation (ground truth)
├── tests/                 # Test suite
└── pyproject.toml
```

## Tests

```bash
python -m pytest tests/ -v
```

All tests use fake biased embeddings and mocked LLM calls — no API key needed to run.
