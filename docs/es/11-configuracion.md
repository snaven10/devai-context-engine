# Configuración

Cómo se configura DevAI: los archivos `config.yaml`, cómo se conecta el servidor MCP a los clientes de IA
(Claude Code, Cursor, …), las variables de entorno, y los hooks de auto-indexación de git.

> Para la elección de modelos de embeddings, estrategias de summarizer/presupuesto de tokens, y tuning por
> hardware, ver [Modelos y Tuning](09-modelos-embeddings-y-tuning.md). Esta página es la referencia de la
> **mecánica de configuración**.

---

## 1. `config.yaml` — configuración del proyecto

`devai init` crea `.devai/config.yaml` en el repo. El CLI (`devai index`, `devai server …`) y el servidor
MCP lo leen para resolver el modelo, el directorio de estado, el modo de storage y los excludes.

### 1.1 Schema completo

```yaml
project:
  name: my-repo                 # alias legible
  path: /ruta/completa/al/repo

state_dir: /ruta/completa/.devai/state   # donde viven vectores + grafo + memoria
language: en                    # en | es  (afecta descripciones de modelos / TUI)

embeddings:
  provider: local               # local | openai | voyage | custom
  model: minilm-l6              # clave del registry — ver Modelos y Tuning
  offline: auto                 # auto (cache, sin chequear Hub) | true | false

storage:
  mode: local                   # local | shared | hybrid
  qdrant_url: localhost:6334    # solo para shared / hybrid
  qdrant_api_key: ""            # solo para shared / hybrid
  local_db_path: ""             # override de la ruta LanceDB (opcional)

indexing:
  exclude:                      # globs que se saltan al indexar
    - "node_modules/**"
    - "vendor/**"
    - ".git/**"
    - "dist/**"
    - "build/**"
    - "*.min.js"
    - "*.lock"

runtime:
  python_path: ""               # binario python explícito (opcional; auto-detectado)
```

### 1.2 Las tres ubicaciones (y cómo se encuentran)

DevAI busca `.devai/config.yaml` **subiendo** desde el directorio actual (`FindConfigFile`). En un workspace
multi-repo normalmente terminás con varios:

| Archivo | Se lee cuando | Define |
|---------|---------------|--------|
| `<repo>/.devai/config.yaml` | `devai index` corrido **dentro de ese repo** | modelo + excludes de ese repo |
| `<workspace>/.devai/config.yaml` | `devai server mcp` corrido desde la raíz | modelo del servicio MCP |
| `<workspace>/.devai/state/config.yaml` | resolución de estado compartido | el `state_dir` compartido |

> **Mantené `embeddings.model` IDÉNTICO en todos.** Son archivos independientes; si uno se desalinea, las
> herramientas que corran desde ese directorio van a indexar/consultar con el modelo equivocado (o de
> dimensión vacía).

### 1.3 Precedencia: `config.yaml` VENCE al env var

