# Planning a Large Refactor

> 🇪🇸 [Leer en español](../es/05-ejemplos/refactorizacion.md)

A step-by-step walkthrough of using DevAI to plan and scope a major refactoring effort — extracting authentication logic into a standalone module — with full impact analysis before writing a single line of code.

---

## Scenario

The authentication and authorization logic in your application is scattered across 15+ files. Every route handler does its own auth checks. Session management is duplicated in three places. Token validation lives inside the API layer instead of a dedicated module.

You need to extract all of this into a clean `auth` module. But touching auth code in a production system without understanding every call site is how you cause outages.

DevAI lets you map the full blast radius before you start.

---

## Step 1 — Understand the Current State

Start by getting a comprehensive view of what "authentication" looks like in this codebase today.

**Tool call:**
```json
{
  "tool": "build_context",
  "arguments": {
    "query": "user authentication and authorization logic",
    "max_tokens": 8000,
    "include_deps": true
  }
}
```

**What DevAI does internally:**
Semantic search across the full index, then expands results by following imports, call chains, and type references to build a dependency-aware context package.

**Response (abbreviated):**
```json
{
  "context": {
    "primary_files": [
      {
        "file": "app/middleware/auth.py",
        "relevant_code": "class AuthMiddleware:\n    async def __call__(self, request, call_next):\n        token = request.headers.get('Authorization')\n        if not token:\n            raise HTTPException(401)\n        user = await self.validate_token(token)\n        request.state.user = user\n        return await call_next(request)\n\n    async def validate_token(self, token: str) -> User:\n        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])\n        return await self.user_repo.get(payload['sub'])"
      },
      {
        "file": "app/api/orders.py",
        "relevant_code": "# Inline auth check — duplicated pattern\ndef require_admin(request):\n    if request.state.user.role != 'admin':\n        raise HTTPException(403, 'Admin required')\n\n@router.post('/orders/{id}/refund')\nasync def refund_order(request: Request, id: str):\n    require_admin(request)\n    ..."
      },
      {
        "file": "app/api/inventory.py",
        "relevant_code": "# Another inline auth check — slightly different implementation\n@router.put('/inventory/{sku}')\nasync def update_inventory(request: Request, sku: str):\n    if request.state.user.role not in ('admin', 'warehouse'):\n        raise HTTPException(403)\n    ..."
      },
      {
        "file": "app/services/session_manager.py",
        "relevant_code": "class SessionManager:\n    async def create_session(self, user_id: str) -> str:\n        session_id = str(uuid4())\n        await self.redis.set(f'session:{session_id}', user_id, ex=3600)\n        return session_id\n\n    async def validate_session(self, session_id: str) -> Optional[str]:\n        return await self.redis.get(f'session:{session_id}')"
      },
      {
        "file": "app/utils/token_helpers.py",
        "relevant_code": "def create_access_token(user_id: str, role: str, expires_delta: timedelta = None) -> str:\n    payload = {'sub': user_id, 'role': role, 'exp': datetime.utcnow() + (expires_delta or timedelta(hours=1))}\n    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')\n\ndef create_refresh_token(user_id: str) -> str:\n    payload = {'sub': user_id, 'type': 'refresh', 'exp': datetime.utcnow() + timedelta(days=30)}\n    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')"
      }
    ],
    "dependencies": [
      "app/models/user.py",
      "app/db/repositories/user_repo.py",
      "app/config.py (SECRET_KEY)"
    ]
  }
}
```

**What the agent learns:**
The auth logic is spread across at least five locations:

| Location | Responsibility |
|---|---|
| `middleware/auth.py` | Token validation, user hydration |
| `api/orders.py` | Inline role checking (`require_admin`) |
| `api/inventory.py` | Different inline role checking (inconsistent) |
| `services/session_manager.py` | Session creation and validation via Redis |
| `utils/token_helpers.py` | JWT token creation |

There are inconsistent authorization patterns: some use a `require_admin` helper, others do inline role checks with different logic. This is the exact kind of sprawl that causes security bugs.

