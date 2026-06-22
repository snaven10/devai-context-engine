# Store Central Multi-Repositorio

> 🌐 [English version](../12-multi-repo-central-store.md)

Cómo configurar múltiples repositorios para alimentar un **índice DevAI compartido** mediante
hooks post-commit, mientras un solo servidor MCP lee ese store unificado para todos ellos.

---

## 1. Modelo Mental

Por defecto, cada `devai init` crea su propio índice aislado dentro del repositorio. La
topología multi-repo invierte esto: se designa **un store central** y se indica a cada
repositorio que escriba en él.

```
   $WORKSPACE/repo-a/   $WORKSPACE/repo-b/   $WORKSPACE/repo-c/
        |                     |                     |
   hook post-commit       hook post-commit       hook post-commit
    (devai index           (devai index           (devai index
     --incremental)         --incremental)         --incremental)
        |                     |                     |
        +----------+----------+----------+----------+
                   |
          STORE CENTRAL
          $WORKSPACE/.devai/state/
           ├── index.db         (grafo + index_state + memoria SQLite)
           ├── vectors/         (vectores de embeddings LanceDB)
           └── config.yaml      (configuración del modelo compartido)
                   |
        +----------+
        |
   devai server mcp
   (un proceso MCP, lee el store compartido)
        |
   Agente IA (Claude Code, Cursor, …)
```

Los tres repositorios aportan sus símbolos, memorias y embeddings a la misma base de datos.
El agente de IA ve el panorama completo a través de una única conexión MCP.

---

## 2. Receta

### 2.1 Elegir (o crear) la ruta del store central

Elige una ruta que **no esté dentro de ningún repositorio individual** para evitar que git
la rastree accidentalmente:

```bash
# Opción A — raíz del workspace (recomendado para monorepos)
export CENTRAL="$WORKSPACE/.devai/state"

# Opción B — estado a nivel de usuario compartido entre todos los proyectos
export CENTRAL="$HOME/.local/share/devai/state"

mkdir -p "$CENTRAL"
```

### 2.2 Inicializar cada repositorio

Ejecuta `devai init` dentro de cada repositorio que contribuirá al store compartido. Tras el
cambio de defecto reciente, `init` ya no fija un `state_dir` por repositorio en `config.yaml`,
por lo que el archivo se crea sin esa línea.

```bash
cd $WORKSPACE/repo-a && devai init
cd $WORKSPACE/repo-b && devai init
cd $WORKSPACE/repo-c && devai init
```

