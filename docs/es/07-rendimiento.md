> 🌐 [English version](../07-performance.md)

# Rendimiento

Este documento cubre qué podés esperar de DevAI en términos de velocidad, uso de recursos y cómo optimizar para tu carga de trabajo.

---

## Rendimiento de Indexación

### Qué Afecta la Velocidad de Indexación

Tres factores dominan:

1. **Tamaño del repositorio** — Más archivos significa más parseo, chunking y embedding. Relación lineal.
2. **Mix de lenguajes** — La velocidad de parseo de tree-sitter varía según la complejidad de la gramática. Las gramáticas de TypeScript/TSX son más pesadas que las de Go o Python. Importa a escala (10K+ archivos).
3. **Proveedor de embeddings** — El cuello de botella. Los embeddings locales son CPU-bound. Los embeddings por API son network-bound.

### Benchmarks de Throughput

| Proveedor | Velocidad | Notas |
|----------|-------|-------|
| Local (minilm-l6-v2) | ~100-500 archivos/minuto | Depende del CPU. Más rápido en máquinas con soporte AVX2. |
| OpenAI API | ~200-1000 archivos/minuto | Network-bound. Los rate limits pueden limitar. |
| Local con GPU | ~500-2000 archivos/minuto | Si PyTorch detecta CUDA/MPS. |

Estos son aproximados. "Archivos por minuto" varía según el tamaño del archivo y la cantidad de chunks — un archivo Go de 50 líneas produce 1-2 chunks, un componente React de 500 líneas produce 10-15.

### Indexación Incremental vs Completa

**La indexación incremental** (predeterminada) procesa solo los archivos que cambiaron desde el último índice, determinado por `git diff`. Para un commit típico que toca 5-20 archivos, la indexación se completa en segundos.

**Una reindexación completa** se dispara por:
- Primer índice de un repositorio
- Cambio de modelo de embedding (las dimensiones del vector difieren)
- Solicitud explícita del usuario (`devai index --full`)
- Vector store corrupto o eliminado

La indexación incremental es la funcionalidad de rendimiento más importante. Es lo que hace que DevAI sea práctico para uso continuo durante el desarrollo.

---

## Rendimiento de Búsqueda

Todos los benchmarks a continuación son para almacenamiento local (LanceDB + SQLite). Los stores conectados por red (Qdrant) agregan latencia.

### Búsqueda Vectorial

- **Latencia típica**: <100ms para repos de hasta 100K chunks
- **Cómo funciona**: El texto de consulta se transforma en embedding, luego se ejecuta una búsqueda de vecinos más cercanos aproximada contra el vector store
- **Escalamiento**: LanceDB usa indexación IVF. El rendimiento degrada gradualmente — un repo de 500K chunks podría tener consultas de 150-200ms
- **Arranque en frío**: La primera consulta después de iniciar el proceso puede ser más lenta (carga del índice). Las consultas subsiguientes se benefician de índices cacheados.

### Consultas de Grafo

- **Latencia típica**: <50ms
- **Cómo funciona**: SQLite con columnas indexadas para nombres de símbolos, rutas de archivos y tipos de relación
- **Caso de uso**: "¿Qué llama a esta función?", "¿Qué importa este módulo?", "Mostrá la jerarquía de clases"
- **Escalamiento**: SQLite maneja millones de aristas sin problema. El grafo es disperso en relación al vector store.

### Búsqueda de Memoria

- **Latencia típica**: <200ms
- **Cómo funciona**: Búsqueda híbrida combinando similitud semántica (vector) con filtrado de metadata (tipo, proyecto, alcance)
- **Por qué más lenta**: Dos etapas — búsqueda vectorial para candidatos, luego filtrado de metadata y ranking

### build_context (Agregado)

- **Latencia típica**: 200-500ms
- **Cómo funciona**: Ejecuta búsqueda vectorial + recorrido de grafo + búsqueda de memoria en paralelo, luego combina y rankea resultados
- **Esta es la herramienta que más usan los agentes.** Está optimizada para consumo por agentes — retorna contexto formateado, no resultados crudos.

---

## Almacenamiento

### Costos Por Chunk

| Componente | Tamaño por chunk | Qué almacena |
|-----------|---------------|----------------|
| Embedding vectorial | ~768 bytes (384 dims x float16) | Representación semántica |
| Metadata | ~200-500 bytes | Ruta de archivo, nombre de símbolo, rango de líneas, lenguaje |
| Contenido | ~200-2000 bytes | Texto de código fuente crudo |
| **Total por chunk** | **~1KB promedio** | |

### Tamaños Típicos de Repositorio

| Tamaño del repo | Chunks estimados | Tamaño del vector store | SQLite (grafo + memoria) |
|-----------|-----------------|-------------------|------------------------|
| 1K archivos | ~5K chunks | ~5MB | ~2MB |
| 10K archivos | ~50K chunks | ~50MB | ~10MB |
| 50K archivos | ~250K chunks | ~250MB | ~40MB |
| 100K archivos | ~500K chunks | ~500MB | ~80MB |

