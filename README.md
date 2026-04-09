# rtfm

Local documentation retrieval service for agent-assisted development.

Indexes framework documentation into typed **Knowledge Units** and exposes them via CLI and HTTP API. Built for LLM-powered workflows where agents need precise, up-to-date API references instead of relying on training data.

## Why

LLMs hallucinate APIs. They confidently generate outdated patterns, mix up v1/v2 syntax, and invent function signatures. rtfm gives your agents a local, searchable ground truth — the actual docs, chunked, typed, and semantically indexed.

## Install

```bash
# Requires Python 3.11+ and uv
uv tool install -e .

# Or for development
git clone <repo-url> && cd rtfm
uv sync
```

## Quick Start

```bash
rtfm init                          # Create ~/.rtfm/config.yaml
# Edit config to add your frameworks, then:
rtfm ingest                        # Download & index documentation
rtfm up                            # Start server in background
# ... later:
rtfm down                          # Stop server
```

## Usage

All query commands require a running server (`rtfm up`).

### Search

Hybrid semantic + keyword search across all indexed documentation.

```bash
rtfm search "dependency injection" -f fastapi
rtfm search "reactive state" -f svelte -t example -v
rtfm search "mapped_column" -f sqlalchemy --json
```

### Lookup

Exact symbol lookup by function, class, or component name.

```bash
rtfm lookup Depends -f fastapi
rtfm lookup '$state' -f svelte
rtfm lookup WebSocket --no-related
```

### Browse

Structural navigation of indexed documentation.

```bash
rtfm browse fastapi                # List all modules
rtfm browse fastapi -m security    # Drill into a module
```

### Bundle

Get everything about a topic in one call — APIs, examples, concepts, and pitfalls grouped by type.

```bash
rtfm bundle "authentication" -f fastapi
rtfm bundle "WebSocket" -f fastapi --json
rtfm bundle "reactive state" -f svelte -k 3 -v
```

### Admin

```bash
rtfm status                        # Live update check, unit counts, disk usage
rtfm update                        # Re-ingest outdated sources
rtfm remove tauri                  # Remove a framework's data
rtfm ingest -f svelte --rebuild    # Force re-ingest
rtfm up                            # Start server in background (no terminal window)
rtfm down                          # Stop background server
rtfm serve                         # Start server in foreground (for systemd)
```

All query commands support `--json` for machine-readable output.

## Configuration

`~/.rtfm/config.yaml`:

```yaml
embedding_model: nomic-ai/nomic-embed-text-v1.5

server:
  host: 127.0.0.1
  port: 8787

sources:
  # llms.txt — simplest source, single pre-built markdown file
  svelte:
    type: llms_txt
    url: https://svelte.dev/llms-full.txt
    language: javascript

  # GitHub — sparse checkout of a docs directory
  fastapi:
    type: github
    repo: fastapi/fastapi
    docs_path: docs/en/docs
    glob: "**/*.md"
    language: python

  # Website + sitemap — crawl filtered URLs from sitemap.xml
  claude-agent-sdk:
    type: website
    sitemap: https://platform.claude.com/sitemap.xml
    url_filter: "/docs/en/agent-sdk/"
    language: python

  # Website + wildcard — crawl all links under a URL prefix
  tokio:
    type: website
    url: https://tokio.rs/tokio/tutorial/*
    language: rust

  # Website + wildcard — works with docs.rs too
  reqwest:
    type: website
    url: https://docs.rs/reqwest/latest/reqwest/*
    language: rust

  # Local markdown files
  # my_docs:
  #   type: local
  #   path: /path/to/docs
  #   glob: "**/*.md"
  #   language: python
```

### Source Types

| Type | How it works | Version tracking |
|------|-------------|-----------------|
| `llms_txt` | HTTP GET on a URL, single markdown file | HTTP ETag |
| `github` | Git sparse checkout of a docs directory | Commit SHA |
| `local` | Reads files from the local filesystem | File hash |
| `website` + `sitemap` | Sitemap → filter URLs → fetch HTML → convert to Markdown | Sitemap hash |
| `website` + `url: .../path/*` | Crawl start page, discover all links under prefix | Per-page ETag |
| `website` + `urls` | Explicit URL list (fallback for JS-rendered sites) | Per-page ETag |

Website sources use per-page ETag caching — on re-ingest, only changed pages are re-fetched.

### Supported Languages

Symbol extraction works for: Python, Rust, JavaScript/TypeScript, Go, C/C++, Java, C#.

Set `language` in your source config to the programming language of the code examples (e.g. `javascript` for Svelte, `python` for FastAPI). This enables symbol-based lookup (`rtfm lookup`).

