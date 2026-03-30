# DevAI Architecture

> 🇪🇸 [Leer en español](es/02-arquitectura.md)

## 1. System Overview

DevAI is a hybrid Go + Python AI Code Intelligence Engine. The Go layer provides a fast, single-binary CLI, an MCP server for AI agent integration, and all user-facing interfaces (TUI, CLI commands, git hooks). The Python layer provides the ML pipeline: code parsing via tree-sitter, AST-aware semantic chunking, embedding generation, vector storage, and graph construction. The two layers communicate exclusively via JSON-RPC 2.0 over stdio pipes -- Go spawns Python as a subprocess, sends requests to its stdin, and reads responses from its stdout. There is no network layer between them. This hybrid design exists because Go compiles to a single binary with instant startup (critical for CLI UX), while Python has the only mature ecosystem for tree-sitter bindings, sentence-transformers, and LanceDB.

## 2. Architecture Diagram

```
                         AI Agent (Claude, Cursor, etc.)
                                     |
                                     | MCP Protocol (stdio)
                                     |
                    +=====================================+
                    |         GO BINARY (devai)           |
                    |                                     |
                    |  +-------------------------------+  |
                    |  |       MCP Server (14 tools)    |  |
                    |  |    internal/mcp/server.go      |  |
                    |  +-------------------------------+  |
                    |          |                           |
                    |  +-------+------+  +-----------+    |
                    |  | ML Client    |  | Branch    |    |
                    |  | (JSON-RPC)   |  | Context   |    |
                    |  | mlclient/    |  | branch/   |    |
                    |  +-------+------+  +-----------+    |
                    |          |         +-----------+    |
                    |          |         | Storage   |    |
                    |          |         | Router    |    |
                    |          |         | storage/  |    |
                    |          |         +-----------+    |
                    |  +-------+------+  +-----------+    |
                    |  | Python       |  | Session   |    |
                    |  | Runtime      |  | Tracker   |    |
                    |  | Discovery    |  | session/  |    |
                    |  | runtime/     |  +-----------+    |
                    |  +--------------+                   |
                    |                                     |
                    |  +-------------------------------+  |
                    |  |  CLI (Cobra)                   |  |
                    |  |  init, index, search, server,  |  |
                    |  |  watch, tui, hooks, push/pull, |  |
                    |  |  sync-index, server configure   |  |
                    |  +-------------------------------+  |
                    |                                     |
                    |  +-------------------------------+  |
                    |  |  TUI (Bubbletea)               |  |
                    |  |  internal/tui/                  |  |
                    |  +-------------------------------+  |
                    +================+====================+
                                     |
                                     | JSON-RPC 2.0 (stdio pipes)
                                     | stdin/stdout of subprocess
                                     |
                    +================+====================+
                    |      PYTHON PROCESS (devai_ml)      |
                    |                                     |
                    |  +-------------------------------+  |
                    |  |  JSON-RPC Server (~900 lines)  |  |
                    |  |  ml/devai_ml/server.py          |  |
                    |  +------+------+---------+-------+  |
                    |         |      |         |          |
                    |  +------+--+ +-+------+ ++-------+  |
                    |  | Parsers | |Chunker | |Embedder|  |
                    |  | tree-   | |semantic| |multi-  |  |
                    |  | sitter  | |4-level | |provider|  |
                    |  | 25+ lang| |AST-    | |(local, |  |
                    |  | + raw   | |aware   | | OpenAI,|  |
                    |  +---------+ +--------+ | Voyage)|  |
                    |                         +--------+  |
                    |  +-------------------------------+  |
                    |  |  Pipeline Orchestrator          |  |
                    |  |  git diff -> parse -> chunk     |  |
                    |  |  -> embed -> store              |  |
                    |  +------+-------+-------+--------+  |
                    |         |       |       |           |
                    +=========|=======|=======|===========+
                              |       |       |
                    +---------+--+ +--+----+ ++----------+
                    |  LanceDB   | | SQLite | |  Qdrant   |
                    |  (vectors) | | (graph,| |  (shared  |
                    |  embedded, | |  memory,| |  vectors) |
                    |  disk-     | |  index  | |  remote,  |
                    |  based)    | |  state) | |  gRPC,    |
                    |            | |  WAL    | |  optional) |
                    | .devai/    | | .devai/ | |           |
                    | state/     | | state/  | |           |
                    | vectors/   | | index.db| |           |
                    +------------+ +--------+ +-----------+
```

## 3. Component Deep Dive

### 3.1 MCP Server

**What it does:** Exposes 15 tools to AI agents via the Model Context Protocol. Tools include `search`, `read_file`, `build_context`, `read_symbol`, `get_references`, `remember`, `recall`, `memory_context`, `memory_stats`, `get_branch_context`, `switch_context`, `get_session_history`, `index_status`, `index_repo`, and `reindex_memories`.

