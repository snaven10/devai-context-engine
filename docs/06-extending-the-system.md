# Extending DevAI

> 🇪🇸 [Leer en español](es/06-extender-el-sistema.md)

This guide covers how to extend DevAI with new capabilities. Each section follows a consistent pattern: where to add code, what interface to implement, and how to wire it in.

DevAI's extension points are intentionally simple. No plugin system, no dynamic loading. You add code in the right place and register it.

---

## 1. Adding a New MCP Tool

An MCP tool requires changes in two places: the Python ML service (the actual logic) and the Go MCP server (the tool registration and argument forwarding).

### Step 1: Add the Python JSON-RPC Handler

All Python handlers live in `ml/devai_ml/server.py`. The dispatch table maps method names to handler functions.

```python
# In server.py — add to the _dispatch dict
"devai/your_tool": self._handle_your_tool,
```

Then write the handler. Every handler receives a `params` dict and returns a result dict:

```python
async def _handle_your_tool(self, params: dict) -> dict:
    """Your tool description."""
    repo_path = params.get("repo_path", "")
    query = params.get("query", "")

    # Do the actual work — call into indexer, store, embeddings, etc.
    results = await self._some_service.do_work(repo_path, query)

    return {
        "results": results,
        "count": len(results),
    }
```

Handlers are async. If your logic is CPU-bound, wrap it with `asyncio.to_thread`.

### Step 2: Register the Tool in the Go MCP Server

In `internal/mcp/server.go`, register the tool with its JSON Schema for arguments:

```go
s.addTool(mcp.Tool{
    Name:        "your_tool",
    Description: "What this tool does — one line for the agent",
    InputSchema: json.RawMessage(`{
        "type": "object",
        "properties": {
            "repo_path": {
                "type": "string",
                "description": "Absolute path to the repository"
            },
            "query": {
                "type": "string",
                "description": "What to search for"
            }
        },
        "required": ["repo_path", "query"]
    }`),
}, s.handleYourTool)
```

### Step 3: Add the Go Handler Function

The handler parses arguments, calls the ML client via JSON-RPC, and formats the response:

```go
func (s *Server) handleYourTool(args map[string]interface{}) (*mcp.CallToolResult, error) {
    repoPath, _ := args["repo_path"].(string)
    query, _ := args["query"].(string)

    result, err := s.mlClient.Call("devai/your_tool", map[string]interface{}{
        "repo_path": repoPath,
        "query":     query,
    })
    if err != nil {
        return mcp.ErrorResult(fmt.Sprintf("your_tool failed: %v", err)), nil
    }

    // Format result as text content for the agent
    return mcp.TextResult(formatYourToolResult(result)), nil
}
```

The pattern is always the same: parse args, call ML, format response. Look at existing handlers like `handleSearch` or `handleBuildContext` for real examples.

### Key Files

| What | Where |
|------|-------|
| Python handler + dispatch | `ml/devai_ml/server.py` |
| Go tool registration | `internal/mcp/server.go` |
| ML client (JSON-RPC calls) | `internal/ml/client.go` |
| MCP types | `internal/mcp/types.go` |

---

## 2. Adding a New Embedding Provider

Embedding providers implement a protocol defined in the embeddings package. DevAI ships with local (minilm-l6) and supports OpenAI-compatible APIs.

### Step 1: Implement the EmbeddingProvider Protocol

Create a new file in `ml/devai_ml/embeddings/`:

```python
# ml/devai_ml/embeddings/your_provider.py

from .base import EmbeddingProvider

class YourProvider(EmbeddingProvider):
    """Your embedding provider."""

    def __init__(self, config: dict):
        self.model_name = config.get("model", "default-model")
        self.dimension = config.get("dimension", 384)
        # Initialize client, load model, etc.

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Returns a list of float vectors, one per input text.
        Each vector must have exactly self.dimension dimensions.
        """
        # Your embedding logic here
        vectors = await self._call_model(texts)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query. May apply query-specific preprocessing."""
        result = await self.embed([text])
        return result[0]

    @property
    def dimensions(self) -> int:
        return self.dimension
```