Estas son estimaciones aproximadas. Los números reales dependen del tamaño de los archivos y el lenguaje (un codebase en Go produce menos chunks por archivo que un codebase React con JSX).

### Ubicación de Almacenamiento

Por defecto, DevAI almacena datos en `.devai/` dentro de la raíz del repositorio. Este directorio debería agregarse a `.gitignore`.

```
.devai/
  vectors/     # Archivos de LanceDB
  graph.db     # SQLite — grafo de código
  memory.db    # SQLite — memorias persistentes
  config.yaml  # Configuración específica del repositorio
```

---

## Tips de Optimización

### Usá Indexación Incremental

Está activada por defecto. No la desactives. Si te encontrás corriendo reindexaciones completas frecuentemente, algo anda mal — abrí un issue.

### Excluí Archivos Generados

Los archivos generados grandes (bundles, salida compilada, directorios de vendor) desperdician tiempo de indexación y contaminan los resultados de búsqueda. Configurá exclusiones:

```yaml
# config.yaml
indexing:
  exclude:
    - "vendor/**"
    - "dist/**"
    - "node_modules/**"
    - "*.min.js"
    - "*.generated.go"
    - "*.pb.go"
```

DevAI respeta `.gitignore` por defecto, pero las exclusiones explícitas en la configuración te dan control más fino.

### Elegí el Proveedor de Embeddings Correcto

| Escenario | Recomendado | Por qué |
|----------|-------------|-----|
| Desarrollo local, sensible a privacidad | `local` (minilm-l6) | Sin red, suficientemente rápido, buena calidad para código |
| Índice inicial grande (50K+ archivos) | `local` con GPU | El embedding por lotes aprovecha bien la GPU |
| Índice compartido del equipo | `openai` o API compatible | Resultados consistentes entre máquinas |
| Entorno air-gapped | `local` | Única opción, funciona bien |

### Modo Watch

Para el ciclo de feedback más rápido, usá el modo watch. DevAI monitorea cambios de archivos y re-indexa automáticamente:

```bash
devai watch
```

Esto re-indexa archivos individuales al guardar. Latencia desde guardar hasta que sea buscable: típicamente 1-3 segundos.

### Batch vs Streaming

Al indexar, DevAI agrupa las solicitudes de embedding en lotes (tamaño de lote predeterminado: 32 textos). Si estás viendo errores de OOM durante la indexación:
- Reducí el tamaño del lote en la configuración
- Asegurate de no estar indexando archivos generados masivos
- Verificá que los patrones de exclusión estén funcionando

---

## Uso de Recursos

### Servicio ML de Python

| Estado | RAM | CPU | Notas |
|-------|-----|-----|-------|
| Idle (modelo cargado) | ~200MB | Casi cero | El modelo se mantiene en memoria para consultas rápidas |
| Indexando (embed por lotes) | ~400-800MB | Alto (1-2 cores) | Picos durante lotes de embedding |
| Consulta de búsqueda | ~250MB | Pico breve | Embedding de la consulta + búsqueda |
| Arranque en frío | ~150MB -> 200MB | Moderado | La carga del modelo toma 2-5 segundos |

### Proceso Go

| Estado | RAM | CPU |
|-------|-----|-----|
| Idle | ~20MB | Casi cero |
| Manejando request MCP | ~25-30MB | Pico breve |
| Inicio | ~15MB | Mínimo |

El proceso Go es liviano por diseño. Es un servidor MCP delgado que reenvía al servicio Python.

### I/O de Disco

- **Indexación**: Escritura intensiva. Upserts de vectores e inserts en SQLite.
- **Búsqueda**: Lectura intensiva. Escaneo de índice vectorial y consultas SQLite.
- **SSD fuertemente recomendado.** El rendimiento de LanceDB en discos mecánicos es significativamente peor.

### Red

- **Embeddings locales**: Cero uso de red.
- **Embeddings por API**: ~1KB por solicitud de embedding, ~3KB de respuesta. Para un repo de 10K archivos, el índice inicial envía ~50K llamadas API (en lotes).
- **Qdrant (si se usa)**: Llamadas de red por búsqueda/upsert. La latencia depende de la ubicación del deployment.

---

## Monitoreo

DevAI loguea el progreso de indexación a stderr. Métricas clave a observar:

- **Archivos procesados / total**: Muestra el progreso de indexación
- **Chunks creados**: Si es mucho más alto de lo esperado, verificá que no haya archivos grandes colándose entre las exclusiones
- **Tiempo de embedding**: Si domina, considerá cambiar de proveedor o agregar GPU
- **Errores/omisiones**: Archivos que fallaron al parsear (generalmente archivos binarios que se colaron en la detección)

Para monitoreo programático, el método JSON-RPC `devai/status` retorna el estado actual de indexación y estadísticas.