---

## Step 2 — Map All Call Sites for Core Auth Function

The most dangerous part of a refactor is missing a call site. Map them all.

**Tool call:**
```json
{
  "tool": "get_references",
  "arguments": {
    "symbol": "authenticate_user"
  }
}
```

**Response:**
```json
{
  "symbol": "authenticate_user",
  "total_references": 47,
  "references": [
    {"file": "app/middleware/auth.py", "line": 28, "kind": "definition"},
    {"file": "app/api/orders.py", "line": 14, "kind": "import"},
    {"file": "app/api/orders.py", "line": 31, "kind": "call"},
    {"file": "app/api/orders.py", "line": 58, "kind": "call"},
    {"file": "app/api/orders.py", "line": 92, "kind": "call"},
    {"file": "app/api/inventory.py", "line": 8, "kind": "import"},
    {"file": "app/api/inventory.py", "line": 22, "kind": "call"},
    {"file": "app/api/customers.py", "line": 11, "kind": "import"},
    {"file": "app/api/customers.py", "line": 35, "kind": "call"},
    {"file": "app/api/customers.py", "line": 67, "kind": "call"},
    {"file": "app/api/webhooks.py", "line": 19, "kind": "call"},
    {"file": "app/api/admin.py", "line": 12, "kind": "import"},
    {"file": "app/api/admin.py", "line": 24, "kind": "call"},
    {"file": "app/api/admin.py", "line": 41, "kind": "call"},
    {"file": "app/api/admin.py", "line": 63, "kind": "call"},
    {"file": "app/api/admin.py", "line": 88, "kind": "call"},
    {"file": "app/api/reports.py", "line": 9, "kind": "import"},
    {"file": "app/api/reports.py", "line": 27, "kind": "call"},
    {"file": "app/workers/email_worker.py", "line": 15, "kind": "call"},
    {"file": "app/workers/audit_worker.py", "line": 22, "kind": "call"},
    {"file": "tests/test_auth.py", "line": 8, "kind": "import"},
    {"file": "tests/test_orders.py", "line": 12, "kind": "import"},
    {"file": "tests/conftest.py", "line": 34, "kind": "call"}
  ]
}
```

*47 total references shown above as representative sample.*

**What the agent learns:**
- `authenticate_user` is called from **47 locations** across the codebase.
- Every API module imports and calls it directly.
- Workers also call it (for validating job payloads).
- Tests import it — test fixtures will need updating too.
- The function lives in `middleware/auth.py` — an odd location for something called by workers.

This is the blast radius. Every one of these 47 references needs to work after the refactor.

---

## Step 3 — Find Related Auth Code

The `authenticate_user` function is one piece. Find the rest: session management, token handling, authorization checks.

**Tool call:**
```json
{
  "tool": "search",
  "arguments": {
    "query": "session management token validation",
    "limit": 15
  }
}
```

**Response:**
```json
{
  "results": [
    {
      "file": "app/services/session_manager.py",
      "line": 1,
      "symbol": "SessionManager",
      "snippet": "class SessionManager:",
      "score": 0.95
    },
    {
      "file": "app/utils/token_helpers.py",
      "line": 5,
      "symbol": "create_access_token",
      "snippet": "def create_access_token(user_id: str, role: str, ...) -> str:",
      "score": 0.92
    },
    {
      "file": "app/utils/token_helpers.py",
      "line": 15,
      "symbol": "create_refresh_token",
      "snippet": "def create_refresh_token(user_id: str) -> str:",
      "score": 0.90
    },
    {
      "file": "app/middleware/auth.py",
      "line": 35,
      "symbol": "AuthMiddleware.validate_token",
      "snippet": "async def validate_token(self, token: str) -> User:",
      "score": 0.88
    },
    {
      "file": "app/api/auth_routes.py",
      "line": 12,
      "symbol": "login",
      "snippet": "async def login(credentials: LoginRequest) -> TokenResponse:",
      "score": 0.85
    },
    {
      "file": "app/api/auth_routes.py",
      "line": 38,
      "symbol": "refresh",
      "snippet": "async def refresh(refresh_token: str) -> TokenResponse:",
      "score": 0.82
    },
    {
      "file": "app/api/auth_routes.py",
      "line": 55,
      "symbol": "logout",
      "snippet": "async def logout(request: Request) -> None:",
      "score": 0.78
    }
  ]
}
```

