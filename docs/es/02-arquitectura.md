> 🌐 [English version](../02-architecture.md)

# Arquitectura de DevAI

## 1. Visión General del Sistema

DevAI es un Motor de Inteligencia de Código con IA híbrido Go + Python. La capa de Go provee una CLI rápida de un solo binario, un servidor MCP para integración con agentes de IA, y todas las interfaces de cara al usuario (TUI, comandos CLI, git hooks). La capa de Python provee el pipeline de ML: parseo de código via tree-sitter, chunking semántico consciente del AST, generación de embeddings, almacenamiento vectorial y construcción de grafos. Las dos capas se comunican exclusivamente via JSON-RPC 2.0 sobre pipes de stdio — Go lanza Python como un subproceso, envía requests a su stdin y lee respuestas de su stdout. No hay capa de red entre ellos. Este diseño híbrido existe porque Go compila a un solo binario con arranque instantáneo (crítico para la UX de CLI), mientras que Python tiene el único ecosistema maduro para bindings de tree-sitter, sentence-transformers y LanceDB.

## 2. Diagrama de Arquitectura

```
                         AI Agent (Claude, Cursor, etc.)
                                     |
                                     | MCP Protocol (stdio)
                                     |
                    +=====================================+
                    |         GO BINARY (devai)           |
                    |                                     |
                    |  +-------------------------------+  |
                    |  |       MCP Server (14 tools)    |  |
                    |  |    internal/mcp/server.go      |  |
                    |  +-------------------------------+  |
                    |          |                           |
                    |  +-------+------+  +-----------+    |
                    |  | ML Client    |  | Branch    |    |
                    |  | (JSON-RPC)   |  | Context   |    |
                    |  | mlclient/    |  | branch/   |    |
                    |  +-------+------+  +-----------+    |
                    |          |         +-----------+    |
                    |          |         | Storage   |    |
                    |          |         | Router    |    |
                    |          |         | storage/  |    |
                    |          |         +-----------+    |
                    |  +-------+------+  +-----------+    |
                    |  | Python       |  | Session   |    |
                    |  | Runtime      |  | Tracker   |    |
                    |  | Discovery    |  | session/  |    |
                    |  | runtime/     |  +-----------+    |
                    |  +--------------+                   |
                    |                                     |
                    |  +-------------------------------+  |
                    |  |  CLI (Cobra)                   |  |
                    |  |  init, index, search, server,  |  |
                    |  |  watch, tui, hooks, push/pull, |  |
                    |  |  sync-index, server configure   |  |
                    |  +-------------------------------+  |
                    |                                     |
                    |  +-------------------------------+  |
                    |  |  TUI (Bubbletea)               |  |
                    |  |  internal/tui/                  |  |
                    |  +-------------------------------+  |
                    +================+====================+
                                     |
                                     | JSON-RPC 2.0 (stdio pipes)
                                     | stdin/stdout of subprocess
                                     |
                    +================+====================+
                    |      PYTHON PROCESS (devai_ml)      |
                    |                                     |
                    |  +-------------------------------+  |
                    |  |  JSON-RPC Server (~900 lines)  |  |
                    |  |  ml/devai_ml/server.py          |  |
                    |  +------+------+---------+-------+  |
                    |         |      |         |          |
                    |  +------+--+ +-+------+ ++-------+  |
                    |  | Parsers | |Chunker | |Embedder|  |
                    |  | tree-   | |semantic| |multi-  |  |
                    |  | sitter  | |4-level | |provider|  |
                    |  | 25+ lang| |AST-    | |(local, |  |
                    |  | + raw   | |aware   | | OpenAI,|  |
                    |  +---------+ +--------+ | Voyage)|  |
                    |                         +--------+  |
                    |  +-------------------------------+  |
                    |  |  Pipeline Orchestrator          |  |
                    |  |  git diff -> parse -> chunk     |  |
                    |  |  -> embed -> store              |  |
                    |  +------+-------+-------+--------+  |
                    |         |       |       |           |
                    +=========|=======|=======|===========+
                              |       |       |
                    +---------+--+ +--+----+ ++----------+
                    |  LanceDB   | | SQLite | |  Qdrant   |
                    |  (vectors) | | (graph,| |  (shared  |
                    |  embedded, | |  memory,| |  vectors) |
                    |  disk-     | |  index  | |  remote,  |
                    |  based)    | |  state) | |  gRPC,    |
                    |            | |  WAL    | |  optional) |
                    | .devai/    | | .devai/ | |           |
                    | state/     | | state/  | |           |
                    | vectors/   | | index.db| |           |
                    +------------+ +--------+ +-----------+
```

## 3. Profundización por Componente

### 3.1 Servidor MCP

**Qué hace:** Expone 15 herramientas a agentes de IA via el Model Context Protocol. Las herramientas incluyen `search`, `read_file`, `build_context`, `read_symbol`, `get_references`, `remember`, `recall`, `memory_context`, `memory_stats`, `get_branch_context`, `switch_context`, `get_session_history`, `index_status`, `index_repo` y `reindex_memories`.

**Por qué existe:** Sin el servidor MCP, los agentes de IA no tienen forma de consultar la inteligencia de código indexada de DevAI. El protocolo MCP es agnóstico al agente — cualquier cliente compatible con MCP (Claude Desktop, Cursor, Cline, agentes personalizados) puede conectarse sin código de integración personalizado.

**Archivos clave:**
- `internal/mcp/server.go` (~730 líneas) — Registro de herramientas, manejo de requests, formateo de respuestas

