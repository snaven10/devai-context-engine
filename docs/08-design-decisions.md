# Design Decisions

> 🇪🇸 [Leer en español](es/08-decisiones-de-diseno.md)

Architecture Decision Records (ADRs) for DevAI. Each entry documents what was decided, why, and what tradeoffs were accepted.

Read this if you are a contributor wondering "why is it built this way?" These are the answers.

---

## ADR-01: Hybrid Go + Python Architecture

**Decision**: Split DevAI into a Go CLI/MCP server and a Python ML service.

**Context**: DevAI needs both high-performance CLI/server capabilities and access to the Python ML ecosystem (tree-sitter, sentence-transformers, LanceDB, PyTorch). No single language covers both well.

**Options Considered**:
- **Pure Python**: Simple. Single process. But Python CLIs are slow to start (200-500ms), MCP server would be heavier than necessary, and distribution is painful (virtualenvs, pip dependency hell).
- **Pure Go**: Fast CLI, easy distribution (single binary). But Go's ML ecosystem is immature — no good tree-sitter bindings, no sentence-transformers equivalent, embedding models would need CGO or external services.
- **Go + Python hybrid**: Go handles CLI, MCP protocol, and process management. Python handles ML — parsing, embedding, vector search. Communication via JSON-RPC over stdio.

**Choice**: Hybrid. Go for the interface layer, Python for the intelligence layer. They communicate over JSON-RPC on stdio pipes.

**Tradeoffs**:
- Two languages to maintain, two build systems
- Process management complexity (Go must start, monitor, and restart the Python service)
- Serialization overhead on every call (negligible in practice — JSON-RPC is fast for the payload sizes involved)
- Contributors need to know both languages

**Status**: Current. The split has proven correct — Go gives us fast startup and easy distribution (single binary + Python wheel), Python gives us the entire ML ecosystem.

---

## ADR-02: JSON-RPC 2.0 over Stdio

**Decision**: Use JSON-RPC 2.0 over stdio pipes for Go-Python communication.

**Context**: The Go process needs to call Python functions and get structured results back. Need a protocol that is simple, low-overhead, and does not require network ports.

**Options Considered**:
- **gRPC**: Type-safe, fast binary serialization, streaming support. But requires protobuf compilation, adds build complexity, and is overkill for a local process-to-process channel.
- **HTTP/REST**: Familiar, debuggable. But requires a port (conflicts possible), HTTP overhead is unnecessary for local IPC, and adds a web server dependency to the Python side.
- **Embedded Python (CGO)**: No IPC needed — call Python directly from Go. But CGO is fragile, cross-compilation breaks, and Python GIL makes concurrency painful.
- **JSON-RPC over stdio**: Simple. The Go process spawns Python as a subprocess, writes JSON to stdin, reads JSON from stdout. No ports, no sockets, no service discovery.

**Choice**: JSON-RPC 2.0 over stdio. The protocol is a single-page spec. Implementation is ~100 lines on each side.

**Tradeoffs**:
- No streaming (request-response only). Not needed for current use cases.
- JSON parsing overhead vs binary protocols. Irrelevant — payloads are small, and the real work (embedding, search) dwarfs serialization cost.
- Debugging requires log inspection (no curl-able endpoint). Acceptable for a local tool.
- Single connection — no parallel requests over one pipe. Solved by request queuing in the Go client.

**Status**: Current. Simple, reliable, zero configuration.

---

## ADR-03: LanceDB as Default Vector Store

**Decision**: Use LanceDB as the default vector storage backend.

**Context**: Need a vector database that works locally, requires zero setup, and handles the scale of typical codebases (10K-100K files, 50K-500K chunks).

**Options Considered**:
- **ChromaDB**: Popular, Python-native. But adds a heavy dependency (SQLite + DuckDB + its own embedding layer), and had stability issues in early versions. Opinionated about embedding — we want to control that ourselves.
- **Qdrant**: Production-grade, excellent performance. But requires a running server (Docker or binary). Not suitable as a default for a CLI tool that should work with zero setup. Supported as an optional backend for shared/team use.
- **FAISS**: Facebook's vector search library. Fast, well-tested. But no persistence out of the box — you manage serialization yourself. No metadata filtering. Low-level.
- **LanceDB**: Embedded (no server), file-based (easy to inspect/delete/backup), supports metadata filtering, good performance up to millions of vectors. Apache Arrow-based — efficient columnar storage.