**Why it exists:** Without the MCP server, AI agents have no way to query DevAI's indexed code intelligence. The MCP protocol is agent-agnostic -- any MCP-compatible client (Claude Desktop, Cursor, Cline, custom agents) can connect without custom integration code.

**Key files:**
- `internal/mcp/server.go` (~730 lines) -- Tool registration, request handling, response formatting

**How it connects:** The MCP server holds a reference to `mlclient.StdioClient`. Every tool handler translates MCP tool calls into JSON-RPC requests, sends them to the Python ML service, and formats the response back as MCP tool results. The server runs in stdio mode (`ServeStdio()`), reading MCP messages from its own stdin and writing to its own stdout.

### 3.2 ML Client (JSON-RPC)

**What it does:** Manages the Python subprocess lifecycle and provides a typed Go interface for sending JSON-RPC 2.0 requests to the ML service. Handles request serialization, response deserialization, and atomic request IDs.

**Why it exists:** Without the ML client, Go would need to either embed Python (CGO nightmare), use gRPC (requires proto compilation and a running server), or shell out per-request (unacceptable latency). The stdio subprocess model gives zero-network-dependency communication with persistent connection semantics.

**Key files:**
- `internal/mlclient/client.go` -- `StdioClient` struct, `Call()` method, subprocess spawn
- `internal/runtime/python.go` -- Python binary resolution (6-step priority chain)

**How it connects:** The CLI and MCP server both instantiate `StdioClient`. On first use, it spawns the Python process using the resolved Python binary, sets up stdin/stdout pipes, and keeps the process alive for the duration of the Go process. All subsequent calls are multiplexed over the same pipes with a mutex.

### 3.3 Python Runtime Discovery

**What it does:** Resolves the correct Python binary to use for spawning the ML service. Uses a 6-step prioritized resolution chain:

1. `DEVAI_PYTHON` environment variable (explicit override)
2. Config file: `.devai/config.yaml` `runtime.python_path`
3. Installed location: `~/.local/share/devai/python/venv/bin/python` (or `LOCALAPPDATA` on Windows)
4. Relative to executable: `{binary_dir}/../ml/.venv/bin/python`
5. Relative to cwd: `ml/.venv/bin/python`
6. System fallback: `python3` (Linux/macOS) or `python` (Windows)

**Why it exists:** DevAI must work in multiple deployment scenarios -- development (source checkout), installed binary (GitHub releases), CI/CD, and containerized. Each scenario places the Python venv in a different location. Without runtime discovery, users would need manual configuration in every environment.

**Key files:**
- `internal/runtime/python.go` -- `FindPython()` function

### 3.4 Branch Context

**What it does:** Manages branch-aware search resolution. Builds a lineage chain (current branch -> parent -> main) and provides branch filters for vector store queries. Supports virtual branch switching without `git checkout`.

**Why it exists:** Without branch context, search would either return only main-branch results (missing feature branch code) or return everything (polluting results with unrelated branches). The overlay model lets search walk the lineage: feature-branch chunks override main-branch chunks for the same file, without duplicating the entire index.

**Key files:**
- `internal/branch/context.go` -- `Context` struct, `BranchFilter()`, `SwitchBranch()`

**How it connects:** The MCP server creates a `Context` when `switch_context` is called. Branch filters are passed as metadata to the Python ML service on every search request.

### 3.5 Storage Router

**What it does:** Selects between local, shared, and hybrid storage modes. Routes storage operations to the appropriate backend based on configuration.

**Why it exists:** Solo developers want embedded-only storage (zero setup). Teams want a shared Qdrant instance (cross-developer search). The router abstracts this so upper layers never branch on storage mode.

**Key files:**
- `internal/storage/router.go` -- `Router` struct, `IsLocal()`, `IsShared()`

**Modes:**
- `local` -- LanceDB + SQLite only (default, zero config)
- `shared` -- Qdrant only (team server)
- `hybrid` -- Write to both, search merges results. Graceful degradation: if Qdrant is unreachable, falls back to local-only without error.

### 3.6 JSON-RPC Server (Python)

**What it does:** Receives JSON-RPC 2.0 requests on stdin, dispatches to the appropriate handler method, and writes JSON-RPC responses to stdout. This is the Python-side counterpart to `mlclient.StdioClient`.

**Why it exists:** Central dispatch for all ML operations. Provides a stable method-based API that the Go layer calls into, isolating Go from Python implementation details.

**Key files:**
- `ml/devai_ml/server.py` (~900 lines) -- `MLService` class, `handle_request()` dispatcher

**How it connects:** Initialized with all stores and components (embedding provider, parser registry, chunker, pipeline orchestrator). Each JSON-RPC method maps to an internal operation: `index` calls the pipeline orchestrator, `search` calls the vector store, `remember` calls the memory store.