**Cómo se conecta:** El servidor MCP mantiene una referencia a `mlclient.StdioClient`. Cada handler de herramienta traduce las llamadas de herramientas MCP en requests JSON-RPC, los envía al servicio Python ML y formatea la respuesta de vuelta como resultados de herramientas MCP. El servidor corre en modo stdio (`ServeStdio()`), leyendo mensajes MCP de su propio stdin y escribiendo a su propio stdout.

### 3.2 Cliente ML (JSON-RPC)

**Qué hace:** Gestiona el ciclo de vida del subproceso Python y provee una interfaz tipada en Go para enviar requests JSON-RPC 2.0 al servicio ML. Maneja la serialización de requests, deserialización de respuestas e IDs de request atómicos.

**Por qué existe:** Sin el cliente ML, Go tendría que embutir Python (pesadilla de CGO), usar gRPC (requiere compilación de protos y un servidor corriendo), o hacer shell-out por request (latencia inaceptable). El modelo de subproceso por stdio da comunicación sin dependencia de red con semántica de conexión persistente.

**Archivos clave:**
- `internal/mlclient/client.go` — Struct `StdioClient`, método `Call()`, spawn del subproceso
- `internal/runtime/python.go` — Resolución del binario Python (cadena de prioridad de 6 pasos)

**Cómo se conecta:** Tanto la CLI como el servidor MCP instancian `StdioClient`. En el primer uso, lanza el proceso Python usando el binario Python resuelto, configura pipes de stdin/stdout, y mantiene el proceso vivo durante toda la duración del proceso Go. Todas las llamadas subsiguientes se multiplexan sobre los mismos pipes con un mutex.

### 3.3 Descubrimiento del Runtime de Python

**Qué hace:** Resuelve el binario Python correcto para lanzar el servicio ML. Usa una cadena de resolución priorizada de 6 pasos:

1. Variable de entorno `DEVAI_PYTHON` (override explícito)
2. Archivo de configuración: `.devai/config.yaml` `runtime.python_path`
3. Ubicación instalada: `~/.local/share/devai/python/venv/bin/python` (o `LOCALAPPDATA` en Windows)
4. Relativo al ejecutable: `{binary_dir}/../ml/.venv/bin/python`
5. Relativo al cwd: `ml/.venv/bin/python`
6. Fallback del sistema: `python3` (Linux/macOS) o `python` (Windows)

**Por qué existe:** DevAI tiene que funcionar en múltiples escenarios de deployment — desarrollo (checkout del source), binario instalado (releases de GitHub), CI/CD y contenedores. Cada escenario coloca el venv de Python en una ubicación distinta. Sin el descubrimiento de runtime, los usuarios necesitarían configuración manual en cada entorno.

**Archivos clave:**
- `internal/runtime/python.go` — Función `FindPython()`

### 3.4 Contexto de Branch

**Qué hace:** Gestiona la resolución de búsqueda consciente de branches. Construye una cadena de linaje (branch actual -> padre -> main) y provee filtros de branch para consultas al vector store. Soporta cambio de branch virtual sin `git checkout`.

**Por qué existe:** Sin contexto de branch, la búsqueda devolvería solo resultados del branch main (perdiendo código de feature branches) o devolvería todo (contaminando resultados con branches no relacionados). El modelo de overlay permite que la búsqueda recorra el linaje: chunks del feature-branch sobreescriben chunks del main-branch para el mismo archivo, sin duplicar el índice entero.

**Archivos clave:**
- `internal/branch/context.go` — Struct `Context`, `BranchFilter()`, `SwitchBranch()`

**Cómo se conecta:** El servidor MCP crea un `Context` cuando se llama a `switch_context`. Los filtros de branch se pasan como metadata al servicio Python ML en cada request de búsqueda.

### 3.5 Router de Almacenamiento

**Qué hace:** Selecciona entre modos de almacenamiento local, compartido e híbrido. Enruta las operaciones de almacenamiento al backend apropiado según la configuración.

**Por qué existe:** Los desarrolladores solos quieren almacenamiento embebido únicamente (cero setup). Los equipos quieren una instancia Qdrant compartida (búsqueda entre desarrolladores). El router abstrae esto para que las capas superiores nunca hagan branching sobre el modo de almacenamiento.

**Archivos clave:**
- `internal/storage/router.go` — Struct `Router`, `IsLocal()`, `IsShared()`

**Modos:**
- `local` — Solo LanceDB + SQLite (por defecto, cero configuración)
- `shared` — Solo Qdrant (servidor de equipo)
- `hybrid` — Escribe en ambos, la búsqueda mergea resultados. Degradación graceful: si Qdrant no es alcanzable, cae a local-only sin error.

### 3.6 Servidor JSON-RPC (Python)

**Qué hace:** Recibe requests JSON-RPC 2.0 por stdin, los despacha al método handler apropiado y escribe respuestas JSON-RPC a stdout. Es la contraparte Python de `mlclient.StdioClient`.

**Por qué existe:** Despacho central para todas las operaciones ML. Provee una API estable basada en métodos que la capa Go invoca, aislando a Go de los detalles de implementación de Python.

**Archivos clave:**
- `ml/devai_ml/server.py` (~900 líneas) — Clase `MLService`, dispatcher `handle_request()`

**Cómo se conecta:** Se inicializa con todos los stores y componentes (proveedor de embeddings, registro de parsers, chunker, orquestador de pipeline). Cada método JSON-RPC mapea a una operación interna: `index` llama al orquestador de pipeline, `search` llama al vector store, `remember` llama al memory store.

### 3.7 Parsers

**Qué hace:** Extrae AST estructurado de archivos fuente. Usa tree-sitter para más de 25 lenguajes (Go, Python, TypeScript, Rust, Java, C, C++, Ruby, etc.) y un parser raw de fallback para lenguajes sin gramáticas de tree-sitter (HTML, CSS, Markdown, etc.).

