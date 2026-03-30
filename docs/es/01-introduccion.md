> 🌐 [English version](../01-introduction.md)

# Introducción

> Volver al [README](../README.md)

---

## ¿Qué es DevAI?

DevAI es un **Motor de Contexto para agentes de IA**. Le da a los asistentes de código con IA — Claude Code, Cursor, Copilot, agentes personalizados — una comprensión semántica y estructurada de tu codebase, en lugar de forzarlos a trabajar mirando por una cerradura leyendo archivos individuales.

No es una herramienta de búsqueda. No es un linter. No es otro indexador que tenés que cuidar como un bebé. DevAI es la capa entre tu código y tu agente de IA que transforma archivos fuente crudos en conocimiento navegable, consultable y persistente.

**DevAI es para los agentes de IA lo que un IDE es para los humanos.** Un IDE te da búsqueda a nivel de proyecto, ir a definición, encontrar todas las referencias y estado persistente del workspace. Sin eso, estás haciendo `cat` de archivos en una terminal. Eso es exactamente lo que los agentes de IA hacen hoy — leen archivos de a uno, pierden el contexto entre turnos y no pueden navegar el código estructuralmente. DevAI soluciona eso.

---

## El Problema

Los agentes de código con IA son poderosos pero ciegos. Operan bajo restricciones duras que hacen que los codebases grandes sean un dolor de cabeza:

- **Visión de cerradura.** Los agentes ven un archivo a la vez. No pueden mantener un módulo entero en memoria de trabajo, ni mucho menos rastrear una cadena de llamadas entre paquetes.
- **Sin conciencia estructural.** `grep` encuentra texto. No sabe que `handleAuth` es un método de `AuthMiddleware` que implementa `http.Handler` y se llama desde tres archivos de rutas.
- **Amnesia.** Cada sesión arranca de cero. El agente que pasó 20 minutos entendiendo tu flujo de autenticación ayer no se acuerda de nada hoy.
- **Desperdicio de contexto.** Sin recuperación dirigida, los agentes meten archivos enteros en su ventana de contexto. La mitad de los tokens se van en código irrelevante. Las partes importantes se truncan.

Estas no son molestias menores. Son la razón por la cual los agentes de IA producen código superficial, incorrecto o incompleto en cualquier cambio que vaya más allá de lo trivial.

---

## Capacidades Principales

DevAI provee cinco capacidades que eliminan estas restricciones:

### 1. Búsqueda Semántica

Encontrá código por *significado*, no por palabras clave. Buscá "authentication middleware" y obtené el handler de autenticación real, incluso si se llama `verifyToken` en un archivo llamado `security.go`. Impulsado por sentence-transformers (MiniLM-L6, vectores de 384 dimensiones) con LanceDB o Qdrant como vector store.

### 2. Grafo de Símbolos

Navegación estructural completa construida a partir del parseo AST con tree-sitter en más de 25 lenguajes. Ir a definición, encontrar todas las referencias, relaciones caller/callee — almacenadas como listas de adyacencia en SQLite. El agente puede rastrear `UserService.Create` → `Repository.Insert` → `db.Exec` en una sola consulta.

### 3. Memoria Persistente

Los agentes pueden hacer `remember` de decisiones, descubrimientos y convenciones — y hacer `recall` en sesiones futuras. Las memorias se deduplican por hash de contenido, soportan upserts por topic-key para conocimiento que evoluciona, y persisten en SQLite. No más re-explicar tu arquitectura en cada sesión.

### 4. Construcción de Contexto

Ensamblaje consciente del presupuesto de tokens que combina resultados de búsqueda, definiciones de símbolos, entradas de memoria e información de dependencias en un único bloque de contexto coherente. El agente pide "todo sobre el flujo de pagos" y recibe una respuesta curada y del tamaño justo — no un volcado crudo.

### 5. Integración MCP

14 herramientas expuestas via el Model Context Protocol sobre stdio. Cualquier agente compatible con MCP puede llamar a `search`, `read_symbol`, `get_references`, `build_context`, `remember`, `recall`, y más. Cero configuración cuando se usa el auto-setup.

---

## Inicio Rápido en 5 Minutos

### Instalación

```bash
curl -fsSL https://raw.githubusercontent.com/snaven10/devai-context-engine/main/scripts/install.sh | bash
```

Descarga el binario de Go y un entorno Python portable. No se requiere Python del sistema.

### Inicializar un repositorio

```bash
cd your-repo
devai init
```

Crea un directorio `.devai/` con configuración y estado. Detecta los lenguajes de tu proyecto automáticamente.

### Indexar el codebase

```bash
devai index
```

Parsea todos los archivos fuente con tree-sitter, genera chunks semánticos, computa embeddings y construye el grafo de símbolos. Incremental — las ejecuciones subsiguientes solo procesan los cambios del `git diff`.

### Buscar desde la CLI

```bash
devai search "authentication middleware"
```

Devuelve resultados rankeados con rutas de archivo, nombres de símbolos y puntajes de relevancia.

### Conectar con Claude Code

```bash
devai server configure claude
```

Escribe automáticamente la entrada del servidor MCP en la configuración de Claude Code. El agente ahora puede llamar a las 14 herramientas de DevAI directamente. Sin editar JSON manualmente.

---

## Cómo Funciona (Versión de 30 Segundos)

```
Your Code ──▶ Tree-sitter AST ──▶ Semantic Chunks ──▶ Embeddings ──▶ Vector DB
                    │                                                (LanceDB/Qdrant)
                    ▼
              Symbol Graph ──▶ SQLite (adjacency lists)

AI Agent ──MCP──▶ DevAI Go Server ──JSON-RPC──▶ Python ML Service
                                                    │
                                        ┌───────────┼───────────┐
                                        ▼           ▼           ▼
                                    Vector DB    Symbol DB    Memory DB
                                        │           │           │
                                        └───────────┼───────────┘
                                                    ▼
                                            Assembled Context ──▶ Agent
```

**Go** maneja la CLI, la TUI, el servidor MCP y la gestión de procesos. **Python** maneja embeddings, parseo AST, chunking, almacenamiento vectorial y memoria. Se comunican via JSON-RPC por stdio — sin dependencia de red, sin puertos que configurar.

La indexación es incremental. Después del primer índice completo, DevAI usa `git diff` para detectar archivos modificados y solo reprocesa esos. Los overlays de branch permiten buscar a través del linaje de branches sin duplicar datos.

---

## Mapa de Documentación

| Documento | Qué cubre |
|----------|---------------|
| [Introducción](01-introduccion.md) | Estás acá — resumen, inicio rápido, modelo mental |
| [Setup](setup.md) | Opciones de instalación, configuración, variables de entorno |
| [Arquitectura](02-arquitectura.md) | Diseño híbrido Go + Python, capas de almacenamiento, flujo de datos |
| [Flujo de Trabajo del Agente](04-flujo-de-trabajo-del-agente.md) | Cómo los agentes de IA interactúan con DevAI, patrones de selección de herramientas |
| [Referencia de Herramientas MCP](mcp-tools.md) | Las 14 herramientas con parámetros, ejemplos y schemas de respuesta |
| [Funcionalidades](features.md) | Desglose detallado de capacidades — búsqueda, grafo, memoria, branches |
| [API](api.md) | Protocolo JSON-RPC entre Go y Python |
| [Schemas](schemas.md) | Schemas de base de datos, formatos de archivos de configuración, estructuras de estado |

---

> **DevAI está en alpha.** Las APIs, flags de CLI y formatos de almacenamiento pueden cambiar entre versiones. Consultá el [README](../README.md) para el estado actual.