### 3.7 Parsers

**What it does:** Extracts structured AST from source files. Uses tree-sitter for 25+ languages (Go, Python, TypeScript, Rust, Java, C, C++, Ruby, etc.) and a raw parser fallback for languages without tree-sitter grammars (HTML, CSS, Markdown, etc.).

**Why it exists:** Without AST-level parsing, the chunker would split code at arbitrary line boundaries, breaking function definitions mid-body. Tree-sitter provides language-agnostic AST access with consistently fast parsing (C-based).

**Key files:**
- `ml/devai_ml/parsers/treesitter_parser.py` -- Tree-sitter integration
- `ml/devai_ml/parsers/raw_parser.py` -- Fallback for unsupported languages
- `ml/devai_ml/parsers/registry.py` -- Language detection and parser selection
- `ml/devai_ml/parsers/queries/` -- Tree-sitter query files per language

### 3.8 Semantic Chunker

**What it does:** Splits parsed source files into semantically meaningful chunks using a 4-level hierarchy:

1. **Module level** -- Package/module declarations, imports
2. **Class/type level** -- Struct, class, interface definitions
3. **Function level** -- Function/method bodies
4. **Block level** -- Large functions split at logical boundaries

**Why it exists:** Embedding quality degrades sharply when chunks contain unrelated code. AST-aware chunking ensures each chunk represents a single semantic unit (one function, one class), producing embeddings that accurately represent that unit's meaning. Never splits mid-symbol.

**Key files:**
- `ml/devai_ml/chunking/semantic_chunker.py` -- `SemanticChunker` class

### 3.9 Embeddings

**What it does:** Converts code chunks into dense vector representations. Supports multiple providers via a factory pattern:

- **Local** (`sentence-transformers`): Default. No API key needed. Runs on CPU/GPU.
- **OpenAI** (`text-embedding-3-small/large`): Hosted, higher quality for natural language.
- **Voyage** (`voyage-code-2`): Specialized for code, highest code-search accuracy.
- **Custom**: User-provided embedding endpoint.

**Why it exists:** Different teams have different constraints. Solo developers want zero-API-key local embeddings. Enterprise teams want Voyage for maximum accuracy. The provider abstraction lets users swap without touching any other component.

**Key files:**
- `ml/devai_ml/embeddings/base.py` -- `EmbeddingProvider` abstract base class
- `ml/devai_ml/embeddings/local.py` -- Sentence-transformers provider
- `ml/devai_ml/embeddings/openai_embed.py` -- OpenAI provider
- `ml/devai_ml/embeddings/voyage_embed.py` -- Voyage provider
- `ml/devai_ml/embeddings/custom.py` -- User-defined endpoint
- `ml/devai_ml/embeddings/factory.py` -- Provider selection from config

### 3.10 Pipeline Orchestrator

**What it does:** Coordinates the full indexing pipeline: detect changed files via `git diff`, parse each file, chunk the AST, generate embeddings, and write to the vector store. Handles incremental indexing (only changed files) and full reindex (on model change or force).

**Why it exists:** Without orchestration, every component would need to know about every other component. The pipeline encapsulates the entire flow so callers (CLI `index` command, MCP `index_repo` tool) just say "index this repo" and the orchestrator handles the rest.

**Key files:**
- `ml/devai_ml/pipeline/orchestrator.py` -- `IndexPipeline` class
- `ml/devai_ml/pipeline/git_state.py` -- Git diff detection

### 3.11 Vector Store (LanceDB)

**What it does:** Stores and queries code chunk embeddings using LanceDB, an embedded columnar vector database. Supports filtered ANN (approximate nearest neighbor) search with metadata filters (repo, branch, language, file path).

**Why it exists:** LanceDB is embedded (no server process), disk-based (handles repos larger than RAM), and supports filtered vector search. This is the default store that works with zero configuration.

**Key files:**
- `ml/devai_ml/stores/vector_store.py` -- `LanceDBVectorStore` class

**Data location:** `.devai/state/vectors/`

### 3.12 Qdrant Store

**What it does:** Stores and queries embeddings on a remote Qdrant server via gRPC. Used in shared/team mode where multiple developers need to search the same index.

**Key files:**
- `ml/devai_ml/stores/qdrant_store.py` -- `QdrantStore` class

### 3.13 Hybrid Store

**What it does:** Write-through store that writes to both LanceDB and Qdrant simultaneously. On search, queries both and merges results. If Qdrant is unreachable, silently degrades to local-only.

**Key files:**
- `ml/devai_ml/stores/hybrid_store.py` -- `HybridStore` class

### 3.14 Graph Store

