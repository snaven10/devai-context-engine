# Architecture

> Back to [DOCS](../DOCS.md) | [README](../README.md)

---

## Overview

DevAI is a hybrid Go + Python system. The Go binary handles the CLI, TUI, MCP server, and subprocess management. The Python service handles ML workloads: embeddings, tree-sitter parsing, semantic chunking, and storage. They communicate over JSON-RPC via stdio — no network dependency.

**Why Go + Python?** Go gives us a fast, cross-platform single binary for the CLI and MCP transport. Python gives us the ML ecosystem — sentence-transformers, tree-sitter, LanceDB. JSON-RPC over stdio means zero configuration between the two.

**Module:** `github.com/gentleman-programming/devai`
**Go version:** 1.24.2
**Python version:** >= 3.11

```
  AI Assistant ──MCP (stdio)──▶ Go CLI
                                  │
                             JSON-RPC stdio
                                  │
                            Python ML Service
                            │       │       │
                        ┌───┘       │       └───┐
                        ▼           ▼           ▼
                    LanceDB     SQLite      SQLite
                    vectors   graph_edges   memories
                                          index_state
                                          file_state
                                          sessions
```

---

## Core Interfaces

| Component | Responsibility |
|-----------|---------------|
| **MCP Server** (`internal/mcp`) | Exposes 14 tools over MCP stdio protocol |
| **ML Client** (`internal/mlclient`) | JSON-RPC client to Python subprocess |
| **Branch Context** (`internal/branch`) | Branch overlay, lineage, tombstone filtering |
| **TUI** (`internal/tui`) | Interactive terminal UI (9 screens) |
| **ML Service** (`ml/devai_ml`) | Embeddings, parsing, chunking, indexing, storage |
| **Index Pipeline** (`ml/devai_ml/pipeline`) | Git-aware incremental indexing orchestration |

---

## Project Structure

```
devai/
├── cmd/devai/
│   ├── main.go                    # Entry point
│   └── cmd/
│       ├── root.go                # Global flags (--config, --verbose)
│       ├── init.go                # devai init
│       ├── index.go               # devai index
│       ├── search.go              # devai search
│       ├── watch.go               # devai watch
│       ├── tui.go                 # devai tui
│       ├── server.go              # devai server {start,status,mcp}
│       ├── hooks.go               # devai hooks {install,uninstall}
│       ├── status.go              # devai status
│       ├── push_index.go          # devai push-index (stub)
│       └── pull_index.go          # devai pull-index (stub)
│
├── internal/
│   ├── mcp/
│   │   └── server.go              # 14 MCP tool registrations + handlers
│   ├── mlclient/
│   │   └── client.go              # JSON-RPC stdio client
│   ├── tui/
│   │   ├── model.go               # State machine (9 screens)
│   │   ├── update.go              # Event handlers
│   │   ├── view.go                # Screen rendering
│   │   └── styles.go              # Lipgloss styles
│   ├── branch/
│   │   └── context.go             # Branch overlay + dedup + tombstones
│   ├── storage/
│   │   └── router.go              # Storage mode routing (local/shared/hybrid)
│   ├── session/                   # (placeholder)
│   ├── config/                    # (placeholder)
│   ├── api/                       # (placeholder)
│   ├── db/                        # (placeholder)
│   └── git/                       # (placeholder)
│
├── ml/devai_ml/
│   ├── server.py                  # JSON-RPC dispatcher (16 methods)
│   ├── embeddings/
│   │   ├── factory.py             # Provider factory (local/openai/voyage/custom)
│   │   ├── local.py               # Sentence Transformers
│   │   ├── openai_embed.py        # OpenAI API
│   │   ├── voyage_embed.py        # Voyage AI API
│   │   └── custom.py              # Custom HTTP endpoint
│   ├── parsers/
│   │   ├── registry.py            # Language registry (25+ languages)
│   │   ├── treesitter_parser.py   # Tree-sitter AST parser
│   │   ├── raw_parser.py          # Raw text parser (no AST)
│   │   └── base.py                # Parser interface
│   ├── chunking/
│   │   └── semantic_chunker.py    # AST-aware chunking (4 levels)
│   ├── pipeline/
│   │   ├── orchestrator.py        # IndexPipeline: diff → parse → chunk → embed → store
│   │   └── git_state.py           # Git state detection
│   ├── stores/
│   │   ├── vector_store.py        # LanceDB vector store
│   │   ├── graph_store.py         # SQLite graph store (adjacency list)
│   │   ├── memory_store.py        # SQLite memory store (topic keys, dedup)
│   │   └── index_state.py         # SQLite index state tracker
│   └── proto/                     # gRPC generated stubs (future)
│
├── proto/
│   └── ml_service.proto           # gRPC service definitions (5 services)
├── go.mod
├── pyproject.toml
├── Makefile
├── Dockerfile                     # Multi-stage: Go builder + Python runtime
└── docker-compose.yml             # DevAI + Qdrant
```

---

## Design Decisions

1. **Hybrid Go + Python** — Go for CLI/TUI/MCP (fast, single binary), Python for ML (ecosystem). Best of both worlds.
2. **JSON-RPC over stdio** — No network config, no ports, no auth. The Python service is a subprocess, not a server.
3. **LanceDB over Qdrant by default** — Embedded, zero config, disk-based. Qdrant available for shared/team mode when needed.
4. **SQLite for relational data** — Single file, WAL mode, no server process. Good enough for graph queries and memory search.
5. **Deterministic vector IDs** — `SHA256(repo:branch:file:start_line)[:32]` enables true upserts without orphaned vectors.
6. **Branch overlay, not branch copies** — Search walks the branch lineage instead of duplicating the entire index per branch.
7. **AST-aware chunking** — Never split mid-symbol. Breadcrumbs preserve hierarchy context.
8. **Incremental by default** — `git diff` between commits. Full reindex only on model change or explicit request.
9. **Agent-agnostic** — Standard MCP over stdio. Works with any compliant client.
10. **Memory dedup** — Normalized hash + 15-min window prevents noise from repeated saves.
11. **No CGO** — Pure Go SQLite driver (`modernc.org/sqlite`) for true cross-platform builds.
12. **Token estimation** — `len(text) // 4` is good enough for chunking decisions. No need for a tokenizer dependency in hot paths.

---

## gRPC Service Definitions (Future)

Proto definitions exist at `proto/ml_service.proto` but are not yet used in production. The current communication layer is JSON-RPC over stdio.

| Service | Methods |
|---------|---------|
| `EmbeddingService` | `Embed`, `EmbedBatch` |
| `ParserService` | `ParseFile`, `ExtractSymbols` |
| `IndexingService` | `IndexFiles` (stream), `IndexRepo` (stream) |
| `GraphService` | `BuildCallGraph`, `BuildDependencyGraph` |
| `HealthService` | `Check` |