**Por qué existe:** Sin parseo a nivel de AST, el chunker cortaría código en límites arbitrarios de línea, rompiendo definiciones de funciones a la mitad del cuerpo. tree-sitter provee acceso AST agnóstico al lenguaje con parseo consistentemente rápido (basado en C).

**Archivos clave:**
- `ml/devai_ml/parsers/treesitter_parser.py` — Integración con tree-sitter
- `ml/devai_ml/parsers/raw_parser.py` — Fallback para lenguajes no soportados
- `ml/devai_ml/parsers/registry.py` — Detección de lenguaje y selección de parser
- `ml/devai_ml/parsers/queries/` — Archivos de query tree-sitter por lenguaje

### 3.8 Chunker Semántico

**Qué hace:** Divide archivos fuente parseados en chunks semánticamente significativos usando una jerarquía de 4 niveles:

1. **Nivel de módulo** — Declaraciones de paquete/módulo, imports
2. **Nivel de clase/tipo** — Definiciones de struct, class, interface
3. **Nivel de función** — Cuerpos de funciones/métodos
4. **Nivel de bloque** — Funciones grandes divididas en límites lógicos

**Por qué existe:** La calidad de los embeddings se degrada marcadamente cuando los chunks contienen código no relacionado. El chunking consciente del AST asegura que cada chunk represente una única unidad semántica (una función, una clase), produciendo embeddings que representan con precisión el significado de esa unidad. Nunca corta a mitad de un símbolo.

**Archivos clave:**
- `ml/devai_ml/chunking/semantic_chunker.py` — Clase `SemanticChunker`

### 3.9 Embeddings

**Qué hace:** Convierte chunks de código en representaciones vectoriales densas. Soporta múltiples proveedores via un patrón factory:

- **Local** (`sentence-transformers`): Por defecto. No se necesita API key. Corre en CPU/GPU.
- **OpenAI** (`text-embedding-3-small/large`): Alojado, mayor calidad para lenguaje natural.
- **Voyage** (`voyage-code-2`): Especializado en código, mayor precisión en búsqueda de código.
- **Custom**: Endpoint de embeddings provisto por el usuario.

**Por qué existe:** Distintos equipos tienen distintas restricciones. Los desarrolladores solos quieren embeddings locales sin API key. Los equipos enterprise quieren Voyage para máxima precisión. La abstracción de proveedores permite cambiar sin tocar ningún otro componente.

**Archivos clave:**
- `ml/devai_ml/embeddings/base.py` — Clase base abstracta `EmbeddingProvider`
- `ml/devai_ml/embeddings/local.py` — Proveedor sentence-transformers
- `ml/devai_ml/embeddings/openai_embed.py` — Proveedor OpenAI
- `ml/devai_ml/embeddings/voyage_embed.py` — Proveedor Voyage
- `ml/devai_ml/embeddings/custom.py` — Endpoint definido por el usuario
- `ml/devai_ml/embeddings/factory.py` — Selección de proveedor desde config

### 3.10 Orquestador de Pipeline

**Qué hace:** Coordina el pipeline de indexación completo: detectar archivos modificados via `git diff`, parsear cada archivo, chunkear el AST, generar embeddings y escribir al vector store. Maneja indexación incremental (solo archivos modificados) y reindexación completa (al cambiar de modelo o forzar).

**Por qué existe:** Sin orquestación, cada componente necesitaría conocer a cada otro componente. El pipeline encapsula el flujo entero para que los callers (comando CLI `index`, herramienta MCP `index_repo`) simplemente digan "indexá este repo" y el orquestador se encargue del resto.

**Archivos clave:**
- `ml/devai_ml/pipeline/orchestrator.py` — Clase `IndexPipeline`
- `ml/devai_ml/pipeline/git_state.py` — Detección de diff via Git

### 3.11 Vector Store (LanceDB)

**Qué hace:** Almacena y consulta embeddings de chunks de código usando LanceDB, una base de datos vectorial columnar embebida. Soporta búsqueda ANN (approximate nearest neighbor) filtrada con filtros de metadata (repo, branch, lenguaje, ruta de archivo).

**Por qué existe:** LanceDB es embebida (sin proceso servidor), basada en disco (maneja repos más grandes que la RAM), y soporta búsqueda vectorial filtrada. Este es el store por defecto que funciona con cero configuración.

**Archivos clave:**
- `ml/devai_ml/stores/vector_store.py` — Clase `LanceDBVectorStore`

**Ubicación de datos:** `.devai/state/vectors/`

### 3.12 Store Qdrant

**Qué hace:** Almacena y consulta embeddings en un servidor Qdrant remoto via gRPC. Se usa en modo compartido/equipo donde múltiples desarrolladores necesitan buscar en el mismo índice.

**Archivos clave:**
- `ml/devai_ml/stores/qdrant_store.py` — Clase `QdrantStore`

### 3.13 Store Híbrido

**Qué hace:** Store de escritura write-through que escribe tanto a LanceDB como a Qdrant simultáneamente. En búsqueda, consulta ambos y mergea resultados. Si Qdrant no es alcanzable, degrada silenciosamente a local-only.

**Archivos clave:**
- `ml/devai_ml/stores/hybrid_store.py` — Clase `HybridStore`

### 3.14 Store de Grafo

**Qué hace:** Mantiene un grafo de relaciones de código usando una lista de adyacencia en SQLite. Almacena aristas como "la función A llama a la función B", "el archivo X importa el módulo Y". Usado por `get_references` y `build_context` para recorrer grafos de llamadas y cadenas de dependencias.

