# MCP Tools Reference

> Back to [DOCS](../DOCS.md) | [README](../README.md)

> **Warning**
> DevAI is in **alpha**. Tool parameters and response schemas may change between versions.

---

14 MCP tools are registered in `internal/mcp/server.go` and exposed via the MCP stdio protocol. Start with `devai server mcp`.

> **Note:** `push_index`, `pull_index`, and `sync_index` are CLI-only commands, not MCP tools.

---

## Quick Setup

### Automatic (recommended)

```bash
devai server configure --all
```

This auto-detects your project config and writes MCP server entries to all supported clients (Claude Code, Cursor). It also generates `.devai/AGENT.md` with tool usage instructions for your AI agent.

**Flags:**

| Flag | Description |
|------|-------------|
| `--claude` | Configure for Claude Code only |
| `--cursor` | Configure for Cursor only |
| `--all` | Configure for all detected clients (default) |
| `--show` | Preview config without writing |
| `--remove` | Remove DevAI from MCP configs |

**Environment variables set automatically:**

| Variable | When |
|----------|------|
| `DEVAI_STATE_DIR` | Always — points to `.devai/state/` |
| `DEVAI_STORAGE_MODE` | When project uses `shared` or `hybrid` mode |
| `DEVAI_QDRANT_URL` | When Qdrant URL is configured |
| `DEVAI_QDRANT_API_KEY` | When Qdrant API key is configured |

### Manual

#### Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "devai": {
      "type": "stdio",
      "command": "/path/to/devai",
      "args": ["server", "mcp"],
      "env": {
        "DEVAI_STATE_DIR": "/path/to/repo/.devai/state"
      }
    }
  }
}
```

#### Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "devai": {
      "type": "stdio",
      "command": "/path/to/devai",
      "args": ["server", "mcp"],
      "env": {
        "DEVAI_STATE_DIR": "/path/to/repo/.devai/state"
      }
    }
  }
}
```

Restart your AI client after configuring.

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

## Index Synchronization (CLI only)

These commands are available via the CLI only, not as MCP tools.

### push_index

Push local vectors to shared Qdrant store. Requires shared or hybrid storage mode.

```bash
devai push-index --repo my-repo [--branch main]
```

### pull_index

Pull vectors from shared Qdrant store to local. Requires shared or hybrid storage mode.

```bash
devai pull-index --repo my-repo [--branch main]
```

### sync_index

Bidirectional sync between local and shared stores. Additive only (no deletes). Uses `indexed_at` timestamps for conflict resolution.

```bash
devai sync-index --repo my-repo [--branch main]
```
