# Semantic Code Search

> 🇪🇸 [Leer en español](../es/03-conceptos-fundamentales/busqueda.md)

## What It Is

DevAI's search is a semantic code search engine. It understands the **meaning** of your query, not just the keywords. When you search for "authentication middleware," it finds the `AuthGuard` class, the `verifyJWT` function, and the `requirePermissions` decorator — even though none of them contain the word "authentication."

## Why It Exists

`grep` finds text. DevAI finds **intent**.

| Approach | Query: "retry logic for failed API calls" |
|---|---|
| **grep/ripgrep** | Matches files containing "retry" or "API" literally. Misses `exponentialBackoff()`, `withRetries()`, or a loop with `catch` and `setTimeout`. |
| **DevAI search** | Returns the `RetryPolicy` class, the `fetchWithBackoff` function, and the error-handling middleware — ranked by semantic relevance. |

The difference matters at scale. In a 500k-line codebase, grep gives you noise. Semantic search gives you answers.

## How It Works Internally

### The Indexing Pipeline

When you run `devai index` (or indexing triggers automatically), this happens:

```
  git diff (since last indexed commit)
       │
       ▼
  Tree-sitter AST Parse (25+ languages)
       │
       ▼
  4-Level Semantic Chunking
       │
       ▼
  Embedding (sentence-transformers, 384-dim)
       │
       ▼
  Storage (LanceDB / Qdrant)
       │
       ▼
  Symbol Graph (SQLite edges)
```

Each step is deterministic and incremental. Only changed files are reprocessed. A full reindex happens only when the embedding model changes.

### The 4 Chunk Levels

Code isn't flat text. A file has structure: imports, classes, functions, control flow blocks. DevAI respects this structure by chunking at four semantic levels:

#### Level 1: File

The file-level chunk captures the high-level shape — imports, top-level declarations, and a symbol list. Think of it as a table of contents.

```
# auth/middleware.py
import jwt
from flask import request, abort
from .models import User, Permission

# Symbols: AuthMiddleware, require_auth, require_permission, decode_token
```

#### Level 2: Class

Each class gets its own chunk: signature, fields, method signatures. Enough to understand what the class **is** without reading every method body.

```
# auth/middleware.py > AuthMiddleware
class AuthMiddleware:
    secret_key: str
    token_header: str = "Authorization"

    def authenticate(self, request) -> User: ...
    def authorize(self, user, permission) -> bool: ...
    def refresh_token(self, token) -> str: ...
```

#### Level 3: Function

Every function or method becomes its own chunk. This is where most search hits land.

```
# auth/middleware.py > AuthMiddleware > authenticate
def authenticate(self, request) -> User:
    token = request.headers.get(self.token_header)
    if not token:
        abort(401, "Missing authentication token")
    payload = jwt.decode(token, self.secret_key, algorithms=["HS256"])
    return User.from_payload(payload)
```

#### Level 4: Block

Large functions (> 512 tokens) are split at control flow boundaries: `if/else`, `for`, `try/catch`, `match`. Each block retains its parent context header so it's never orphaned.

```
# auth/middleware.py > AuthMiddleware > authenticate > try-block
try:
    payload = jwt.decode(token, self.secret_key, algorithms=["HS256"])
    if payload.get("exp") < time.time():
        raise ExpiredTokenError()
    return User.from_payload(payload)
```

**Constraints:** max 512 tokens, min 64 tokens per chunk. Chunks below 64 tokens are merged upward into their parent.

### Context Headers (Breadcrumbs)

Every chunk carries a context header showing its position in the code hierarchy:

```
file > class > method > block
auth/middleware.py > AuthMiddleware > authenticate > try-block
```

This means search results always show WHERE in the codebase a chunk lives, not just the code itself.

### Deterministic IDs

Each chunk gets a stable, deterministic ID:

```
sha256("myrepo:main:auth/middleware.py:42")[:32]
```

Format: `sha256(repo:branch:file:line)[:32]`

This enables true upserts. When a function changes, the chunk at that location is **replaced**, not duplicated. When a function is deleted, its chunk is removed. No orphans, no duplicates, no garbage collection needed.

