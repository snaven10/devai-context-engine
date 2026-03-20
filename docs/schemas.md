# Database Schemas

> Back to [DOCS](../DOCS.md) | [README](../README.md)

---

DevAI uses **LanceDB** for vector storage and **SQLite** (WAL mode) for relational data. All state lives in `.devai/state/` or `$DEVAI_STATE_DIR`.

## Tables Overview

- **LanceDB `code_vectors`** — embeddings with metadata (repo, branch, file, symbol, language, line numbers)
- **SQLite `graph_edges`** — call graph and import relationships (adjacency list)
- **SQLite `memories`** — persistent structured memories with dedup and topic key upserts
- **SQLite `sessions`** — session lifecycle tracking
- **SQLite `index_state`** — last indexed commit per repo/branch
- **SQLite `file_state`** — per-file content hashes for change detection
- **SQLite `branch_lineage`** — branch hierarchy and merge-base tracking

---

## Vector Store (LanceDB)

```
id              string        # deterministic: SHA256(repo:branch:file:start_line)[:32]
text            string        # chunk content
vector          list[float32] # embedding (dim depends on model, 384 for minilm-l6)
repo            string        # repository path
branch          string        # git branch
commit          string        # git commit hash
file            string        # file path
symbol          string        # symbol name (empty for file-level chunks)
symbol_type     string        # function, class, struct, method, etc.
language        string        # programming language
start_line      int32         # starting line number
end_line        int32         # ending line number
chunk_level     string        # file, class, function, block, memory
content_hash    string        # SHA256[:16] of content
is_deletion     bool          # tombstone flag for branch overlay
memory_type     string        # insight, decision, note, bug (memory entries)
memory_scope    string        # shared, local (memory entries)
memory_tags     string        # comma-separated tags (memory entries)
```

**Deterministic IDs:** `SHA256(repo:branch:file:start_line)[:32]` — enables true upserts without orphaned vectors.

**Search filtering:** Supports conditions on `repo`, `branch`, `language`, `symbol_type`. Results ranked by L2 distance, deduplicated by `file:start_line`.

---

## Graph Edges (SQLite)

```sql
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,            -- source symbol or file
    target TEXT NOT NULL,            -- target symbol or file
    kind TEXT NOT NULL,              -- calls, imports, inherits, implements, references
    source_file TEXT NOT NULL,       -- file containing the reference
    target_file TEXT,                -- file being referenced (NULL if unresolved)
    line INTEGER NOT NULL DEFAULT 0, -- line number of reference
    repo TEXT NOT NULL,
    branch TEXT NOT NULL,
    metadata TEXT,                   -- JSON blob for extra data
    UNIQUE(source, target, kind, repo, branch, source_file)
);

CREATE INDEX idx_edges_source ON graph_edges(repo, branch, source);
CREATE INDEX idx_edges_target ON graph_edges(repo, branch, target);
CREATE INDEX idx_edges_source_file ON graph_edges(repo, branch, source_file);
```

**Edge kinds:**

| Kind | Description |
|------|-------------|
| `calls` | Function/method invocation |
| `imports` | Module/file import |
| `inherits` | Class inheritance |
| `implements` | Interface implementation |
| `references` | Generic symbol reference |

---

## Memories (SQLite)

```sql
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'note',  -- insight, decision, note, bug,
                                               -- architecture, pattern, discovery
    scope TEXT NOT NULL DEFAULT 'shared',       -- shared (team) or local (personal)
    project TEXT NOT NULL DEFAULT '',
    topic_key TEXT,                             -- stable key for upserts
    tags TEXT NOT NULL DEFAULT '',              -- comma-separated
    author TEXT NOT NULL DEFAULT '',
    repo TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '',             -- comma-separated file paths
    revision_count INTEGER NOT NULL DEFAULT 1,  -- times updated via topic_key
    duplicate_count INTEGER NOT NULL DEFAULT 1, -- times seen via content hash
    normalized_hash TEXT NOT NULL DEFAULT '',    -- SHA256 of normalized content
    vector_id TEXT,                             -- link to LanceDB vector
    session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT                             -- soft delete
);

CREATE INDEX idx_memories_topic ON memories(project, scope, topic_key)
    WHERE topic_key IS NOT NULL;
CREATE INDEX idx_memories_hash ON memories(normalized_hash, project, scope);
CREATE INDEX idx_memories_type ON memories(memory_type);
CREATE INDEX idx_memories_project ON memories(project);
CREATE INDEX idx_memories_created ON memories(created_at DESC);
```

### Memory Save Priority

1. If `topic_key` matches existing in same `(project, scope)` → **UPDATE**, increment `revision_count`
2. If `normalized_hash` matches within 900s (15 min) in same `(project, scope)` → increment `duplicate_count`
3. Else → **INSERT** new entry

**Deduplication:** Content is lowercased + whitespace collapsed, then SHA256 hashed.

---

## Sessions (SQLite)

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL DEFAULT '',
    directory TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    summary TEXT
);

CREATE INDEX idx_sessions_project ON sessions(project, started_at DESC);
```

---

## Index State (SQLite)

```sql
CREATE TABLE index_state (
    repo_path TEXT NOT NULL,
    branch TEXT NOT NULL,
    last_commit TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_dimension INTEGER NOT NULL,
    file_count INTEGER DEFAULT 0,
    symbol_count INTEGER DEFAULT 0,
    chunk_count INTEGER DEFAULT 0,
    indexed_at TEXT NOT NULL,
    PRIMARY KEY (repo_path, branch)
);

CREATE TABLE file_state (
    repo_path TEXT NOT NULL,
    branch TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,      -- SHA256[:16] for change detection
    language TEXT NOT NULL DEFAULT '',
    symbol_count INTEGER DEFAULT 0,
    chunk_count INTEGER DEFAULT 0,
    PRIMARY KEY (repo_path, branch, file_path)
);

CREATE TABLE branch_lineage (
    repo_path TEXT NOT NULL,
    branch TEXT NOT NULL,
    base_branch TEXT NOT NULL,
    merge_base_commit TEXT,
    PRIMARY KEY (repo_path, branch)
);
```

---

## SQLite Configuration

```
journal_mode = WAL          -- concurrent reads during writes
busy_timeout = 5000         -- 5s wait on lock contention
synchronous = NORMAL        -- balance safety + performance
foreign_keys = ON
```
