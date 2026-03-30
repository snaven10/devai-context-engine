# Memory

> 🇪🇸 [Leer en español](../es/03-conceptos-fundamentales/memoria.md)

## What It Is

DevAI memory is a persistent, structured knowledge store for AI agents. It captures decisions, discoveries, patterns, and bugs that survive across sessions — giving agents long-term memory that chat history was never designed to provide.

## Why It Exists

AI agents have an amnesia problem. Every session starts from zero. The architecture decision you debated for 30 minutes? Gone. The root cause of that production bug? Forgotten. The naming convention the team agreed on? Lost.

Naive approaches don't solve this:

| Approach | Problem |
|---|---|
| **Chat history** | Ephemeral. Lost on session close. Grows unboundedly. Not searchable by concept. |
| **Code comments** | Static. Can't capture decisions, tradeoffs, or context that led to the code. Pollutes the codebase. |
| **External docs** | Disconnected from code. Stale within weeks. Agents can't query them semantically. |
| **Vector-only stores** | No structure. Can't distinguish a bug fix from an architecture decision. No deduplication. |

DevAI memory is structured (typed, scoped, tagged), deduplicated (no redundant entries from repeated saves), and searchable (hybrid semantic + metadata filtering). It's designed specifically for the AI agent use case: frequent writes from automated workflows, semantic recall by natural language queries, and topic-based upserts that keep knowledge current instead of accumulating duplicates.

## How It Works Internally

### Storage

Memories are stored in SQLite with the following fields:

| Field | Type | Description |
|---|---|---|
| `title` | string | Short, searchable summary (e.g., "Fixed N+1 query in UserList") |
| `content` | text | Full structured content (what, why, where, learned) |
| `type` | enum | `insight`, `decision`, `note`, `bug`, `architecture`, `pattern`, `discovery` |
| `scope` | enum | `shared` (team-visible) or `local` (personal) |
| `project` | string | Project identifier |
| `topic_key` | string | Stable key for upserts (e.g., `architecture/auth-model`) |
| `tags` | list | Searchable tags |
| `author` | string | Who created it |
| `files` | list | Related file paths |
| `revision_count` | int | How many times this memory has been updated |
| `duplicate_count` | int | How many duplicate saves were deduplicated |

### Memory Types

Each type signals **intent** and enables filtered recall:

| Type | When to Use | Example |
|---|---|---|
| `decision` | Architecture or technology choice with tradeoffs | "Chose Zustand over Redux for state management" |
| `architecture` | Structural design of a system or component | "Payment module uses hexagonal architecture" |
| `bug` / `bugfix` | Root cause and fix for a resolved bug | "Fixed race condition in WebSocket reconnect" |
| `discovery` | Non-obvious finding about the codebase or tools | "LanceDB doesn't support concurrent writes from multiple processes" |
| `pattern` | Established convention or coding pattern | "All API handlers follow the Result monad pattern" |
| `insight` | Observation or learning that doesn't fit other types | "Tree-sitter Go grammar doesn't parse generics correctly" |
| `note` | General-purpose memory | Session summaries, meeting notes, TODOs |

### Deduplication

Agents save memories aggressively — after every bug fix, every decision, every discovery. Without deduplication, the store would fill with near-identical entries.

DevAI deduplicates at write time:

```
  New memory arrives
       │
       ▼
  Normalize content
  (lowercase + collapse whitespace)
       │
       ▼
  SHA256 hash of normalized content
       │
       ▼
  Check: same hash within 15-minute window?
       │
       ├── YES → Increment duplicate_count, skip insert
       │
       └── NO → Check: same topic_key + project + scope?
                    │
                    ├── YES → Upsert (update existing, increment revision_count)
                    │
                    └── NO → Insert new memory
```

The 15-minute window handles the common case: an agent calling `remember` multiple times in the same session with effectively the same content. The topic key upsert handles the evolution case: the same concept being refined over multiple sessions.

### Topic Key Upserts

Topic keys are the mechanism for **evolving knowledge**. Instead of creating a new memory every time you learn more about a topic, the topic key ensures the existing memory is updated:

```
Session 1:
  remember(
    title: "Auth architecture",
    topic_key: "architecture/auth",
    content: "Using JWT with refresh tokens. HS256 signing."
  )
  → Creates new memory (revision 1)

Session 2:
  remember(
    title: "Auth architecture",
    topic_key: "architecture/auth",
    content: "Using JWT with refresh tokens. Switched to RS256 for key rotation support."
  )
  → Updates existing memory (revision 2), preserves history
```