**What it does:** Maintains a code relationship graph using a SQLite adjacency list. Stores edges like "function A calls function B", "file X imports module Y". Used by `get_references` and `build_context` to traverse call graphs and dependency chains.

**Why it exists:** Vector search finds semantically similar code but cannot answer structural questions like "what calls this function?" or "what depends on this module?". The graph store provides exact structural relationships.

**Key files:**
- `ml/devai_ml/stores/graph_store.py` -- `SQLiteGraphStore` class

**Data location:** `.devai/state/index.db` (shared SQLite database)

### 3.15 Memory Store

**What it does:** Persistent memory for AI agent sessions. Stores memories with metadata (type, scope, project, topic_key), embeds them for semantic recall, and handles deduplication via normalized content hashing with a 15-minute window. Supports topic_key-based upserts: if a memory with the same topic_key exists, it updates instead of creating a duplicate.

**Why it exists:** AI agents lose context between sessions. The memory store lets agents persist decisions, discoveries, and session summaries, then recall them semantically in future sessions.

**Key files:**
- `ml/devai_ml/stores/memory_store.py` -- `MemoryStore` class, `Memory` dataclass

**Data location:** `.devai/state/index.db` (shared SQLite database)

### 3.16 Index State

**What it does:** Tracks which commits have been indexed for each repo/branch combination. Enables incremental indexing by telling the pipeline orchestrator "last indexed commit was X, diff from X to HEAD".

**Key files:**
- `ml/devai_ml/stores/index_state.py` -- `IndexStateStore` class

### 3.17 Store Factory

**What it does:** Creates the appropriate vector store based on environment configuration (`DEVAI_STORAGE_MODE`, Qdrant connection env vars). Returns a `LanceDBVectorStore`, `QdrantStore`, or `HybridStore`.

**Key files:**
- `ml/devai_ml/stores/factory.py` -- `StorageConfig`, `create_storage_config_from_env()`, `create_vector_store()`

### 3.18 Session Tracking

**What it does:** Tracks active agent sessions with timestamps. Used by `get_session_history` to provide agents with context about recent interactions.

**Key files:**
- `internal/session/session.go`

### 3.19 TUI

**What it does:** Terminal user interface built with Bubbletea. Provides interactive search, results browsing, and index status viewing.

**Key files:**
- `internal/tui/model.go` -- Bubbletea model
- `internal/tui/view.go` -- View rendering
- `internal/tui/update.go` -- Message handling
- `internal/tui/styles.go` -- Lipgloss styling

## 4. Data Flow Diagrams

### 4.1 Indexing Pipeline

```
User runs: devai index /path/to/repo
            |
            v
   +--------+---------+
   |  CLI index cmd    |
   |  cmd/devai/cmd/   |
   |  index.go         |
   +--------+----------+
            |
            | JSON-RPC: "index"
            | params: {repo, branch, force}
            v
   +--------+----------+
   |  Pipeline          |
   |  Orchestrator      |
   +--------+----------+
            |
            | Step 1: What changed?
            v
   +--------+----------+
   |  Git State         |
   |  git diff          |
   |  last_commit..HEAD |
   +--------+----------+
            |
            | list of changed files
            v
   +--------+----------+
   |  Parser Registry   |
   |  detect language   |
   |  select parser     |
   +--------+----------+
            |
            | Step 2: Parse to AST
            v
   +--------+----------+
   |  Tree-sitter /     |
   |  Raw Parser        |
   |  file -> AST nodes |
   +--------+----------+
            |
            | AST nodes with metadata
            v
   +--------+----------+
   |  Semantic Chunker  |
   |  4-level split     |
   |  module > class >  |
   |  function > block  |
   +--------+----------+
            |
            | chunks with boundaries
            v
   +--------+----------+
   |  Embedding         |
   |  Provider          |
   |  chunks -> vectors |
   +--------+----------+
            |
            | vectors + metadata
            v
   +--------+----------+         +----------------+
   |  Vector Store      +-------->  LanceDB       |
   |  (+ Graph Store)   |        | .devai/state/  |
   +--------+-----------+        | vectors/       |
            |                    +----------------+
            | graph edges
            v
   +--------+----------+         +----------------+
   |  Graph Store       +-------->  SQLite         |
   |  call/import edges |        | .devai/state/  |
   +-------------------+         | index.db       |
            |                    +----------------+
            v
   +--------+----------+
   |  Index State       |
   |  record indexed    |
   |  commit SHA        |
   +--------------------+
```

### 4.2 Search Query