**Por qué existe:** La búsqueda vectorial encuentra código semánticamente similar pero no puede responder preguntas estructurales como "¿qué llama a esta función?" o "¿qué depende de este módulo?". El store de grafo provee relaciones estructurales exactas.

**Archivos clave:**
- `ml/devai_ml/stores/graph_store.py` — Clase `SQLiteGraphStore`

**Ubicación de datos:** `.devai/state/index.db` (base de datos SQLite compartida)

### 3.15 Store de Memoria

**Qué hace:** Memoria persistente para sesiones de agentes de IA. Almacena memorias con metadata (type, scope, project, topic_key), las embebe para recall semántico, y maneja deduplicación via hashing de contenido normalizado con una ventana de 15 minutos. Soporta upserts basados en topic_key: si existe una memoria con el mismo topic_key, actualiza en lugar de crear un duplicado.

**Por qué existe:** Los agentes de IA pierden el contexto entre sesiones. El store de memoria permite a los agentes persistir decisiones, descubrimientos y resúmenes de sesión, para luego hacer recall semántico en sesiones futuras.

**Archivos clave:**
- `ml/devai_ml/stores/memory_store.py` — Clase `MemoryStore`, dataclass `Memory`

**Ubicación de datos:** `.devai/state/index.db` (base de datos SQLite compartida)

### 3.16 Estado del Índice

**Qué hace:** Rastrea qué commits han sido indexados para cada combinación de repo/branch. Habilita la indexación incremental al decirle al orquestador de pipeline "el último commit indexado fue X, hacé diff desde X hasta HEAD".

**Archivos clave:**
- `ml/devai_ml/stores/index_state.py` — Clase `IndexStateStore`

### 3.17 Factory de Stores

**Qué hace:** Crea el vector store apropiado basándose en la configuración del entorno (`DEVAI_STORAGE_MODE`, variables de entorno de conexión a Qdrant). Retorna un `LanceDBVectorStore`, `QdrantStore` o `HybridStore`.

**Archivos clave:**
- `ml/devai_ml/stores/factory.py` — `StorageConfig`, `create_storage_config_from_env()`, `create_vector_store()`

### 3.18 Tracking de Sesiones

**Qué hace:** Rastrea sesiones activas de agentes con timestamps. Usado por `get_session_history` para proveer a los agentes contexto sobre interacciones recientes.

**Archivos clave:**
- `internal/session/session.go`

### 3.19 TUI

**Qué hace:** Interfaz de usuario de terminal construida con Bubbletea. Provee búsqueda interactiva, navegación de resultados y visualización del estado del índice.

**Archivos clave:**
- `internal/tui/model.go` — Modelo Bubbletea
- `internal/tui/view.go` — Renderizado de vistas
- `internal/tui/update.go` — Manejo de mensajes
- `internal/tui/styles.go` — Estilos Lipgloss

## 4. Diagramas de Flujo de Datos

### 4.1 Pipeline de Indexación

```
El usuario ejecuta: devai index /path/to/repo
            |
            v
   +--------+---------+
   |  CLI index cmd    |
   |  cmd/devai/cmd/   |
   |  index.go         |
   +--------+----------+
            |
            | JSON-RPC: "index"
            | params: {repo, branch, force}
            v
   +--------+----------+
   |  Pipeline          |
   |  Orchestrator      |
   +--------+----------+
            |
            | Paso 1: ¿Qué cambió?
            v
   +--------+----------+
   |  Git State         |
   |  git diff          |
   |  last_commit..HEAD |
   +--------+----------+
            |
            | lista de archivos modificados
            v
   +--------+----------+
   |  Parser Registry   |
   |  detectar lenguaje |
   |  seleccionar parser|
   +--------+----------+
            |
            | Paso 2: Parsear a AST
            v
   +--------+----------+
   |  Tree-sitter /     |
   |  Raw Parser        |
   |  archivo -> nodos  |
   |  AST               |
   +--------+----------+
            |
            | nodos AST con metadata
            v
   +--------+----------+
   |  Semantic Chunker  |
   |  división 4 niveles|
   |  módulo > clase >  |
   |  función > bloque  |
   +--------+----------+
            |
            | chunks con límites
            v
   +--------+----------+
   |  Embedding         |
   |  Provider          |
   |  chunks -> vectores|
   +--------+----------+
            |
            | vectores + metadata
            v
   +--------+----------+         +----------------+
   |  Vector Store      +-------->  LanceDB       |
   |  (+ Graph Store)   |        | .devai/state/  |
   +--------+-----------+        | vectors/       |
            |                    +----------------+
            | aristas del grafo
            v
   +--------+----------+         +----------------+
   |  Graph Store       +-------->  SQLite         |
   |  aristas call/     |        | .devai/state/  |
   |  import            |        | index.db       |
   +-------------------+         +----------------+
            |
            v
   +--------+----------+
   |  Index State       |
   |  registrar commit  |
   |  SHA indexado      |
   +--------------------+
```

### 4.2 Consulta de Búsqueda