## How It Works

### Ingestion Pipeline

```
Source (GitHub / HTTP / local / website)
  -> Downloader (sparse checkout, HTTP GET, file read, crawl + HTML->MD)
    -> Parser (Markdown + RST heading-aware splitter)
      -> Extractor (multi-label classifier + symbol extraction)
        -> Storage (SQLite + ChromaDB)
```

**Parser** splits documents at heading boundaries, tracking a full heading hierarchy (e.g. `["ORM", "Session", "execute()"]`). Supports both Markdown (`##`) and reStructuredText (`===` underline) heading styles.

**Classifier** assigns each section one or more types using structured signals. A section can be both `api` and `pitfall` (e.g. a deprecated API reference):
- Sphinx directives (`.. autoclass::`, `.. warning::`, `.. deprecated::`)
- MkDocs admonitions (`!!! warning`, `!!! example`)
- FastAPI Blocks syntax (`/// warning`, `/// danger`)
- Code analysis (definitions in code blocks -> `api`, long code -> `example`)
- Text signals (`breaking change`, `deprecated` -> `pitfall`)

**Symbol extraction** finds function/class/interface names from code blocks, RST autodoc directives (`.. autoclass:: Session`), and inline code in headings (`` `Depends` ``).

### Dual-Index Storage

| SQLite + FTS5 | ChromaDB |
|---------------|----------|
| Keyword search (BM25 ranking) | Semantic search (cosine similarity) |
| Exact symbol lookup | Conceptual similarity |
| Structural browsing | "How do I do X" queries |
| Metadata, versions | Vector embeddings (nomic-embed-text-v1.5) |

### Hybrid Search (RRF)

Queries run against both indexes in parallel. Results are merged using [Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) (k=60):

```
score(doc) = 1/(k + rank_semantic) + 1/(k + rank_keyword)
```

This combines the strengths of both: semantic search finds conceptually related docs ("how do I authenticate" -> OAuth2 tutorial), keyword search finds exact matches ("mapped_column" -> the exact API reference).

## Knowledge Unit Types

| Type | What it contains |
|------|-----------------|
| `api` | Function/class/component definition with signature, parameters, types |
| `example` | Complete, working code block with context |
| `concept` | Explanatory text about a pattern or architecture |
| `pitfall` | Gotchas, breaking changes, deprecations + workarounds |

A section can have multiple types. An API reference with a deprecation warning produces both an `api` and a `pitfall` unit, so `rtfm bundle` finds it in both buckets.

## HTTP API

All endpoints are also available via HTTP when the server is running.

| Endpoint | Description |
|----------|-------------|
| `GET /search?q=...&framework=...&type=...&top_k=10` | Hybrid search |
| `GET /lookup/{symbol}?framework=...` | Symbol lookup |
| `GET /browse?framework=...&module=...` | Structural navigation |
| `GET /bundle?q=...&framework=...&top_k=5` | Topic bundle |

Swagger UI: http://127.0.0.1:8787/docs

Response models are fully typed with Pydantic — Swagger shows complete JSON schemas for all endpoints.

## Architecture

```
src/rtfm/
  models.py              KnowledgeUnit, configs, Pydantic response models
  cli.py                 Click CLI: search, lookup, browse, bundle, up, down, status, ...
  server.py              FastAPI with Annotated params, Depends, response models
  storage.py             Dual-index: ChromaDB (semantic) + SQLite/FTS5 (keyword)
  search.py              Hybrid search (RRF), symbol lookup, browse, bundle
  ingest/
    pipeline.py          Orchestrates download -> parse -> extract -> store
    downloaders.py       GitHub, HTTP, local, website (sitemap/crawl/HTML->MD + ETag cache)
    parser.py            Markdown + RST -> Sections (heading-aware splitter)
    extractor.py         Sections -> KnowledgeUnits (multi-label classifier + symbols)
```

## Data Storage

```
~/.rtfm/
  config.yaml            Source configuration
  server.pid             Background server PID
  server.log             Background server log
  data/
    rtfm.db              SQLite (units, symbols, versions, FTS5 index)
    chroma/              ChromaDB vector index
  cache/
    <source>.json        Per-page ETag + Markdown cache for website sources
```

## Development

```bash
uv sync                                          # Install dependencies
uv run python -m pytest tests/ -v                # Run tests (138 tests)
uv run python -m pytest tests/ --cov=rtfm        # Coverage report
uv run ruff check src/rtfm/                      # Linter
uv run mypy src/rtfm/                            # Type checking (strict)
uv run pyright src/rtfm/                         # Type checking (strict)
uv run ty check src/rtfm/                        # Type checking
```

## License

MIT