```
AI Agent sends MCP tool call: search(query="auth middleware", limit=5)
            |
            | MCP protocol (stdio)
            v
   +--------+----------+
   |  MCP Server        |
   |  handleSearch()    |
   +--------+----------+
            |
            | Resolve branch context
            v
   +--------+----------+
   |  Branch Context    |
   |  lineage:          |
   |  feature/auth ->   |
   |  develop -> main   |
   +--------+----------+
            |
            | JSON-RPC: "search"
            | params: {query, branches: [...], limit: 5}
            v
   +--------+----------+
   |  ML Service        |
   |  handle_request()  |
   +--------+----------+
            |
            | Step 1: Embed the query
            v
   +--------+----------+
   |  Embedding         |
   |  Provider          |
   |  "auth middleware"  |
   |  -> [0.12, -0.34,  |
   |      0.56, ...]    |
   +--------+----------+
            |
            | query vector
            v
   +--------+----------+
   |  Vector Store      |
   |  ANN search with   |
   |  branch filter     |
   |  top-k by cosine   |
   |  similarity        |
   +--------+----------+
            |
            | ranked chunks with scores
            v
   +--------+----------+
   |  MCP Server        |
   |  format as MCP     |
   |  tool result       |
   +--------+----------+
            |
            | MCP response (stdio)
            v
         AI Agent receives results:
         [{file, lines, content, score}, ...]
```

### 4.3 Memory Lifecycle

```
AI Agent calls: remember(title="Fixed N+1 in UserList",
                         type="bugfix", content="...",
                         project="myapp", topic_key="bugfix/user-n1")
            |
            | MCP protocol (stdio)
            v
   +--------+----------+
   |  MCP Server        |
   |  handleRemember()  |
   +--------+----------+
            |
            | JSON-RPC: "remember"
            v
   +--------+----------+
   |  Memory Store      |
   +--------+----------+
            |
            | Step 1: Deduplication check
            | hash = sha256(normalize(content))
            | Check: same hash within 15-min window?
            |   YES -> skip (return existing ID)
            |   NO  -> continue
            |
            | Step 2: Topic key upsert check
            | Check: existing memory with same topic_key?
            |   YES -> UPDATE existing row
            |   NO  -> INSERT new row
            |
            | Step 3: Embed the memory
            v
   +--------+----------+
   |  Embedding         |
   |  Provider          |
   |  content -> vector |
   +--------+----------+
            |
            | Step 4: Store
            v
   +--------+----------+         +----------------+
   |  SQLite            |        | memory table:  |
   |  (structured data) +------->| id, title,     |
   |                    |        | type, content, |
   +--------+-----------+        | topic_key,     |
            |                    | hash, created  |
            v                    +----------------+
   +--------+----------+         +----------------+
   |  Vector Store      +-------->  LanceDB       |
   |  (memory vectors)  |        | memory vectors |
   +--------------------+        +----------------+

            --- Later, agent calls recall ---

AI Agent calls: recall(query="N+1 query fix", project="myapp")
            |
            v
   +--------+----------+
   |  Memory Store      |
   +--------+----------+
            |
            | Step 1: Embed query
            | Step 2: Vector search in memory vectors
            | Step 3: Filter by project
            | Step 4: Return full content (not truncated)
            v
         AI Agent receives:
         [{title, type, content, created, score}, ...]
```

## 5. Storage Architecture

### 5.1 Local Mode (Default)

```
.devai/
  state/
    vectors/          <-- LanceDB (embedded, columnar)
      code.lance/     <-- Code chunk embeddings
      memory.lance/   <-- Memory embeddings
    index.db          <-- SQLite (WAL mode)
                          Tables: graph_edges, memories, index_state
```

- **When to use:** Solo developer, single machine
- **Pros:** Zero configuration, no external dependencies, works offline
- **Cons:** Not shareable across developers

### 5.2 Shared Mode

```
                    +-------------------+
                    |   Qdrant Server   |
                    |   (remote, gRPC)  |
                    |   collections:    |
                    |     code_chunks   |
                    |     memories      |
                    +-------------------+

Local machine:
.devai/
  state/
    index.db          <-- SQLite (graph + index state still local)
```

- **When to use:** Team with shared infrastructure
- **Pros:** All developers search the same index, no duplicate indexing
- **Cons:** Requires Qdrant deployment, network dependency

### 5.3 Hybrid Mode

```
Write path:
  chunk --> LanceDB (local)
       \-> Qdrant  (remote)    [write-through]

Search path:
  query --> LanceDB (local)  --> merge + deduplicate
       \-> Qdrant  (remote) -/

Degradation:
  Qdrant down? --> local-only (silent, no error)
  Qdrant back? --> resume writes (no manual intervention)
```

- **When to use:** Team that wants resilience
- **Pros:** Works offline, shared when connected, automatic failover
- **Cons:** Double storage cost, slight write latency

### 5.4 Deterministic IDs

All chunks use deterministic IDs: `sha256(repo:branch:file:start_line)`. This guarantees:
- Re-indexing the same code produces the same ID (true upsert, no duplicates)
- Cross-store consistency: LanceDB and Qdrant entries for the same chunk have the same ID
- Tombstone filtering: deleted chunks are identified by ID without scanning