```
El agente de IA envía llamada MCP: search(query="auth middleware", limit=5)
            |
            | MCP protocol (stdio)
            v
   +--------+----------+
   |  MCP Server        |
   |  handleSearch()    |
   +--------+----------+
            |
            | Resolver contexto de branch
            v
   +--------+----------+
   |  Branch Context    |
   |  linaje:           |
   |  feature/auth ->   |
   |  develop -> main   |
   +--------+----------+
            |
            | JSON-RPC: "search"
            | params: {query, branches: [...], limit: 5}
            v
   +--------+----------+
   |  ML Service        |
   |  handle_request()  |
   +--------+----------+
            |
            | Paso 1: Embeber la consulta
            v
   +--------+----------+
   |  Embedding         |
   |  Provider          |
   |  "auth middleware"  |
   |  -> [0.12, -0.34,  |
   |      0.56, ...]    |
   +--------+----------+
            |
            | vector de consulta
            v
   +--------+----------+
   |  Vector Store      |
   |  búsqueda ANN con  |
   |  filtro de branch  |
   |  top-k por         |
   |  similitud coseno  |
   +--------+----------+
            |
            | chunks rankeados con puntajes
            v
   +--------+----------+
   |  MCP Server        |
   |  formatear como    |
   |  resultado MCP     |
   +--------+----------+
            |
            | respuesta MCP (stdio)
            v
         El agente de IA recibe resultados:
         [{file, lines, content, score}, ...]
```

### 4.3 Ciclo de Vida de la Memoria

```
El agente de IA llama: remember(title="Fixed N+1 in UserList",
                         type="bugfix", content="...",
                         project="myapp", topic_key="bugfix/user-n1")
            |
            | MCP protocol (stdio)
            v
   +--------+----------+
   |  MCP Server        |
   |  handleRemember()  |
   +--------+----------+
            |
            | JSON-RPC: "remember"
            v
   +--------+----------+
   |  Memory Store      |
   +--------+----------+
            |
            | Paso 1: Chequeo de deduplicación
            | hash = sha256(normalize(content))
            | Verificar: ¿mismo hash en ventana de 15 min?
            |   SÍ -> saltar (devolver ID existente)
            |   NO -> continuar
            |
            | Paso 2: Chequeo de upsert por topic key
            | Verificar: ¿memoria existente con mismo topic_key?
            |   SÍ -> UPDATE fila existente
            |   NO -> INSERT nueva fila
            |
            | Paso 3: Embeber la memoria
            v
   +--------+----------+
   |  Embedding         |
   |  Provider          |
   |  content -> vector |
   +--------+----------+
            |
            | Paso 4: Almacenar
            v
   +--------+----------+         +----------------+
   |  SQLite            |        | tabla memory:  |
   |  (datos            +------->| id, title,     |
   |  estructurados)    |        | type, content, |
   +--------+-----------+        | topic_key,     |
            |                    | hash, created  |
            v                    +----------------+
   +--------+----------+         +----------------+
   |  Vector Store      +-------->  LanceDB       |
   |  (vectores de      |        | vectores de    |
   |   memoria)         |        | memoria        |
   +--------------------+        +----------------+

            --- Más tarde, el agente llama recall ---

El agente de IA llama: recall(query="N+1 query fix", project="myapp")
            |
            v
   +--------+----------+
   |  Memory Store      |
   +--------+----------+
            |
            | Paso 1: Embeber la consulta
            | Paso 2: Búsqueda vectorial en vectores de memoria
            | Paso 3: Filtrar por proyecto
            | Paso 4: Devolver contenido completo (sin truncar)
            v
         El agente de IA recibe:
         [{title, type, content, created, score}, ...]
```

## 5. Arquitectura de Almacenamiento

### 5.1 Modo Local (Por Defecto)

```
.devai/
  state/
    vectors/          <-- LanceDB (embebido, columnar)
      code.lance/     <-- Embeddings de chunks de código
      memory.lance/   <-- Embeddings de memoria
    index.db          <-- SQLite (modo WAL)
                          Tablas: graph_edges, memories, index_state
```

- **Cuándo usar:** Desarrollador solo, una sola máquina
- **Pros:** Cero configuración, sin dependencias externas, funciona offline
- **Contras:** No se puede compartir entre desarrolladores

### 5.2 Modo Compartido

```
                    +-------------------+
                    |   Qdrant Server   |
                    |   (remoto, gRPC)  |
                    |   collections:    |
                    |     code_chunks   |
                    |     memories      |
                    +-------------------+

Máquina local:
.devai/
  state/
    index.db          <-- SQLite (grafo + estado del índice siguen siendo locales)
```

- **Cuándo usar:** Equipo con infraestructura compartida
- **Pros:** Todos los desarrolladores buscan en el mismo índice, sin indexación duplicada
- **Contras:** Requiere deployment de Qdrant, dependencia de red

### 5.3 Modo Híbrido

```
Ruta de escritura:
  chunk --> LanceDB (local)
       \-> Qdrant  (remoto)    [write-through]

Ruta de búsqueda:
  query --> LanceDB (local)  --> merge + deduplicar
       \-> Qdrant  (remoto) -/

Degradación:
  ¿Qdrant caído? --> local-only (silencioso, sin error)
  ¿Qdrant volvió? --> retomar escrituras (sin intervención manual)
```

- **Cuándo usar:** Equipo que quiere resiliencia
- **Pros:** Funciona offline, compartido cuando está conectado, failover automático
- **Contras:** Doble costo de almacenamiento, leve latencia de escritura

### 5.4 IDs Determinísticos

Todos los chunks usan IDs determinísticos: `sha256(repo:branch:file:start_line)`. Esto garantiza:
- Re-indexar el mismo código produce el mismo ID (upsert verdadero, sin duplicados)
- Consistencia entre stores: las entradas de LanceDB y Qdrant para el mismo chunk tienen el mismo ID
- Filtrado por tombstone: los chunks eliminados se identifican por ID sin escaneo

## 6. Protocolo de Comunicación

### 6.1 Go -> Python: JSON-RPC 2.0 sobre stdio

El `StdioClient` de Go escribe requests JSON-RPC al stdin del subproceso Python y lee respuestas de su stdout. Cada request/respuesta es una única línea JSON terminada en `\n`.