The protocol requires: `embed(texts)` for batch embedding, `embed_query(text)` for single queries, and a `dimensions` property.

### Step 2: Register in the Factory

In `ml/devai_ml/embeddings/factory.py`:

```python
from .your_provider import YourProvider

PROVIDERS = {
    "local": LocalProvider,
    "openai": OpenAIProvider,
    "your_provider": YourProvider,  # Add here
}

def create_provider(config: dict) -> EmbeddingProvider:
    provider_type = config.get("provider", "local")
    provider_class = PROVIDERS.get(provider_type)
    if not provider_class:
        raise ValueError(f"Unknown embedding provider: {provider_type}")
    return provider_class(config)
```

### Step 3: Update Config Options

Users select the provider via `config.yaml`:

```yaml
embeddings:
  provider: your_provider
  model: your-model-name
  dimension: 768
```

### Reference

Use `ml/devai_ml/embeddings/local.py` as the canonical reference implementation. It shows model loading, batching, and dimension handling.

---

## 3. Adding Language Support

DevAI uses tree-sitter for AST parsing. Adding a language means installing its grammar and registering the file extension mapping.

### Step 1: Install the Tree-Sitter Grammar

```bash
pip install tree-sitter-{language}
```

Tree-sitter grammars are published as Python packages. DevAI currently supports 25+ languages this way.

### Step 2: Add Extension Mapping in the Registry

In `ml/devai_ml/parsers/registry.py`:

```python
LANGUAGE_MAP = {
    ".py": "python",
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".rs": "rust",
    ".your_ext": "your_language",  # Add here
    # ...
}
```

The registry maps file extensions to tree-sitter language names. When the indexer encounters a file, it looks up the extension, loads the corresponding grammar, and parses the AST.

### Step 3 (Optional): Add Custom Query Patterns

For better code graph edges (function calls, imports, class hierarchies), you can add tree-sitter query patterns:

```python
# Custom queries for extracting specific AST nodes
QUERIES = {
    "your_language": {
        "functions": "(function_definition name: (identifier) @name)",
        "classes": "(class_definition name: (identifier) @name)",
        "imports": "(import_statement) @import",
    }
}
```

Without custom queries, DevAI falls back to generic AST traversal. This works but produces less precise edges in the code graph.

### How the Registry Works

1. Indexer receives a file path
2. Registry checks the extension against `LANGUAGE_MAP`
3. If matched, loads the tree-sitter grammar via `tree_sitter_languages`
4. Parser produces an AST
5. Chunker walks the AST to create semantic chunks (functions, classes, blocks)
6. Edge extractor identifies relationships (calls, imports, inheritance)

Files with unrecognized extensions are skipped during AST parsing but can still be indexed as raw text chunks.

---

## 4. Adding a Storage Backend

Storage backends handle persistence for vectors, graphs, and memories. Each has a defined interface.

### Step 1: Implement the Store Interface

Create a new file in `ml/devai_ml/stores/`:

```python
# ml/devai_ml/stores/your_store.py

class YourStore:
    """Your storage backend."""

    def __init__(self, config: dict):
        self.path = config.get("path", "./data")
        # Initialize connection, create tables, etc.

    async def search(
        self,
        query_vector: list[float],
        limit: int = 10,
        filters: dict | None = None,
    ) -> list[dict]:
        """Search for similar vectors.

        Returns list of dicts with: id, content, metadata, score.
        """
        # Your search implementation
        pass

    async def upsert(self, items: list[dict]) -> None:
        """Insert or update items.

        Each item has: id, content, vector, metadata.
        Uses deterministic IDs — same ID means update, not duplicate.
        """
        pass

    async def delete(self, ids: list[str]) -> None:
        """Delete items by ID."""
        pass

    async def get(self, id: str) -> dict | None:
        """Get a single item by ID."""
        pass
```