**What the agent learns:** The full auth surface area includes:

| Component | Current Location | Responsibility |
|---|---|---|
| `SessionManager` | `services/session_manager.py` | Redis-backed session CRUD |
| `create_access_token` | `utils/token_helpers.py` | JWT access token generation |
| `create_refresh_token` | `utils/token_helpers.py` | JWT refresh token generation |
| `validate_token` | `middleware/auth.py` | JWT decode + user lookup |
| `login` | `api/auth_routes.py` | Login endpoint |
| `refresh` | `api/auth_routes.py` | Token refresh endpoint |
| `logout` | `api/auth_routes.py` | Session invalidation |

Auth logic is spread across four directories: `middleware/`, `utils/`, `services/`, and `api/`. No wonder there are inconsistencies.

---

## Step 4 — Check Impact on UserSession

The session model is likely referenced everywhere that auth is used. How big is the blast radius?

**Tool call:**
```json
{
  "tool": "get_references",
  "arguments": {
    "symbol": "UserSession"
  }
}
```

**Response:**
```json
{
  "symbol": "UserSession",
  "total_references": 23,
  "references": [
    {"file": "app/models/session.py", "line": 8, "kind": "definition"},
    {"file": "app/services/session_manager.py", "line": 4, "kind": "import"},
    {"file": "app/services/session_manager.py", "line": 18, "kind": "type_annotation"},
    {"file": "app/services/session_manager.py", "line": 32, "kind": "constructor"},
    {"file": "app/middleware/auth.py", "line": 6, "kind": "import"},
    {"file": "app/middleware/auth.py", "line": 42, "kind": "type_annotation"},
    {"file": "app/api/auth_routes.py", "line": 5, "kind": "import"},
    {"file": "app/api/auth_routes.py", "line": 48, "kind": "constructor"},
    {"file": "app/api/orders.py", "line": 6, "kind": "import"},
    {"file": "app/api/orders.py", "line": 33, "kind": "type_annotation"},
    {"file": "app/api/admin.py", "line": 7, "kind": "import"},
    {"file": "app/api/admin.py", "line": 26, "kind": "type_annotation"},
    {"file": "app/api/customers.py", "line": 5, "kind": "import"},
    {"file": "app/api/reports.py", "line": 4, "kind": "import"},
    {"file": "app/workers/audit_worker.py", "line": 8, "kind": "import"},
    {"file": "tests/test_auth.py", "line": 6, "kind": "import"},
    {"file": "tests/test_auth.py", "line": 15, "kind": "constructor"},
    {"file": "tests/test_orders.py", "line": 8, "kind": "import"},
    {"file": "tests/conftest.py", "line": 12, "kind": "import"},
    {"file": "tests/conftest.py", "line": 38, "kind": "constructor"}
  ]
}
```

**What the agent learns:**
`UserSession` is referenced in **23 files** — 6 in production code, 1 in workers, 3 in tests, plus the definition and session manager. All of these will need their import paths updated when `UserSession` moves into the new `auth` module.

---

## Step 5 — Review Past Architecture Decisions

Before designing the new module structure, check if the team has discussed this before.

