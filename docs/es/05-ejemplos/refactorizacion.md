> 🌐 [English version](../../05-examples/refactoring.md)

# Planificación de un Refactor Grande

Un recorrido paso a paso del uso de DevAI para planificar y delimitar un esfuerzo de refactorización importante — extrayendo la lógica de autenticación en un módulo independiente — con análisis de impacto completo antes de escribir una sola línea de código.

---

## Escenario

La lógica de autenticación y autorización en tu aplicación está desperdigada en más de 15 archivos. Cada handler de ruta hace sus propias verificaciones de auth. La gestión de sesiones está duplicada en tres lugares. La validación de tokens vive dentro de la capa de API en vez de un módulo dedicado.

Necesitás extraer todo esto en un módulo `auth` limpio. Pero tocar código de auth en un sistema en producción sin entender cada sitio de llamada es cómo causás caídas.

DevAI te permite mapear el radio de impacto completo antes de empezar.

---

## Paso 1 — Entender el Estado Actual

Empezá obteniendo una vista comprensiva de cómo luce "autenticación" en este codebase hoy.

**Llamada a herramienta:**
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

**Lo que DevAI hace internamente:**
Búsqueda semántica en todo el índice, luego expande resultados siguiendo imports, cadenas de llamadas y referencias de tipos para construir un paquete de contexto que reconoce dependencias.

**Respuesta (abreviada):**
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

**Lo que el agente aprende:**
La lógica de auth está distribuida en al menos cinco ubicaciones:

| Ubicación | Responsabilidad |
|---|---|
| `middleware/auth.py` | Validación de token, hidratación de usuario |
| `api/orders.py` | Verificación de rol inline (`require_admin`) |
| `api/inventory.py` | Verificación de rol inline diferente (inconsistente) |
| `services/session_manager.py` | Creación y validación de sesión via Redis |
| `utils/token_helpers.py` | Creación de tokens JWT |

Hay patrones de autorización inconsistentes: algunos usan un helper `require_admin`, otros hacen verificaciones de rol inline con lógica diferente. Este es exactamente el tipo de dispersión que causa bugs de seguridad.

---

## Paso 2 — Mapear Todos los Sitios de Llamada de la Función Core de Auth

La parte más peligrosa de un refactor es no detectar un sitio de llamada. Mapeá todos.

**Llamada a herramienta:**
```json
{
  "tool": "get_references",
  "arguments": {
    "symbol": "authenticate_user"
  }
}
```

**Respuesta:**
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

*47 referencias totales, mostradas arriba como muestra representativa.*

**Lo que el agente aprende:**
- `authenticate_user` se llama desde **47 ubicaciones** en todo el codebase.
- Todos los módulos de API lo importan y lo llaman directamente.
- Los workers también lo llaman (para validar payloads de trabajos).
- Los tests lo importan — los fixtures de test van a necesitar actualización también.
- La función vive en `middleware/auth.py` — una ubicación extraña para algo que llaman los workers.

Este es el radio de impacto. Cada una de estas 47 referencias tiene que funcionar después del refactor.

---

## Paso 3 — Encontrar Código de Auth Relacionado

La función `authenticate_user` es una pieza. Encontrá el resto: gestión de sesiones, manejo de tokens, verificaciones de autorización.

**Llamada a herramienta:**
```json
{
  "tool": "search",
  "arguments": {
    "query": "session management token validation",
    "limit": 15
  }
}
```

**Respuesta:**
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

**Lo que el agente aprende:** La superficie completa de auth incluye:

| Componente | Ubicación Actual | Responsabilidad |
|---|---|---|
| `SessionManager` | `services/session_manager.py` | CRUD de sesiones con Redis |
| `create_access_token` | `utils/token_helpers.py` | Generación de access token JWT |
| `create_refresh_token` | `utils/token_helpers.py` | Generación de refresh token JWT |
| `validate_token` | `middleware/auth.py` | Decodificación JWT + búsqueda de usuario |
| `login` | `api/auth_routes.py` | Endpoint de login |
| `refresh` | `api/auth_routes.py` | Endpoint de renovación de token |
| `logout` | `api/auth_routes.py` | Invalidación de sesión |

La lógica de auth está distribuida en cuatro directorios: `middleware/`, `utils/`, `services/` y `api/`. No es sorpresa que haya inconsistencias.

---

## Paso 4 — Verificar el Impacto en UserSession

El modelo de sesión probablemente está referenciado en todos los lugares donde se usa auth. ¿Qué tan grande es el radio de impacto?

**Llamada a herramienta:**
```json
{
  "tool": "get_references",
  "arguments": {
    "symbol": "UserSession"
  }
}
```

**Respuesta:**
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

**Lo que el agente aprende:**
`UserSession` está referenciado en **23 archivos** — 6 en código de producción, 1 en workers, 3 en tests, más la definición y el session manager. Todos estos van a necesitar que se actualicen sus rutas de import cuando `UserSession` se mueva al nuevo módulo `auth`.

---

## Paso 5 — Revisar Decisiones Arquitectónicas Pasadas

Antes de diseñar la estructura del nuevo módulo, verificá si el equipo discutió esto antes.

**Llamada a herramienta:**
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

**Respuesta:**
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

**Lo que el agente aprende:** Contexto crítico que cambia el plan de refactor:

