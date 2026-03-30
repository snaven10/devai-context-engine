> 🌐 [English version](../06-extending-the-system.md)

# Extender DevAI

Esta guía cubre cómo extender DevAI con nuevas capacidades. Cada sección sigue un patrón consistente: dónde agregar código, qué interfaz implementar y cómo conectarlo.

Los puntos de extensión de DevAI son intencionalmente simples. Sin sistema de plugins, sin carga dinámica. Agregás código en el lugar correcto y lo registrás.

---

## 1. Agregar una Nueva Herramienta MCP

Una herramienta MCP requiere cambios en dos lugares: el servicio ML de Python (la lógica real) y el servidor MCP en Go (el registro de la herramienta y el reenvío de argumentos).

### Paso 1: Agregar el Handler Python de JSON-RPC

Todos los handlers de Python viven en `ml/devai_ml/server.py`. La tabla de dispatch mapea nombres de métodos a funciones handler.

```python
# In server.py — add to the _dispatch dict
"devai/your_tool": self._handle_your_tool,
```

Después escribí el handler. Cada handler recibe un dict `params` y retorna un dict de resultado:

```python
async def _handle_your_tool(self, params: dict) -> dict:
    """Your tool description."""
    repo_path = params.get("repo_path", "")
    query = params.get("query", "")

    # Do the actual work — call into indexer, store, embeddings, etc.
    results = await self._some_service.do_work(repo_path, query)

    return {
        "results": results,
        "count": len(results),
    }
```

Los handlers son async. Si tu lógica es CPU-bound, envolvela con `asyncio.to_thread`.

### Paso 2: Registrar la Herramienta en el Servidor MCP de Go

En `internal/mcp/server.go`, registrá la herramienta con su JSON Schema para los argumentos:

```go
s.addTool(mcp.Tool{
    Name:        "your_tool",
    Description: "What this tool does — one line for the agent",
    InputSchema: json.RawMessage(`{
        "type": "object",
        "properties": {
            "repo_path": {
                "type": "string",
                "description": "Absolute path to the repository"
            },
            "query": {
                "type": "string",
                "description": "What to search for"
            }
        },
        "required": ["repo_path", "query"]
    }`),
}, s.handleYourTool)
```

### Paso 3: Agregar la Función Handler en Go

El handler parsea argumentos, llama al cliente ML via JSON-RPC y formatea la respuesta:

```go
func (s *Server) handleYourTool(args map[string]interface{}) (*mcp.CallToolResult, error) {
    repoPath, _ := args["repo_path"].(string)
    query, _ := args["query"].(string)

    result, err := s.mlClient.Call("devai/your_tool", map[string]interface{}{
        "repo_path": repoPath,
        "query":     query,
    })
    if err != nil {
        return mcp.ErrorResult(fmt.Sprintf("your_tool failed: %v", err)), nil
    }

    // Format result as text content for the agent
    return mcp.TextResult(formatYourToolResult(result)), nil
}
```

El patrón es siempre el mismo: parsear args, llamar a ML, formatear respuesta. Mirá handlers existentes como `handleSearch` o `handleBuildContext` para ejemplos reales.

### Archivos Clave

| Qué | Dónde |
|------|-------|
| Handler Python + dispatch | `ml/devai_ml/server.py` |
| Registro de herramienta Go | `internal/mcp/server.go` |
| Cliente ML (llamadas JSON-RPC) | `internal/ml/client.go` |
| Tipos MCP | `internal/mcp/types.go` |

---

## 2. Agregar un Nuevo Proveedor de Embeddings

Los proveedores de embeddings implementan un protocolo definido en el paquete de embeddings. DevAI viene con local (minilm-l6) y soporta APIs compatibles con OpenAI.

### Paso 1: Implementar el Protocolo EmbeddingProvider

Creá un archivo nuevo en `ml/devai_ml/embeddings/`:

```python
# ml/devai_ml/embeddings/your_provider.py

from .base import EmbeddingProvider

class YourProvider(EmbeddingProvider):
    """Your embedding provider."""

    def __init__(self, config: dict):
        self.model_name = config.get("model", "default-model")
        self.dimension = config.get("dimension", 384)
        # Initialize client, load model, etc.

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Returns a list of float vectors, one per input text.
        Each vector must have exactly self.dimension dimensions.
        """
        # Your embedding logic here
        vectors = await self._call_model(texts)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query. May apply query-specific preprocessing."""
        result = await self.embed([text])
        return result[0]

    @property
    def dimensions(self) -> int:
        return self.dimension
```