**Tool call:**
```json
{
  "tool": "recall",
  "arguments": {
    "query": "authentication architecture",
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
      "id": "mem_2a4f8c1e",
      "title": "Chose JWT over opaque tokens for API auth",
      "type": "decision",
      "content": "What: Chose JWT (HS256) for API authentication over opaque tokens with server-side lookup.\nWhy: Stateless auth reduces Redis load. Trade-off: tokens can't be revoked instantly (must wait for expiry). Mitigated by short-lived access tokens (1 hour) + refresh token rotation.\nWhere: app/utils/token_helpers.py, app/middleware/auth.py\nLearned: HS256 is fine for single-service. If we go multi-service, switch to RS256 with a shared public key.",
      "topic_key": "architecture/auth-token-strategy",
      "created_at": "2025-06-20T14:22:00Z"
    },
    {
      "id": "mem_9d3b7e5f",
      "title": "Added session manager alongside JWT for admin dashboard",
      "type": "decision",
      "content": "What: Added Redis-backed SessionManager for the admin dashboard, separate from JWT auth used by API clients.\nWhy: Admin dashboard needs instant session revocation (security requirement). JWT's 1-hour expiry window is unacceptable for admin access. So we have a hybrid: JWT for API, sessions for dashboard.\nWhere: app/services/session_manager.py\nLearned: This creates two parallel auth paths. Should eventually unify under a single auth module.",
      "created_at": "2025-08-05T09:15:00Z"
    }
  ]
}
```

**What the agent learns:** Critical context that changes the refactor plan:

1. **JWT vs sessions is intentional**, not accidental duplication. API clients use JWT, admin dashboard uses Redis sessions. Both must be preserved.
2. The previous engineer already noted this "should eventually unify under a single auth module" — this refactor was anticipated.
3. If the service goes multi-service, HS256 needs to become RS256. The refactor should make this easy to change later.

Without this memory, the agent might have tried to eliminate the "duplicate" session system, breaking admin auth.

---

## Step 6 — Document the Refactor Plan

The agent now has enough information to create a comprehensive plan. Save it for the implementation phase.

**Tool call:**
```json
{
  "tool": "remember",
  "arguments": {
    "content": "Auth refactor plan: extract all auth logic into standalone app/auth/ module.\n\nCurrent state:\n- authenticate_user: 47 references across 15+ files\n- UserSession: 23 references across 13 files\n- Auth logic scattered across middleware/, utils/, services/, api/\n- Two auth paths: JWT (API clients) + Redis sessions (admin dashboard) — both intentional\n\nTarget structure:\n  app/auth/\n    __init__.py          — public API: authenticate_user, require_role, create_token\n    tokens.py            — JWT creation + validation (from utils/token_helpers.py + middleware/auth.py)\n    sessions.py          — SessionManager (from services/session_manager.py)\n    middleware.py         — AuthMiddleware (from middleware/auth.py)\n    models.py            — UserSession (from models/session.py)\n    permissions.py       — Role-based checks (consolidate inline checks from api/)\n    constants.py         — AUTH_HEADER, TOKEN_PREFIX, algorithm config\n\nMigration strategy:\n1. Create app/auth/ with new module structure\n2. Move code, keeping old imports as re-exports temporarily\n3. Update all 47 authenticate_user references to import from app.auth\n4. Update all 23 UserSession references\n5. Consolidate 3 different role-checking patterns into permissions.py\n6. Remove old files and temporary re-exports\n7. Update tests (6 test files affected)\n\nRisks:\n- Two auth paths (JWT + sessions) must both work post-refactor\n- Workers use authenticate_user in sync context — verify async compatibility\n- HS256 → RS256 migration should be easy after refactor (extract algorithm config)\n\nEstimate: 47 + 23 = 70 import updates across ~20 files. Medium risk, high value.",
    "type": "decision",
    "topic_key": "architecture/auth-refactor",
    "tags": ["refactor", "authentication", "architecture", "planning"],
    "files": [
      "app/middleware/auth.py",
      "app/utils/token_helpers.py",
      "app/services/session_manager.py",
      "app/models/session.py",
      "app/api/auth_routes.py"
    ]
  }
}
```

**Response:**
```json
{
  "status": "saved",
  "id": "mem_f1a9c3d7",
  "topic_key": "architecture/auth-refactor"
}
```

---

## Impact Analysis Summary

Before writing any code, the agent has a complete picture:

