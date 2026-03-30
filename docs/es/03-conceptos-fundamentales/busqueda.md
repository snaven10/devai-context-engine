> 🌐 [English version](../../03-core-concepts/search.md)

# Busqueda Semantica de Codigo

## Que es

La busqueda de DevAI es un motor de busqueda semantica de codigo. Entiende el **significado** de tu consulta, no solo las palabras clave. Cuando buscas "authentication middleware", encuentra la clase `AuthGuard`, la funcion `verifyJWT` y el decorator `requirePermissions` — aunque ninguno de ellos contenga la palabra "authentication".

## Por que existe

`grep` encuentra texto. DevAI encuentra **intencion**.

| Enfoque | Consulta: "retry logic for failed API calls" |
|---|---|
| **grep/ripgrep** | Coincide con archivos que contienen "retry" o "API" literalmente. No encuentra `exponentialBackoff()`, `withRetries()`, ni un loop con `catch` y `setTimeout`. |
| **Busqueda DevAI** | Devuelve la clase `RetryPolicy`, la funcion `fetchWithBackoff` y el middleware de manejo de errores — rankeados por relevancia semantica. |

La diferencia importa a escala. En un codebase de 500k lineas, grep te da ruido. La busqueda semantica te da respuestas.

## Como funciona internamente

### El pipeline de indexacion

Cuando ejecutas `devai index` (o la indexacion se dispara automaticamente), esto es lo que pasa:

```
  git diff (desde el ultimo commit indexado)
       │
       ▼
  Tree-sitter AST Parse (25+ lenguajes)
       │
       ▼
  Chunking Semantico en 4 Niveles
       │
       ▼
  Embedding (sentence-transformers, 384-dim)
       │
       ▼
  Almacenamiento (LanceDB / Qdrant)
       │
       ▼
  Symbol Graph (aristas en SQLite)
```

Cada paso es deterministico e incremental. Solo los archivos modificados se reprocesan. Un reindex completo ocurre unicamente cuando cambia el modelo de embedding.

### Los 4 niveles de chunk

El codigo no es texto plano. Un archivo tiene estructura: imports, clases, funciones, bloques de control de flujo. DevAI respeta esta estructura haciendo chunking en cuatro niveles semanticos:

#### Nivel 1: Archivo

El chunk a nivel de archivo captura la forma general — imports, declaraciones de nivel superior y una lista de simbolos. Pensalo como una tabla de contenidos.

```
# auth/middleware.py
import jwt
from flask import request, abort
from .models import User, Permission

# Symbols: AuthMiddleware, require_auth, require_permission, decode_token
```

#### Nivel 2: Clase

Cada clase obtiene su propio chunk: firma, campos, firmas de metodos. Suficiente para entender que **es** la clase sin leer el cuerpo de cada metodo.

```
# auth/middleware.py > AuthMiddleware
class AuthMiddleware:
    secret_key: str
    token_header: str = "Authorization"

    def authenticate(self, request) -> User: ...
    def authorize(self, user, permission) -> bool: ...
    def refresh_token(self, token) -> str: ...
```

#### Nivel 3: Funcion

Cada funcion o metodo se convierte en su propio chunk. Aca es donde aterrizan la mayoria de los resultados de busqueda.

```
# auth/middleware.py > AuthMiddleware > authenticate
def authenticate(self, request) -> User:
    token = request.headers.get(self.token_header)
    if not token:
        abort(401, "Missing authentication token")
    payload = jwt.decode(token, self.secret_key, algorithms=["HS256"])
    return User.from_payload(payload)
```

#### Nivel 4: Bloque

Las funciones grandes (> 512 tokens) se dividen en los limites de control de flujo: `if/else`, `for`, `try/catch`, `match`. Cada bloque conserva el header de contexto de su padre para que nunca quede huerfano.

```
# auth/middleware.py > AuthMiddleware > authenticate > try-block
try:
    payload = jwt.decode(token, self.secret_key, algorithms=["HS256"])
    if payload.get("exp") < time.time():
        raise ExpiredTokenError()
    return User.from_payload(payload)
```

**Restricciones:** maximo 512 tokens, minimo 64 tokens por chunk. Los chunks por debajo de 64 tokens se fusionan hacia arriba con su padre.

### Headers de contexto (Breadcrumbs)

Cada chunk lleva un header de contexto que muestra su posicion en la jerarquia del codigo:

```
file > class > method > block
auth/middleware.py > AuthMiddleware > authenticate > try-block
```

Esto significa que los resultados de busqueda siempre muestran DONDE vive un chunk en el codebase, no solo el codigo en si.

### IDs deterministicos

Cada chunk recibe un ID estable y deterministico:

```
sha256("myrepo:main:auth/middleware.py:42")[:32]
```

Formato: `sha256(repo:branch:file:line)[:32]`

Esto habilita upserts reales. Cuando una funcion cambia, el chunk en esa ubicacion es **reemplazado**, no duplicado. Cuando una funcion se elimina, su chunk se remueve. Sin huerfanos, sin duplicados, sin necesidad de garbage collection.

