> 🌐 [English version](../04-agent-workflow.md)

# Flujo de Trabajo del Agente

> Volver al [README](../README.md)

---

## Modelo Mental

DevAI es Jarvis para el Tony Stark del agente de IA.

El agente tiene la inteligencia. Puede razonar, planificar, escribir código, debuggear. Lo que le falta es *conciencia situacional* — no puede ver el campo de batalla completo. DevAI provee el heads-up display: mapas estructurales de código, búsqueda semántica, navegación de referencias cruzadas y memoria persistente entre sesiones.

Sin DevAI, un agente navegando un codebase grande es un cirujano operando con los ojos vendados, leyendo la historia clínica del paciente una línea a la vez. Con DevAI, el agente tiene la historia completa, las radiografías y las notas de cada cirugía anterior.

---

## Cómo Funciona MCP

El [Model Context Protocol](https://modelcontextprotocol.io) (MCP) es un estándar para conectar agentes de IA con herramientas externas. DevAI lo usa como su interfaz principal.

**Transporte:** stdio. El proceso host del agente de IA lanza `devai server mcp` como un subproceso. La comunicación ocurre sobre stdin/stdout usando JSON-RPC 2.0. Sin HTTP, sin puertos, sin autenticación.

**Ciclo de vida:**

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
    │      ... (la sesión continúa) ...    │
    │                                      │
    │───── shutdown ──────────────────────▶│
    │                                      │── terminate Python
```

**Registro:** Cuando el agente llama a `tools/list`, DevAI devuelve las 14 definiciones de herramientas con parámetros JSON Schema. El runtime del agente valida los parámetros antes de cada llamada. Sin descubrimiento de herramientas en runtime — todo se declara de entrada.

---

## El Flujo de Trabajo Típico del Agente

Un agente bien configurado sigue un patrón predecible cuando trabaja con DevAI:

```
1. ORIENTARSE     recall del contexto previo, buscar código relevante
2. ENTENDER       leer símbolos, rastrear referencias, construir contexto completo
3. ACTUAR         escribir código, corregir bugs, implementar funcionalidades
4. PERSISTIR      remember de decisiones, descubrimientos, convenciones
```

### Fase 1: Orientarse

El agente recibe una tarea del usuario. Antes de escribir una sola línea de código, se orienta.

```
Usuario: "Corregí la race condition en el session store"

El agente piensa: Necesito entender el session store primero.

El agente llama: recall(query: "session store race condition")
  → Devuelve: La sesión anterior encontró un data race en SessionStore.Get
              cuando requests concurrentes golpean el mismo session ID.
              Se agregó Mutex a Set pero no a Get. Archivo: internal/store/session.go

El agente llama: search(query: "session store concurrent access")
  → Devuelve: 3 resultados rankeados por relevancia
              1. internal/store/session.go — struct SessionStore, métodos Get/Set
              2. internal/store/session_test.go — TestConcurrentAccess (fallando actualmente)
              3. internal/middleware/auth.go — llama a SessionStore.Get en el request handler
```

El agente ahora sabe *dónde* mirar, *qué se intentó antes* y *cómo se ve el codebase* alrededor del problema — todo antes de leer un solo archivo.

### Fase 2: Entender

Con los resultados de búsqueda apuntando al área correcta, el agente profundiza.

```
El agente llama: read_symbol(name: "SessionStore")
  → Devuelve: Definición completa del struct con todos los métodos
              type SessionStore struct { mu sync.Mutex; data map[string]*Session }
              func (s *SessionStore) Get(id string) (*Session, error)
              func (s *SessionStore) Set(id string, sess *Session) error
              func (s *SessionStore) Delete(id string) error

El agente llama: get_references(symbol: "SessionStore.Get")
  → Devuelve: 4 sitios de llamada
              internal/middleware/auth.go:47      — middleware de autenticación, llamado por request
              internal/handler/dashboard.go:23    — handler del dashboard
              internal/handler/api.go:89          — handler de la API
              internal/store/session_test.go:34   — archivo de tests
```

Ahora el agente tiene comprensión estructural — no solo coincidencias de texto, sino el grafo de símbolos real.

### Fase 3: Actuar

El agente escribe la corrección. Esta fase es la competencia central del agente — DevAI se hace a un lado.

### Fase 4: Persistir

Después de completar la corrección, el agente persiste lo que aprendió.

```
El agente llama: remember(
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

La próxima sesión — o el próximo agente — puede hacer `recall` de esto y evitar redescubrir el mismo problema.

---

## Patrones de Selección de Herramientas

Usá este árbol de decisión para elegir la herramienta correcta en cada situación:

| El agente necesita... | Herramienta | Por qué esta |
|----------------|------|-------------|
| Encontrar código relacionado a un concepto | `search` | Búsqueda vectorial semántica. Encuentra código por significado, no solo palabras clave. Devuelve chunks rankeados con rutas de archivo y puntajes. |
| Contexto comprehensivo para un tema | `build_context` | Ensambla resultados de búsqueda + info de símbolos + memoria en un único bloque con presupuesto de tokens. Usalo cuando el agente necesita el panorama completo, no solo un puntero. |
| Definición completa de un símbolo específico | `read_symbol` | Devuelve el código fuente completo de una función, clase, struct o tipo. Más rápido y preciso que leer un archivo entero. |
| Todos los lugares donde se usa un símbolo | `get_references` | Devuelve cada sitio de llamada, import y uso. Esencial para entender el impacto de los cambios. |
| Leer un archivo específico | `read_file` | Cuando sabés la ruta exacta. Soporta rangos de líneas opcionales para evitar cargar archivos enteros. |
| Guardar una decisión o descubrimiento | `remember` | Persiste en SQLite con dedup y upsert por topic-key. Sobrevive entre sesiones y resets de contexto. |
| Verificar si algo se discutió antes | `recall` | Busca en la memoria por query. Devuelve contenido completo, no resúmenes truncados. |
| Revisar acciones pasadas en esta sesión | `get_session_history` | Devuelve el log de llamadas a herramientas de la sesión actual. Útil para agentes revisando su propio trabajo. |
| Indexar o reindexar archivos | `index` | Dispara indexación incremental. Solo reprocesa archivos modificados via git diff. |

### Anti-Patrones

- **No uses `search` cuando sabés el nombre exacto del símbolo.** Usá `read_symbol` en su lugar. Search es para descubrimiento; `read_symbol` es para recuperación.
- **No uses `read_file` para explorar un codebase.** Usá `search` o `build_context` primero para encontrar lo que importa, después `read_file` para la sección específica.
- **No te saltees `recall` al inicio de la sesión.** Cinco segundos de búsqueda en memoria ahorran cinco minutos de re-exploración.
- **No te olvides de hacer `remember`.** Si el agente tomó una decisión no obvia, corrigió un bug sutil o descubrió un gotcha — persistilo. Las sesiones futuras dependen de eso.

---

## Ejemplo de Punta a Punta: Debuggeando un Bug en un Codebase Grande

**Escenario:** El usuario reporta "La API devuelve 500 en /api/users cuando la base de datos está bajo carga." El codebase tiene más de 200 archivos en 15 paquetes.

### Paso 1: Verificar memoria para contexto previo

```json
{
  "tool": "recall",
  "params": {
    "query": "API 500 error users database load",
    "project": "myapp"
  }
}
```

**Resultado:** No se encontró memoria previa. Es un problema nuevo.

### Paso 2: Búsqueda semántica de la superficie del error

```json
{
  "tool": "search",
  "params": {
    "query": "API users endpoint handler error handling",
    "top_k": 5
  }
}
```

**Resultado:**

```
1. internal/handler/users.go:45     score: 0.87  "func (h *UserHandler) List(...)"
2. internal/handler/users.go:82     score: 0.81  "func (h *UserHandler) Create(...)"
3. internal/service/user_service.go  score: 0.76  "func (s *UserService) GetAll(...)"
4. internal/middleware/recovery.go   score: 0.71  "panic recovery middleware"
5. internal/db/pool.go              score: 0.68  "connection pool configuration"
```

### Paso 3: Leer el símbolo del handler

```json
{
  "tool": "read_symbol",
  "params": {
    "name": "UserHandler.List"
  }
}
```

**Resultado:**

```go
func (h *UserHandler) List(w http.ResponseWriter, r *http.Request) {
    users, err := h.service.GetAll(r.Context())
    if err != nil {
        http.Error(w, "internal error", 500)  // sin wrapping de error, sin logging
        return
    }
    json.NewEncoder(w).Encode(users)
}
```

### Paso 4: Rastrear la cadena de llamadas

```json
{
  "tool": "get_references",
  "params": {
    "symbol": "UserService.GetAll"
  }
}
```

**Resultado:**

```
internal/handler/users.go:46        — UserHandler.List
internal/handler/admin.go:112       — AdminHandler.ExportUsers
internal/job/daily_report.go:34     — DailyReportJob.Run
```

### Paso 5: Leer la implementación del servicio

```json
{
  "tool": "read_symbol",
  "params": {
    "name": "UserService.GetAll"
  }
}
```

**Resultado:**

```go
func (s *UserService) GetAll(ctx context.Context) ([]User, error) {
    return s.repo.FindAll(ctx)  // sin timeout, sin retry, sin chequeo de conexión
}
```

### Paso 6: Construir contexto completo para la capa de base de datos

```json
{
  "tool": "build_context",
  "params": {
    "query": "database connection pool timeout retry",
    "max_tokens": 6000
  }
}
```

**Resultado:** Contexto ensamblado incluyendo la configuración del connection pool (máximo 10 conexiones, sin timeout), la implementación del repositorio (SQL crudo sin deadline de contexto), y entradas de memoria relacionadas.

### Paso 7: El agente diagnostica y corrige

El agente ahora tiene el panorama completo:

```
Causa raíz: UserService.GetAll pasa el contexto sin deadline al
repositorio. Bajo carga de la base de datos, las queries se quedan
colgadas indefinidamente. El connection pool agota sus 10 conexiones,
y los requests subsiguientes reciben un error "connection pool
exhausted" que sale como un 500.

Corrección: Agregar context.WithTimeout en la capa de servicio, agregar
retry con backoff en el repositorio, incrementar el tamaño del pool,
agregar wrapping de error estructurado en el handler.
```

### Paso 8: Persistir el hallazgo

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

### Resumen del Flujo de Datos

```
recall ──▶ (sin contexto previo)
              │
search ──▶ handler/users.go, service/user_service.go, db/pool.go
              │
read_symbol ──▶ código fuente de UserHandler.List
              │
get_references ──▶ 3 sitios de llamada para UserService.GetAll
              │
read_symbol ──▶ código fuente de UserService.GetAll (sin timeout visible)
              │
build_context ──▶ contexto completo de la capa DB (config del pool, impl del repo)
              │
    ┌─────────┘
    ▼
Razonamiento del agente: sin deadline + pool chico = agotamiento bajo carga
    │
    ▼
El agente escribe la corrección ──▶ 4 archivos modificados
    │
    ▼
remember ──▶ persistido para sesiones futuras
```

---

## Historial de Sesión

DevAI rastrea cada llamada a herramientas dentro de una sesión. Los agentes pueden revisar sus propias acciones usando `get_session_history`.

**Por qué importa:** Cuando el contexto de un agente se compacta (la conversación del LLM se resume para ahorrar tokens), pierde el registro detallado de lo que hizo. El historial de sesión provee un log de verdad absoluta que sobrevive a la compactación.

**Qué se registra:**

- Nombre de la herramienta y parámetros de cada llamada
- Timestamp
- Resumen del resultado
- ID de sesión (estable durante la sesión, cambia entre sesiones)

**Uso típico:**

```json
{
  "tool": "get_session_history",
  "params": {
    "session_id": "current",
    "limit": 20
  }
}
```

Esto devuelve las últimas 20 llamadas a herramientas en la sesión actual. El agente puede usar esto para:

- Verificar que no haya buscado algo ya (evitar trabajo duplicado)
- Revisar qué encontró antes de que el contexto se compactara
- Construir un resumen de acciones tomadas para el usuario

---

## Configurando el Acceso del Agente

### Setup Automático

```bash
devai server configure claude    # Claude Code
devai server configure cursor    # Cursor
devai server configure --all     # Todos los clientes detectados
```

Esto escribe la entrada del servidor MCP en el archivo de configuración del cliente. El agente puede llamar a las herramientas de DevAI inmediatamente — sin necesidad de editar JSON manualmente.

### Qué Se Configura

- Comando del servidor MCP: `devai server mcp`
- Directorio de trabajo: la raíz del repositorio actual
- Entorno: `DEVAI_STATE_DIR` apuntando a `.devai/state/`
- Modo de almacenamiento: auto-detectado desde `.devai/config.yaml`

### Verificando la Conexión

Después de la configuración, pedile al agente que ejecute una llamada de herramienta simple:

```
"Buscá la función main en este codebase"
```

Si DevAI está conectado, el agente va a llamar a `search(query: "main function entry point")` y devolver resultados. Si no, va a caer a lecturas de archivo — una señal clara de que la conexión MCP no está activa.

---

> **DevAI está en alpha.** Los parámetros de herramientas y los schemas de respuesta pueden cambiar entre versiones. Consultá la [Referencia de Herramientas MCP](mcp-tools.md) para los schemas actuales.
