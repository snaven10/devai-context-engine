# Modelos de embeddings, summarizer y tuning

Guía práctica de los modelos disponibles, las estrategias de presupuesto de
tokens, qué configuración conviene según el hardware, y los comportamientos
verificados empíricamente (pruebas del 2026-05-28).

> **Contexto**: esta instalación migró de `minilm-l6` (384 dims, entrenado en
> inglés) a **`ml-mpnet`** (768 dims, multilingüe) para mejorar el recall y el
> resumen de memorias escritas en **español**. La documentación de abajo explica
> por qué, cómo, y cómo ajustarlo a otros equipos.

---

## 1. Modelos de embeddings disponibles

El registro vive en `ml/devai_ml/embeddings/local.py` (`MODEL_REGISTRY`). Todos
corren localmente vía `sentence-transformers`. Se selecciona con la clave en
`embeddings.model` del `config.yaml`, o con `devai model use <clave>`.

| Clave | Modelo | Dims | Tamaño | Velocidad | Idioma | Fuerte en |
|-------|--------|------|--------|-----------|--------|-----------|
| `minilm-l6` | all-MiniLM-L6-v2 | 384 | 22 MB | muy rápida | 🇬🇧 inglés | máquinas con pocos recursos, código/texto en inglés |
| `minilm-l12` | all-MiniLM-L12-v2 | 384 | 33 MB | rápida | 🇬🇧 inglés | algo más de precisión que L6, sigue liviano |
| `bge-small` | BAAI/bge-small-en-v1.5 | 384 | 33 MB | rápida | 🇬🇧 inglés | mejor recuperación que MiniLM en inglés |
| `bge-base` | BAAI/bge-base-en-v1.5 | 768 | 110 MB | media | 🇬🇧 inglés | máxima precisión en inglés, repos grandes |
| **`ml-minilm`** | paraphrase-multilingual-MiniLM-L12-v2 | 384 | 470 MB | rápida | 🌍 50+ idiomas | **español rápido**, equipos chicos con contenido multilingüe |
| **`ml-mpnet`** | paraphrase-multilingual-mpnet-base-v2 | 768 | 1.1 GB | media | 🌍 50+ idiomas | **máxima calidad en español** (torch), equipos con CPU decente o GPU |
| **`ml-granite`** | granite-embedding-97m-multilingual-r2 (**ONNX int8**) | 384 | 94 MB | **muy rápida** | 🌍 multilingüe | **mejor multilingüe en CPU**: top recall + indexado más rápido + mitad de almacenamiento |
| `ml-granite-lg` | granite-embedding-311m-multilingual-r2 (**ONNX int8**) | 768 | 299 MB | media | 🌍 multilingüe | hermano de 768 dims; `ml-granite` lo iguala/supera en CPU — usar solo si necesitás 768 dims |

> 🔹 **`ml-granite` / `ml-granite-lg` se cargan vía el backend ONNX**
> (`onnx/model_quint8_avx2.onnx`). El backend (`optimum`) lo instala
> automáticamente `devai setup`; si instalaste el paquete a mano, corré
> `pip install 'devai-ml[onnx]'`. Requiere una CPU x86 con **AVX2**. No requieren
> prefijos `query:`/`passage:`.

### Cuál elegir

- **Contenido en español/mixto en CPU** → **`ml-granite`** es ahora el mejor default:
  le gana a `ml-mpnet` en recall indexando ~6x más rápido y usando la mitad del
  almacenamiento de vectores (ver el benchmark abajo). Caé a `ml-mpnet` (torch) solo
  si tu CPU no tiene AVX2 o no podés instalar el extra `onnx`; `ml-minilm` si querés
  la opción torch multilingüe más liviana. Ninguno requiere prefijos.
- **Solo inglés** → `bge-base` (mejor) o `minilm-l6` (más liviano).
- **Evitar los `e5`**: rinden por debajo de su potencial acá porque el provider
  no agrega los prefijos `query:`/`passage:` que esos modelos necesitan.

