# Features Deep Dive

> Back to [DOCS](../DOCS.md) | [README](../README.md)

> **Warning**
> DevAI is in **alpha**. All features listed below are implemented and functional in local development, but have not been battle-tested in production environments. Edge cases and bugs are expected.

---

## 1. Semantic Code Search

**Problem:** Text search (`grep`, `rg`) finds literal matches but misses semantic equivalents. Searching for "authentication handler" won't find `verifyToken()` or `checkCredentials()`.

**Solution:** DevAI embeds code chunks into a vector space where semantically similar code clusters together. A natural language query is embedded and matched against the codebase by cosine similarity.

**How it works:**
1. Code is parsed into AST nodes via tree-sitter
2. Nodes are chunked at symbol boundaries (file, class, function, block)
3. Each chunk is embedded using sentence-transformers (default: `minilm-l6`, 384 dimensions)
4. Embeddings are stored in LanceDB with metadata (file, symbol, language, line numbers)
5. Search queries are embedded and matched by L2 distance
6. Results are deduplicated by `file:start_line` and ranked by score

---

## 2. AST-Aware Semantic Chunking

**Problem:** Naive chunking (fixed token windows) splits code mid-function, losing context.

**Solution:** DevAI chunks at AST boundaries — never mid-symbol.

**Levels:**

```
file (imports + symbol list)
  └─ class (signature + fields)
      └─ function (complete body)
          └─ block (for large functions, split at logical boundaries)
```

**Constants:**

```
MAX_CHUNK_TOKENS         = 512    (upper bound per chunk)
MIN_CHUNK_TOKENS         = 64     (below this, merge with neighbor)
LARGE_FUNCTION_THRESHOLD = 1024   (above this, split at block boundaries)
```

Each chunk includes a **breadcrumb header** (`file > class > method`) so the AI always knows the structural context.

---

## 3. Git-Aware Incremental Indexing

**Problem:** Full reindexing on every change is slow for large repositories.

**Solution:** DevAI computes `git diff` between the last indexed commit and HEAD, processing only changed files.

**Pipeline:**

```
git diff (last_commit → HEAD)
  → detect added/modified/deleted/renamed files
  → for deletions: remove vectors + graph edges + file state
  → for renames: update paths in vectors + graph edges + file state
  → for additions/modifications: parse → chunk → embed → store
  → update index_state with new commit hash
```

**Model change detection:** If the embedding model name or dimension changes, DevAI forces a full reindex automatically.

**Content hash check:** Each file's content is hashed (SHA256[:16]). If the hash matches the stored value, the file is skipped even if git reports it as modified.

---

## 4. Branch Overlay & Deduplication

**Problem:** Feature branches contain changes that should take priority over main, but main has the rest of the codebase.

**Solution:** DevAI searches multiple branches in priority order and deduplicates results.

```
Active branch (priority 0)  →  searches first
Merge-base (priority 1)     →  fallback
Main/master (priority N)    →  base fallback
```

**Deduplication algorithm:**
1. Search all branches in lineage order
2. Track best match per file (lowest priority number = highest priority)
3. Filter tombstones (files deleted in higher-priority branches)
4. Return deduplicated results

**Tombstones:** When a file is deleted on a feature branch, a deletion marker is stored. This prevents the main branch version from appearing in search results.

---

## 5. Persistent Memory

**Problem:** AI agents forget everything between sessions — decisions, patterns, bugs, architectural context.

**Solution:** Structured memory with rich metadata, deduplication, and topic key upserts.

**Memory types:** `insight`, `decision`, `note`, `bug`, `architecture`, `pattern`, `discovery`

**Topic key upserts:** Saving a memory with the same `topic_key` in the same `(project, scope)` updates the existing entry and increments `revision_count`. This prevents memory bloat from evolving topics.

**Content deduplication:** Content is normalized (lowercased, whitespace collapsed) and hashed. Identical content within a 15-minute window increments `duplicate_count` instead of creating a new entry.

**Hybrid search:** `recall` combines vector similarity (semantic) with metadata filtering (type, project, scope) for accurate retrieval.

---

## 6. Call Graph & Dependency Analysis

**Problem:** AI agents can't answer "where is this function called?" or "what does this module depend on?" without reading every file.

**Solution:** During indexing, DevAI extracts edges from the AST: function calls, imports, inheritance, and references. These are stored in the graph store (SQLite adjacency list).

**Edge kinds:**
- `calls` — function/method invocations
- `imports` — module/file imports
- `inherits` — class inheritance
- `implements` — interface implementation
- `references` — generic symbol references

**Query:** `get_references` traverses the graph store and returns all edges where the symbol appears as source or target.

---

## 7. Intelligent Context Assembly

**Problem:** AI agents have limited context windows. Sending too much irrelevant code wastes tokens; sending too little misses critical context.

**Solution:** `build_context` combines memory recall + code search with token budgeting.

1. Recall relevant memories (decisions, patterns affecting the query)
2. Search code semantically
3. Optionally include dependency graph context
4. Assemble results within the `max_tokens` budget
5. Return formatted, AI-ready context