**Formato de request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "search",
  "params": {
    "query": "authentication middleware",
    "repo": "/home/user/myproject",
    "branch": "main",
    "limit": 10,
    "language": "go"
  }
}
```

**Respuesta exitosa:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "chunks": [
      {
        "file": "internal/auth/middleware.go",
        "start_line": 15,
        "end_line": 42,
        "content": "func AuthMiddleware(next http.Handler) ...",
        "score": 0.89,
        "language": "go",
        "symbol": "AuthMiddleware"
      }
    ]
  }
}
```

**Respuesta de error:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32602,
    "message": "Repository not indexed: /home/user/myproject"
  }
}
```

### 6.2 Agente -> Go: Protocolo MCP sobre stdio

El servidor MCP usa la librería `mcp-go` (`github.com/mark3labs/mcp-go`). Los agentes de IA se conectan a DevAI configurándolo como un servidor MCP que se comunica por stdio. El protocolo MCP maneja el listado de herramientas, las llamadas a herramientas y las respuestas.

### 6.3 Threading de Requests

El `StdioClient` usa un mutex (`sync.Mutex`) para serializar las llamadas JSON-RPC. Solo un request está en vuelo a la vez. Los IDs de request se incrementan atómicamente (`atomic.Int64`). Esto es suficiente porque las llamadas de herramientas MCP desde los agentes son inherentemente secuenciales.

## 7. Decisiones de Diseño

### 7.1 Arquitectura Híbrida Go + Python

**Qué:** CLI e infraestructura en Go, pipeline ML en Python.

**Por qué:** Go compila a un solo binario estático con arranque instantáneo (<50ms), crítico para herramientas CLI y git hooks. Python tiene el único ecosistema maduro para bindings de tree-sitter, sentence-transformers, LanceDB y clientes Qdrant. Escribir embeddings en Go significaría reimplementar o usar CGO (frágil, derrota el objetivo de binario único).

**Tradeoffs:** Dos runtimes para instalar y gestionar. El venv de Python agrega ~500MB al disco. El spawn del subproceso agrega ~1-2s en la primera llamada (amortizado a lo largo de la sesión).

**Alternativas consideradas:** Go puro (sin ecosistema ML), Python puro (CLI lenta, sin binario único), gRPC (protocolo más pesado, requiere compilación de protos, gestión de puertos).

### 7.2 JSON-RPC sobre stdio (no gRPC, no HTTP)

**Qué:** Go lanza Python como subproceso y se comunica via pipes stdin/stdout usando JSON-RPC 2.0.

**Por qué:** Sin conflictos de puertos, sin configuración de red, sin setup de TLS, sin service discovery. El ciclo de vida del proceso Python está atado al proceso Go — sin procesos huérfanos. Funciona idénticamente en Linux, macOS y Windows.

**Tradeoffs:** Comunicación single-threaded (serializada con mutex). Sin soporte de streaming (cada respuesta es completa). No se puede escalar Python horizontalmente.

**Alternativas consideradas:** gRPC (requiere compilación de protos, gestión de puertos, stubs de proto), HTTP REST (conflictos de puertos, gestión de ciclo de vida de procesos), memoria compartida (específico de plataforma, complejo).

### 7.3 LanceDB como Vector Store por Defecto

**Qué:** Base de datos vectorial columnar embebida, sin proceso servidor requerido.

**Por qué:** Experiencia de cero configuración. `devai init && devai index` simplemente funciona sin instalar ni correr un servidor de base de datos. Almacenamiento basado en disco maneja repositorios más grandes que la RAM. Búsqueda ANN nativa con filtrado de metadata.

**Tradeoffs:** Acceso de proceso único (sin escritores concurrentes desde procesos diferentes). No accesible por red (de ahí Qdrant para modo compartido).

**Alternativas consideradas:** ChromaDB (más pesado, modo servidor por defecto), FAISS (sin filtrado de metadata, solo en memoria), Qdrant solo (requiere setup de servidor para uso básico), Milvus (pesado, overkill para uso de un solo desarrollador).

### 7.4 SQLite para Datos Estructurados (modo WAL)

**Qué:** Aristas del grafo, memorias y estado del índice almacenados en una única base de datos SQLite.

**Por qué:** Cero configuración, probado en batalla, modo WAL para rendimiento de lectura concurrente, backup de un solo archivo. Se alinea con la filosofía "sin servidor" del modo local.

**Tradeoffs:** Restricción de escritor único (aceptable para herramienta de usuario único). No apto para deployments multi-servidor.

**Alternativas consideradas:** PostgreSQL (requiere servidor), DuckDB (no diseñado para OLTP), archivos JSON separados (sin capacidad de consulta, sin atomicidad).

### 7.5 IDs de Chunk Determinísticos via sha256

**Qué:** Cada ID de chunk es `sha256(repo:branch:file:start_line)`.

**Por qué:** Habilita upserts verdaderos: re-indexar el mismo código produce el mismo ID, así el store sobreescribe en lugar de duplicar. Consistencia entre stores entre LanceDB y Qdrant. Filtrado por tombstone sin escaneos completos.

**Tradeoffs:** Si los números de línea se desplazan (insertar líneas arriba de una función), la misma función obtiene un nuevo ID. Mitigado al reindexar el archivo completo en cada cambio, lo que reemplaza todos los IDs viejos.

**Alternativas consideradas:** UUID (sin dedup, requiere delete-before-insert), hash de contenido (mismo código en distintas ubicaciones colisionaría), solo ruta de archivo (no puede manejar múltiples chunks por archivo).

### 7.6 Búsqueda por Overlay de Branches

**Qué:** La búsqueda recorre el linaje de branches (feature -> develop -> main) en lugar de mantener índices separados por branch.

**Por qué:** Evita duplicar el índice entero por cada branch. Un índice de feature branch solo contiene chunks que difieren de su padre. La búsqueda mergea resultados de todas las capas del linaje, con branches hijos tomando prioridad (sobreescribiendo chunks del padre para el mismo archivo/ubicación).

**Tradeoffs:** El cómputo de linaje requiere operaciones git. Lógica de merge compleja para chunks en conflicto.

**Alternativas consideradas:** Índice completo por branch (explosión de almacenamiento), indexar solo main (pierde código de feature branches), filtrado basado en tags (sin jerarquía).

### 7.7 Chunking Consciente del AST (Nunca Cortar a Mitad de Símbolo)

**Qué:** El código se divide en los límites del AST (función, clase, módulo), nunca en conteos arbitrarios de líneas.

**Por qué:** Un chunk que contiene la mitad de una función produce un embedding sin sentido. Los límites conscientes del AST aseguran que cada chunk represente una unidad semántica completa, produciendo embeddings que capturan con precisión el propósito de esa unidad.

**Tradeoffs:** Requiere gramáticas tree-sitter por lenguaje (más de 25 mantenidas). Funciones muy largas pueden producir chunks grandes (mitigado por el splitting a nivel de bloque).

**Alternativas consideradas:** Chunking de tamaño fijo (rápido pero límites sin sentido), splitting basado en regex (frágil entre lenguajes), splitting por conteo de caracteres (peor que basado en líneas).

### 7.8 Indexación Incremental via git diff

**Qué:** Al ejecutar `devai index`, solo los archivos modificados desde el último commit indexado se re-parsean, re-chunkean y re-embeben.

**Por qué:** La reindexación completa de un repositorio grande toma minutos. La indexación incremental toma segundos. El pipeline almacena el SHA del último commit indexado en `IndexStateStore` y hace diff desde ahí.

**Tradeoffs:** Requiere rastrear el estado del índice por repo/branch. La reindexación completa sigue siendo necesaria al cambiar de modelo de embeddings (un modelo diferente produce vectores incompatibles).

**Alternativas consideradas:** Siempre reindexación completa (demasiado lento para repos grandes), basado en mtime de archivo (no confiable con operaciones git), inotify/fswatch (específico de plataforma, pierde operaciones git).

### 7.9 MCP Agnóstico al Agente

**Qué:** DevAI expone herramientas via el Model Context Protocol, no una API personalizada.

**Por qué:** MCP es el estándar emergente para integración de herramientas de agentes de IA. Al implementar MCP, DevAI funciona con Claude Desktop, Cursor, Cline y cualquier futuro agente compatible con MCP sin escribir integraciones personalizadas para cada uno.

**Tradeoffs:** MCP aún está evolucionando (el protocolo puede cambiar). El transporte solo por stdio limita las opciones de deployment (sin servidor MCP remoto todavía).

**Alternativas consideradas:** API REST personalizada (requiere integración por agente), formato de function calling de OpenAI (específico del vendor), Language Server Protocol (diseñado para editores, no para agentes de IA).

### 7.10 Deduplicación de Memoria (Hash Normalizado + Ventana de 15 min)

**Qué:** Antes de almacenar una memoria, se normaliza el contenido (minúsculas, eliminar espacios en blanco), se computa sha256, y se verifica si el mismo hash fue almacenado en los últimos 15 minutos. Si es así, se omite.

**Por qué:** Los agentes de IA a menudo llaman a `remember` múltiples veces con contenido semánticamente idéntico (reintentos, resúmenes reformulados). Sin dedup, el store de memoria se llena de casi-duplicados que contaminan los resultados de recall.

**Tradeoffs:** La ventana de 15 minutos es una heurística. Memorias genuinamente diferentes con texto normalizado idéntico dentro de la ventana se descartan. La normalización puede ser demasiado agresiva para memorias con mucho código.

**Alternativas consideradas:** Dedup solo por coincidencia exacta (pierde duplicados reformulados), dedup por similitud semántica (costoso, requiere comparación de embeddings en cada guardado), sin dedup (contaminación del store).

### 7.11 Degradación Graceful en Modo Híbrido

**Qué:** En modo híbrido, si Qdrant no es alcanzable, las operaciones caen silenciosamente a local-only. Cuando Qdrant vuelve, las escrituras se retoman automáticamente.

**Por qué:** Un corte de red no debería bloquear el flujo de trabajo de un desarrollador. El store local siempre tiene una copia completa (write-through), así que la búsqueda local-only es completamente funcional.

**Tradeoffs:** Durante el downtime de Qdrant, el índice compartido queda atrasado. Otros desarrolladores consultando Qdrant ven datos stale hasta que las escrituras del desarrollador desconectado se pongan al día.

### 7.12 Upserts por Topic Key

**Qué:** Las memorias con un campo `topic_key` actualizan (upsert) en lugar de crear duplicados. Mismo topic_key = misma memoria lógica, contenido diferente = actualización de contenido.

**Por qué:** Algunas memorias representan estado que evoluciona (por ejemplo, "architecture/auth-model" evoluciona a medida que se construye el sistema de autenticación). Sin upserts, cada evolución crea una nueva fila, y recall devuelve múltiples versiones en conflicto.

**Tradeoffs:** Requiere que los callers elijan topic keys estables y significativos. Un typo en la key crea un duplicado en lugar de actualizar.

## 8. Estructura de Directorios

```
devai/
|
+-- cmd/devai/                     # Entrypoint CLI en Go
|   +-- main.go                    #   main(), comando raíz Cobra
|   +-- cmd/                       #   Subcomandos
|       +-- root.go                #     Comando raíz, flags globales
|       +-- init.go                #     devai init (crear .devai/)
|       +-- index.go               #     devai index (disparar pipeline de indexación)
|       +-- search.go              #     devai search (interfaz de búsqueda CLI)
|       +-- server.go              #     devai server (iniciar servidor MCP)
|       +-- mcp_configure.go       #     devai server configure (auto-setup MCP)
|       +-- watch.go               #     devai watch (watcher de archivos para auto-index)
|       +-- tui.go                 #     devai tui (UI de terminal interactiva)
|       +-- hooks.go               #     devai hooks (integración con git hooks)
|       +-- push_index.go          #     devai push-index (subir índice a remoto)
|       +-- pull_index.go          #     devai pull-index (descargar índice desde remoto)
|       +-- sync_index.go          #     devai sync-index (sync bidireccional)
|       +-- status.go              #     devai status (mostrar estado del índice)
|       +-- setup.go               #     devai setup (configuración inicial)
|
+-- internal/                      # Paquetes internos de Go (no importables externamente)
|   +-- mcp/
|   |   +-- server.go              #   Servidor MCP: 15 handlers de herramientas, ~730 líneas
|   +-- mlclient/
|   |   +-- client.go              #   Cliente JSON-RPC stdio para subproceso Python
|   +-- runtime/
|   |   +-- python.go              #   Resolución de binario Python en 6 pasos
|   +-- branch/
|   |   +-- context.go             #   Linaje de branch y búsqueda por overlay
|   +-- storage/
|   |   +-- router.go              #   Enrutamiento de modo local/shared/hybrid
|   +-- session/
|   |   +-- session.go             #   Tracking de sesiones para agentes
|   +-- config/
|   |   +-- ...                    #   Carga de configuración (.devai/config.yaml)
|   +-- git/
|   |   +-- ...                    #   Operaciones Git (branch actual, diff, log)
|   +-- tui/
|   |   +-- model.go               #   Modelo Bubbletea
|   |   +-- view.go                #   Renderizado de vistas
|   |   +-- update.go              #   Manejo de mensajes/eventos
|   |   +-- styles.go              #   Estilos Lipgloss
|   +-- db/
|   |   +-- ...                    #   Utilidades de base de datos
|   +-- api/
|   |   +-- ...                    #   Tipos y helpers de API
|   +-- output/
|   |   +-- ...                    #   Formateo de output (JSON, tabla, plano)
|   +-- mcpclient/
|       +-- ...                    #   Cliente MCP (para testing/integración)
|
+-- ml/                            # Capa ML en Python
|   +-- devai_ml/
|   |   +-- __init__.py
|   |   +-- server.py              #   Servidor JSON-RPC (~900 líneas), clase MLService
|   |   +-- parsers/
|   |   |   +-- registry.py        #     Detección de lenguaje, despacho de parser
|   |   |   +-- treesitter_parser.py #   Parseo AST con tree-sitter (más de 25 lenguajes)
|   |   |   +-- raw_parser.py      #     Fallback para lenguajes no soportados
|   |   |   +-- base.py            #     Clase base abstracta de Parser
|   |   |   +-- queries/           #     Archivos de query tree-sitter (.scm) por lenguaje
|   |   +-- chunking/
|   |   |   +-- semantic_chunker.py #   Chunking consciente del AST de 4 niveles
|   |   +-- embeddings/
|   |   |   +-- base.py            #     ABC de EmbeddingProvider
|   |   |   +-- factory.py         #     Selección de proveedor desde config
|   |   |   +-- local.py           #     sentence-transformers (por defecto)
|   |   |   +-- openai_embed.py    #     OpenAI text-embedding-3
|   |   |   +-- voyage_embed.py    #     Voyage voyage-code-2
|   |   |   +-- custom.py          #     Endpoint definido por el usuario
|   |   +-- pipeline/
|   |   |   +-- orchestrator.py    #     Coordinación completa del pipeline de indexación
|   |   |   +-- git_state.py       #     Detección de diff Git para índice incremental
|   |   +-- stores/
|   |   |   +-- factory.py         #     Selección de modo de almacenamiento desde env
|   |   |   +-- vector_store.py    #     Vector store LanceDB (por defecto)
|   |   |   +-- qdrant_store.py    #     Vector store remoto Qdrant
|   |   |   +-- hybrid_store.py    #     Store dual write-through
|   |   |   +-- graph_store.py     #     Lista de adyacencia SQLite (grafo de código)
|   |   |   +-- memory_store.py    #     Memorias SQLite con dedup + upserts
|   |   |   +-- index_state.py     #     Rastrear último commit indexado por repo/branch
|   |   +-- indexing/              #     Utilidades de indexación
|   |   +-- resolution/            #     Resolución de símbolos
|   |   +-- proto/                 #     Definiciones Proto (futuro gRPC)
|   +-- tests/                     #   Suite de tests Python
|
+-- proto/                         # Definiciones de Protocol Buffers (uso futuro)
+-- scripts/                       # Scripts de build/instalación/release
+-- docs/                          # Documentación
|   +-- setup.md                   #   Guía de instalación
|   +-- architecture.md            #   Resumen de arquitectura (legacy)
|   +-- 02-architecture.md         #   Este archivo
|   +-- api.md                     #   Referencia de API
|   +-- mcp-tools.md               #   Catálogo de herramientas MCP
|   +-- features.md                #   Lista de funcionalidades
|   +-- schemas.md                 #   Schemas de datos
|
+-- .devai/                        # Estado de DevAI por proyecto (gitignored)
    +-- state/
        +-- vectors/               #   Archivos de datos LanceDB
        +-- index.db               #   SQLite (grafo, memorias, estado del índice)
```
