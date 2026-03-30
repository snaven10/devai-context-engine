> 🌐 [English version](../../05-examples/onboarding.md)

# Incorporación a un Codebase Desconocido

Un recorrido paso a paso de un desarrollador usando DevAI para pasar de cero conocimiento de un codebase a contribuidor productivo — en una sola sesión en vez de una semana leyendo código.

---

## Escenario

Acabás de unirte a un equipo que mantiene un servicio backend con más de 400 archivos distribuidos en 30 paquetes. El README está desactualizado. No hay documentación de arquitectura. La última persona que entendía el sistema completo se fue hace seis meses.

Tenés DevAI indexado y listo. Así es cómo construís un modelo mental rápido.

---

## Paso 0 — Indexar el Repositorio

Antes que nada, el codebase necesita ser indexado.

```bash
devai init
devai index
```

**Lo que DevAI hace internamente:**
- Parsea cada archivo usando tree-sitter para construir el grafo de código (símbolos, referencias, imports, cadenas de llamadas).
- Genera embeddings vectoriales para búsqueda semántica.
- Detecta lenguajes, frameworks y estructura del proyecto.

Podés verificar el progreso:

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

El codebase está listo. Hora de explorar.

---

## Paso 1 — Entender la Arquitectura

Empezá con la visión general. No leas archivos. Pedí la arquitectura.

**Llamada a herramienta:**
```json
{
  "tool": "build_context",
  "arguments": {
    "query": "main application architecture and entry points",
    "max_tokens": 8000
  }
}
```

**Lo que DevAI hace internamente:**
Combina búsqueda semántica, recorrido del grafo de código y análisis de dependencias para armar una imagen coherente de la arquitectura del sistema. Prioriza puntos de entrada, módulos de nivel superior y archivos de configuración.

**Respuesta (abreviada):**
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

**Lo que aprendés:**
- App FastAPI con tres grupos de rutas principales.
- Usa Celery workers respaldados por Redis para trabajos asincrónicos.
- PostgreSQL es la base de datos primaria.
- Autenticación y rate limiting manejados via middleware.
- Configuración via variables de entorno con pydantic `BaseSettings`.

Una llamada a herramienta. Ya tenés la vista a 30.000 pies de altura.

---

## Paso 2 — Encontrar Componentes Clave de Infraestructura

Ahora profundizá en la capa de base de datos — ¿cómo se comunica este servicio con Postgres?

**Llamada a herramienta:**
```json
{
  "tool": "search",
  "arguments": {
    "query": "database connection and configuration",
    "limit": 10
  }
}
```

**Respuesta:**
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

**Lo que aprendés:**
- `DatabasePool` es la abstracción central — un singleton que envuelve el motor async de SQLAlchemy.
- Las sesiones se proveen via `get_db_session()` — probablemente una dependencia de FastAPI.
- Alembic maneja las migraciones.

---

## Paso 3 — Entender Cómo se Usa la Base de Datos

Encontraste `DatabasePool`. Ahora mirá cómo se usa en todo el codebase.

**Llamada a herramienta:**
```json
{
  "tool": "get_references",
  "arguments": {
    "symbol": "DatabasePool"
  }
}
```

**Respuesta:**
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

**Lo que aprendés:**
- `DatabasePool` se inicializa al arranque de la app (`main.py:12`) y se cierra al apagar (`main.py:45`).
- Hay un `initialize_sync` separado para los Celery workers — no pueden usar init async.
- Los tests usan su propia URL de DB via `conftest.py`.
- Las sesiones se adquieren a través de `get_db_session()` que llama a `DatabasePool.get_instance()`.

Ahora entendés el ciclo de vida completo de la DB sin haber leído un solo archivo manualmente.

---

## Paso 4 — Leer Detalles Clave de Implementación

Hacé zoom en la implementación del pool para entender el manejo de conexiones.

**Llamada a herramienta:**
```json
{
  "tool": "read_symbol",
  "arguments": {
    "name": "DatabasePool.get_connection"
  }
}
```

**Respuesta:**
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

**Lo que aprendés:**
- Las conexiones se manejan via un context manager async.
- Un semáforo limita la concurrencia — sin agotamiento de conexiones.
- Hay una protección contra usar el pool antes de la inicialización.

---

## Paso 5 — Consultar Conocimiento Existente del Equipo

Antes de formarte tus propias opiniones sobre la arquitectura, verificá si el equipo documentó decisiones en la memoria.

**Llamada a herramienta:**
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

**Respuesta:**
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

