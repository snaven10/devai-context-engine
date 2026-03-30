# Agent Workflow

> Back to [README](../README.md)
> 🇪🇸 [Leer en español](es/04-flujo-de-trabajo-del-agente.md)

---

## Mental Model

DevAI is Jarvis to the AI agent's Tony Stark.

The agent has the intelligence. It can reason, plan, write code, debug. What it lacks is *situational awareness* — it cannot see the full battlefield. DevAI provides the heads-up display: structural code maps, semantic search, cross-reference navigation, and persistent memory across sessions.

Without DevAI, an agent navigating a large codebase is a surgeon operating blindfolded, reading the patient's chart one line at a time. With DevAI, the agent has the full chart, the X-rays, and notes from every previous surgery.

---

## How MCP Works

The [Model Context Protocol](https://modelcontextprotocol.io) (MCP) is a standard for connecting AI agents to external tools. DevAI uses it as its primary interface.

**Transport:** stdio. The AI agent's host process spawns `devai server mcp` as a subprocess. Communication happens over stdin/stdout using JSON-RPC 2.0. No HTTP, no ports, no authentication.

**Lifecycle:**

```
Agent Host                          DevAI MCP Server
    │                                      │
    │──── spawn devai server mcp ─────────▶│
    │                                      │── spawn Python ML service
    │◀──── initialize (capabilities) ──────│
    │                                      │
    │───── tools/list ────────────────────▶│
    │◀──── [14 tool definitions] ──────────│
    │                                      │
    │───── tools/call (search, ...) ──────▶│──── JSON-RPC ──▶ Python
    │◀──── result ─────────────────────────│◀─── result ─────┘
    │                                      │
    │      ... (session continues) ...     │
    │                                      │
    │───── shutdown ──────────────────────▶│
    │                                      │── terminate Python
```

**Registration:** When the agent calls `tools/list`, DevAI returns all 14 tool definitions with JSON Schema parameters. The agent's runtime validates parameters before each call. No tool discovery at runtime — everything is declared upfront.

---

## The Typical Agent Workflow

A well-configured agent follows a predictable pattern when working with DevAI:

```
1. ORIENT     recall prior context, search for relevant code
2. UNDERSTAND read symbols, trace references, build full context
3. ACT        write code, fix bugs, implement features
4. PERSIST    remember decisions, discoveries, conventions
```

### Phase 1: Orient

The agent receives a user task. Before writing a single line of code, it orients itself.

```
User: "Fix the race condition in the session store"

Agent thinks: I need to understand the session store first.

Agent calls: recall(query: "session store race condition")
  → Returns: Previous session found a data race in SessionStore.Get
             when concurrent requests hit the same session ID.
             Mutex was added to Set but not Get. File: internal/store/session.go

Agent calls: search(query: "session store concurrent access")
  → Returns: 3 results ranked by relevance
             1. internal/store/session.go — SessionStore struct, Get/Set methods
             2. internal/store/session_test.go — TestConcurrentAccess (currently failing)
             3. internal/middleware/auth.go — calls SessionStore.Get in request handler
```

The agent now knows *where* to look, *what* was tried before, and *what the codebase looks like* around the problem — all before reading a single file.

### Phase 2: Understand

With search results pointing to the right area, the agent dives deeper.

```
Agent calls: read_symbol(name: "SessionStore")
  → Returns: Full struct definition with all methods
             type SessionStore struct { mu sync.Mutex; data map[string]*Session }
             func (s *SessionStore) Get(id string) (*Session, error)
             func (s *SessionStore) Set(id string, sess *Session) error
             func (s *SessionStore) Delete(id string) error

Agent calls: get_references(symbol: "SessionStore.Get")
  → Returns: 4 call sites
             internal/middleware/auth.go:47      — auth middleware, called per request
             internal/handler/dashboard.go:23    — dashboard handler
             internal/handler/api.go:89          — API handler
             internal/store/session_test.go:34   — test file
```

Now the agent has structural understanding — not just text matches, but the actual symbol graph.

### Phase 3: Act

The agent writes the fix. This phase is the agent's core competency — DevAI steps back.

### Phase 4: Persist

After completing the fix, the agent persists what it learned.

```
Agent calls: remember(
  title: "Fixed race condition in SessionStore.Get",
  content: "Added RLock to SessionStore.Get. The Set method had a
            Mutex but Get was unprotected, causing data races under
            concurrent request load. All callers (auth middleware,
            dashboard, API handlers) are now safe.",
  type: "bugfix",
  project: "devai",
  topic_key: "bugfix/session-store-race"
)
```

The next session — or the next agent — can `recall` this and avoid re-discovering the same issue.

---

## Tool Selection Patterns

Use this decision tree to pick the right tool for each situation:

| Agent needs... | Tool | Why this one |
|----------------|------|-------------|
| Find code related to a concept | `search` | Semantic vector search. Finds code by meaning, not just keywords. Returns ranked chunks with file paths and scores. |
| Comprehensive context for a topic | `build_context` | Assembles search results + symbol info + memory into a single token-budgeted block. Use when the agent needs a full picture, not just a pointer. |
| Full definition of a specific symbol | `read_symbol` | Returns the complete source of a function, class, struct, or type. Faster and more precise than reading an entire file. |
| All places a symbol is used | `get_references` | Returns every call site, import, and usage. Essential for understanding impact of changes. |
| Read a specific file | `read_file` | When you know the exact path. Supports optional line ranges to avoid loading entire files. |
| Save a decision or discovery | `remember` | Persists to SQLite with dedup and topic-key upsert. Survives across sessions and context resets. |
| Check if something was discussed before | `recall` | Searches memory by query. Returns full content, not truncated summaries. |
| Review past actions in this session | `get_session_history` | Returns the tool call log for the current session. Useful for agents reviewing their own work. |
| Index or reindex files | `index` | Triggers incremental indexing. Only reprocesses changed files via git diff. |

### Anti-Patterns

- **Do not use `search` when you know the exact symbol name.** Use `read_symbol` instead. Search is for discovery; `read_symbol` is for retrieval.
- **Do not use `read_file` to explore a codebase.** Use `search` or `build_context` first to find what matters, then `read_file` for the specific section.
- **Do not skip `recall` at session start.** Five seconds of memory lookup saves five minutes of re-exploration.
- **Do not forget to `remember`.** If the agent made a non-obvious decision, fixed a subtle bug, or discovered a gotcha — persist it. Future sessions depend on it.

---

## End-to-End Example: Debugging a Bug in a Large Codebase

**Scenario:** User reports "API returns 500 on /api/users when the database is under load." The codebase has 200+ files across 15 packages.

### Step 1: Check memory for prior context

```json
{
  "tool": "recall",
  "params": {
    "query": "API 500 error users database load",
    "project": "myapp"
  }
}
```

**Result:** No prior memory found. This is a new issue.

### Step 2: Semantic search for the error surface

```json
{
  "tool": "search",
  "params": {
    "query": "API users endpoint handler error handling",
    "top_k": 5
  }
}
```

**Result:**

```
1. internal/handler/users.go:45     score: 0.87  "func (h *UserHandler) List(...)"
2. internal/handler/users.go:82     score: 0.81  "func (h *UserHandler) Create(...)"
3. internal/service/user_service.go  score: 0.76  "func (s *UserService) GetAll(...)"
4. internal/middleware/recovery.go   score: 0.71  "panic recovery middleware"
5. internal/db/pool.go              score: 0.68  "connection pool configuration"
```

### Step 3: Read the handler symbol

```json
{
  "tool": "read_symbol",
  "params": {
    "name": "UserHandler.List"
  }
}
```

**Result:**

```go
func (h *UserHandler) List(w http.ResponseWriter, r *http.Request) {
    users, err := h.service.GetAll(r.Context())
    if err != nil {
        http.Error(w, "internal error", 500)  // no error wrapping, no logging
        return
    }
    json.NewEncoder(w).Encode(users)
}
```

### Step 4: Trace the call chain

```json
{
  "tool": "get_references",
  "params": {
    "symbol": "UserService.GetAll"
  }
}
```

**Result:**

```
internal/handler/users.go:46        — UserHandler.List
internal/handler/admin.go:112       — AdminHandler.ExportUsers
internal/job/daily_report.go:34     — DailyReportJob.Run
```

### Step 5: Read the service implementation

```json
{
  "tool": "read_symbol",
  "params": {
    "name": "UserService.GetAll"
  }
}
```

**Result:**

```go
func (s *UserService) GetAll(ctx context.Context) ([]User, error) {
    return s.repo.FindAll(ctx)  // no timeout, no retry, no connection check
}
```

### Step 6: Build full context for the database layer

```json
{
  "tool": "build_context",
  "params": {
    "query": "database connection pool timeout retry",
    "max_tokens": 6000
  }
}
```

**Result:** Assembled context including the connection pool config (max 10 connections, no timeout), the repository implementation (raw SQL with no context deadline), and related memory entries.

### Step 7: Agent diagnoses and fixes

The agent now has the full picture:

```
Root cause: UserService.GetAll passes context without a deadline to
the repository. Under database load, queries hang indefinitely. The
connection pool exhausts its 10 connections, and subsequent requests
get a "connection pool exhausted" error that surfaces as a 500.

Fix: Add context.WithTimeout in the service layer, add retry with
backoff in the repository, increase pool size, add proper error
wrapping in the handler.
```

### Step 8: Persist the finding

```json
{
  "tool": "remember",
  "params": {
    "title": "Fixed 500 errors under DB load in /api/users",
    "content": "Root cause: no context deadline on DB queries. Under load, connection pool (max 10) exhausted. Fix: added 5s timeout in UserService, retry with backoff in repo, increased pool to 25, added structured error logging in handler.",
    "type": "bugfix",
    "project": "myapp",
    "topic_key": "bugfix/users-500-db-load"
  }
}
```

### Data Flow Summary

```
recall ──▶ (no prior context)
              │
search ──▶ handler/users.go, service/user_service.go, db/pool.go
              │
read_symbol ──▶ UserHandler.List source
              │
get_references ──▶ 3 call sites for UserService.GetAll
              │
read_symbol ──▶ UserService.GetAll source (no timeout visible)
              │
build_context ──▶ full DB layer context (pool config, repo impl)
              │
    ┌─────────┘
    ▼
Agent reasoning: no deadline + small pool = exhaustion under load
    │
    ▼
Agent writes fix ──▶ 4 files modified
    │
    ▼
remember ──▶ persisted for future sessions
```

---

## Session History

DevAI tracks every tool call within a session. Agents can review their own actions using `get_session_history`.

**Why this matters:** When an agent's context is compacted (the LLM's conversation is summarized to save tokens), it loses the detailed record of what it did. Session history provides a ground-truth log that survives compaction.

