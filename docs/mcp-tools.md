# MCP Tools Reference

> Back to [DOCS](../DOCS.md) | [README](../README.md)

---

All 17 tools are registered in `internal/mcp/server.go` and exposed via the MCP stdio protocol. Start with `devai server mcp`.

---

## Search & Read

### search

Semantic search across indexed code. Returns relevant code chunks ranked by similarity.

**Parameters:**
- **`query`** (required) — natural language search query
- `repo` — repository path filter
- `branch` — branch to search (default: current)
- `limit` — maximum results (default: 10)
- `language` — filter by programming language

### read_file

Read a file's contents with optional line range.

**Parameters:**
- **`path`** (required) — file path to read
- `start_line` — start line (1-indexed)
- `end_line` — end line (inclusive)

### build_context

Build AI-ready context from the codebase for a given query. Combines memory recall + code search with token budgeting.

**Parameters:**
- **`query`** (required) — what context is needed
- `max_tokens` — token budget (default: 4096)
- `branch` — branch context
- `include_deps` — include dependency graph context (default: true)

### read_symbol

Get a symbol's definition, code, and documentation.

**Parameters:**
- **`name`** (required) — symbol name to look up
- `repo` — repository filter
- `branch` — branch context

### get_references

Find all usages of a symbol across the codebase.

**Parameters:**
- **`symbol`** (required) — symbol name
- `repo` — repository filter
- `branch` — branch context

---

## Indexing & Status

### index_repo

Trigger repository indexing. Supports incremental or full reindex.

**Parameters:**
- **`path`** (required) — repository path
- `branch` — branch to index (default: current)
- `incremental` — incremental index (default: true)

### index_status

Show index freshness and statistics per branch.

**Parameters:**
- `repo` — repository filter

### get_branch_context

Get current branch information and index statistics.

**Parameters:**
- `branch` — branch name (default: current)

### switch_context

Switch the active search context to a different repository or branch.

**Parameters:**
- `repo` — repository path
- `branch` — branch name

### get_session_history

Get recent session activity (queries, tool calls, files accessed).

**Parameters:**
- `limit` — maximum events (default: 20)
- `type` — filter by event type

---

## Memory

### remember

Save a structured memory entry with rich metadata. Supports topic_key for upserts and content deduplication.

**Parameters:**
- `content` — memory content (preferred, supports structured format: **What/Why/Where/Learned**)
- `text` — alias for content (backward compat)
- `title` — short searchable title (auto-generated if omitted)
- `type` — insight, decision, note, bug, architecture, pattern, discovery (default: note)
- `scope` — shared (team) or local (personal, default: shared)
- `project` — project context for scoping
- `topic_key` — stable key for upserts (e.g. `architecture/auth-model`)
- `tags` — comma-separated
- `files` — comma-separated file paths
- `repo` — repository context
- `branch` — branch context

### recall

Search memories using hybrid semantic + metadata search.

**Parameters:**
- **`query`** (required) — search query
- `scope` — shared, local, or all (default: all)
- `type` — filter by memory type
- `project` — filter by project
- `limit` — maximum results (default: 10)

### memory_context

Get recent memories without search — quick context recovery.

**Parameters:**
- `project` — filter by project
- `scope` — shared or local
- `limit` — maximum results (default: 20)

### memory_stats

Get memory system statistics: total count, breakdown by type and project.

**Parameters:** none

---

## Index Synchronization

### push_index

Push local vectors to shared Qdrant store. Requires shared or hybrid storage mode.

**Parameters:**
- **`repo`** (required) — repository name
- `branch` — branch filter (default: all branches)

### pull_index

Pull vectors from shared Qdrant store to local. Requires shared or hybrid storage mode.

**Parameters:**
- **`repo`** (required) — repository name
- `branch` — branch filter (default: all branches)

### sync_index

Bidirectional sync between local and shared stores. Additive only (no deletes). Uses `indexed_at` timestamps for conflict resolution.

**Parameters:**
- **`repo`** (required) — repository name
- `branch` — branch filter (default: all branches)