Rules:
- Same `topic_key` + `project` + `scope` = update existing
- Different `topic_key` = new memory (never overwrites unrelated topics)
- No `topic_key` = always creates new memory

### Hybrid Search (Recall)

When you query memories, DevAI uses hybrid search combining semantic similarity and metadata filtering:

```
  recall(query: "authentication architecture", project: "myapp")
       │
       ├──► Semantic search
       │    Embed query → search memory vectors in LanceDB
       │    Returns: memories ranked by cosine similarity
       │
       ├──► Metadata filtering
       │    Filter by: project, type, scope, tags
       │
       └──► Merge + rank
            Combined relevance score
            Return top-K results with FULL content
```

Memory vectors are stored in the same LanceDB instance as code vectors, but with distinct metadata fields (`memory_type`, `memory_scope`, `memory_tags`) that enable precise filtering.

The key design choice: **recall returns full content, not truncated summaries**. Memories are structured to be concise at write time, so they can be returned in full at read time. No two-step "search then fetch" — one call gives you everything.

## When It Is Used

- **MCP `remember` tool**: AI agents save structured memories
- **MCP `recall` tool**: AI agents query memories by natural language + filters
- **MCP `memory_context` tool**: Get memory-enriched context for a topic
- **MCP `memory_stats` tool**: Inspect memory store health and size
- **Context builder**: Automatically includes relevant memories when assembling context

## Example: Architecture Decision Lifecycle

### Saving

An AI agent (or human via CLI) completes an architecture discussion and saves it:

```
remember(
  title: "Chose event sourcing for order management",
  type: "decision",
  scope: "shared",
  project: "ecommerce",
  topic_key: "architecture/order-management",
  content: """
    What: Adopted event sourcing pattern for the order management domain.
    Why: Need full audit trail for compliance. CQRS read models give us
         flexible querying without denormalization trade-offs.
    Where: services/orders/, events/order_events.py, projections/
    Learned: Event store requires careful schema versioning. Using
             upcasters for backward compatibility.
  """,
  tags: ["event-sourcing", "cqrs", "orders"],
  files: ["services/orders/aggregate.py", "events/order_events.py"]
)
```

### Recalling (weeks later, different session)

A new agent session needs to modify the order system:

```
recall(query: "order management architecture", project: "ecommerce")

Result:
  ┌─────────────────────────────────────────────────────────────┐
  │ Title: Chose event sourcing for order management            │
  │ Type: decision | Scope: shared | Revisions: 3              │
  │                                                             │
  │ What: Adopted event sourcing pattern for the order          │
  │       management domain.                                    │
  │ Why: Need full audit trail for compliance...                │
  │ Where: services/orders/, events/order_events.py...          │
  │ Learned: Event store requires careful schema versioning...  │
  │                                                             │
  │ Tags: event-sourcing, cqrs, orders                         │
  │ Files: services/orders/aggregate.py, events/order_events.py │
  └─────────────────────────────────────────────────────────────┘
```

The agent now knows: this is an event-sourced system, uses CQRS, has a schema versioning concern, and the relevant files are in `services/orders/`. It can proceed with the modification without re-discovering any of this.

### Updating (the topic evolves)

Later, a migration from file-based to database-backed event store is completed:

```
remember(
  title: "Migrated order event store to PostgreSQL",
  type: "decision",
  topic_key: "architecture/order-management",
  project: "ecommerce",
  content: """
    What: Migrated event store from file-based to PostgreSQL with
          pg_partman for time-based partitioning.
    Why: File store hit performance wall at ~1M events.
    Where: services/orders/event_store.py, migrations/
    Learned: Partitioning by month keeps query performance under 50ms
             up to ~100M events.
  """
)
→ Updates existing memory (revision 4), preserves continuity
```

## Mental Model

Think of DevAI memory as a **team's shared notebook** — but one that's searchable by meaning, automatically deduplicates, and is always available to every AI agent working on the project.

Chat history is like a whiteboard: useful during the meeting, erased afterward. Code comments are like sticky notes: they annotate one spot but can't capture the reasoning behind a system-wide decision. DevAI memory is the notebook where you write down "we chose X because Y, and watch out for Z" — and six months later, any agent (or human) can ask "why did we choose X?" and get the full answer.