1. **JWT vs sesiones es intencional**, no duplicación accidental. Los clientes API usan JWT, el dashboard de admin usa sesiones Redis. Ambos deben preservarse.
2. El ingeniero anterior ya notó que esto "debería eventualmente unificarse bajo un solo módulo de auth" — este refactor estaba anticipado.
3. Si el servicio pasa a ser multi-servicio, HS256 necesita convertirse en RS256. El refactor debería hacer esto fácil de cambiar después.

Sin esta memoria, el agente podría haber intentado eliminar el sistema de sesiones "duplicado", rompiendo la auth del admin.

---

## Paso 6 — Documentar el Plan de Refactor

El agente ahora tiene suficiente información para crear un plan comprensivo. Guardalo para la fase de implementación.

**Llamada a herramienta:**
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

**Respuesta:**
```json
{
  "status": "saved",
  "id": "mem_f1a9c3d7",
  "topic_key": "architecture/auth-refactor"
}
```

---

## Resumen del Análisis de Impacto

Antes de escribir una sola línea de código, el agente tiene el panorama completo:

```
  ESTADO ACTUAL                              ESTADO OBJETIVO
  ─────────────                              ────────────────

  middleware/                                 auth/
    auth.py ──────────────────────────┐        __init__.py    (API pública)
      AuthMiddleware                  │        tokens.py      (operaciones JWT)
      validate_token                  ├──────► sessions.py    (sesiones Redis)
      authenticate_user (47 refs)     │        middleware.py   (auth de request)
                                      │        models.py      (UserSession)
  utils/                              │        permissions.py  (verificación de roles)
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

  api/  (verificaciones de rol inline)       api/  (usa auth.require_role)
    orders.py ─── require_admin ────────────► orders.py ─── from auth import require_role
    inventory.py ── if role in (...) ───────► inventory.py ── require_role('warehouse')
    admin.py ── if role != 'admin' ─────────► admin.py ── require_role('admin')
```

---

## Grafo de Dependencias de Referencias

```
                    ┌─────────────────────┐
                    │   47 Sitios de      │
                    │   Llamada (handlers │
                    │   de API, workers,  │
                    │   tests)            │
                    └─────────┬───────────┘
                              │ import
                              ▼
                    ┌─────────────────────┐
                    │  authenticate_user  │ ◄── Actualmente en middleware/auth.py
                    └─────────┬───────────┘     Mover a auth/__init__.py
                              │
                    ┌─────────┴───────────┐
                    │                     │
                    ▼                     ▼
          ┌─────────────────┐   ┌─────────────────┐
          │  validate_token │   │ SessionManager   │
          │  (ruta JWT)     │   │ (ruta sesión)    │
          └────────┬────────┘   └────────┬────────┘
                   │                     │
                   ▼                     ▼
          ┌─────────────────┐   ┌─────────────────┐
          │ token_helpers   │   │ Redis            │
          │ (crear/verificar)│   │ (almacén de     │
          └────────┬────────┘   │  sesiones)       │
                   │            └─────────────────┘
                   ▼
          ┌─────────────────┐
          │ UserSession     │ ◄── 23 referencias, todas necesitan
          │ (modelo)        │     actualización de import
          └─────────────────┘
```

---

## Plan de Ejecución del Refactor

Basado en el análisis, el agente recomienda este enfoque por fases:

```
  Fase 1: Crear (sin rotura)            Fase 2: Migrar (controlado)
  ─────────────────────────────         ──────────────────────────────

  Crear módulo app/auth/                Actualizar imports archivo por archivo:
  Copiar código a la nueva estructura   - Handlers de API (6 archivos)
  Agregar re-exports en ubicaciones     - Workers (2 archivos)
  antiguas                              - Tests (6 archivos)
  Correr tests → todos pasan            Correr tests después de cada archivo

  Fase 3: Consolidar                    Fase 4: Limpieza
  ────────────────────                  ──────────────────

  Unificar 3 patrones de               Eliminar archivos viejos:
  verificación de roles en             - middleware/auth.py
  auth/permissions.py                   - utils/token_helpers.py
  Extraer config a constants.py         - services/session_manager.py
  Correr tests → todos pasan            Eliminar re-exports temporales
                                        Correr suite completa de tests
```

---

## Lo que DevAI Aportó

| Capacidad | Herramienta Usada | Valor |
|---|---|---|
| **Mapeo del estado actual** | `build_context` | Ensambló todo el código relacionado con auth en una sola vista a través de 4 directorios |
| **Análisis de radio de impacto** | `get_references` | Encontró los 47 sitios de llamada de `authenticate_user` — sin búsqueda manual |
| **Descubrimiento de código relacionado** | `search` | Encontró gestión de sesiones y helpers de tokens que la búsqueda semántica conectó con auth |
| **Cuantificación de impacto** | `get_references` | Identificó 23 referencias a `UserSession` — alcance exacto de cambios de import necesarios |
| **Contexto histórico** | `recall` | Trajo a la superficie la decisión de JWT vs sesiones — evitó que el agente rompiera la auth del admin |
| **Persistencia del plan** | `remember` | Guardó el plan de refactor completo con lista de archivos, riesgos y estrategia de migración |

**Total de llamadas a herramientas: 6.** El agente mapeó toda la superficie de auth (70+ referencias en 20 archivos), descubrió una restricción de diseño crítica del historial del equipo, y produjo un plan de refactor por fases — todo sin abrir un solo archivo manualmente.

La diferencia entre un refactor que sale bien y uno que causa una caída en producción es la calidad del análisis de impacto. DevAI hace que un análisis exhaustivo sea lo predeterminado, no un lujo.