---

## 8. Multi-Language Support

DevAI supports **25+ languages** through two parser tiers:

**Tree-sitter AST parsing (18 languages):**
Python, JavaScript, TypeScript, Go, Java, Rust, C, C++, Ruby, PHP, Kotlin, Swift, Scala, Dart, C#, Lua, Zig, Elixir

**Raw text parsing (12 formats):**
HTML, CSS, SCSS, Sass, Less, JSON, YAML, XML, Markdown, SQL, GraphQL, Protobuf

Raw text languages are chunked and embedded but without symbol extraction or graph edges.

**File extensions mapped:**

```
.py .js .jsx .mjs .cjs .ts .tsx .mts .go .java .rs
.c .h .cpp .hpp .cc .cxx .hh .rb .php .kt .kts
.swift .scala .sc .dart .cs .lua .zig .ex .exs
.html .htm .css .scss .sass .less .json .yaml .yml
.xml .svg .md .sql .graphql .gql .proto
```

---

## 9. Embedding Provider Flexibility

DevAI supports multiple embedding providers:

| Provider | Config Value | Model Default | Dimension |
|----------|-------------|---------------|-----------|
| Sentence Transformers | `local` | `minilm-l6` | 384 |
| OpenAI | `openai` | `small` | — |
| Voyage AI | `voyage` | `code-3` | — |
| Custom HTTP | `custom` | — | configurable |

**Local provider** runs entirely offline — no API keys, no network dependency. Models are downloaded once and cached.

---

## 10. Interactive TUI

The TUI provides a complete interface for browsing and managing indexes without the AI assistant:

- **Dashboard** — repo overview with status
- **Search** — semantic code search with vim navigation (`j`/`k`)
- **Repositories** — per-repo statistics (files, symbols, chunks)
- **Branches** — per-branch index freshness and commit info
- **Memory** — browse and search persistent memories
- **History** — session tool call timeline with timings
- **Detail view** — full code with line numbers

Built with Bubbletea v1 + Bubbles + Lipgloss. State machine pattern: Model (state) → Update (events) → View (render).

---

## 11. Multi-Backend Storage

**Problem:** Single-machine vector storage doesn't scale to teams. Developers can't share code intelligence across machines.

**Solution:** Three storage modes with transparent backend switching.

**How hybrid mode works:**

- **Write-through:** Every upsert goes to local LanceDB first, then to shared Qdrant
- **Read-local-first:** Searches query local store first. If local returns results, those are used. If empty, falls back to shared Qdrant
- **Graceful degradation:** If Qdrant is unreachable, DevAI continues with local-only mode. Failed writes are queued in a bounded retry queue (max 10,000 operations)
- **Health monitoring:** Background thread checks Qdrant health every 60 seconds. On recovery, queued operations are automatically flushed
- **ID mapping:** LanceDB string IDs are mapped to Qdrant UUIDs via deterministic UUID5 (same ID always produces same UUID — idempotent upserts)

**Push/Pull synchronization:**

- `devai push-index` — copies local vectors to Qdrant (local → shared)
- `devai pull-index` — copies Qdrant vectors to local (shared → local)
- `devai sync-index` — bidirectional merge with last-write-wins conflict resolution via `indexed_at` timestamps

---

## 12. Index Synchronization (Push/Pull/Sync)

**Problem:** Developers index repos locally but need to share with the team. Or they need to pull team indexes to a new machine.

**Solution:** Three CLI commands for bidirectional index synchronization.

**Push (local → shared):**

```
devai push-index --repo my-repo [--branch main]
```

Reads all vectors from local LanceDB via `scroll_all()`, maps IDs to UUID5, and upserts to Qdrant in batches of 1000.

**Pull (shared → local):**

```
devai pull-index --repo my-repo [--branch main]
```

Reads all vectors from Qdrant via `scroll_all()`, restores original LanceDB IDs from `_lance_id` payload, and upserts to local store.

**Sync (bidirectional):**

```
devai sync-index --repo my-repo [--branch main]
```

Additive merge — no deletes. Conflict resolution: last-write-wins based on `indexed_at` timestamp.

---

## Storage Mode Quick Reference

| Mode | Vector Backend | Offline? | Team Sharing? | Configuration |
|------|---------------|----------|---------------|---------------|
| **Local** (default) | LanceDB on disk | Yes | No | `DEVAI_STORAGE_MODE=local` |
| **Shared** | Qdrant (remote) | No | Yes | `DEVAI_STORAGE_MODE=shared` + `DEVAI_QDRANT_URL` |
| **Hybrid** | LanceDB + Qdrant | Degrades gracefully | Yes | `DEVAI_STORAGE_MODE=hybrid` + `DEVAI_QDRANT_URL` |

> **Note:** Memory (decisions, patterns, bugs) is always stored in local SQLite regardless of storage mode. Memory sharing via Qdrant is not yet implemented.

See [Setup & Configuration](setup.md#storage-modes) for detailed configuration instructions.
