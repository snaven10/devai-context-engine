# Onboarding to an Unfamiliar Codebase

> 🇪🇸 [Leer en español](../es/05-ejemplos/incorporacion.md)

A step-by-step walkthrough of a developer using DevAI to go from zero knowledge of a codebase to productive contributor — in a single session instead of a week of reading code.

---

## Scenario

You just joined a team that maintains a backend service with 400+ files across 30 packages. The README is outdated. There are no architecture docs. The last person who understood the full system left six months ago.

You have DevAI indexed and ready. Here's how to build a mental model fast.

---

## Step 0 — Index the Repository

Before anything else, the codebase needs to be indexed.

```bash
devai init
devai index
```

**What DevAI does internally:**
- Parses every file using tree-sitter to build the code graph (symbols, references, imports, call chains).
- Generates vector embeddings for semantic search.
- Detects languages, frameworks, and project structure.

You can check progress:

```json
{
  "tool": "index_status",
  "arguments": {}
}
```

```json
{
  "status": "complete",
  "repo": "backend-service",
  "files_indexed": 437,
  "symbols_extracted": 3842,
  "languages": ["python", "sql"],
  "duration_seconds": 12
}
```

The codebase is ready. Time to explore.

---

## Step 1 — Understand the Architecture

Start with the big picture. Don't read files. Ask for architecture.

**Tool call:**
```json
{
  "tool": "build_context",
  "arguments": {
    "query": "main application architecture and entry points",
    "max_tokens": 8000
  }
}
```

**What DevAI does internally:**
Combines semantic search, code graph traversal, and dependency analysis to assemble a coherent picture of the system's architecture. Prioritizes entry points, top-level modules, and configuration files.

**Response (abbreviated):**
```json
{
  "context": {
    "primary_files": [
      {
        "file": "app/main.py",
        "relevant_code": "app = FastAPI(title='OrderService')\napp.include_router(orders_router, prefix='/api/v1/orders')\napp.include_router(inventory_router, prefix='/api/v1/inventory')\napp.include_router(webhooks_router, prefix='/webhooks')\napp.add_middleware(AuthMiddleware)\napp.add_middleware(RateLimitMiddleware)"
      },
      {
        "file": "app/config.py",
        "relevant_code": "class Settings(BaseSettings):\n    database_url: str\n    redis_url: str\n    stripe_api_key: str\n    sentry_dsn: Optional[str]\n    worker_concurrency: int = 4\n    class Config:\n        env_file = '.env'"
      },
      {
        "file": "app/workers/celery_app.py",
        "relevant_code": "celery = Celery('orders', broker=settings.redis_url)\ncelery.autodiscover_tasks(['app.workers'])"
      }
    ],
    "architecture_summary": "FastAPI application with 3 route groups (orders, inventory, webhooks), Celery workers for async processing, PostgreSQL via SQLAlchemy, Redis for caching and task queue."
  }
}
```

**What you learn:**
- FastAPI app with three main route groups.
- Uses Celery workers backed by Redis for async jobs.
- PostgreSQL is the primary database.
- Auth and rate limiting handled via middleware.
- Configuration via environment variables with pydantic `BaseSettings`.

One tool call. You already have the 30,000-foot view.

---

## Step 2 — Find Key Infrastructure Components

Now drill into the database layer — how does this service talk to Postgres?

**Tool call:**
```json
{
  "tool": "search",
  "arguments": {
    "query": "database connection and configuration",
    "limit": 10
  }
}
```

**Response:**
```json
{
  "results": [
    {
      "file": "app/db/pool.py",
      "line": 8,
      "symbol": "DatabasePool",
      "snippet": "class DatabasePool:\n    \"\"\"Singleton connection pool wrapping SQLAlchemy async engine.\"\"\"",
      "score": 0.96
    },
    {
      "file": "app/db/session.py",
      "line": 15,
      "symbol": "get_db_session",
      "snippet": "async def get_db_session() -> AsyncGenerator[AsyncSession, None]:",
      "score": 0.89
    },
    {
      "file": "app/db/migrations/env.py",
      "line": 1,
      "symbol": null,
      "snippet": "# Alembic migration environment",
      "score": 0.72
    }
  ]
}
```

**What you learn:**
- `DatabasePool` is the central abstraction — a singleton that wraps SQLAlchemy's async engine.
- Sessions are provided via `get_db_session()` — likely a FastAPI dependency.
- Alembic handles migrations.

---

## Step 3 — Understand How the Database is Used

You found `DatabasePool`. Now see how it's used across the codebase.