**Lo que aprendés:**
- El equipo eligió conscientemente SQLAlchemy sobre asyncpg crudo — y documentó el tradeoff.
- Hubo un bug de fuga de conexiones en los Celery workers que llevó al método `initialize_sync` separado. Ahora sabés POR QUÉ ese código existe, no solo QUÉ hace.
- `greenlet` es una dependencia requerida — un gotcha que de otra forma te pegaría durante la configuración local.

Esto es memoria institucional. Sin DevAI, este conocimiento se habría ido con el ingeniero anterior.

---

## Paso 6 — Guardar Tu Entendimiento

Construiste un modelo mental sólido. Persistilo para que se acumule en futuras sesiones.

**Llamada a herramienta:**
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

**Respuesta:**
```json
{
  "status": "saved",
  "id": "mem_e4f2b1c9",
  "topic_key": "architecture/backend-service-overview"
}
```

En la próxima sesión, cualquier agente (o cualquier miembro del equipo) puede llamar `recall(query="backend-service architecture")` y obtener este resumen instantáneamente.

---

## La Progresión de la Incorporación

```
  TIEMPO        TRADICIONAL                         CON DEVAI
  ──────        ────────────                        ──────────

  0-5 min       Clonar repo                         Clonar repo
                Leer README (desactualizado)        devai init && devai index

  5-30 min      Hacer grep buscando "main"          build_context → arquitectura completa
                Abrir 20 archivos al azar           search → componentes clave encontrados
                Preguntar en Slack "dónde vive X?"  get_references → mapa de dependencias

  30-60 min     Todavía leyendo archivos de config  read_symbol → detalles de implementación
                No encontrás el código de conexión  recall → decisiones pasadas del equipo
                Sin idea de por qué los workers     remember → guardar modelo mental
                son diferentes

  Día 2-3       Empezando a entender la estructura  ── Ya productivo ──
                Encontraste una wiki del 2023

  Día 4-5       Podés hacer cambios chicos
                Seguís pegándote con gotchas

  Semana 2      Algo productivo
```

---

## Checklist de Incorporación con DevAI

Usá esta secuencia para cualquier codebase nuevo:

```
  ┌──────────────────────────────────────────────────────────┐
  │                   FLUJO DE INCORPORACIÓN                  │
  │                                                          │
  │  1. INDEXAR                                              │
  │     devai init && devai index                            │
  │         │                                                │
  │         ▼                                                │
  │  2. ARQUITECTURA                                         │
  │     build_context("main architecture and entry points")  │
  │         │                                                │
  │         ▼                                                │
  │  3. COMPONENTES CLAVE                                    │
  │     search("database connection")                        │
  │     search("authentication middleware")                  │
  │     search("API route definitions")                      │
  │         │                                                │
  │         ▼                                                │
  │  4. RELACIONES                                           │
  │     get_references("CoreClass") para cada componente     │
  │         │                                                │
  │         ▼                                                │
  │  5. IMPLEMENTACIÓN                                       │
  │     read_symbol("ClassName.method") para caminos         │
  │     críticos                                             │
  │         │                                                │
  │         ▼                                                │
  │  6. CONOCIMIENTO DEL EQUIPO                              │
  │     recall("architecture decisions")                     │
  │     recall("known bugs gotchas")                         │
  │         │                                                │
  │         ▼                                                │
  │  7. PERSISTIR                                            │
  │     remember(tu modelo mental, type="architecture")      │
  │                                                          │
  └──────────────────────────────────────────────────────────┘
```

---

## Lo que DevAI Aportó

| Capacidad | Herramienta Usada | Valor |
|---|---|---|
| **Visión de la arquitectura** | `build_context` | Ensambló puntos de entrada, configuración e infraestructura en una sola vista coherente |
| **Descubrimiento de componentes** | `search` | Encontró `DatabasePool` y componentes relacionados via consulta semántica, no adivinando nombres de archivo |
| **Mapeo de dependencias** | `get_references` | Mostró exactamente dónde y cómo se usa `DatabasePool` en todo el codebase |
| **Detalle de implementación** | `read_symbol` | Recuperó el código completo del método sin navegar manualmente a los archivos |
| **Memoria institucional** | `recall` | Trajo a la superficie decisiones pasadas del equipo y correcciones de bugs — conocimiento que de otro modo se habría perdido |
| **Persistencia de conocimiento** | `remember` | Guardó el modelo mental del desarrollador para futuras sesiones y miembros del equipo |

**Total de llamadas a herramientas: 7.** De cero conocimiento del codebase a un entendimiento arquitectónico documentado en una sola sesión. Lo que tradicionalmente lleva una semana de leer código y preguntar a compañeros se comprime en una hora de exploración dirigida.