> ⚠️ **Cambiar de modelo cambia la dimensión del vector** (384 ↔ 768). El vector
> store es incompatible entre dimensiones → **obliga a re-indexar todo**. Ver §6.

### Benchmark (medido, solo CPU)

Corpus de dominio de 49 documentos / 40 consultas (contenido técnico en español),
medido en una máquina solo-CPU. El throughput de indexado usa lotes sostenidos de 32.

| Modelo | Backend | Dims | Recall@1 | MRR | Velocidad indexado (textos/s) | RAM pico | Disco |
|--------|---------|------|----------|-----|------------------------------|----------|-------|
| `ml-mpnet` (default anterior) | torch | 768 | 87.5% | 0.921 | 17.5 | 1248 MB | 1060 MB |
| e5-base (cuant. de terceros) | ONNX int8 | 768 | 82.5% | 0.906 | 22.3 | 559 MB | 270 MB |
| granite-97m | torch | 384 | 95.0% | 0.975 | 9.3 | 841 MB | 186 MB |
| **`ml-granite` (granite-97m)** | **ONNX int8** | **384** | **95.0%** | **0.975** | **58.7** | 822 MB | **94 MB** |
| `ml-granite-lg` (granite-311m) | ONNX int8 | 768 | 92.5% | 0.963 | 15.0 | 1177 MB | 299 MB |

**Conclusiones:**
- **El backend ONNX es lo que desbloquea todo.** El mismo granite-97m pasa de 9.3 a
  58.7 textos/s (**6.3x**) al cambiar torch → ONNX int8, con **calidad idéntica**
  (la cuantización int8 no degradó al modelo de 97M).
- `ml-granite` gana en cada eje frente al default anterior `ml-mpnet`: más recall
  (95% vs 87.5%), indexado más rápido, mitad de dimensión (vector store más chico)
  y la menor huella en disco — con RAM comparable.
- **La cuantización es sensible al modelo.** En el granite-311m más grande, el ONNX
  int8 *sí* bajó la calidad (97.5% → 92.5%), así que el modelo chico es el punto
  óptimo en CPU.
- La familia e5 sigue siendo la peor acá (sin soporte de prefijos) — confirma la advertencia de arriba.

> Tiempo de indexado proyectado a este throughput: ~50k chunks tardan **~14 min**
> con `ml-granite` vs **~48 min** con `ml-mpnet` (y ~5 h con granite-311m en torch).

### Tamaño de chunk vs la ventana de contexto del modelo

El chunker (`semantic_chunker.py`) mide los chunks en **tokens** (`DEVAI_MAX_CHUNK_TOKENS`,
default **512**), cortando por AST para nunca partir un símbolo a la mitad. Pero el
embedder solo embebe hasta su `max_seq_length`:

| Modelo | `max_seq_length` | vs el chunk de 512 tokens |
|--------|------------------|----------------------------|
| `ml-mpnet` | **128 tokens** | los chunks se **truncaban** — la cola de los chunks grandes nunca llegaba al vector |
| `ml-granite` | **32768 tokens** | el chunk completo se embebe, sin truncar |

O sea, `ml-mpnet` tenía un desajuste latente: emite chunks de 512 tokens pero solo
embebe los primeros 128. `ml-granite` elimina ese desperdicio.

**¿Un chunk más grande mejora el recall?** Medido sobre un repo real (137 archivos, 12
queries con ground truth) a través del **pipeline real (vector fetch → rerank flashrank → top-k)**:

| tamaño chunk | chunks | Recall@1 | Recall@3 |
|--------------|--------|----------|----------|
| 256 | 603 | 58% | 67% |
| 512 | 582 | 58% | 67% |
| 1024 | 570 | 58% | 67% |

**El recall es el mismo entre 256–1024.** El tamaño de chunk no es la palanca que parece.
La razón: el **reranker lee el TEXTO completo del chunk** (guardado sin truncar), no el
embedding — así que mientras el chunk correcto entre en la ventana de fetch, el reranker
lo recupera aunque el embedder lo haya truncado. (Por eso también `ml-mpnet` no queda tan
lisiado en la práctica como sugiere su ventana de 128 tokens.)

