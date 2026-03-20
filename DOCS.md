# DevAI — Technical Documentation

> **DevAI** (/dɛv.aɪ/) — An AI code intelligence engine that gives AI agents semantic understanding of codebases through AST-level indexing, vector search, and persistent memory.

---

[README](README.md) &#8226; [Architecture](#architecture) &#8226; [API](#api) &#8226; [MCP Tools](#mcp-tools) &#8226; [Schemas](#schemas) &#8226; [Setup](#setup) &#8226; [Features](#features)

---

## Documentation Index

### [Architecture](docs/architecture.md)

System overview, component diagram, project structure, design decisions, and gRPC future plans.

- How Go + Python communicate (JSON-RPC over stdio)
- Core interfaces (MCP Server, ML Client, Branch Context, TUI, Index Pipeline)
- Full file tree with descriptions
- 12 design decisions with rationale

### [JSON-RPC API](docs/api.md)

Complete reference for the 16 Python ML service methods.

- **Embedding:** `embed`, `embed_batch`
- **Search:** `search`, `read_symbol`, `get_references`
- **Parsing:** `parse_file`, `index_repo`, `health`
- **Memory:** `remember`, `recall`, `memory_context`, `memory_update`, `memory_stats`
- **Status:** `get_branch_context`, `get_session_history`, `index_status`

All methods documented with exact params and return schemas.

### [MCP Tools](docs/mcp-tools.md)

Reference for the 14 MCP tools exposed via `devai server mcp`.

- **Search & Read:** `search`, `read_file`, `build_context`, `read_symbol`, `get_references`
- **Indexing & Status:** `index_repo`, `index_status`, `get_branch_context`, `switch_context`, `get_session_history`
- **Memory:** `remember`, `recall`, `memory_context`, `memory_stats`

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

- From source, Docker, binary locations
- Project config (`.devai/config.yaml`)
- 5 environment variables
- 4 embedding providers
- Go dependencies (7 packages with versions)
- Python dependencies (8 core + 2 optional)
- 15 Makefile targets
- Docker multi-stage build

### [Features Deep Dive](docs/features.md)

10 features explained with Problem → Solution → How It Works.

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