**Choice**: LanceDB as default. Qdrant supported as an optional backend for shared deployments.

**Tradeoffs**:
- LanceDB is younger than FAISS/Qdrant — less battle-tested at extreme scale
- File-based storage means no concurrent writes from multiple processes (not a problem — DevAI is single-user per repo)
- Smaller community than ChromaDB (but cleaner API and fewer surprises)

**Status**: Current. LanceDB has been reliable. The file-based model fits perfectly — `.devai/vectors/` is just files you can delete and rebuild.

---

## ADR-04: SQLite for Structured Data

**Decision**: Use SQLite for the code graph and persistent memories.

**Context**: Need structured storage for relationships (function calls, imports, class hierarchies) and user memories (decisions, discoveries, session summaries). Must work locally with zero setup.

**Options Considered**:
- **PostgreSQL**: Full-featured, great for teams. But requires a running server. Absurd for a CLI tool's local data.
- **In-memory only**: Fastest possible. But data lost on restart — unacceptable for memories, and rebuilding the code graph is expensive.
- **SQLite**: Embedded, zero-config, single-file database. Handles millions of rows. ACID-compliant. Available everywhere.

**Choice**: SQLite. Two databases: `graph.db` for code relationships, `memory.db` for persistent memories.

**Tradeoffs**:
- No concurrent writes (WAL mode helps but does not eliminate). Fine — single-user tool.
- No network access (cannot share a SQLite file across machines easily). Shared deployments use Qdrant for vectors; graph/memory remain local.
- Schema migrations must be handled manually. Acceptable at current scale.

**Status**: Current. SQLite is the right tool for this job. It will remain the default for local storage.

---

## ADR-05: Deterministic Vector IDs

**Decision**: Generate vector IDs deterministically from content identity (file path + chunk range + branch), not randomly.

**Context**: When a file changes, its chunks need to be updated in the vector store. Need to know which existing chunks to replace.

**Options Considered**:
- **UUIDs**: Simple, no collisions. But you cannot look up "the chunk for lines 10-50 of main.go" without a secondary index. Updates require delete-by-metadata + insert (two operations, race-prone).
- **Auto-increment**: Even worse — no way to correlate chunks across reindexes.
- **Deterministic (hash of path + range + branch)**: The same logical chunk always has the same ID. Upsert is a single operation. No orphaned vectors after reindex.

**Choice**: Deterministic IDs. Formula: `hash(repo + file_path + chunk_start + chunk_end + branch)`.

**Tradeoffs**:
- If chunking boundaries change (e.g., a function grows), old chunk IDs become orphans. Solved by cleanup during incremental reindex.
- Hash collisions are theoretically possible. SHA-256 truncation makes this negligible.
- Slightly more complex ID generation vs `uuid4()`. Worth it for clean upsert semantics.

**Status**: Current. This decision eliminates an entire class of data consistency bugs.

---

## ADR-06: Branch Overlay, Not Copies

**Decision**: Handle branches by overlaying branch-specific changes on top of the main index, not by maintaining separate indexes per branch.

**Context**: Developers switch branches frequently. Maintaining a full index per branch would multiply storage and indexing time.

**Options Considered**:
- **Per-branch index**: Each branch gets its own complete vector store. Clean isolation. But storage grows linearly with branches, and indexing a new branch means full reindex.
- **Ignore branches**: Index only the current HEAD. Simple. But switching branches invalidates search results until reindex completes.
- **Branch overlay**: Maintain one base index (main/default branch). When on a feature branch, overlay changed files on top. Search merges base + overlay results.

**Choice**: Branch overlay. The base index covers the majority of code (unchanged files). Only branch-specific diffs are indexed separately.