**Recomendación:** elegí el tamaño de chunk por **eficiencia, no por recall**. Dejá **512**
(default) o subí a **1024** (menos chunks → índice más chico y rápido; método entero en un
chunk). **256 no aporta** — más almacenamiento, mismo recall. Con `ml-granite` (32768) ningún
tamaño se trunca jamás. *(Caveat: medido con 12 queries — la señal "todos iguales" es robusta,
pero diferencias finas pedirían un harness de 50+ queries.)*

#### Tope de RAM: `DEVAI_EMBED_MAX_CHARS` (protección contra OOM en CPU)

La ventana de 32768 tokens del modelo **no** es un límite de RAM. Los encoders exportados a ONNX
no acotan la secuencia de entrada, y el **raw parser** (archivos no-AST: `json`/`sql`/`md`
minificados, lockfiles, bundles) puede emitir **un solo chunk de cientos de miles de tokens**
— se observó hasta ~2.8M chars. En CPU la atención O(N²) sobre ese blob infla el arena de ONNX
a **~20 GB** y el OOM killer del SO mata el indexador a mitad del repo.

Por eso `embed()` recorta cada texto a **`DEVAI_EMBED_MAX_CHARS` (default 4096 ≈ 1024 tokens)**
antes de embeber. 4096 = `large_function_threshold`, así que **ningún chunk de código se trunca**
(el chunker por AST los mantiene ≤1024 tokens) — solo se recortan los blobs raw gigantes. El
texto **guardado** queda intacto; solo el *vector* se calcula sobre el prefijo, y el reranker lee
el texto completo igual, así que el recall del código no se ve afectado.

| Env | Default | Efecto |
|-----|---------|--------|
| `DEVAI_EMBED_MAX_CHARS` | `4096` | Chars por texto que llegan al encoder. `0` lo desactiva. Subilo si tenés RAM/GPU de sobra; bajalo (ej. `2048` → pico ~1–3 GB) si vas justo de memoria. |
| `DEVAI_EMBED_BATCH_SIZE` | `16` | Batch del encoder. Un chunk grande paddea todo el batch, así que batches más chicos también bajan el pico. |

> Medido (CPU, 8 cores, granite int8): con `MAX_CHARS=2048` el pico de RAM por repo baja de
> **~20.9 GB (OOM) → ~1–3 GB**; 4096 se queda cómodo bajo un cap de cgroup de 20 GB. En máquinas
> ajustadas, combinalo con un `systemd-run --user --scope -p MemoryMax=…` para que un descontrol
> nunca tumbe la máquina entera (incluido WSL).

---

## 2. El pipeline de respuesta: rerank → presupuesto de tokens

Cuando llamás `recall` o `search`, el flujo es:

```
vector search (top_k_fetch)  →  reranker  →  token budget (fit)  →  respuesta
```