The key contract: `upsert` must be idempotent (same ID = update). DevAI uses deterministic vector IDs, so the store must handle upserts correctly.

### Step 2: Register in the Factory

In `ml/devai_ml/stores/factory.py`:

```python
from .your_store import YourStore

STORES = {
    "lancedb": LanceDBStore,
    "qdrant": QdrantStore,
    "your_store": YourStore,
}
```

### Step 3: Wire into the Storage Router

The storage router dispatches to the correct store based on data type (vectors, graphs, memories). If your store handles a specific type, update the router configuration:

```yaml
storage:
  vectors: your_store    # or keep lancedb
  graphs: sqlite         # usually stays sqlite
  memories: your_store   # or keep default
```

---

## 5. Modifying the Indexing Pipeline

The indexing pipeline is the core of DevAI. Understanding its flow is essential before modifying it.

### How the Orchestrator Works

The indexing pipeline follows this sequence:

```
git diff → file list → filter → parse (tree-sitter) → chunk → embed → store
```

1. **Git diff**: Determines which files changed since last index
2. **File list**: Expands to full paths, respects `.gitignore` and config exclusions
3. **Filter**: Skips binary files, large files, excluded patterns
4. **Parse**: Tree-sitter AST for supported languages, raw text for others
5. **Chunk**: AST-aware splitting (functions, classes as natural boundaries)
6. **Embed**: Batch embedding via configured provider
7. **Store**: Upsert chunks with deterministic IDs into vector store

### Where to Hook In

**Custom file filtering** — Modify the filter stage to include/exclude files based on custom criteria. This happens before parsing, so it is cheap.

**Custom chunking** — The chunker decides how to split parsed ASTs into indexable units. The default strategy uses function and class boundaries. To change chunk sizes or boundaries, modify the chunking logic.

**Post-processing** — After embedding but before storage, you can add metadata enrichment, duplicate detection, or custom scoring.

**Edge extraction** — The code graph builder runs alongside chunking. It extracts call relationships, imports, and type hierarchies from the AST. Custom edge types go here.

### How to Modify Chunking Behavior

Chunking is AST-aware by default:

- **Functions/methods**: Each becomes its own chunk
- **Classes**: Split into per-method chunks with class context preserved
- **Top-level code**: Chunked by logical blocks (imports, constants, etc.)
- **Large functions**: Split at logical boundaries if they exceed the token limit

To modify, look at the chunking module. The key parameters are:
- `max_chunk_tokens`: Upper limit per chunk (default ~500 tokens)
- `context_lines`: How many surrounding lines to include for context
- `overlap`: Token overlap between adjacent chunks for continuity

The chunker receives an AST and produces a list of `Chunk` objects, each with content, metadata (file, line range, symbol name), and a deterministic ID.

### Key Files

| What | Where |
|------|-------|
| Orchestrator | `ml/devai_ml/indexer/` |
| Chunking | `ml/devai_ml/indexer/chunker.py` |
| AST parsing | `ml/devai_ml/parsers/` |
| Edge extraction | `ml/devai_ml/graph/` |
| Embedding dispatch | `ml/devai_ml/embeddings/` |
| Storage dispatch | `ml/devai_ml/stores/` |

---

## General Extension Principles

1. **Follow existing patterns.** Every extension point has at least one reference implementation. Read it before writing yours.
2. **Register, do not discover.** DevAI uses explicit registration (factory dicts, dispatch tables), not classpath scanning or plugin discovery. This is intentional — it keeps the system predictable.
3. **Deterministic IDs everywhere.** Vectors, graph nodes, and memories all use deterministic IDs derived from content and path. Your extensions must preserve this property.
4. **Async by default.** Python handlers are async. If your code is CPU-bound, use `asyncio.to_thread`.
5. **Test with a real repo.** The best test is indexing a real repository and verifying search results. Unit tests for individual components, integration tests with actual repos.
