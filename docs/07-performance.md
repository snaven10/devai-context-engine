# Performance

> 🇪🇸 [Leer en español](es/07-rendimiento.md)

This document covers what to expect from DevAI in terms of speed, resource usage, and how to optimize for your workload.

---

## Indexing Performance

### What Affects Indexing Speed

Three factors dominate:

1. **Repository size** — More files means more parsing, chunking, and embedding. Linear relationship.
2. **Language mix** — Tree-sitter parsing speed varies by grammar complexity. TypeScript/TSX grammars are heavier than Go or Python. Matters at scale (10K+ files).
3. **Embedding provider** — The bottleneck. Local embeddings are CPU-bound. API-based embeddings are network-bound.

### Throughput Benchmarks

| Provider | Speed | Notes |
|----------|-------|-------|
| Local (minilm-l6-v2) | ~100-500 files/minute | CPU-dependent. Faster on machines with AVX2 support. |
| OpenAI API | ~200-1000 files/minute | Network-bound. Rate limits may throttle. |
| Local with GPU | ~500-2000 files/minute | If PyTorch detects CUDA/MPS. |

These are approximate. "Files per minute" varies by file size and chunk count — a 50-line Go file produces 1-2 chunks, a 500-line React component produces 10-15.

### Incremental vs Full Indexing

**Incremental indexing** (default) processes only files changed since the last index, determined by `git diff`. For a typical commit touching 5-20 files, indexing completes in seconds.

**Full reindex** is triggered by:
- First index of a repository
- Embedding model change (vector dimensions differ)
- Explicit user request (`devai index --full`)
- Corrupted or deleted vector store

Incremental indexing is the single most important performance feature. It makes DevAI practical for continuous use during development.

---

## Search Performance

All benchmarks below are for local storage (LanceDB + SQLite). Network-attached stores (Qdrant) add latency.

### Vector Search

- **Typical latency**: <100ms for repos up to 100K chunks
- **How it works**: Query text is embedded, then approximate nearest neighbor search runs against the vector store
- **Scaling**: LanceDB uses IVF indexing. Performance degrades gracefully — a 500K chunk repo might see 150-200ms queries
- **Cold start**: First query after process start may be slower (index loading). Subsequent queries benefit from cached indices.

### Graph Queries

- **Typical latency**: <50ms
- **How it works**: SQLite with indexed columns for symbol names, file paths, and relationship types
- **Use case**: "What calls this function?", "What does this module import?", "Show the class hierarchy"
- **Scaling**: SQLite handles millions of edges without issue. The graph is sparse relative to the vector store.

### Memory Search

- **Typical latency**: <200ms
- **How it works**: Hybrid search combining semantic similarity (vector) with metadata filtering (type, project, scope)
- **Why slower**: Two-stage — vector search for candidates, then metadata filtering and ranking

### build_context (Aggregated)

- **Typical latency**: 200-500ms
- **How it works**: Runs vector search + graph traversal + memory search in parallel, then merges and ranks results
- **This is the tool agents use most.** It is optimized for agent consumption — returns formatted context, not raw results.

---

## Storage

### Per-Chunk Costs

| Component | Size per chunk | What it stores |
|-----------|---------------|----------------|
| Vector embedding | ~768 bytes (384 dims x float16) | Semantic representation |
| Metadata | ~200-500 bytes | File path, symbol name, line range, language |
| Content | ~200-2000 bytes | Raw source code text |
| **Total per chunk** | **~1KB average** | |

### Typical Repository Sizes

| Repo size | Estimated chunks | Vector store size | SQLite (graph + memory) |
|-----------|-----------------|-------------------|------------------------|
| 1K files | ~5K chunks | ~5MB | ~2MB |
| 10K files | ~50K chunks | ~50MB | ~10MB |
| 50K files | ~250K chunks | ~250MB | ~40MB |
| 100K files | ~500K chunks | ~500MB | ~80MB |

These are rough estimates. Actual numbers depend on file sizes and language (a Go codebase produces fewer chunks per file than a React codebase with JSX).

### Storage Location

By default, DevAI stores data in `.devai/` within the repository root. This directory should be added to `.gitignore`.

```
.devai/
  vectors/     # LanceDB files
  graph.db     # SQLite — code graph
  memory.db    # SQLite — persistent memories
  config.yaml  # Repository-specific config
```

---

## Optimization Tips

### Use Incremental Indexing

It is on by default. Do not disable it. If you find yourself running full reindexes frequently, something is wrong — file an issue.

### Exclude Generated Files

Large generated files (bundles, compiled output, vendor directories) waste indexing time and pollute search results. Configure exclusions:

```yaml
# config.yaml
indexing:
  exclude:
    - "vendor/**"
    - "dist/**"
    - "node_modules/**"
    - "*.min.js"
    - "*.generated.go"
    - "*.pb.go"
```

DevAI respects `.gitignore` by default, but explicit exclusions in config give you finer control.

### Choose the Right Embedding Provider

| Scenario | Recommended | Why |
|----------|-------------|-----|
| Local development, privacy-sensitive | `local` (minilm-l6) | No network, fast enough, good quality for code |
| Large initial index (50K+ files) | `local` with GPU | Batch embedding is GPU-friendly |
| Shared team index | `openai` or compatible API | Consistent results across machines |
| Air-gapped environment | `local` | Only option, works well |

### Watch Mode

For the fastest feedback loop, use watch mode. DevAI monitors file changes and re-indexes automatically:

```bash
devai watch
```

This re-indexes individual files on save. Latency from save to searchable: typically 1-3 seconds.

### Batch vs Streaming

When indexing, DevAI batches embedding requests (default batch size: 32 texts). If you are seeing OOM errors during indexing:
- Reduce batch size in config
- Ensure you are not indexing massive generated files
- Check that exclusion patterns are working

---

## Resource Usage

### Python ML Service

| State | RAM | CPU | Notes |
|-------|-----|-----|-------|
| Idle (model loaded) | ~200MB | Near zero | Model stays in memory for fast queries |
| Indexing (batch embed) | ~400-800MB | High (1-2 cores) | Spikes during embedding batches |
| Search query | ~250MB | Brief spike | Embedding the query + search |
| Cold start | ~150MB → 200MB | Moderate | Model loading takes 2-5 seconds |

### Go Process

| State | RAM | CPU |
|-------|-----|-----|
| Idle | ~20MB | Near zero |
| Handling MCP request | ~25-30MB | Brief spike |
| Startup | ~15MB | Minimal |

The Go process is lightweight by design. It is a thin MCP server that forwards to the Python service.

### Disk I/O

- **Indexing**: Write-heavy. Vector upserts and SQLite inserts.
- **Search**: Read-heavy. Vector index scan and SQLite queries.
- **SSD strongly recommended.** LanceDB performance on spinning disks is significantly worse.

### Network

- **Local embeddings**: Zero network usage.
- **API embeddings**: ~1KB per embedding request, ~3KB response. For a 10K file repo, initial index sends ~50K API calls (batched).
- **Qdrant (if used)**: Network calls per search/upsert. Latency depends on deployment location.

---

## Monitoring

DevAI logs indexing progress to stderr. Key metrics to watch:

- **Files processed / total**: Shows indexing progress
- **Chunks created**: If this is much higher than expected, check for large files slipping through exclusions
- **Embedding time**: If this dominates, consider switching providers or adding GPU
- **Errors/skips**: Files that failed to parse (usually binary files that slipped through detection)

For programmatic monitoring, the `devai/status` JSON-RPC method returns current indexing state and statistics.