El protocolo requiere: `embed(texts)` para embedding por lotes, `embed_query(text)` para consultas individuales, y una propiedad `dimensions`.

### Paso 2: Registrar en la Factory

En `ml/devai_ml/embeddings/factory.py`:

```python
from .your_provider import YourProvider

PROVIDERS = {
    "local": LocalProvider,
    "openai": OpenAIProvider,
    "your_provider": YourProvider,  # Add here
}

def create_provider(config: dict) -> EmbeddingProvider:
    provider_type = config.get("provider", "local")
    provider_class = PROVIDERS.get(provider_type)
    if not provider_class:
        raise ValueError(f"Unknown embedding provider: {provider_type}")
    return provider_class(config)
```

### Paso 3: Actualizar Opciones de Configuración

Los usuarios seleccionan el proveedor via `config.yaml`:

```yaml
embeddings:
  provider: your_provider
  model: your-model-name
  dimension: 768
```

### Referencia

Usá `ml/devai_ml/embeddings/local.py` como la implementación de referencia canónica. Muestra carga de modelo, batching y manejo de dimensiones.

---

## 3. Agregar Soporte de Lenguaje

DevAI usa tree-sitter para el parseo de AST. Agregar un lenguaje significa instalar su gramática y registrar el mapeo de extensión de archivo.

### Paso 1: Instalar la Gramática de Tree-Sitter

```bash
pip install tree-sitter-{language}
```

Las gramáticas de tree-sitter se publican como paquetes de Python. DevAI actualmente soporta más de 25 lenguajes de esta manera.

### Paso 2: Agregar Mapeo de Extensión en el Registro

En `ml/devai_ml/parsers/registry.py`:

```python
LANGUAGE_MAP = {
    ".py": "python",
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".rs": "rust",
    ".your_ext": "your_language",  # Add here
    # ...
}
```

El registro mapea extensiones de archivo a nombres de lenguaje de tree-sitter. Cuando el indexador encuentra un archivo, busca la extensión, carga la gramática correspondiente y parsea el AST.

### Paso 3 (Opcional): Agregar Patrones de Consulta Personalizados

Para mejores aristas en el grafo de código (llamadas a funciones, imports, jerarquías de clases), podés agregar patrones de consulta de tree-sitter:

```python
# Custom queries for extracting specific AST nodes
QUERIES = {
    "your_language": {
        "functions": "(function_definition name: (identifier) @name)",
        "classes": "(class_definition name: (identifier) @name)",
        "imports": "(import_statement) @import",
    }
}
```

Sin consultas personalizadas, DevAI recurre a recorrido genérico del AST. Esto funciona pero produce aristas menos precisas en el grafo de código.

### Cómo Funciona el Registro

1. El indexador recibe una ruta de archivo
2. El registro verifica la extensión contra `LANGUAGE_MAP`
3. Si hay coincidencia, carga la gramática de tree-sitter via `tree_sitter_languages`
4. El parser produce un AST
5. El chunker recorre el AST para crear chunks semánticos (funciones, clases, bloques)
6. El extractor de aristas identifica relaciones (llamadas, imports, herencia)

Los archivos con extensiones no reconocidas se omiten durante el parseo de AST pero pueden ser indexados como chunks de texto plano.

---

## 4. Agregar un Backend de Almacenamiento

Los backends de almacenamiento manejan la persistencia para vectores, grafos y memorias. Cada uno tiene una interfaz definida.

### Paso 1: Implementar la Interfaz del Store

Creá un archivo nuevo en `ml/devai_ml/stores/`:

```python
# ml/devai_ml/stores/your_store.py

class YourStore:
    """Your storage backend."""

    def __init__(self, config: dict):
        self.path = config.get("path", "./data")
        # Initialize connection, create tables, etc.

    async def search(
        self,
        query_vector: list[float],
        limit: int = 10,
        filters: dict | None = None,
    ) -> list[dict]:
        """Search for similar vectors.

        Returns list of dicts with: id, content, metadata, score.
        """
        # Your search implementation
        pass

    async def upsert(self, items: list[dict]) -> None:
        """Insert or update items.

        Each item has: id, content, vector, metadata.
        Uses deterministic IDs — same ID means update, not duplicate.
        """
        pass

    async def delete(self, ids: list[str]) -> None:
        """Delete items by ID."""
        pass

    async def get(self, id: str) -> dict | None:
        """Get a single item by ID."""
        pass
```

El contrato clave: `upsert` debe ser idempotente (mismo ID = actualización). DevAI usa IDs de vector determinísticos, así que el store debe manejar upserts correctamente.

### Paso 2: Registrar en la Factory

En `ml/devai_ml/stores/factory.py`:

