> 🌐 [English version](../../03-core-concepts/mcp-integration.md)

# Integracion MCP

## Que es

DevAI expone sus capacidades — busqueda, memoria, grafo de simbolos, construccion de contexto — como herramientas via el [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). MCP es un estandar abierto que permite a las aplicaciones de IA (Claude Code, Cursor, Windsurf, etc.) descubrir y llamar herramientas externas a traves de una interfaz unificada. DevAI implementa un servidor MCP que convierte la inteligencia de tu codebase en herramientas que cualquier cliente compatible con MCP puede usar.

## Por que existe

Sin MCP, cada integracion con herramientas de IA es a medida. Necesitarias una extension de VS Code, un plugin de JetBrains, un wrapper de CLI, y una API custom — cada uno con su propio protocolo, autenticacion y carga de mantenimiento.

MCP estandariza esto. DevAI implementa un servidor. Cualquier cliente MCP puede usarlo. Hoy son Claude Code y Cursor. Manana es lo que sea que salga. Cero trabajo de integracion por cliente.

## Como funciona

### Arquitectura

```
  ┌──────────────────┐     stdio      ┌──────────────────┐
  │   Cliente MCP    │◄──(stdin/──────►│   DevAI MCP      │
  │  (Claude Code,   │   stdout)      │   Server (Go)    │
  │   Cursor, etc.)  │               │                  │
  └──────────────────┘               └────────┬─────────┘
                                              │
                                     JSON-RPC │
                                              │
                                     ┌────────▼─────────┐
                                     │   DevAI ML       │
                                     │   Server (Python) │
                                     │                  │
                                     │  - Embeddings    │
                                     │  - LanceDB       │
                                     │  - SQLite        │
                                     │  - Tree-sitter   │
                                     └──────────────────┘
```

