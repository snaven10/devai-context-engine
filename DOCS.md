# DevAI — Technical Documentation

> **DevAI** (/dɛv.aɪ/) — An AI code intelligence engine that gives AI agents semantic understanding of codebases through AST-level indexing, vector search, and persistent memory.

> **Warning**
> **Alpha — Active Development.** This project is functional but not production-ready. APIs, CLI flags, storage formats, and config schemas may change without notice. No releases have been published yet — install from source or wait for v0.1.0.

---

[README](README.md) &#8226; [Architecture](#architecture) &#8226; [API](#api) &#8226; [MCP Tools](#mcp-tools) &#8226; [Schemas](#schemas) &#8226; [Setup](#setup) &#8226; [Features](#features)

---

## Quick Install

```bash
# Linux / macOS (downloads binary + portable Python — no prerequisites)
curl -fsSL https://raw.githubusercontent.com/snaven10/devai-context-engine/main/scripts/install.sh | bash

# Windows (PowerShell)
irm https://raw.githubusercontent.com/snaven10/devai-context-engine/main/scripts/install.ps1 | iex

# From source (requires Go 1.24+, Python 3.11+)
git clone https://github.com/snaven10/devai-context-engine.git devai && cd devai && make build
```

Flags: `--gpu` (CUDA PyTorch), `--version TAG` (pin release), `--uninstall` (remove). See [Setup](docs/setup.md) for full details.

> **Note:** The install scripts download from GitHub releases. Until the first release is published, use the "from source" method.

---

## Documentation Index

### [Architecture](docs/architecture.md)

System overview, component diagram, project structure, design decisions, and gRPC future plans.

- How Go + Python communicate (JSON-RPC over stdio)
- Core interfaces (MCP Server, ML Client, Branch Context, TUI, Index Pipeline)
- Full file tree with descriptions
- 12 design decisions with rationale

### [JSON-RPC API](docs/api.md)

Complete reference for the 19 Python ML service methods.

- **Embedding:** `embed`, `embed_batch`
- **Search:** `search`, `read_symbol`, `get_references`
- **Parsing:** `parse_file`, `index_repo`, `health`
- **Memory:** `remember`, `recall`, `memory_context`, `memory_update`, `memory_stats`
- **Status:** `get_branch_context`, `get_session_history`, `index_status`

All methods documented with exact params and return schemas.

### [MCP Tools](docs/mcp-tools.md)

Reference for the 17 MCP tools exposed via `devai server mcp`.

- **Search & Read:** `search`, `read_file`, `build_context`, `read_symbol`, `get_references`
- **Indexing & Status:** `index_repo`, `index_status`, `get_branch_context`, `switch_context`, `get_session_history`
- **Memory:** `remember`, `recall`, `memory_context`, `memory_stats`
- **Sync:** `push_index`, `pull_index`, `sync_index`

Each tool with parameters, types, defaults, and required flags.

### [Database Schemas](docs/schemas.md)

Exact SQL CREATE statements and LanceDB schema definitions.

- LanceDB vector store (18 fields, deterministic IDs)
- SQLite `graph_edges` (adjacency list, 5 edge kinds)
- SQLite `memories` (topic key upserts, dedup, 5 indexes)
- SQLite `sessions`, `index_state`, `file_state`, `branch_lineage`
- SQLite configuration (WAL, timeout, sync)

### [Setup & Configuration](docs/setup.md)

Installation, configuration, dependencies, Makefile targets, and Docker.

- **One-line install scripts** (Linux, macOS, Windows) — no Go/Python required
- Install flags: `--gpu`, `--version`, `--uninstall`
- From source (for developers), Docker
- Project config (`.devai/config.yaml`) — all options documented
- 8 environment variables
- Storage mode configuration (local, shared, hybrid) with examples
- Push/pull/sync workflow for team index sharing
- Claude Code MCP server setup
- Qdrant deployment options
- 4 embedding providers
- Go dependencies (7 packages with versions)
- Python dependencies (8 core + 2 optional)
- 15 Makefile targets
- Docker multi-stage build

### [Features Deep Dive](docs/features.md)

12 features explained with Problem → Solution → How It Works.

1. Semantic Code Search
2. AST-Aware Semantic Chunking
3. Git-Aware Incremental Indexing
4. Branch Overlay & Deduplication
5. Persistent Memory
6. Call Graph & Dependency Analysis
7. Intelligent Context Assembly
8. Multi-Language Support (25+ languages)
9. Embedding Provider Flexibility
10. Interactive TUI
11. Multi-Backend Storage
12. Index Synchronization (Push/Pull/Sync)

### [Storage Modes](docs/setup.md#storage-modes)

Configuration guide for local, shared, and hybrid storage backends with Qdrant.

- Claude Code settings.json configuration
- Docker Compose setup for Qdrant
- Migrating existing local indexes

---

## Quick Reference

### CLI Commands

```
devai init [path]           Initialize .devai/ in a repository
devai index                 Index current repo (incremental by default)
devai search <query>        Semantic search
devai watch [path]          Watch for changes and auto-index
devai tui                   Interactive terminal UI
devai server start          Start Python ML service
devai server status         Check ML service health
devai server mcp            Start MCP server (stdio)
devai status                ML service health + model info
devai index-status          Per-branch index statistics
devai hooks install [path]  Install git post-commit hook
devai hooks uninstall       Remove auto-index hook
```

### Terminal UI Screens

| Screen | Description |
|--------|-------------|
| Dashboard | Overview of indexed repos |
| Search | Semantic code search (`/`) |
| Repositories | Browse repos with stats |
| Branches | Per-branch index status |
| Memory | Search persistent memories |
| History | Tool call timeline |
| Detail | Code with line numbers (`Enter`) |
| Index Repo | Trigger new indexing |

**Navigation:** `j`/`k` or arrows, `Enter` select, `b` back, `q` quit.

---

## Current Limitations

This is alpha software. Key limitations to be aware of:

- **No published releases yet** — install scripts will fail until the first GitHub release is cut
- **Memories are local only** — SQLite-backed, not shared via Qdrant
- **Windows is untested** — the PowerShell install script exists but has not been validated
- **GPU install path is untested** — `--gpu` flag exists but CUDA support has not been verified
- **First run requires internet** — the embedding model (~90 MB) is downloaded on first use
- **Large venv** — ~1.5 GB for CPU-only PyTorch, no slim option
- **gRPC transport not active** — proto definitions exist but only JSON-RPC stdio is used