## Indexacion incremental

DevAI trackea el SHA del ultimo commit indexado por repositorio. En ejecuciones posteriores:

```
  Ultimo indexado: a1b2c3d
  HEAD actual:     e4f5g6h
       │
       ▼
  git diff a1b2c3d..e4f5g6h --name-only
       │
       ▼
  Solo reparsear + re-embeddear archivos modificados
       │
       ▼
  Upsert de chunks (los IDs deterministicos manejan las actualizaciones)
```

En la practica, indexar un repo de 200 archivos toma ~30 segundos la primera vez y <2 segundos en actualizaciones incrementales.

Un reindex completo se fuerza unicamente cuando cambia el modelo de embedding (porque todos los vectores deben recalcularse para mantener consistencia).

## Busqueda consciente de branches

DevAI no crea indices separados por branch. En su lugar, usa una estrategia de **overlay**:

```
  main (completamente indexado)
    │
    ├── feature/auth (overlay: 3 archivos modificados)
    │
    └── feature/payments (overlay: 7 archivos modificados)
```

Cuando buscas en `feature/auth`:
1. Los resultados del overlay de `feature/auth` tienen prioridad
2. Los resultados de `main` llenan el resto
3. Los archivos eliminados en el branch se filtran (tombstones)

Esto significa que los cambios de branch son instantaneos — no se requiere reindexacion. Solo los archivos modificados en el branch se indexan como overlay.

## Proveedores de embedding

| Proveedor | Modelo | Dimensiones | Velocidad | Costo |
|---|---|---|---|---|
| **Local** (default) | `all-MiniLM-L6-v2` | 384 | ~500 chunks/seg | Gratis |
| OpenAI | `text-embedding-3-small` | 384* | ~2000 chunks/seg | $0.02/1M tokens |
| Voyage | `voyage-code-2` | 1024 | ~1500 chunks/seg | $0.12/1M tokens |
| Custom | Cualquier modelo sentence-transformers | Variable | Variable | Variable |

*La dimensionalidad es configurable; DevAI trunca para coincidir con el indice.

El proveedor local es el default y el recomendado para la mayoria de los casos de uso. Corre enteramente en CPU, no requiere API keys, y es lo suficientemente rapido para repos de hasta ~1M lineas.

## Cuando se usa

- **MCP `search` tool**: Llamado por agentes de IA (Claude Code, Cursor) para encontrar codigo relevante
- **`devai search` CLI**: Busqueda directa por linea de comandos
- **Context builder**: Internamente usa busqueda para ensamblar contexto para consultas
- **Indexacion**: Automaticamente con `devai index` o disparada por herramientas MCP

## Ejemplo: Buscando "authentication middleware"

Esto es lo que pasa internamente cuando vos (o un agente de IA) buscas "authentication middleware":

```
1. EMBEDDEAR CONSULTA
   "authentication middleware"
      → sentence-transformers encode
      → [0.12, -0.34, 0.56, ...] (vector de 384 dimensiones)

2. BUSQUEDA VECTORIAL (LanceDB)
   Encontrar los top-K chunks mas cercanos al vector de consulta
   Filtro: repo="myapp", branch="main" (+ overlays)

   Resultados (rankeados por similitud coseno):
   ┌──────┬──────────────────────────────────────────┬───────┐
   │ Rank │ Chunk                                    │ Score │
   ├──────┼──────────────────────────────────────────┼───────┤
   │  1   │ auth/middleware.py > AuthMiddleware       │ 0.92  │
   │  2   │ auth/decorators.py > require_auth         │ 0.87  │
   │  3   │ auth/jwt.py > verify_token                │ 0.84  │
   │  4   │ tests/test_auth.py > TestAuthMiddleware   │ 0.79  │
   │  5   │ config/security.py > AUTH_CONFIG           │ 0.71  │
   └──────┴──────────────────────────────────────────┴───────┘

3. RETORNO
   Cada resultado incluye:
   - ruta del archivo + rango de lineas
   - header de contexto (breadcrumb)
   - contenido del codigo
   - score de similitud
   - tipo de simbolo + lenguaje
```

La operacion completa toma 10-50ms para un codebase tipico. Sin I/O de archivos en tiempo de consulta — todo son vectores pre-indexados.

## Modelo mental

Pensa en la busqueda de DevAI como **Google para tu codebase**. Google no matchea tu consulta palabra por palabra contra paginas web — entiende lo que estas buscando y encuentra paginas que son semanticamente relevantes. DevAI hace lo mismo, pero para codigo: entiende que "retry logic" y `exponentialBackoff()` tratan sobre el mismo concepto, aunque no compartan ninguna palabra clave.

El pipeline de indexacion es como el web crawler de Google: visita cada archivo, entiende su estructura (via tree-sitter AST), lo divide en chunks significativos, y almacena representaciones vectoriales. Al momento de la consulta, tu pregunta en lenguaje natural se convierte al mismo espacio vectorial y se matchea contra el indice. Rapido, preciso y mantenido incrementalmente.