Esta es la sorpresa más común. El **CLI Go y el MCP leen `embeddings.model` del `config.yaml` más cercano y
se lo pasan al servicio Python, sobreescribiendo `DEVAI_EMBEDDING_MODEL`.** Setear solo el env var **NO**
alcanza para cambiar el modelo — cambialo en `config.yaml` (o con `devai model use <key>`, que edita el
archivo por vos). Ver el runbook de migración en
[Modelos y Tuning](09-modelos-embeddings-y-tuning.md#7-gotchas-al-migrar-de-modelo-aprendidos-en-producción).

Los env vars **sí** aplican para parámetros que no tienen campo en `config.yaml` (presupuesto de tokens,
summarizer, rerank, idle timeout — ver §3).

---

## 2. Configuración del MCP — conectar DevAI a clientes de IA

DevAI habla con los agentes vía **Model Context Protocol** sobre **stdio**. El cliente lanza
`devai server mcp` como subproceso y llama a las herramientas por JSON-RPC.

### 2.1 Automático: `devai server configure`

```bash
devai server configure --all      # Claude Code + Cursor (default)
devai server configure --claude   # solo Claude Code
devai server configure --show     # previsualizar sin escribir
devai server configure --remove   # quitar el entry de devai
devai server configure --claude --scope project   # escribe un .mcp.json de proyecto en vez del ~/.claude.json global
devai server configure --claude --env DEVAI_EMBEDDING_MODEL=ml-mpnet  # fija vars de tuning en el entry
```

(a) Resuelve la ruta absoluta del binario `devai`, (b) detecta el `config.yaml` + state dir del proyecto,
(c) escribe el entry del servidor MCP en cada config de cliente, y (d) genera `.devai/AGENT.md`
(instrucciones de uso de las herramientas para el agente).

| Cliente | Archivo escrito | Clave |
|---------|-----------------|-------|
| Claude Code | `~/.claude.json` | `mcpServers.devai` |
| Cursor / Windsurf | `~/.cursor/mcp.json` | `mcpServers.devai` |

> `--scope project` escribe el entry de Claude en `<projectRoot>/.mcp.json` (merge no destructivo, solo Claude Code).
> `--env KEY=VALUE` es repetible y mergea sobre los defaults (`DEVAI_STATE_DIR`, Qdrant).
>
> **Nota sobre el modelo y `--env`:** por defecto `server configure` *no* escribe `DEVAI_EMBEDDING_MODEL`
> en `env` — el modelo se resuelve desde `config.yaml` (§1.3). Cuando lo pasás explícitamente (p. ej. el
> `--env DEVAI_EMBEDDING_MODEL=…` del instalador), *sí* queda fijado en el entry y actúa como el modelo
> efectivo hasta que exista un `config.yaml` (que entonces vuelve a tener prioridad, según §1.3).

El entry que escribe:

```json
{
  "type": "stdio",
  "command": "/ruta/abs/devai",
  "args": ["server", "mcp"],
  "env": {
    "DEVAI_STATE_DIR": "/ruta/abs/.devai/state"
  }
}
```

> **Nota — el modelo NO se escribe en `env`.** `server configure` solo setea `DEVAI_STATE_DIR` (más
> `DEVAI_STORAGE_MODE` / vars de Qdrant cuando el storage es `shared`/`hybrid`). El modelo de embeddings se
> resuelve del `config.yaml` (§1.3). Si querés fijar parámetros de tuning (summarizer, token strategy,
> rerank), agregalos al bloque `env` a mano — ver §3.

### 2.2 Config por proyecto: `.mcp.json`

Claude Code también soporta un `.mcp.json` **a nivel de proyecto** en la raíz del repo/workspace, que es el
lugar correcto para fijar tuning por proyecto. Misma estructura que el entry de arriba, p. ej.:

```json
{
  "mcpServers": {
    "devai": {
      "command": "/ruta/abs/devai",
      "args": ["server", "mcp"],
      "env": {
        "DEVAI_STATE_DIR": "/ruta/abs/.devai/state",
        "DEVAI_EMBEDDING_MODEL": "ml-mpnet",
        "DEVAI_TOKEN_STRATEGY": "summarize",
        "DEVAI_SUMMARIZER_PROVIDER": "extractive",
        "DEVAI_MAX_OUTPUT_TOKENS": "4000"
      }
    }
  }
}
```

Tras cualquier cambio, **reiniciá / reconectá el MCP** en tu cliente para que tome efecto.

### 2.3 `.devai/AGENT.md`

`server configure` también deja un `AGENT.md` que le dice al agente que prefiera las herramientas de DevAI
(`search`, `build_context`, `read_symbol`, `get_references`, `recall`/`remember`) por sobre leer archivos a
mano. Apuntá las instrucciones de tu agente ahí, o pegá su contenido en las reglas del proyecto.

### 2.4 El asistente de instalación

`scripts/install.sh` es **consciente del TTY**. Corrido desde una terminal, te lleva por un asistente corto; si
se lo pipea (`curl … | bash`) corre de forma no interactiva con los defaults + flags y nunca bloquea en un
prompt.

| Prompt | Default | Flag |
|--------|---------|------|
| Directorio de instalación | `~/.local/share/devai` | `--install-dir DIR` |
| Directorio de estado (`DEVAI_STATE_DIR`) | `<install-dir>/state` | `--state-dir DIR` |
| PyTorch CPU o GPU | CPU | `--gpu` |
| Modelo de embeddings | `minilm-l6` (o `ml-mpnet`) | `--model KEY` |
| Configurar cliente de IA | `claude` (o `cursor` / `both` / `none`) | `--client NAME` |
| Scope de config de Claude | `global` (o `project`) | `--scope SCOPE` |
| Instalar hook de auto-index de git | sí | `--hooks` / `--no-hooks` |
| Aceptar todos los defaults, sin prompts | — | `--yes` (implícito cuando no hay TTY) |

Tras instalar, el asistente delega la configuración del cliente a `devai server configure` y (opcionalmente)
instala el hook de git via `devai hooks install` — nunca escribe JSON de cliente directamente.

---

## 3. Variables de Entorno

Las lee el servicio ML de Python al arrancar. Útiles en el bloque `env` del MCP o en tu shell. (Los nombres
son los autoritativos de la config del servicio.)

**Núcleo / rutas**

| Variable | Propósito | Default |
|----------|-----------|---------|
| `DEVAI_STATE_DIR` | Dónde viven vectores/grafo/memoria | `~/.local/share/devai/state` |
| `DEVAI_LOCAL_DB_PATH` | Override de la ruta de vectores LanceDB | `<state_dir>/vectors` |
| `DEVAI_PYTHON` | Binario python explícito para el servicio ML | auto-detectado |

**Embeddings**

| Variable | Propósito | Default |
|----------|-----------|---------|
| `DEVAI_EMBEDDING_MODEL` | Clave del modelo *(la sobreescribe `config.yaml`, §1.3)* | `minilm-l6` |
| `DEVAI_EMBEDDING_PROVIDER` | `local` \| `openai` \| `voyage` \| `custom` | `local` |
| `DEVAI_EMBEDDING_DEVICE` | `cpu` \| `cuda` | `cpu` |
| `DEVAI_EMBEDDING_API_KEY` | API key para proveedores de embeddings remotos | — |
| `DEVAI_EMBEDDINGS_OFFLINE` | `auto` \| `true` \| `false` | `auto` |
| `DEVAI_EMBED_MAX_CHARS` | Guarda RAM — máximo de caracteres que se le pasan al encoder por texto (NO el límite de contexto del modelo). Bajalo (p. ej. `2048`) en máquinas con poca RAM para evitar OOM en chunks minificados o de texto largo no-código. | `4096` |
| `DEVAI_EMBED_BATCH_SIZE` | Textos por batch de embeddings. Bajalo (p. ej. `8`) para reducir el pico de RAM. | `16` |

**Presupuesto de tokens y summarizer**

| Variable | Propósito | Default |
|----------|-----------|---------|
| `DEVAI_TOKEN_STRATEGY` | `drop` \| `soft_truncate` \| `hard_truncate` \| `summarize` | `drop` |
| `DEVAI_MAX_OUTPUT_TOKENS` | Presupuesto de tokens de las respuestas | `4000` |
| `DEVAI_TOKEN_ENCODING` | Nombre del encoding del tokenizador | `cl100k_base` |
| `DEVAI_SUMMARIZER_PROVIDER` | `noop` \| `extractive` \| `flan-t5` \| `openai` | `extractive` |
| `DEVAI_SUMMARIZER_MODEL` | ID de modelo para summarizers no extractivos (p. ej. `google/flan-t5-small`) | específico del proveedor |
| `DEVAI_SUMMARIZER_DEVICE` | `cpu` \| `cuda` para summarizers locales | `cpu` |
| `DEVAI_SUMMARIZER_API_KEY` | API key para el summarizer `openai` | — |
| `DEVAI_SUMMARIZER_TARGET_TOKENS` | Longitud objetivo de los resúmenes | `200` |
| `DEVAI_SUMMARIZER_REQUIRE_LOCAL` | Bloquea proveedores no locales (falla en lugar de usar un summarizer remoto) | `true` |

**Rerank**

| Variable | Propósito | Default |
|----------|-----------|---------|
| `DEVAI_RERANK_ENABLED` | Activa/desactiva el reranking | `true` |
| `DEVAI_RERANK_PROVIDER` | `noop` \| `flashrank` | `flashrank` |
| `DEVAI_RERANK_MODEL` | modelo flashrank; usar `ms-marco-MultiBERT-L-12` para rerank **multilingüe** | `ms-marco-MiniLM-L-12-v2` |
| `DEVAI_RERANK_TOP_K_FETCH` | Candidatos traídos antes del rerank | `15` |
| `DEVAI_RERANK_CACHE_DIR` | Dónde se cachean los archivos del modelo flashrank | `<install>/flashrank` |

**Chunking**

| Variable | Propósito | Default |
|----------|-----------|---------|
| `DEVAI_MAX_CHUNK_TOKENS` | Límite superior de un chunk de código | `512` |
| `DEVAI_MIN_CHUNK_TOKENS` | Límite inferior de un chunk de código | `64` |
| `DEVAI_LARGE_FUNCTION_THRESHOLD` | Tamaño en tokens a partir del cual se divide una función | `1024` |

**Almacenamiento y servicio**

| Variable | Propósito | Default |
|----------|-----------|---------|
| `DEVAI_STORAGE_MODE` | `local` \| `shared` \| `hybrid` | `local` |
| `DEVAI_QDRANT_URL` / `DEVAI_QDRANT_API_KEY` | Qdrant shared/hybrid | — |
| `DEVAI_ML_IDLE_TIMEOUT_SEC` | Segundos idle antes de que el servicio ML salga (`0` desactiva) | `1800` |
| `DEVAI_API_TOKEN` | Token Bearer para el modo servidor HTTP (`devai server http`) | — |

> **Variables que NO son configurables por entorno** — estas cadenas aparecen en el código pero **no** se leen
> desde el entorno, por lo que **no** son tunables:
> - `DEVAI_ML_READY` — lo setea el runtime para señalar que el servicio ML está listo.
> - `DEVAI_AUTO_INDEX` — el **texto de los markers** begin/end del bloque del hook git post-commit (ver §4), no una variable.
> - `DEVAI_UUID_NAMESPACE` — una **constante** UUID hardcodeada en el store de Qdrant, no un env var.

> Los re-index largos (repos grandes en CPU) pueden superar el idle watchdog. Poné
> `DEVAI_ML_IDLE_TIMEOUT_SEC=0` mientras re-indexás — ver
> [Modelos y Tuning](09-modelos-embeddings-y-tuning.md#7-gotchas-al-migrar-de-modelo-aprendidos-en-producción).

Ver [Modelos y Tuning §3–§4](09-modelos-embeddings-y-tuning.md) para el set completo de variables de
summarizer/presupuesto/rerank y sus trade-offs.

---

## 4. Hooks de Auto-Indexación de Git

`devai hooks install` agrega un **git post-commit hook** que re-indexa el repo (incremental, en background)
después de cada commit, para que el índice nunca se quede viejo.

```bash
devai hooks install [repo-path]     # instala o actualiza (default: repo actual)
devai hooks uninstall [repo-path]   # quita solo la sección de devai
```

### Qué escribe

Un **bloque delimitado** en `.git/hooks/post-commit`:

```sh
# >>> DEVAI_AUTO_INDEX >>>
# Auto-index after each commit. Managed by 'devai hooks install/uninstall' — do not edit by hand.
( cd "$(git rev-parse --show-toplevel)" && DEVAI_STATE_DIR="…/.devai/state" "/abs/devai" index --incremental ) >/dev/null 2>&1 &
# <<< DEVAI_AUTO_INDEX <<<
```

- **`cd "$(git rev-parse --show-toplevel)"`** — corre desde la raíz del repo para que el indexer resuelva el
  nombre real del repo.
- **`>/dev/null 2>&1 &`** — en background y silenciado; el commit nunca se bloquea ni se ensucia con logs.
- **Convive con otros hooks.** El bloque está delimitado por markers BEGIN/END: re-correr `install`
  reemplaza solo ese bloque, y `uninstall` quita solo ese bloque — cualquier otra lógica del post-commit se
  preserva. Si el archivo queda solo con el shebang, `uninstall` lo borra entero.

> Tip: combinalo con `DEVAI_STATE_DIR` apuntando a un **estado compartido del workspace** para que varios
> repos mantengan un único índice unificado.

---

## 5. Resumen Rápido

1. **Modelo y excludes** → `.devai/config.yaml` (por repo). `config.yaml` le gana a `DEVAI_EMBEDDING_MODEL`.
2. **Conectar a un cliente de IA** → `devai server configure --all` (escribe `mcpServers.devai`), o un
   `.mcp.json` por proyecto para tuning específico.
3. **Tuning** (summarizer, presupuesto de tokens, rerank, idle timeout) → env vars en el bloque `env` del MCP.
4. **Mantener el índice fresco** → `devai hooks install`.
5. **Reconectá el MCP** después de cambiar cualquier config de cliente o env.
