# JSON-RPC API Reference

> Back to [DOCS](../DOCS.md) | [README](../README.md)

---

The Python ML service communicates over JSON-RPC 2.0 via stdio. All 16 methods are dispatched through `devai_ml/server.py`.

**Request format:**

```json
{"method": "search", "params": {"query": "auth middleware", "limit": 5}, "id": 1}
```

**Response format:**

```json
{"jsonrpc": "2.0", "result": {...}, "id": 1}
```

**Error format:**

```json
{"jsonrpc": "2.0", "error": {"code": -32000, "message": "..."}, "id": 1}
```

---

## Embedding Methods

### embed

Embed a single text.

```
Params:  { text: string }
Returns: { vector: float[], dimension: int, model: string }
```

### embed_batch

Embed multiple texts.

```
Params:  { texts: string[] }
Returns: { vectors: float[][], dimension: int, model: string, count: int }
```

---

## Search & Retrieval

### search

Semantic search across indexed code. Results are deduplicated by `file:start_line`.

```
Params:  { query: string, limit?: int, branch?: string, language?: string, repo?: string }
Returns: {
  query: string,
  count: int,
  results: [{
    score: float,
    file: string,
    symbol: string,
    symbol_type: string,
    language: string,
    start_line: int,
    end_line: int,
    chunk_level: string,
    branch: string,
    text: string            // max 500 chars
  }]
}
```

### read_symbol

Find a symbol's definition and code.

```
Params:  { name: string, branch?: string, repo?: string }
Returns: {
  symbol: string,
  found: bool,
  file: string,
  start_line: int,
  end_line: int,
  language: string,
  symbol_type: string,
  branch: string,
  code: string,
  score: float
}
```

### get_references

Find all usages of a symbol (max 200 results).

```
Params:  { symbol: string, repo?: string, branch?: string }
Returns: {
  symbol: string,
  count: int,
  references: [{
    source: string,
    target: string,
    kind: string,
    file: string,
    line: int
  }]
}
```

---

## Parsing & Indexing

### parse_file

Parse a file and extract symbols, imports, and edges.

```
Params:  { file_path: string, content?: string, language?: string }
Returns: {
  file_path: string,
  language: string,
  symbols: [{
    name: string,
    kind: string,           // function, class, method, struct, etc.
    start_line: int,
    end_line: int,
    signature: string,
    parent?: string,
    visibility?: string
  }],
  imports: [{
    module: string,
    alias?: string,
    names: string[],
    line: int
  }],
  edges: [{
    source: string,
    target: string,
    kind: string,           // calls, imports, inherits, implements, references
    line: int
  }]
}
```

### index_repo

Index a repository (incremental or full).

```
Params:  { repo_path: string, branch?: string, incremental?: bool }
Returns: {
  repo_path: string,
  branch: string,
  commit: string,
  files_processed: int,
  files_skipped: int,
  chunks_created: int,
  symbols_found: int,
  edges_found: int,
  duration_seconds: float,
  errors: string[]
}
```

### health

Service health check.

```
Params:  {}
Returns: {
  status: "serving",
  model_loaded: string,
  model_dimension: int,
  languages_supported: string[]
}
```

---

## Memory

### remember

Save a structured memory entry. Supports topic_key upserts and content deduplication.

```
Params:  {
  content: string,          // preferred (or "text" as alias)
  title?: string,           // auto-generated if omitted
  type?: string,            // default: "note"
  scope?: string,           // default: "shared"
  project?: string,
  topic_key?: string,       // for upserts
  tags?: string,            // comma-separated
  author?: string,
  repo?: string,
  branch?: string,
  files?: string            // comma-separated
}
Returns: {
  saved: bool,
  id: int,
  title: string,
  type: string,
  scope: string,
  topic_key?: string,
  revision_count: int,
  duplicate_count: int,
  is_update: bool
}
```

### recall

Search memories with hybrid semantic + metadata search.

```
Params:  { query: string, scope?: string, type?: string, project?: string, limit?: int }
Returns: {
  query: string,
  count: int,
  memories: [{
    id: int,
    title: string,
    content: string,
    type: string,
    scope: string,
    project: string,
    topic_key?: string,
    tags: string,
    files: string,
    revision_count: int,
    duplicate_count: int,
    created_at: string,
    updated_at: string,
    score: float
  }]
}
```

### memory_context

Get recent memories without search (fast context recovery).

```
Params:  { project?: string, scope?: string, limit?: int }
Returns: { ... }  // same structure as recall
```

### memory_update

Update an existing memory by ID.

```
Params:  { id: int, title?: string, content?: string, memory_type?: string,
           tags?: string, files?: string, topic_key?: string }
Returns: { updated: bool, id: int, revision_count: int }
```

### memory_stats

Aggregate memory statistics.

```
Params:  {}
Returns: { total: int, by_type: {string: int}, by_project: {string: int} }
```

---

## Context & Status

### get_branch_context

Branch info and index stats.

```
Params:  { branch?: string, repo?: string }
Returns: {
  count: int,
  repos: [{
    repo: string,
    branch: string,
    last_commit: string,
    model: string,
    files: int,
    symbols: int,
    chunks: int,
    indexed_at: string
  }]
}
```

### get_session_history

Recent tool calls and events.

```
Params:  { limit?: int }
Returns: {
  count: int,
  events: [{
    timestamp: string,
    event_type: string,
    tool: string,
    summary: string,
    repo: string,
    branch: string
  }]
}
```

### index_status

Per-branch index freshness.

```
Params:  { repo?: string }
Returns: {
  count: int,
  repos: [{
    repo: string,
    name: string,
    branch: string,
    last_commit: string,
    model: string,
    dimension: int,
    files: int,
    symbols: int,
    chunks: int,
    indexed_at: string,
    status: "indexed"
  }]
}
```