**Tool call:**
```json
{
  "tool": "get_references",
  "arguments": {
    "symbol": "DatabasePool"
  }
}
```

**Response:**
```json
{
  "symbol": "DatabasePool",
  "references": [
    {
      "file": "app/db/pool.py",
      "line": 8,
      "context": "class DatabasePool:",
      "kind": "definition"
    },
    {
      "file": "app/db/session.py",
      "line": 18,
      "context": "pool = DatabasePool.get_instance()",
      "kind": "call"
    },
    {
      "file": "app/main.py",
      "line": 12,
      "context": "await DatabasePool.initialize(settings.database_url)",
      "kind": "call"
    },
    {
      "file": "app/main.py",
      "line": 45,
      "context": "await DatabasePool.close()",
      "kind": "call"
    },
    {
      "file": "app/workers/celery_app.py",
      "line": 9,
      "context": "DatabasePool.initialize_sync(settings.database_url)",
      "kind": "call"
    },
    {
      "file": "tests/conftest.py",
      "line": 22,
      "context": "await DatabasePool.initialize(test_db_url)",
      "kind": "call"
    }
  ]
}
```

**What you learn:**
- `DatabasePool` is initialized at app startup (`main.py:12`) and closed at shutdown (`main.py:45`).
- There's a separate `initialize_sync` for Celery workers — they can't use async init.
- Tests use their own DB URL via `conftest.py`.
- Sessions are acquired through `get_db_session()` which calls `DatabasePool.get_instance()`.

You now understand the full DB lifecycle without reading a single file manually.

---

## Step 4 — Read Key Implementation Details

Zoom into the pool implementation to understand connection management.

**Tool call:**
```json
{
  "tool": "read_symbol",
  "arguments": {
    "name": "DatabasePool.get_connection"
  }
}
```

**Response:**
```json
{
  "symbol": "DatabasePool.get_connection",
  "kind": "method",
  "file": "app/db/pool.py",
  "start_line": 34,
  "end_line": 58,
  "code": "async def get_connection(self) -> AsyncConnection:\n    \"\"\"Acquire a connection from the pool.\n\n    Uses a semaphore to limit concurrent connections to pool_size.\n    Connections are returned to the pool automatically via context manager.\n    \"\"\"\n    if not self._engine:\n        raise RuntimeError('DatabasePool not initialized. Call initialize() first.')\n\n    async with self._semaphore:\n        async with self._engine.connect() as conn:\n            yield conn"
}
```

**What you learn:**
- Connections are managed via an async context manager.
- A semaphore limits concurrency — no connection starvation.
- There's a guard against using the pool before initialization.

---

## Step 5 — Check for Existing Team Knowledge

Before forming your own opinions about the architecture, check if the team has documented decisions in memory.

**Tool call:**
```json
{
  "tool": "recall",
  "arguments": {
    "query": "architecture decisions database",
    "project": "backend-service",
    "limit": 5
  }
}
```

**Response:**
```json
{
  "memories": [
    {
      "id": "mem_7b2e9f4a",
      "title": "Chose SQLAlchemy async over raw asyncpg",
      "type": "decision",
      "content": "What: Chose SQLAlchemy 2.0 async engine over raw asyncpg for database access.\nWhy: Team consensus that ORM reduces boilerplate for CRUD-heavy service. Raw asyncpg was faster in benchmarks (15% on read-heavy queries) but the productivity tradeoff wasn't worth it for our scale (~500 req/s).\nWhere: app/db/pool.py, app/db/session.py\nLearned: SQLAlchemy 2.0 async support requires greenlet. Added to dependencies explicitly because it's not auto-installed on all platforms.",
      "topic_key": "architecture/database-choice",
      "created_at": "2025-09-14T10:32:00Z"
    },
    {
      "id": "mem_c3d1a8e5",
      "title": "Fixed connection leak in worker tasks",
      "type": "bugfix",
      "content": "What: Celery tasks were leaking database connections because they used sync init but async session cleanup.\nWhy: Workers would exhaust the pool after ~200 tasks.\nWhere: app/workers/celery_app.py\nLearned: Workers need initialize_sync() and must explicitly call connection.close() — can't rely on async context manager in sync Celery tasks.",
      "created_at": "2025-11-02T16:45:00Z"
    }
  ]
}
```

**What you learn:**
- The team consciously chose SQLAlchemy over raw asyncpg — and documented the tradeoff.
- There was a connection leak bug in Celery workers that led to the separate `initialize_sync` method. Now you know WHY that code exists, not just WHAT it does.
- `greenlet` is a required dependency — a gotcha you'd otherwise hit during local setup.