- **Transporte**: stdio (stdin/stdout). El cliente MCP lanza el servidor DevAI como proceso hijo y se comunica via I/O estandar.
- **Servidor MCP en Go**: Construido con la libreria [mark3labs/mcp-go](https://github.com/mark3labs/mcp-go). Maneja la negociacion del protocolo MCP, descubrimiento de herramientas y ruteo de requests.
- **Servidor ML en Python**: Realiza el trabajo real — embedding, busqueda, indexacion, operaciones de memoria. El servidor Go se comunica con el via JSON-RPC.

### Patron de handler

Cada herramienta MCP sigue el mismo flujo interno:

```
  Request MCP (del cliente)
       │
       ▼
  Parsear argumentos (validar tipos, aplicar defaults)
       │
       ▼
  Llamar al servidor ML via JSON-RPC
       │
       ▼
  Formatear respuesta (texto estructurado para consumo del LLM)
       │
       ▼
  Retornar resultado MCP (al cliente)
```

## Referencia de herramientas

DevAI expone 14 herramientas via MCP. Tres operaciones adicionales (`push_index`, `pull_index`, `sync_index`) son solo CLI.

### Inteligencia de codigo

| Herramienta | Descripcion | Parametros clave |
|---|---|---|
| `search` | Busqueda semantica de codigo en repositorios indexados | `query` (string), `repo` (string, opcional), `branch` (string, opcional), `language` (string, opcional), `symbol_type` (string, opcional), `limit` (int, default 10) |
| `read_file` | Leer contenido de archivo con rango de lineas opcional | `path` (string), `start_line` (int, opcional), `end_line` (int, opcional) |
| `read_symbol` | Obtener la definicion completa de una funcion, clase o tipo | `name` (string), `repo` (string, opcional) |
| `get_references` | Encontrar todos los call sites y usos de un simbolo | `symbol` (string), `repo` (string, opcional) |
| `build_context` | Ensamblar contexto consciente del presupuesto de tokens desde codigo + memorias | `query` (string), `max_tokens` (int, default 8000), `repo` (string, opcional) |

### Indexacion

| Herramienta | Descripcion | Parametros clave |
|---|---|---|
| `index_repo` | Indexar o re-indexar un repositorio | `path` (string), `branch` (string, opcional), `full` (bool, default false) |
| `index_status` | Verificar el estado de indexacion de un repositorio | `path` (string) |

### Contexto de branch

| Herramienta | Descripcion | Parametros clave |
|---|---|---|
| `get_branch_context` | Obtener contexto sobre el branch actual y sus cambios | `path` (string), `branch` (string, opcional) |
| `switch_context` | Cambiar el contexto de branch activo para busqueda | `path` (string), `branch` (string) |

### Memoria

| Herramienta | Descripcion | Parametros clave |
|---|---|---|
| `remember` | Guardar una memoria estructurada | `title` (string), `content` (string), `type` (string), `project` (string), `scope` (string, default "shared"), `topic_key` (string, opcional), `tags` (list, opcional), `files` (list, opcional) |
| `recall` | Buscar memorias por consulta en lenguaje natural | `query` (string), `project` (string), `type` (string, opcional), `scope` (string, opcional), `limit` (int, default 10) |
| `memory_context` | Obtener contexto enriquecido con memorias para un tema | `query` (string), `project` (string) |
| `memory_stats` | Obtener estadisticas sobre el memory store | `project` (string, opcional) |

### Sesion

| Herramienta | Descripcion | Parametros clave |
|---|---|---|
| `get_session_history` | Recuperar historial de interacciones de sesion | `session_id` (string, opcional), `limit` (int, default 20) |

### Solo CLI (No expuestas via MCP)

| Comando | Descripcion |
|---|---|
| `devai push-index` | Subir indice local a almacenamiento remoto |
| `devai pull-index` | Descargar indice desde almacenamiento remoto |
| `devai sync-index` | Sincronizacion bidireccional de indices |

Estos son solo CLI porque involucran operaciones potencialmente destructivas (sobreescribir indices) y transferencias de larga duracion que no encajan en el modelo request-response de MCP.

## Configuracion

### Auto-configurar para Claude Code

```bash
devai server configure claude
```

Esto escribe la configuracion del servidor MCP en el archivo de configuracion de Claude Code (`~/.claude.json` o `.mcp.json` a nivel de proyecto), registrando DevAI como un servidor MCP disponible con la ruta correcta al binario y los argumentos.

### Auto-configurar para Cursor

```bash
devai server configure cursor
```

Igual que arriba, pero escribe en la ubicacion de configuracion MCP de Cursor.

### Configuracion manual

Para otros clientes MCP, agrega esto a tu configuracion MCP:

```json
{
  "mcpServers": {
    "devai": {
      "command": "devai",
      "args": ["server", "start"],
      "transport": "stdio"
    }
  }
}
```

El binario del servidor debe estar en tu `PATH`. Automaticamente localiza e inicia el servidor ML de Python.

## Cuando se usa

La integracion MCP es la **interfaz principal** para workflows de desarrollo asistidos por IA. Cuando usas DevAI con Claude Code o Cursor:

1. El editor/CLI lanza el servidor MCP de DevAI
2. El agente de IA descubre las herramientas disponibles via el protocolo MCP
3. Durante la conversacion, el agente llama herramientas segun las necesite:
   - `search` para encontrar codigo relevante
   - `build_context` para ensamblar contexto comprensivo
   - `remember` / `recall` para persistir y recuperar conocimiento
   - `read_symbol` / `get_references` para navegacion precisa de codigo
4. Los resultados se retornan como texto estructurado que el agente incorpora en su razonamiento

## Ejemplo: Que pasa cuando Claude Code llama a `search`

```
Usuario (en Claude Code): "Encontra la logica de retry para llamadas API"

1. DESCUBRIMIENTO DE HERRAMIENTAS (ya hecho al inicio de la sesion)
   Claude Code sabe que DevAI expone una herramienta `search`

2. LLAMADA A HERRAMIENTA
   Claude Code envia request MCP:
   {
     "method": "tools/call",
     "params": {
       "name": "search",
       "arguments": {
         "query": "retry logic for API calls",
         "limit": 10
       }
     }
   }

3. SERVIDOR MCP EN GO
   Recibe request via stdin
   Parsea argumentos: query="retry logic for API calls", limit=10
   Envia llamada JSON-RPC al servidor ML de Python

4. SERVIDOR ML EN PYTHON
   Embeddea la consulta → vector de 384 dimensiones
   Busca en LanceDB los chunks mas cercanos
   Retorna resultados rankeados con metadata

5. SERVIDOR MCP EN GO
   Formatea resultados como texto estructurado:

   "Found 7 results:

   [1] services/http/retry.py:12-45 (score: 0.93)
   class RetryPolicy:
       def __init__(self, max_retries=3, backoff_factor=2.0):
           ...

   [2] services/http/client.py:67-89 (score: 0.87)
   async def fetch_with_retry(url, policy=None):
       ..."

   Retorna respuesta MCP via stdout

6. CLAUDE CODE
   Recibe resultados
   Los incorpora en la respuesta al usuario
   "Encontre la logica de retry en services/http/retry.py..."
```

Latencia total: 50-200ms dependiendo del tamano del indice. El usuario ve los resultados inline en la conversacion, como si el agente de IA simplemente "supiera" donde estaba el codigo.

## Agregar nuevas herramientas

Para agregar una nueva herramienta MCP a DevAI:

1. **Lado Go** (`internal/mcp/server.go`): Registrar la herramienta con su schema y funcion handler
2. **Lado Python** (`ml/devai_ml/server.py`): Implementar el metodo JSON-RPC que hace el trabajo real
3. **Testear ambos lados**: El handler parsea argumentos y formatea la salida; el metodo ML implementa la logica

Para instrucciones detalladas, consulta la guia [Extendiendo DevAI](../04-extending/adding-tools.md).

## Modelo mental

MCP es un **puerto USB para herramientas de IA**. USB estandarizo como los perifericos se conectan a las computadoras — antes de USB, cada dispositivo necesitaba su propio conector propietario. MCP estandariza como los agentes de IA se conectan a capacidades externas.

DevAI es un dispositivo que conectas via MCP. El agente de IA (Claude Code, Cursor) es la computadora. Una vez conectado, el agente puede usar las capacidades de DevAI — busqueda, memoria, grafo de simbolos — sin saber nada sobre embeddings, LanceDB o tree-sitter. Simplemente llama herramientas y obtiene resultados. Cambia a un cliente MCP diferente, y todo sigue funcionando. Esa es la idea.