```
  CURRENT STATE                              TARGET STATE
  ─────────────                              ────────────

  middleware/                                 auth/
    auth.py ──────────────────────────┐        __init__.py    (public API)
      AuthMiddleware                  │        tokens.py      (JWT ops)
      validate_token                  ├──────► sessions.py    (Redis sessions)
      authenticate_user (47 refs)     │        middleware.py   (request auth)
                                      │        models.py      (UserSession)
  utils/                              │        permissions.py  (role checks)
    token_helpers.py ─────────────────┤        constants.py   (config)
      create_access_token             │
      create_refresh_token            │
                                      │
  services/                           │
    session_manager.py ───────────────┤
      SessionManager                  │
                                      │
  models/                             │
    session.py ───────────────────────┘
      UserSession (23 refs)

  api/  (inline role checks)                 api/  (uses auth.require_role)
    orders.py ─── require_admin ────────────► orders.py ─── from auth import require_role
    inventory.py ── if role in (...) ───────► inventory.py ── require_role('warehouse')
    admin.py ── if role != 'admin' ─────────► admin.py ── require_role('admin')
```

---

## Reference Dependency Graph

```
                    ┌─────────────────────┐
                    │   47 Call Sites      │
                    │   (API handlers,     │
                    │    workers, tests)   │
                    └─────────┬───────────┘
                              │ import
                              ▼
                    ┌─────────────────────┐
                    │  authenticate_user  │ ◄── Currently in middleware/auth.py
                    └─────────┬───────────┘     Move to auth/__init__.py
                              │
                    ┌─────────┴───────────┐
                    │                     │
                    ▼                     ▼
          ┌─────────────────┐   ┌─────────────────┐
          │  validate_token │   │ SessionManager   │
          │  (JWT path)     │   │ (session path)   │
          └────────┬────────┘   └────────┬────────┘
                   │                     │
                   ▼                     ▼
          ┌─────────────────┐   ┌─────────────────┐
          │ token_helpers   │   │ Redis            │
          │ (create/verify) │   │ (session store)  │
          └────────┬────────┘   └─────────────────┘
                   │
                   ▼
          ┌─────────────────┐
          │ UserSession     │ ◄── 23 references, all need import update
          │ (model)         │
          └─────────────────┘
```

---

## Refactor Execution Plan

Based on the analysis, the agent recommends this phased approach:

```
  Phase 1: Create (no breakage)         Phase 2: Migrate (controlled)
  ─────────────────────────────         ──────────────────────────────

  Create app/auth/ module               Update imports file by file:
  Copy code into new structure          - API handlers (6 files)
  Add re-exports in old locations       - Workers (2 files)
  Run tests → all pass                  - Tests (6 files)
                                        Run tests after each file

  Phase 3: Consolidate                  Phase 4: Cleanup
  ────────────────────                  ──────────────────

  Merge 3 role-check patterns           Remove old files:
  into auth/permissions.py              - middleware/auth.py
  Extract config to constants.py        - utils/token_helpers.py
  Run tests → all pass                  - services/session_manager.py
                                        Remove temporary re-exports
                                        Run full test suite
```

---

## What DevAI Provided

| Capability | Tool Used | Value |
|---|---|---|
| **Current state mapping** | `build_context` | Assembled all auth-related code into a single view across 4 directories |
| **Blast radius analysis** | `get_references` | Found all 47 call sites for `authenticate_user` — no manual searching |
| **Related code discovery** | `search` | Found session management and token helpers that semantic search connected to auth |
| **Impact quantification** | `get_references` | Identified 23 `UserSession` references — exact scope of import changes needed |
| **Historical context** | `recall` | Surfaced the JWT vs sessions decision — prevented the agent from breaking admin auth |
| **Plan persistence** | `remember` | Saved the complete refactor plan with file list, risks, and migration strategy |

**Total tool calls: 6.** The agent mapped the full auth surface area (70+ references across 20 files), discovered a critical design constraint from team history, and produced a phased refactor plan — all without opening a single file manually.

The difference between a refactor that goes smoothly and one that causes a production outage is the quality of the impact analysis. DevAI makes thorough analysis the default, not a luxury.