1. **Reranker** (`DEVAI_RERANK_*`): por defecto `flashrank` (ms-marco-MiniLM-L-12-v2).
   Reordena por relevancia y recorta a `limit`. El modelo por defecto es **INGLÉS**
   — reordena bien pero da scores más bajos en consultas cross-lingual (query en
   inglés contra memoria en español: rankea #1 correcto pero con score ~0.37). Para
   contenido no-inglés, setear **`DEVAI_RERANK_MODEL=ms-marco-MultiBERT-L-12`** — un
   modelo flashrank multilingüe (misma velocidad ONNX/CPU, ~150 ms para 15
   candidatos). Medido: el mismo query cross-lingual salta de **~0.37 → ~0.99**. NO
   requiere re-index — el reranker corre solo en query-time. Otras opciones flashrank:
   `ms-marco-TinyBERT-L-2-v2` (el más rápido), `ms-marco-MiniLM-L-12-v2` (default,
   inglés), `ms-marco-MultiBERT-L-12` (multilingüe).

2. **Token budget** (`DEVAI_TOKEN_*` + `DEVAI_SUMMARIZER_*`): ajusta el contenido
   para no exceder `DEVAI_MAX_OUTPUT_TOKENS`. Aquí se decide drop/resumen/truncado.

### La fórmula del presupuesto por item

```
presupuesto_por_item = max(DEVAI_MAX_OUTPUT_TOKENS / limit, 128)
```

Cada memoria que **cabe** en su tajada se devuelve **verbatim**; la que se pasa,
se procesa según la estrategia. Con `MAX_OUTPUT_TOKENS=8000`:

| `limit` | tajada/item | efecto |
|---------|-------------|--------|
| 4 | 2000 tok | casi todo verbatim |
| 8 | 1000 tok | medianas verbatim, grandes resumidas |
| 12 | 666 tok | muchas resumidas |
| 18 | 444 tok | casi todas resumidas |

**Regla práctica**: una memoria sale verbatim ⟺ `tamaño_memoria ≤ 8000 / limit`.
Una memoria de 600 tok es verbatim hasta `limit ≤ 13`; una de 2000 tok, hasta `limit ≤ 4`.

---

## 3. Estrategias de presupuesto (`DEVAI_TOKEN_STRATEGY`)

| Estrategia | Qué hace | Costo CPU | Pierde items | Recomendación |
|-----------|----------|-----------|--------------|---------------|
| `drop` | descarta items enteros desde el peor rankeado hasta caber | **cero** | **SÍ** ❌ | evitar para memorias — oculta resultados relevantes |
| `soft_truncate` | corta cada item grande en borde de oración (conserva el principio) | **cero** | no | bueno para equipos chicos / hojear |
| `hard_truncate` | corta en conteo exacto de chars | cero | no | rara vez |
| `summarize` | resume cada item grande con el summarizer | depende del summarizer | no | **recomendado** con `extractive` |

> **El bug original**: con `drop` + `MAX_OUTPUT_TOKENS=4000`, 1-2 memorias grandes
> llenaban el presupuesto y las demás se **botaban silenciosamente** →
> `items_dropped: 9` → uno concluía "esa memoria no existe" cuando sí existía.
> Cualquier estrategia ≠ `drop` mantiene `output_count == input_count`.

---

## 4. Summarizers (`DEVAI_SUMMARIZER_PROVIDER`)

| Provider | Tipo | Local | Veredicto |
|----------|------|-------|-----------|
| `noop` | ninguno | ✅ | con `strategy=summarize` cae a truncado — no sirve |
| **`extractive`** | extractivo (elige oraciones por similitud al query) | ✅ | **recomendado**: reusa el modelo de embeddings, no corrompe identificadores, encuentra contenido enterrado |
| `flan-t5` | abstractivo (genera texto) | ✅ | **NO usar para código/español**: corrompe identificadores (ej. un símbolo `getStatusById` sale `getStatuById`) y palabras en español (`Diseño`→`Diseo`), límite de 512 tokens de entrada, lento. Parcheado para transformers 5.x pero igual no recomendado |
| `openai` | abstractivo cloud | ❌ | bloqueado por `require_local=true` (fuga de datos) |

**`extractive` es la elección correcta** para una herramienta de memoria de código:
- Preserva identificadores **verbatim** (elige oraciones completas, no parte palabras).
- Es **query-focused**: trae las oraciones relevantes a lo que buscaste, aunque
  estén al final de una memoria larga.
- Reusa el modelo de embeddings ya cargado → no descarga nada extra.

---

## 5. Configuración recomendada por hardware

El factor de CPU más pesado es **el modelo de embeddings** (ml-mpnet 768d es ~5x
más lento que minilm-l6 en CPU). La estrategia de resumen es secundaria
(`extractive` agrega ~0.5-1 s por recall al embeber oraciones; `soft_truncate` es gratis).

### 🖥️ PC con CPU, contenido en ESPAÑOL — RECOMENDADO
```jsonc
DEVAI_EMBEDDING_MODEL    = "ml-granite"        // 384d multilingüe, ONNX int8
DEVAI_EMBEDDING_DEVICE   = "cpu"
DEVAI_TOKEN_STRATEGY     = "summarize"
DEVAI_SUMMARIZER_PROVIDER= "extractive"
DEVAI_MAX_OUTPUT_TOKENS  = "8000"
```
> Mejor calidad **y** indexado más rápido en CPU (ver el benchmark en §1). El backend
> ONNX viene con `devai setup`; necesita una CPU con AVX2. Si tu CPU no tiene AVX2,
> usá `ml-mpnet` (mejor calidad torch) o `ml-minilm` (más liviano) abajo.

### 🖥️ PC pequeña / sin GPU (o GPU débil), contenido en ESPAÑOL
```jsonc
DEVAI_EMBEDDING_MODEL    = "ml-minilm"        // 384d multilingüe, rápido
DEVAI_EMBEDDING_DEVICE   = "cpu"
DEVAI_TOKEN_STRATEGY     = "soft_truncate"    // cero CPU extra, no pierde items
DEVAI_MAX_OUTPUT_TOKENS  = "6000"
DEVAI_RERANK_PROVIDER    = "flashrank"
```
> **¿Desactivar drop y summarize en PC chica?** Sí a `drop` (pierde memorias,
> nunca conviene). En cuanto a `summarize`: en un equipo chico conviene
> `soft_truncate` en su lugar — mantiene TODAS las memorias y **no gasta CPU**
> extra (no embebe oraciones). Usá `summarize`+`extractive` solo si tolerás
> ~1 s más por recall a cambio de resúmenes query-focused.

### 🖥️ PC potente / con GPU, contenido en ESPAÑOL  (← esta instalación)
```jsonc
DEVAI_EMBEDDING_MODEL    = "ml-mpnet"         // 768d multilingüe, máxima calidad
DEVAI_EMBEDDING_DEVICE   = "cpu"              // o "cuda" si hay GPU buena
DEVAI_TOKEN_STRATEGY     = "summarize"
DEVAI_SUMMARIZER_PROVIDER= "extractive"
DEVAI_MAX_OUTPUT_TOKENS  = "8000"
```

### 🖥️ Contenido solo en INGLÉS
```jsonc
DEVAI_EMBEDDING_MODEL    = "bge-base"   // o "minilm-l6" si el equipo es chico
DEVAI_TOKEN_STRATEGY     = "summarize"
DEVAI_SUMMARIZER_PROVIDER= "extractive"
```

### Costo medido (CPU, sin GPU — laptop con GPU Maxwell vieja, solo CPU)
- `ml-granite` (ONNX int8): ~58 textos/seg en batch — el más rápido de los
  multilingües; ~50k chunks en ~14 min. Mitad de dimensión (384) → store más chico.
- `ml-mpnet`: ~225 ms por embed de memoria; ~17-27 chunks/seg en batch; ~50k chunks ~48 min.
- Re-index de un repo grande (~1500 archivos, ~7000 chunks, 58k edges): ~2 h con
  `ml-mpnet`, proporcionalmente más rápido con `ml-granite`.
- Recall normal: ~1-2 s. (`minilm-l6` era ~5x más rápido que `ml-mpnet`.)

---

## 6. Comportamientos verificados (pruebas 2026-05-28)

Batería de pruebas empíricas sobre memorias reales con `ml-mpnet` + `extractive`:

| Prueba | Qué se midió | Resultado |
|--------|--------------|-----------|
| Contenido al FINAL | query apuntando al último párrafo | `summarize`/extractive **lo encuentra** ✅; `soft_truncate` lo pierde ❌ |
| Umbral verbatim | barrido de presupuesto | verbatim si `presupuesto ≥ tamaño memoria`; resume si menos |
| Presupuesto mínimo (60 tok) | compresión extrema | coherente, **identificadores intactos, cero corrupción** |
| 3 estrategias | drop/summarize/soft_truncate | drop = todo-o-nada; summarize = comprime lo relevante; soft = lineal |
| Multilingüe EN→ES | query en inglés, memoria en español | match #1 correcto — score ~0.37 con reranker inglés, ~0.99 con `ms-marco-MultiBERT-L-12` |
| Código (`search`) | — | fuerza `drop` automáticamente — **el código nunca se resume** (evita corromper identificadores) |

**Conclusiones**:
- `extractive` trae contenido relevante aunque esté enterrado en una memoria larga
  → es la estrategia correcta para recall por consulta puntual.
- El cruce multilingüe funciona (query inglés ↔ contenido español) gracias a mpnet.
- `summarize`/`soft_truncate` nunca pierden memorias (`output_count == input_count`).

### Cheat sheet de uso

| Querés... | Configurá / usá |
|-----------|-----------------|
| Detalle exacto de algo puntual | `limit 3-5` → verbatim completo |
| Explorar un tema amplio | `limit 12-18` → muchos resultados al grano, 0 perdidos |
| Buscar en otro idioma | nada — `ml-mpnet`/`ml-minilm` ya lo bridgean |
| Que siempre traiga lo relevante aunque esté enterrado | `summarize` + `extractive` (ya activo) |

---

## 7. Gotchas al migrar de modelo (aprendidos en producción)

1. **`config.yaml` vence al env var.** El CLI Go (`devai index`) y el MCP leen
   `embeddings.model` del `config.yaml` y lo pasan a Python **sobrescribiendo**
   `DEVAI_EMBEDDING_MODEL`. **Cada repo tiene su propio `.devai/config.yaml`** +
   uno en la raíz del workspace + uno en `state/`. Cambiar solo el env NO basta:
   usar `devai model use <clave>` en CADA repo, o editar todos los `config.yaml`.
   (El default del template está en `cmd/devai/cmd/init.go` → ya apunta a `ml-mpnet`.)

2. **Wipear `vectors/` no basta — limpiar `file_state`.** El re-index chequea el
   hash por archivo en la tabla `file_state` (en `index.db`) y **salta** los que
   coinciden, aunque los vectores ya no existan. `--incremental=false` NO bypasea
   el chequeo. Hay que `DELETE FROM file_state` (y `index_state`) para forzar el
   re-embed. **`index.db` contiene las memorias y el grafo → NO borrarlo**, solo
   esas dos tablas. Las memorias se re-embeben con el script
   `reembed_memories.py` (no hay comando nativo).

3. **El idle watchdog (1800 s) mata el re-index largo.** `index_repo` es UNA sola
   llamada RPC; el watchdog mide "idle" como tiempo sin requests nuevos, no
   actividad de CPU. Un repo grande con `ml-mpnet` tarda > 30 min → el watchdog
   mata el ML service (`reading response: EOF`). Para re-indexar:
   `DEVAI_ML_IDLE_TIMEOUT_SEC=0`.

### Procedimiento completo de cambio de modelo
```bash
# 1. cambiar el modelo en TODOS los config.yaml
for r in repoA repoB ...; do (cd "$r" && devai model use ml-mpnet); done
# 2. apagar el MCP/ML service (liberar el LanceDB)
# 3. wipe del vector store (conserva index.db con memorias+grafo)
rm -rf "$DEVAI_STATE_DIR/vectors"
# 4. limpiar file_state + index_state en index.db (NO memories)
#    sqlite3 index.db "DELETE FROM file_state; DELETE FROM index_state;"
# 5. re-indexar cada repo con el watchdog apagado
for r in repoA repoB ...; do
  (cd "$r" && DEVAI_ML_IDLE_TIMEOUT_SEC=0 devai index --incremental=false)
done
# 6. re-embeber memorias con el modelo nuevo
DEVAI_EMBEDDING_MODEL=ml-mpnet python reembed_memories.py
# 7. reconectar el MCP
```

---

## 8. Dónde vive cada configuración

| Archivo | Lo lee | Para qué |
|---------|--------|----------|
| `<repo>/.devai/config.yaml` | CLI `devai index` (desde ese repo) | modelo + excludes al indexar ese repo |
| `<workspace>/.devai/config.yaml` | MCP (cwd = raíz) | modelo del servicio MCP |
| `<workspace>/.devai/state/config.yaml` | resolución de state compartido | state_dir compartido |
| `.mcp.json` (env del cliente) | MCP en runtime | strategy, summarizer, max_tokens, rerank, idle timeout |

**Todos deben tener el MISMO modelo** o reaparece el gotcha #1.