## 6. Communication Protocol

### 6.1 Go -> Python: JSON-RPC 2.0 over stdio

The Go `StdioClient` writes JSON-RPC requests to the Python subprocess's stdin and reads responses from its stdout. Each request/response is a single JSON line terminated by `\n`.

**Request format:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "search",
  "params": {
    "query": "authentication middleware",
    "repo": "/home/user/myproject",
    "branch": "main",
    "limit": 10,
    "language": "go"
  }
}
```

**Success response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "chunks": [
      {
        "file": "internal/auth/middleware.go",
        "start_line": 15,
        "end_line": 42,
        "content": "func AuthMiddleware(next http.Handler) ...",
        "score": 0.89,
        "language": "go",
        "symbol": "AuthMiddleware"
      }
    ]
  }
}
```

**Error response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32602,
    "message": "Repository not indexed: /home/user/myproject"
  }
}
```

### 6.2 Agent -> Go: MCP Protocol over stdio

The MCP server uses the `mcp-go` library (`github.com/mark3labs/mcp-go`). AI agents connect to DevAI by configuring it as an MCP server that communicates over stdio. The MCP protocol handles tool listing, tool calls, and responses.

### 6.3 Request Threading

The `StdioClient` uses a mutex (`sync.Mutex`) to serialize JSON-RPC calls. Only one request is in-flight at a time. Request IDs are atomically incremented (`atomic.Int64`). This is sufficient because MCP tool calls from agents are inherently sequential.

## 7. Design Decisions

### 7.1 Go + Python Hybrid Architecture

**What:** CLI and infrastructure in Go, ML pipeline in Python.

**Why:** Go compiles to a single static binary with instant startup (<50ms), critical for CLI tools and git hooks. Python has the only mature ecosystem for tree-sitter bindings, sentence-transformers, LanceDB, and Qdrant clients. Writing embeddings in Go would mean reimplementing or using CGO (fragile, defeats single-binary goal).

**Tradeoffs:** Two runtimes to install and manage. Python venv adds ~500MB to disk. Subprocess spawn adds ~1-2s on first call (amortized over session).

**Alternatives considered:** Pure Go (no ML ecosystem), pure Python (slow CLI, no single binary), gRPC (heavier protocol, requires proto compilation, port management).

### 7.2 JSON-RPC over stdio (not gRPC, not HTTP)

**What:** Go spawns Python as a subprocess and communicates via stdin/stdout pipes using JSON-RPC 2.0.

**Why:** No port conflicts, no network configuration, no TLS setup, no service discovery. The Python process lifecycle is tied to the Go process -- no orphan processes. Works identically on Linux, macOS, and Windows.

**Tradeoffs:** Single-threaded communication (mutex-serialized). No streaming support (each response is complete). Cannot scale Python horizontally.

**Alternatives considered:** gRPC (requires proto compilation, port management, proto stubs), HTTP REST (port conflicts, process lifecycle management), shared memory (platform-specific, complex).

### 7.3 LanceDB as Default Vector Store

**What:** Embedded columnar vector database, no server process required.

**Why:** Zero-config experience. `devai init && devai index` just works without installing or running a database server. Disk-based storage handles repositories larger than RAM. Native ANN search with metadata filtering.

**Tradeoffs:** Single-process access (no concurrent writers from different processes). Not network-accessible (hence Qdrant for shared mode).

**Alternatives considered:** ChromaDB (heavier, server-mode default), FAISS (no metadata filtering, memory-only), Qdrant-only (requires server setup for basic usage), Milvus (heavy, overkill for single-developer use).

### 7.4 SQLite for Structured Data (WAL mode)

**What:** Graph edges, memories, and index state stored in a single SQLite database.

**Why:** Zero configuration, battle-tested, WAL mode for concurrent read performance, single-file backup. Matches the "no server" philosophy of the local mode.

**Tradeoffs:** Single-writer constraint (acceptable for single-user tool). Not suitable for multi-server deployments.

**Alternatives considered:** PostgreSQL (requires server), DuckDB (not designed for OLTP), separate JSON files (no query capability, no atomicity).

### 7.5 Deterministic Chunk IDs via sha256

**What:** Every chunk ID is `sha256(repo:branch:file:start_line)`.

**Why:** Enables true upserts: re-indexing the same code produces the same ID, so the store overwrites instead of duplicating. Cross-store consistency between LanceDB and Qdrant. Tombstone filtering without full scans.

**Tradeoffs:** If line numbers shift (inserting lines above a function), the same function gets a new ID. Mitigated by reindexing the full file on change, which replaces all old IDs.

**Alternatives considered:** UUID (no dedup, requires delete-before-insert), content hash (same code at different locations would collide), file-path-only (can't handle multiple chunks per file).

### 7.6 Branch Overlay Search

**What:** Search walks the branch lineage (feature -> develop -> main) instead of maintaining separate indexes per branch.

**Why:** Avoids duplicating the entire index for every branch. A feature branch index only contains chunks that differ from its parent. Search merges results from all lineage layers, with child branches taking priority (overriding parent chunks for the same file/location).

**Tradeoffs:** Lineage computation requires git operations. Complex merge logic for conflicting chunks.

**Alternatives considered:** Full index per branch (storage explosion), main-only indexing (misses feature branch code), tag-based filtering (no hierarchy).

### 7.7 AST-Aware Chunking (Never Split Mid-Symbol)

**What:** Code is split at AST boundaries (function, class, module), never at arbitrary line counts.

**Why:** A chunk containing half a function produces a meaningless embedding. AST-aware boundaries ensure each chunk represents a complete semantic unit, producing embeddings that accurately capture that unit's purpose.

**Tradeoffs:** Requires tree-sitter grammars per language (25+ maintained). Very long functions may still produce large chunks (mitigated by block-level splitting).

**Alternatives considered:** Fixed-size chunking (fast but meaningless boundaries), regex-based splitting (fragile across languages), character-count splitting (worse than line-based).

### 7.8 Incremental Indexing via git diff

**What:** On `devai index`, only files changed since the last indexed commit are re-parsed, re-chunked, and re-embedded.

**Why:** Full reindexing a large repository takes minutes. Incremental indexing takes seconds. The pipeline stores the last indexed commit SHA in `IndexStateStore` and diffs from there.

**Tradeoffs:** Requires tracking index state per repo/branch. Full reindex still needed on embedding model change (different model produces incompatible vectors).

**Alternatives considered:** Always full reindex (too slow for large repos), file-mtime-based (unreliable with git operations), inotify/fswatch (platform-specific, misses git operations).

### 7.9 Agent-Agnostic MCP

**What:** DevAI exposes tools via the Model Context Protocol, not a custom API.

**Why:** MCP is the emerging standard for AI agent tool integration. By implementing MCP, DevAI works with Claude Desktop, Cursor, Cline, and any future MCP-compatible agent without writing custom integrations for each.

**Tradeoffs:** MCP is still evolving (protocol may change). Stdio-only transport limits deployment options (no remote MCP server yet).

**Alternatives considered:** Custom REST API (requires per-agent integration), OpenAI function calling format (vendor-specific), Language Server Protocol (designed for editors, not AI agents).

### 7.10 Memory Deduplication (Normalized Hash + 15-min Window)

**What:** Before storing a memory, normalize the content (lowercase, strip whitespace), compute sha256, and check if the same hash was stored within the last 15 minutes. If so, skip.

**Why:** AI agents often call `remember` multiple times with semantically identical content (retries, rephrased summaries). Without dedup, the memory store fills with near-duplicates that pollute recall results.

**Tradeoffs:** 15-minute window is a heuristic. Genuinely different memories with identical normalized text within the window are dropped. Normalization may be too aggressive for code-heavy memories.

**Alternatives considered:** Exact-match dedup only (misses rephrased duplicates), semantic similarity dedup (expensive, requires embedding comparison on every save), no dedup (store pollution).

### 7.11 Graceful Hybrid Degradation

**What:** In hybrid mode, if Qdrant is unreachable, operations silently fall back to local-only. When Qdrant comes back, writes resume automatically.

**Why:** A network hiccup should not block a developer's workflow. The local store always has a complete copy (write-through), so local-only search is fully functional.

**Tradeoffs:** During Qdrant downtime, the shared index falls behind. Other developers querying Qdrant see stale data until the disconnected developer's writes catch up.

### 7.12 Topic Key Upserts

**What:** Memories with a `topic_key` field update (upsert) instead of creating duplicates. Same topic_key = same logical memory, different content = content update.

**Why:** Some memories represent evolving state (e.g., "architecture/auth-model" evolves as the auth system is built). Without upserts, each evolution creates a new row, and recall returns multiple conflicting versions.

**Tradeoffs:** Requires callers to choose stable, meaningful topic keys. A typo in the key creates a duplicate instead of updating.

## 8. Directory Structure

```
devai/
|
+-- cmd/devai/                     # Go CLI entrypoint
|   +-- main.go                    #   main(), Cobra root command
|   +-- cmd/                       #   Subcommands
|       +-- root.go                #     Root command, global flags
|       +-- init.go                #     devai init (create .devai/)
|       +-- index.go               #     devai index (trigger indexing pipeline)
|       +-- search.go              #     devai search (CLI search interface)
|       +-- server.go              #     devai server (start MCP server)
|       +-- mcp_configure.go       #     devai server configure (auto-setup MCP)
|       +-- watch.go               #     devai watch (file watcher for auto-index)
|       +-- tui.go                 #     devai tui (interactive terminal UI)
|       +-- hooks.go               #     devai hooks (git hook integration)
|       +-- push_index.go          #     devai push-index (upload index to remote)
|       +-- pull_index.go          #     devai pull-index (download index from remote)
|       +-- sync_index.go          #     devai sync-index (bidirectional sync)
|       +-- status.go              #     devai status (show index status)
|       +-- setup.go               #     devai setup (initial configuration)
|
+-- internal/                      # Go internal packages (not importable externally)
|   +-- mcp/
|   |   +-- server.go              #   MCP server: 15 tool handlers, ~730 lines
|   +-- mlclient/
|   |   +-- client.go              #   JSON-RPC stdio client for Python subprocess
|   +-- runtime/
|   |   +-- python.go              #   6-step Python binary resolution
|   +-- branch/
|   |   +-- context.go             #   Branch lineage and overlay search
|   +-- storage/
|   |   +-- router.go              #   Local/shared/hybrid mode routing
|   +-- session/
|   |   +-- session.go             #   Session tracking for agents
|   +-- config/
|   |   +-- ...                    #   Configuration loading (.devai/config.yaml)
|   +-- git/
|   |   +-- ...                    #   Git operations (current branch, diff, log)
|   +-- tui/
|   |   +-- model.go               #   Bubbletea model
|   |   +-- view.go                #   View rendering
|   |   +-- update.go              #   Message/event handling
|   |   +-- styles.go              #   Lipgloss styles
|   +-- db/
|   |   +-- ...                    #   Database utilities
|   +-- api/
|   |   +-- ...                    #   API types and helpers
|   +-- output/
|   |   +-- ...                    #   Output formatting (JSON, table, plain)
|   +-- mcpclient/
|       +-- ...                    #   MCP client (for testing/integration)
|
+-- ml/                            # Python ML layer
|   +-- devai_ml/
|   |   +-- __init__.py
|   |   +-- server.py              #   JSON-RPC server (~900 lines), MLService class
|   |   +-- parsers/
|   |   |   +-- registry.py        #     Language detection, parser dispatch
|   |   |   +-- treesitter_parser.py #   Tree-sitter AST parsing (25+ languages)
|   |   |   +-- raw_parser.py      #     Fallback for unsupported languages
|   |   |   +-- base.py            #     Parser abstract base class
|   |   |   +-- queries/           #     Tree-sitter query files (.scm) per language
|   |   +-- chunking/
|   |   |   +-- semantic_chunker.py #   4-level AST-aware chunking
|   |   +-- embeddings/
|   |   |   +-- base.py            #     EmbeddingProvider ABC
|   |   |   +-- factory.py         #     Provider selection from config
|   |   |   +-- local.py           #     sentence-transformers (default)
|   |   |   +-- openai_embed.py    #     OpenAI text-embedding-3
|   |   |   +-- voyage_embed.py    #     Voyage voyage-code-2
|   |   |   +-- custom.py          #     User-defined endpoint
|   |   +-- pipeline/
|   |   |   +-- orchestrator.py    #     Full indexing pipeline coordination
|   |   |   +-- git_state.py       #     Git diff detection for incremental index
|   |   +-- stores/
|   |   |   +-- factory.py         #     Storage mode selection from env
|   |   |   +-- vector_store.py    #     LanceDB vector store (default)
|   |   |   +-- qdrant_store.py    #     Qdrant remote vector store
|   |   |   +-- hybrid_store.py    #     Write-through dual store
|   |   |   +-- graph_store.py     #     SQLite adjacency list (code graph)
|   |   |   +-- memory_store.py    #     SQLite memories with dedup + upserts
|   |   |   +-- index_state.py     #     Track last indexed commit per repo/branch
|   |   +-- indexing/              #     Indexing utilities
|   |   +-- resolution/            #     Symbol resolution
|   |   +-- proto/                 #     Proto definitions (future gRPC)
|   +-- tests/                     #   Python test suite
|
+-- proto/                         # Protocol buffer definitions (future use)
+-- scripts/                       # Build/install/release scripts
+-- docs/                          # Documentation
|   +-- setup.md                   #   Installation guide
|   +-- architecture.md            #   Architecture overview (legacy)
|   +-- 02-architecture.md         #   This file
|   +-- api.md                     #   API reference
|   +-- mcp-tools.md               #   MCP tool catalog
|   +-- features.md                #   Feature list
|   +-- schemas.md                 #   Data schemas
|
+-- .devai/                        # Per-project DevAI state (gitignored)
    +-- state/
        +-- vectors/               #   LanceDB data files
        +-- index.db               #   SQLite (graph, memories, index state)
```