**What is recorded:**

- Tool name and parameters for each call
- Timestamp
- Result summary
- Session ID (stable across the session, changes between sessions)

**Typical usage:**

```json
{
  "tool": "get_session_history",
  "params": {
    "session_id": "current",
    "limit": 20
  }
}
```

This returns the last 20 tool calls in the current session. The agent can use this to:

- Verify it has not already searched for something (avoid duplicate work)
- Review what it found earlier before context was compacted
- Build a summary of actions taken for the user

---

## Configuring Agent Access

### Automatic Setup

```bash
devai server configure claude    # Claude Code
devai server configure cursor    # Cursor
devai server configure --all     # All detected clients
```

This writes the MCP server entry to the client's configuration file. The agent can call DevAI tools immediately — no manual JSON editing required.

### What Gets Configured

- MCP server command: `devai server mcp`
- Working directory: the current repository root
- Environment: `DEVAI_STATE_DIR` pointing to `.devai/state/`
- Storage mode: auto-detected from `.devai/config.yaml`

### Verifying the Connection

After configuration, ask the agent to run a simple tool call:

```
"Search for the main function in this codebase"
```

If DevAI is connected, the agent will call `search(query: "main function entry point")` and return results. If not, it will fall back to file reads — a clear signal that the MCP connection is not active.

---

> **DevAI is in alpha.** Tool parameters and response schemas may change between versions. See the [MCP Tools Reference](mcp-tools.md) for current schemas.