> **Usa el mismo modelo de embeddings en todos los repositorios.** Mezclar modelos crea
> incompatibilidades de dimensión vectorial que corrompen el store — `devai index` ahora
> aborta al detectar el mismatch, pero un store parcialmente escrito aún requiere un
> re-índice completo para recuperarse. Ver
> [Configuración §1.3](11-configuracion.md#13-precedencia-configyaml-gana-sobre-la-variable-de-entorno)
> y la sección de errores frecuentes en
> [Modelos y Tuning](09-modelos-embeddings-y-tuning.md).

### 2.3 Apuntar cada repositorio al store central

**Ruta A — mediante variable de entorno (recomendada para CI / ejecuciones puntuales):**

```bash
DEVAI_STATE_DIR="$CENTRAL" devai index                # índice completo
DEVAI_STATE_DIR="$CENTRAL" devai index --incremental  # índice incremental
```

**Ruta B — mediante `config.yaml` (permanente, por repositorio):**

Agrega o establece `state_dir` en el `.devai/config.yaml` de cada repositorio:

```yaml
state_dir: /ruta/absoluta/al/store/central   # compartido entre todos los repos

embeddings:
  model: ml-granite   # debe ser idéntico en cada repositorio
```

> Si estableces `state_dir` en `config.yaml`, ya no necesitas `DEVAI_STATE_DIR` en la
> línea de comandos — el CLI lo lee del archivo. Ambos enfoques son equivalentes; elige el
> que mejor encaje en tu flujo de trabajo.

**Sobreescribe la ruta de LanceDB si es necesario** (solo cuando se separan los vectores de la BD):

```bash
export DEVAI_LOCAL_DB_PATH="$CENTRAL/vectors"
```

### 2.4 Instalar los hooks post-commit

Instala el hook de auto-indexación en cada repositorio, apuntando al store central. El hook
ahora embebe el modelo de embeddings activo y `DEVAI_EMBED_MAX_CHARS` automáticamente:

```bash
cd $WORKSPACE/repo-a
DEVAI_STATE_DIR="$CENTRAL" devai hooks install

cd $WORKSPACE/repo-b
DEVAI_STATE_DIR="$CENTRAL" devai hooks install

cd $WORKSPACE/repo-c
DEVAI_STATE_DIR="$CENTRAL" devai hooks install
```

El bloque resultante en `.git/hooks/post-commit` de cada repositorio tendrá este aspecto:

```sh
# >>> DEVAI_AUTO_INDEX >>>
# Auto-index after each commit. Managed by 'devai hooks install/uninstall' — do not edit by hand.
( cd "$(git rev-parse --show-toplevel)" && DEVAI_STATE_DIR="/ruta/absoluta/al/store/central" DEVAI_EMBEDDING_MODEL="ml-granite" DEVAI_EMBED_MAX_CHARS="2048" "/ruta/absoluta/a/devai" index --incremental ) >/dev/null 2>&1 &
# <<< DEVAI_AUTO_INDEX <<<
```

Nota: el comando completo del hook es **una sola línea** — sin continuaciones con barra inversa. `DEVAI_EMBEDDING_MODEL` se incluye cuando había un modelo activo al momento de instalar; si no había ninguno, se omite.

### 2.5 Apuntar el cliente MCP al store central

En `.mcp.json` (scope de proyecto) o `~/.claude.json` (global), establece
`DEVAI_STATE_DIR` con la misma ruta central:

```json
{
  "mcpServers": {
    "devai": {
      "command": "/ruta/absoluta/a/devai",
      "args": ["server", "mcp"],
      "env": {
        "DEVAI_STATE_DIR": "/ruta/absoluta/al/store/central"
      }
    }
  }
}
```

Usa `devai server configure --claude --env DEVAI_STATE_DIR="$CENTRAL"` para generar esta
entrada automáticamente. Tras cualquier cambio, reinicia / reconecta el MCP en tu cliente de IA.

### 2.6 Ejecutar el índice inicial

Ejecuta un índice completo en cada repositorio para poblar el store central:

```bash
for repo in repo-a repo-b repo-c; do
  ( cd "$WORKSPACE/$repo" && DEVAI_STATE_DIR="$CENTRAL" devai index )
done
```

> En repositorios grandes o CPUs lentas, el watchdog de inactividad del ML puede dispararse
> a mitad de la ejecución. Establece `DEVAI_ML_IDLE_TIMEOUT_SEC=0` durante el proceso:
> ```bash
> DEVAI_ML_IDLE_TIMEOUT_SEC=0 DEVAI_STATE_DIR="$CENTRAL" devai index
> ```

---

## 3. Guardas para Worktrees

Un git worktree comparte el directorio `.git/hooks/` del repositorio padre mediante el
puntero `gitdir`. Sin una guarda, hacer commit dentro de un worktree dispara el mismo hook
post-commit e indexa el worktree como un **phantom** — símbolos duplicados con una ruta de
working-tree diferente.

Agrega una guarda al inicio del bloque `# >>> DEVAI_AUTO_INDEX >>>` (o justo antes) para
omitir la indexación en worktrees con nombre conocido:

```sh
# Omitir indexación en worktrees que sombreen el repositorio padre.
# Ajusta el patrón de sufijo para que coincida con tus nombres de worktree.
case "$(git rev-parse --show-toplevel)" in
  *-desp|*-hotfix|*_wt) exit 0 ;;
esac
```

El patrón `case` debe coincidir con el sufijo o nombre que uses para los worktrees
(`*-desp`, `*_worktree`, `*/worktrees/*`, etc.).

El `exit 0` dentro de la guarda `case` termina el **script del hook post-commit completo**
de inmediato, con un código de salida limpio (git procede normalmente). Esto es correcto —
y el caso habitual — cuando el bloque de auto-indexación de devai es la única lógica
post-commit del archivo.

**Advertencia:** si tu archivo de hook contiene otras tareas post-commit (linters, scripts
de notificación, etc.), un `exit 0` al inicio del archivo también las saltará en los
worktrees que coincidan con el patrón. En ese caso, protege solo el bloque devai en lugar
del archivo completo — por ejemplo, envuelve la línea devai en un `if` que la omita en los
worktrees coincidentes — para que tus otras tareas sigan ejecutándose.

---

## 4. Errores Frecuentes (Gotchas)

### 4.1 El modelo debe ser igual en todos los repositorios (error A)

Cada `embeddings.model` en `.devai/config.yaml` de cada repo debe ser idéntico. Un modelo
distinto produce vectores de dimensión diferente; `devai index` aborta al detectar el
mismatch tras agregar la guarda, pero un store parcialmente escrito aún requiere un
re-índice completo. Establece el modelo una vez en un `config.yaml` compartido en la raíz
del workspace si es posible, o audita con:

```bash
grep -r "model:" $WORKSPACE/*/.devai/config.yaml 2>/dev/null
```

Ver el [runbook de migración de modelos en Modelos y Tuning](09-modelos-embeddings-y-tuning.md).

### 4.2 `DEVAI_EMBED_MAX_CHARS` previene OOM en chunks grandes

Los archivos grandes o minificados pueden producir chunks de texto enormes. Sin un límite,
el modelo de embeddings asigna el tensor completo para cada chunk y puede agotar la RAM a
mitad de la indexación (especialmente con `ml-granite` en CPU).

`devai hooks install` **siempre** incrusta `DEVAI_EMBED_MAX_CHARS` en el hook — su valor
por defecto es `"2048"` cuando la variable de entorno no está definida, por lo que la
protección contra OOM se aplica automáticamente. Para usar un techo diferente, define
`DEVAI_EMBED_MAX_CHARS` en tu shell **antes** de ejecutar `devai hooks install`:

```bash
DEVAI_EMBED_MAX_CHARS=4096 DEVAI_STATE_DIR="$CENTRAL" devai hooks install
```

El valor queda incrustado en la línea del hook y se aplica a cada ejecución de indexación
en segundo plano.

### 4.3 Nunca commitear `.mcp.json`

`.mcp.json` puede contener claves de API (`DEVAI_API_TOKEN`, claves de embedding, claves de
Qdrant). Agrégalo al `.gitignore`:

```bash
echo ".mcp.json" >> .gitignore
```

### 4.4 `config.yaml` gana sobre `DEVAI_EMBEDDING_MODEL`

Si el `config.yaml` de un repo tiene `embeddings.model: minilm-l6` pero defines
`DEVAI_EMBEDDING_MODEL=ml-granite` en el entorno, el CLI usa `minilm-l6`. El archivo de
configuración siempre gana sobre la variable de entorno para la clave del modelo. Cambia el
archivo (o ejecuta `devai model use <clave>`) para cambiar de modelo. Ver
[Configuración §1.3](11-configuracion.md).

---

## 5. Nota de Transición

Antes de los cambios de defecto recientes:

- `devai init` escribía un `state_dir:` explícito en el `config.yaml` de cada repositorio,
  apuntando al `.devai/state/` propio del repo. El sharing multi-repo requería sobrescribir
  ese campo manualmente en cada archivo.
- `devai hooks install` escribía un `devai index --incremental` sin incrustar el modelo ni
  el límite de chars, lo que significaba que los hooks podían usar el modelo incorrecto tras
  un `devai model use` o quedarse sin RAM en archivos grandes.

Tras los cambios recientes de defecto:

- `devai init` omite `state_dir` (sin defecto por repositorio). El store central se adopta
  automáticamente cuando se define `DEVAI_STATE_DIR` o se establece `state_dir` en `config.yaml`.
- `devai hooks install` incrusta el modelo activo y `DEVAI_EMBED_MAX_CHARS` en el bloque del
  hook, de modo que los hooks se mantienen sincronizados al cambiar el modelo o el límite.

Si tienes **repositorios existentes** inicializados con el comportamiento anterior (con
`state_dir` explícito por repositorio), sobrescribe mediante:

1. Editar cada `.devai/config.yaml` para establecer `state_dir: /ruta/absoluta/al/store/central`, o
2. Pasar siempre `DEVAI_STATE_DIR="$CENTRAL"` en la línea de comandos y en el entorno del MCP.

Vuelve a ejecutar `DEVAI_STATE_DIR="$CENTRAL" devai hooks install` en cada repositorio tras
la transición para regenerar los hooks con la ruta correcta del store.