## Incremental Indexing

DevAI tracks the last indexed commit SHA per repository. On subsequent runs:

```
  Last indexed: a1b2c3d
  Current HEAD:  e4f5g6h
       │
       ▼
  git diff a1b2c3d..e4f5g6h --name-only
       │
       ▼
  Only reparse + re-embed changed files
       │
       ▼
  Upsert chunks (deterministic IDs handle updates)
```

In practice, indexing a 200-file repo takes ~30 seconds the first time and <2 seconds on incremental updates.

A full reindex is forced only when the embedding model changes (because all vectors must be recomputed for consistency).

## Branch-Aware Search

DevAI doesn't create separate indexes per branch. Instead, it uses an **overlay** strategy:

```
  main (fully indexed)
    │
    ├── feature/auth (overlay: 3 changed files)
    │
    └── feature/payments (overlay: 7 changed files)
```

When you search on `feature/auth`:
1. Search results from `feature/auth` overlay take priority
2. Results from `main` fill the rest
3. Deleted files in the branch are filtered out (tombstones)

This means branch switches are instant — no reindexing required. Only changed files in the branch are indexed as an overlay.

## Embedding Providers

| Provider | Model | Dimensions | Speed | Cost |
|---|---|---|---|---|
| **Local** (default) | `all-MiniLM-L6-v2` | 384 | ~500 chunks/sec | Free |
| OpenAI | `text-embedding-3-small` | 384* | ~2000 chunks/sec | $0.02/1M tokens |
| Voyage | `voyage-code-2` | 1024 | ~1500 chunks/sec | $0.12/1M tokens |
| Custom | Any sentence-transformers model | Varies | Varies | Varies |

*Dimensionality is configurable; DevAI truncates to match the index.

The local provider is the default and recommended for most use cases. It runs entirely on CPU, requires no API keys, and is fast enough for repos up to ~1M lines.

## When It Is Used

- **MCP `search` tool**: Called by AI agents (Claude Code, Cursor) to find relevant code
- **`devai search` CLI**: Direct command-line search
- **Context builder**: Internally uses search to assemble context for queries
- **Indexing**: Automatically on `devai index` or triggered by MCP tools

## Example: Searching for "authentication middleware"

Here's what happens internally when you (or an AI agent) search for "authentication middleware":

```
1. EMBED QUERY
   "authentication middleware"
      → sentence-transformers encode
      → [0.12, -0.34, 0.56, ...] (384-dim vector)

2. VECTOR SEARCH (LanceDB)
   Find top-K chunks nearest to query vector
   Filter: repo="myapp", branch="main" (+ overlays)

   Results (ranked by cosine similarity):
   ┌──────┬──────────────────────────────────────────┬───────┐
   │ Rank │ Chunk                                    │ Score │
   ├──────┼──────────────────────────────────────────┼───────┤
   │  1   │ auth/middleware.py > AuthMiddleware       │ 0.92  │
   │  2   │ auth/decorators.py > require_auth         │ 0.87  │
   │  3   │ auth/jwt.py > verify_token                │ 0.84  │
   │  4   │ tests/test_auth.py > TestAuthMiddleware   │ 0.79  │
   │  5   │ config/security.py > AUTH_CONFIG           │ 0.71  │
   └──────┴──────────────────────────────────────────┴───────┘

3. RETURN
   Each result includes:
   - file path + line range
   - context header (breadcrumb)
   - code content
   - similarity score
   - symbol type + language
```

The entire operation takes 10-50ms for a typical codebase. No file I/O at query time — everything is pre-indexed vectors.

## Mental Model

Think of DevAI search as **Google for your codebase**. Google doesn't match your query word-for-word against web pages — it understands what you're looking for and finds pages that are semantically relevant. DevAI does the same thing, but for code: it understands that "retry logic" and `exponentialBackoff()` are about the same concept, even though they share zero keywords.

The indexing pipeline is like Google's web crawler: it visits every file, understands its structure (via tree-sitter AST), breaks it into meaningful chunks, and stores vector representations. At query time, your natural language question is converted to the same vector space and matched against the index. Fast, accurate, and incrementally maintained.