This is institutional memory. Without DevAI, this knowledge would have left with the previous engineer.

---

## Step 6 — Save Your Understanding

You've built a solid mental model. Persist it so it compounds for future sessions.

**Tool call:**
```json
{
  "tool": "remember",
  "arguments": {
    "content": "Project architecture summary for backend-service:\n\n- FastAPI app with three route groups: orders, inventory, webhooks\n- PostgreSQL via SQLAlchemy 2.0 async engine, wrapped in DatabasePool singleton\n- DatabasePool uses semaphore-based connection limiting\n- Celery workers for async processing, backed by Redis\n- Workers use synchronous DB init (initialize_sync) due to past connection leak bug\n- Alembic for migrations\n- Auth handled via AuthMiddleware, rate limiting via RateLimitMiddleware\n- Config via pydantic BaseSettings with .env file\n- Key files: app/main.py (entry), app/db/pool.py (DB), app/config.py (settings), app/workers/celery_app.py (async jobs)",
    "type": "architecture",
    "topic_key": "architecture/backend-service-overview",
    "tags": ["onboarding", "architecture", "database", "celery"]
  }
}
```

**Response:**
```json
{
  "status": "saved",
  "id": "mem_e4f2b1c9",
  "topic_key": "architecture/backend-service-overview"
}
```

Next session, any agent (or any team member) can call `recall(query="backend-service architecture")` and get this summary instantly.

---

## The Onboarding Progression

```
  TIME          TRADITIONAL                         WITH DEVAI
  ──────        ────────────                        ──────────

  0-5 min       Clone repo                          Clone repo
                Read README (outdated)              devai init && devai index

  5-30 min      Grep around for "main"              build_context → full architecture
                Open 20 files randomly              search → key components found
                Ask Slack "where does X live?"       get_references → dependency map

  30-60 min     Still reading config files          read_symbol → implementation details
                Can't find DB connection code       recall → team's past decisions
                No idea why workers are different   remember → save mental model

  Day 2-3       Starting to understand structure    ── Already productive ──
                Found a wiki page from 2023

  Day 4-5       Can make small changes
                Still hitting gotchas

  Week 2        Somewhat productive
```

---

## Onboarding Checklist with DevAI

Use this sequence for any new codebase:

```
  ┌──────────────────────────────────────────────────────────┐
  │                   ONBOARDING FLOW                        │
  │                                                          │
  │  1. INDEX                                                │
  │     devai init && devai index                            │
  │         │                                                │
  │         ▼                                                │
  │  2. ARCHITECTURE                                         │
  │     build_context("main architecture and entry points")  │
  │         │                                                │
  │         ▼                                                │
  │  3. KEY COMPONENTS                                       │
  │     search("database connection")                        │
  │     search("authentication middleware")                  │
  │     search("API route definitions")                      │
  │         │                                                │
  │         ▼                                                │
  │  4. RELATIONSHIPS                                        │
  │     get_references("CoreClass") for each key component   │
  │         │                                                │
  │         ▼                                                │
  │  5. IMPLEMENTATION                                       │
  │     read_symbol("ClassName.method") for critical paths   │
  │         │                                                │
  │         ▼                                                │
  │  6. TEAM KNOWLEDGE                                       │
  │     recall("architecture decisions")                     │
  │     recall("known bugs gotchas")                         │
  │         │                                                │
  │         ▼                                                │
  │  7. PERSIST                                              │
  │     remember(your mental model, type="architecture")     │
  │                                                          │
  └──────────────────────────────────────────────────────────┘
```

---

## What DevAI Provided

| Capability | Tool Used | Value |
|---|---|---|
| **Architecture overview** | `build_context` | Assembled entry points, config, and infrastructure into a single coherent view |
| **Component discovery** | `search` | Found `DatabasePool` and related components via semantic query, not filename guessing |
| **Dependency mapping** | `get_references` | Showed exactly where and how `DatabasePool` is used across the entire codebase |
| **Implementation detail** | `read_symbol` | Retrieved full method code without manually navigating to files |
| **Institutional memory** | `recall` | Surfaced past team decisions and bug fixes — knowledge that would otherwise be lost |
| **Knowledge persistence** | `remember` | Saved the developer's mental model for future sessions and team members |

**Total tool calls: 7.** From zero codebase knowledge to a documented architectural understanding in a single session. What traditionally takes a week of reading code and asking coworkers is compressed into an hour of directed exploration.