```python
from .your_store import YourStore

STORES = {
    "lancedb": LanceDBStore,
    "qdrant": QdrantStore,
    "your_store": YourStore,
}
```

### Paso 3: Conectar al Router de Almacenamiento

El router de almacenamiento despacha al store correcto basado en el tipo de datos (vectores, grafos, memorias). Si tu store maneja un tipo específico, actualizá la configuración del router:

```yaml
storage:
  vectors: your_store    # or keep lancedb
  graphs: sqlite         # usually stays sqlite
  memories: your_store   # or keep default
```

---

## 5. Modificar el Pipeline de Indexación

El pipeline de indexación es el core de DevAI. Entender su flujo es esencial antes de modificarlo.

### Cómo Funciona el Orquestador

El pipeline de indexación sigue esta secuencia:

```
git diff → lista de archivos → filtro → parseo (tree-sitter) → chunk → embed → almacenar
```

1. **Git diff**: Determina qué archivos cambiaron desde el último índice
2. **Lista de archivos**: Expande a rutas completas, respeta `.gitignore` y exclusiones de configuración
3. **Filtro**: Omite archivos binarios, archivos grandes, patrones excluidos
4. **Parseo**: AST de tree-sitter para lenguajes soportados, texto plano para los demás
5. **Chunk**: División consciente del AST (funciones, clases como límites naturales)
6. **Embed**: Embedding por lotes via el proveedor configurado
7. **Almacenar**: Upsert de chunks con IDs determinísticos en el vector store

### Dónde Engancharse

**Filtrado personalizado de archivos** — Modificá la etapa de filtro para incluir/excluir archivos basándote en criterios personalizados. Esto sucede antes del parseo, así que es barato.

**Chunking personalizado** — El chunker decide cómo dividir los ASTs parseados en unidades indexables. La estrategia predeterminada usa límites de funciones y clases. Para cambiar tamaños o límites de chunks, modificá la lógica de chunking.

**Post-procesamiento** — Después del embedding pero antes del almacenamiento, podés agregar enriquecimiento de metadata, detección de duplicados o scoring personalizado.

**Extracción de aristas** — El constructor del grafo de código corre en paralelo con el chunking. Extrae relaciones de llamada, imports y jerarquías de tipos del AST. Los tipos de aristas personalizados van acá.

### Cómo Modificar el Comportamiento de Chunking

El chunking es consciente del AST por defecto:

- **Funciones/métodos**: Cada uno se convierte en su propio chunk
- **Clases**: Se dividen en chunks por método con contexto de clase preservado
- **Código de nivel superior**: Se divide en chunks por bloques lógicos (imports, constantes, etc.)
- **Funciones grandes**: Se dividen en límites lógicos si exceden el límite de tokens

Para modificar, mirá el módulo de chunking. Los parámetros clave son:
- `max_chunk_tokens`: Límite superior por chunk (por defecto ~500 tokens)
- `context_lines`: Cuántas líneas circundantes incluir como contexto
- `overlap`: Superposición de tokens entre chunks adyacentes para continuidad

El chunker recibe un AST y produce una lista de objetos `Chunk`, cada uno con contenido, metadata (archivo, rango de líneas, nombre de símbolo) y un ID determinístico.

### Archivos Clave

| Qué | Dónde |
|------|-------|
| Orquestador | `ml/devai_ml/indexer/` |
| Chunking | `ml/devai_ml/indexer/chunker.py` |
| Parseo de AST | `ml/devai_ml/parsers/` |
| Extracción de aristas | `ml/devai_ml/graph/` |
| Dispatch de embedding | `ml/devai_ml/embeddings/` |
| Dispatch de almacenamiento | `ml/devai_ml/stores/` |

---

## Principios Generales de Extensión

1. **Seguí los patrones existentes.** Cada punto de extensión tiene al menos una implementación de referencia. Leela antes de escribir la tuya.
2. **Registrá, no descubrás.** DevAI usa registro explícito (dicts de factory, tablas de dispatch), no escaneo de classpath ni descubrimiento de plugins. Esto es intencional — mantiene el sistema predecible.
3. **IDs determinísticos en todos lados.** Vectores, nodos de grafo y memorias usan IDs determinísticos derivados del contenido y la ruta. Tus extensiones deben preservar esta propiedad.
4. **Async por defecto.** Los handlers de Python son async. Si tu código es CPU-bound, usá `asyncio.to_thread`.
5. **Testeá con un repo real.** El mejor test es indexar un repositorio real y verificar los resultados de búsqueda. Tests unitarios para componentes individuales, tests de integración con repos reales.