**Tradeoffs**:
- Search merging adds complexity — must handle "file deleted in branch" and "chunk replaced in branch" correctly
- Stale base index (if main moves ahead) can return slightly outdated results for unchanged files. Acceptable — code search is approximate anyway.
- Overlay cleanup needed when branches are merged/deleted

**Status**: Current. Dramatically reduces storage and makes branch switching near-instant.

---

## ADR-07: AST-Aware Chunking

**Decision**: Use tree-sitter ASTs to create semantically meaningful chunks, not arbitrary line or token splits.

**Context**: Code search quality depends heavily on chunk quality. A chunk should be a coherent unit of code — a function, a class method, a type definition. Splitting mid-function destroys semantic meaning.

**Options Considered**:
- **Line-based (fixed N lines)**: Simple. But splits functions in half, mixes unrelated code in one chunk, produces poor embeddings.
- **Token-based (fixed N tokens)**: Better than lines for embedding models. Same fundamental problem — no awareness of code structure.
- **File-level (one chunk per file)**: Preserves all context. But large files exceed embedding model limits, and search returns entire files instead of relevant sections.
- **AST-aware**: Parse the code, identify natural boundaries (functions, classes, blocks), chunk along those boundaries. Each chunk is a complete semantic unit.

**Choice**: AST-aware chunking via tree-sitter. Functions and methods become individual chunks. Classes are split per-method with class context preserved. Top-level code is grouped by logical blocks.

**Tradeoffs**:
- Requires a parser for each language (tree-sitter grammars). Currently 25+ languages supported. Unsupported languages fall back to line-based chunking.
- Parse errors in broken code can produce poor chunks. Tree-sitter is error-tolerant, so this is rare.
- More complex than line splitting. Worth it — search quality improvement is dramatic.
- Chunk sizes vary (a 5-line utility function vs a 200-line method). Large chunks are split at logical sub-boundaries.

**Status**: Current. This is one of DevAI's core differentiators. AST-aware chunking produces significantly better search results than naive splitting.

---

## ADR-08: Incremental Indexing via Git Diff

**Decision**: Use `git diff` to determine which files changed, and only reindex those files.

**Context**: Full reindexing a large repo takes minutes. Developers change a few files per commit. Reindexing everything on every change is wasteful.

**Options Considered**:
- **Full reindex every time**: Simple, always correct. But 5-10 minutes for a large repo on every save is unacceptable.
- **File watcher (fsnotify)**: Real-time, catches every change. But noisy (editor temp files, build artifacts), misses changes made outside the editor, and does not handle branch switches.
- **Git diff**: Precise. Knows exactly what changed. Works across branch switches (diff between current HEAD and last indexed commit). Handles renames, deletes, and moves correctly.

**Choice**: Git diff as the change detection mechanism. The indexer stores the last indexed commit SHA and diffs against it.

**Tradeoffs**:
- Only works in git repos. Non-git directories require full reindex. Acceptable — DevAI is built for development workflows.
- Uncommitted changes require diffing against the working tree (slightly more complex than commit-to-commit diff). Implemented.
- Cannot detect changes to files outside the repo (external config, shared libraries). By design — those are not part of the repo.

**Status**: Current. This makes DevAI practical for continuous development use.

---

## ADR-09: Agent-Agnostic MCP Interface

**Decision**: Expose DevAI capabilities as MCP (Model Context Protocol) tools, not a custom protocol.

**Context**: DevAI needs to be usable by AI agents (Claude, GPT, Copilot, custom agents). Each agent platform has its own integration mechanism. Building custom integrations for each is unsustainable.

**Options Considered**:
- **Custom protocol**: Maximum control over the interface. But every agent needs a custom adapter. N agents = N adapters to maintain.
- **HTTP API**: Universal, any client can call it. But agents need specific tool schemas to know what is available. HTTP is too low-level — you end up building a tool layer on top anyway.
- **MCP (Model Context Protocol)**: Standardized protocol for exposing tools to AI agents. Agents that support MCP can use DevAI immediately. Schema-first — tools are self-describing.

**Choice**: MCP. DevAI exposes 14 tools via MCP. Any MCP-compatible agent can use them without custom integration code.

