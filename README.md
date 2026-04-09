# rtfm

Local documentation retrieval for agent-assisted development.

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
```

## Usage

```bash
# Topic search — returns API refs, examples, concepts, pitfalls
rtfm dependency injection -f fastapi
rtfm "reactive state" -f svelte
rtfm WebSocket -f fastapi -k 3

# Symbol lookup — single word triggers exact match
rtfm Depends -f fastapi
rtfm '$state' -f svelte
rtfm Client -f reqwest
```

Output is **JSON when piped** (for agents), **formatted text in terminal** (for humans). Override with `--json` or `--pretty`.

Single-word queries try symbol lookup first, then fall back to topic search. Multi-word queries return a topic bundle grouped by type.

### Admin

```bash
rtfm status                        # Health scores, unit counts, update check
rtfm update                        # Re-ingest outdated sources
rtfm remove tauri                  # Remove a framework's data
rtfm ingest -f svelte --rebuild    # Force re-ingest
rtfm up                            # Start server in background
rtfm down                          # Stop background server
rtfm serve                         # Foreground server (for systemd)
```

## Configuration

`~/.rtfm/config.yaml`:

```yaml
embedding_model: nomic-ai/nomic-embed-text-v1.5
min_health_score: 80               # Reject imports scoring below this

server:
  host: 127.0.0.1
  port: 8787

sources:
  # llms.txt — single pre-built markdown file
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

  # Website + sitemap
  claude-agent-sdk:
    type: website
    sitemap: https://platform.claude.com/sitemap.xml
    url_filter: "/docs/en/managed-agents/"
    language: python

  # Website + wildcard crawl
  tokio:
    type: website
    url: https://tokio.rs/tokio/tutorial/*
    language: rust

  # docs.rs (auto-detected as rustdoc, parsed from raw HTML)
  reqwest:
    type: website
    url: https://docs.rs/reqwest/latest/reqwest/*
    language: rust

  # TypeDoc (explicit doc_system for github.io sites)
  jira-js:
    type: website
    url: https://mrrefactoring.github.io/jira.js/*
    language: javascript
    doc_system: typedoc
```

### Source Types

| Type | How it works | Version tracking |
|------|-------------|-----------------|
| `llms_txt` | HTTP GET on a URL, single markdown file | HTTP ETag |
| `github` | Git sparse checkout of a docs directory | Commit SHA |
| `local` | Reads files from the local filesystem | File hash |
| `website` + `sitemap` | Sitemap → filter URLs → fetch HTML → convert to Markdown | Sitemap hash |
| `website` + `url: .../path/*` | Crawl start page, discover all links under prefix | URL set hash |

### Doc System Detection

rtfm auto-detects the documentation system and uses a specialized parser:

| System | Detection | Sources |
|--------|-----------|---------|
| Sphinx/RST | `.. autoclass::` directives | sqlalchemy, numpy |
| MkDocs | `!!! admonition`, `::: module.path` inserts | fastapi, pydantic, polars |
| rustdoc | `docs.rs` URL or `class="rustdoc"` in HTML | reqwest |
| TypeDoc | `class="tsd-"` in HTML or `doc_system: typedoc` | jira-js |
| llms.txt | `type: llms_txt` in config | svelte, tauri, claude-code |

Override with `doc_system:` in source config when auto-detection fails.

## How It Works

### Ingestion Pipeline

```
Source (GitHub / HTTP / local / website)
  → Downloader (sparse checkout, HTTP GET, file read, crawl + HTML→MD or raw HTML)
    → Parser (doc-system-aware: Sphinx, MkDocs, rustdoc, TypeDoc, generic MD)
      → Extractor (classifier + symbol extraction + definition site tracking)
        → Storage (SQLite + ChromaDB)
```

**Parser** splits documents at heading boundaries (or autodoc directive boundaries for Sphinx). Tracks a full heading hierarchy. Doc-system-specific parsers handle HTML directly (rustdoc, TypeDoc) instead of losing structure in HTML→MD conversion.

**Extractor** assigns each section one or more types and extracts symbols. **Definition sites** are tracked — sections where a symbol is canonically defined (via `.. autoclass::`, `::: module.Symbol`, rustdoc struct definitions, etc.) are marked so lookup always returns the authoritative reference, not a random mention.

**Relevance decay** downweights release notes, changelogs, and migration guides so they don't dominate search results over actual documentation.

### Dual-Index Storage

| SQLite + FTS5 | ChromaDB |
|---------------|----------|
| Keyword search (BM25) | Semantic search (cosine similarity) |
| Exact symbol lookup | Conceptual similarity |
| Definition site ranking | "How do I do X" queries |
| Metadata, versions | Vector embeddings (nomic-embed-text-v1.5) |

### Hybrid Search (RRF)

Queries run against both indexes in parallel. Results are merged using Reciprocal Rank Fusion (k=60), then multiplied by per-unit relevance decay.

### Import Health Score

Every ingested source gets a health score (0-100, grade A-F) based on:

| Signal | What it measures |
|--------|-----------------|
| Type diversity | Are api/example/concept/pitfall all present? |
| Definition coverage | % of symbols with canonical definition sites |
| Content quality | Average content length, stub ratio |
| Changelog noise | % of units from release notes / changelogs |
| Doc system detection | Was a specialized parser used? |

Sources scoring below `min_health_score` (default: 80) are auto-rejected during ingest.

## Knowledge Unit Types

| Type | What it contains |
|------|-----------------|
| `api` | Function/class/component definition with signature, parameters, types |
| `example` | Complete, working code block with context |
| `concept` | Explanatory text about a pattern or architecture |
| `pitfall` | Gotchas, breaking changes, deprecations + workarounds |

## Architecture

```
src/rtfm/
  models.py              KnowledgeUnit, configs, Pydantic response models
  cli.py                 Agent-first CLI: rtfm <query> -f <framework>
  server.py              FastAPI HTTP API
  storage.py             Dual-index: ChromaDB (semantic) + SQLite/FTS5 (keyword)
  search.py              Hybrid search (RRF), symbol lookup, bundle
  health.py              Import health score computation
  reporter.py            CLI progress rendering
  ingest/
    pipeline.py          Orchestrates download → parse → extract → store
    detect.py            Auto-detection of doc systems
    downloaders.py       GitHub, HTTP, local, website (sitemap/crawl/HTML→MD)
    parsers/
      base.py            Parser protocol + Section dataclass
      generic_md.py      Markdown + RST (default fallback)
      sphinx_rst.py      Sphinx autodoc directive splitting
      mkdocs_md.py       MkDocs/Material
      rustdoc_html.py    docs.rs raw HTML parsing
      typedoc_html.py    TypeDoc raw HTML parsing
    extractors/
      base.py            Extractor protocol
      generic.py         Multi-label classifier + symbol extraction
      sphinx.py          Autodoc definition sites + changelog decay
      mkdocs.py          mkdocstrings definition sites
      rustdoc.py         All-API extractor for rustdoc
      typedoc.py         All-API extractor for TypeDoc
```

## HTTP API

All endpoints are available via HTTP when the server is running.

| Endpoint | Description |
|----------|-------------|
| `GET /search?q=...&framework=...&type=...&top_k=10` | Hybrid search |
| `GET /lookup/{symbol}?framework=...` | Symbol lookup |
| `GET /browse?framework=...&module=...` | Structural navigation |
| `GET /bundle?q=...&framework=...&top_k=5` | Topic bundle |

Swagger UI: http://127.0.0.1:8787/docs

## Development

```bash
uv sync
uv run python -m pytest tests/ -v     # 200 tests
uv run ruff check src/rtfm/
```

## License

MIT