**Tradeoffs**:
- MCP is still evolving — spec changes may require updates
- Not all agents support MCP yet (but adoption is growing fast)
- MCP's tool schema is less expressive than a custom TypeScript/Python SDK
- Agents that do not support MCP need a wrapper (but that wrapper is thinner than a full custom integration)

**Status**: Current. MCP adoption is accelerating. This was the right bet.

---

## ADR-10: Memory Deduplication via Content Hashing

**Decision**: Automatically deduplicate memories using content hashing, not manual cleanup.

**Context**: AI agents store memories frequently — session summaries, decisions, discoveries. Without dedup, the memory store fills with near-identical entries. Manual cleanup is unrealistic (agents cannot be trusted to manage their own memory hygiene).

**Options Considered**:
- **Append-only**: Every `remember` call creates a new entry. Simple. But memory search returns 15 copies of the same decision, drowning useful results.
- **Manual cleanup**: Rely on the agent or user to delete old memories. Never happens in practice.
- **Content hash dedup**: Hash the memory content. If a memory with the same hash exists, skip the insert. Combined with topic_key upserts (ADR-12) for evolving topics.

**Choice**: Automatic dedup via content hashing. Same content = same memory, not a duplicate.

**Tradeoffs**:
- Minor content changes (whitespace, formatting) produce different hashes. Acceptable — semantically different content should be stored.
- Hash computation adds negligible overhead
- Cannot store intentionally duplicated content (no valid use case for this)

**Status**: Current. Memory quality improved dramatically after implementing this.

---

## ADR-11: Graceful Hybrid Degradation

**Decision**: When the shared backend (Qdrant) is unavailable, degrade gracefully to local-only storage instead of failing.

**Context**: DevAI supports both local (LanceDB/SQLite) and shared (Qdrant) storage. In team setups, the Qdrant server may be temporarily unreachable (network issues, server restart, VPN disconnect).

**Options Considered**:
- **Fail-fast**: If configured for shared storage and it is unavailable, throw an error. Clear behavior. But blocks the developer — they cannot use DevAI at all until the server is back.
- **Local-only mode**: Never use shared storage. Simple. But loses the team knowledge sharing that makes shared storage valuable.
- **Graceful degradation**: Try shared storage. If unavailable, fall back to local. Log a warning. Sync when shared storage comes back.

**Choice**: Graceful degradation. Local always works. Shared is best-effort.

**Tradeoffs**:
- Stale local data when shared is updated by teammates. Acceptable — code search is approximate.
- Sync-on-reconnect adds complexity. But the alternative (blocking the developer) is worse.
- Silent fallback might confuse users who expect shared results. Solved by logging warnings.

**Status**: Current. Developers should never be blocked by infrastructure issues in a local-first tool.

---

## ADR-12: Topic Key Upserts for Memory

**Decision**: Use topic_key-based upserts for memory updates. Same topic_key = update existing memory, not create a new one.

**Context**: Some memories represent evolving knowledge — architecture decisions that get refined, bug patterns that accumulate examples, session summaries that supersede previous ones. Append-only creates clutter. Versioned history adds complexity without clear value.

**Options Considered**:
- **Versioned history**: Keep every version of a topic, tagged with timestamps. Full audit trail. But memory search returns all versions, and agents cannot meaningfully use version history.
- **Manual dedup**: Require the caller to delete-then-insert. Error-prone — if delete fails or is forgotten, duplicates accumulate.
- **Topic key upsert**: If a memory with the same `topic_key` exists, replace its content. One topic = one memory entry, always current.

**Choice**: Topic key upserts. `remember(topic_key="architecture/auth-model", ...)` always updates the same memory entry.

**Tradeoffs**:
- No history — previous versions are overwritten. If history is needed, the caller should use a different topic_key (e.g., `architecture/auth-model/v2`). This is intentionally not automated.
- Topic key collisions between different projects are possible. Mitigated by including project name in queries.
- Requires discipline in topic_key naming. Documented conventions help (see memory protocol).

**Status**: Current. Combined with content hash dedup (ADR-10), this keeps the memory store clean and current without manual intervention.
