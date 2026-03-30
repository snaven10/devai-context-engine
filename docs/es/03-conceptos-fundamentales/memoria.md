> 🌐 [English version](../../03-core-concepts/memory.md)

# Memoria

## Que es

La memoria de DevAI es un almacen de conocimiento persistente y estructurado para agentes de IA. Captura decisiones, descubrimientos, patrones y bugs que sobreviven entre sesiones — dandole a los agentes memoria a largo plazo que el historial de chat nunca fue disenado para proveer.

## Por que existe

Los agentes de IA tienen un problema de amnesia. Cada sesion arranca de cero. La decision de arquitectura que debatiste durante 30 minutos? Se fue. La causa raiz de ese bug en produccion? Olvidada. La convencion de nombres que el equipo acordo? Perdida.

Los enfoques ingenuos no resuelven esto:

| Enfoque | Problema |
|---|---|
| **Historial de chat** | Efimero. Se pierde al cerrar la sesion. Crece sin limites. No es buscable por concepto. |
| **Comentarios en codigo** | Estaticos. No pueden capturar decisiones, tradeoffs o el contexto que llevo al codigo. Contaminan el codebase. |
| **Documentacion externa** | Desconectada del codigo. Obsoleta en semanas. Los agentes no pueden consultarla semanticamente. |
| **Stores solo vectoriales** | Sin estructura. No pueden distinguir un bugfix de una decision de arquitectura. Sin deduplicacion. |

La memoria de DevAI es estructurada (tipada, con scope, con tags), deduplicada (sin entradas redundantes por guardados repetidos) y buscable (busqueda hibrida semantica + filtrado por metadata). Esta disenada especificamente para el caso de uso de agentes de IA: escrituras frecuentes desde workflows automatizados, recall semantico por consultas en lenguaje natural y upserts por topic que mantienen el conocimiento actualizado en vez de acumular duplicados.

## Como funciona internamente

### Almacenamiento

Las memorias se almacenan en SQLite con los siguientes campos:

| Campo | Tipo | Descripcion |
|---|---|---|
| `title` | string | Resumen corto y buscable (ej: "Fixed N+1 query in UserList") |
| `content` | text | Contenido estructurado completo (que, por que, donde, aprendizaje) |
| `type` | enum | `insight`, `decision`, `note`, `bug`, `architecture`, `pattern`, `discovery` |
| `scope` | enum | `shared` (visible para el equipo) o `local` (personal) |
| `project` | string | Identificador del proyecto |
| `topic_key` | string | Clave estable para upserts (ej: `architecture/auth-model`) |
| `tags` | list | Tags buscables |
| `author` | string | Quien lo creo |
| `files` | list | Rutas de archivos relacionados |
| `revision_count` | int | Cuantas veces se actualizo esta memoria |
| `duplicate_count` | int | Cuantos guardados duplicados se deduplicaron |

### Tipos de memoria

Cada tipo senala **intencion** y habilita recall filtrado:

| Tipo | Cuando usarlo | Ejemplo |
|---|---|---|
| `decision` | Eleccion de arquitectura o tecnologia con tradeoffs | "Chose Zustand over Redux for state management" |
| `architecture` | Diseno estructural de un sistema o componente | "Payment module uses hexagonal architecture" |
| `bug` / `bugfix` | Causa raiz y solucion de un bug resuelto | "Fixed race condition in WebSocket reconnect" |
| `discovery` | Hallazgo no obvio sobre el codebase o herramientas | "LanceDB doesn't support concurrent writes from multiple processes" |
| `pattern` | Convencion o patron de codigo establecido | "All API handlers follow the Result monad pattern" |
| `insight` | Observacion o aprendizaje que no encaja en otros tipos | "Tree-sitter Go grammar doesn't parse generics correctly" |
| `note` | Memoria de proposito general | Resumenes de sesion, notas de reuniones, TODOs |

### Deduplicacion

Los agentes guardan memorias agresivamente — despues de cada bugfix, cada decision, cada descubrimiento. Sin deduplicacion, el store se llenaria de entradas casi identicas.

DevAI deduplica en el momento de escritura:

```
  Nueva memoria llega
       │
       ▼
  Normalizar contenido
  (minusculas + colapsar espacios en blanco)
       │
       ▼
  SHA256 hash del contenido normalizado
       │
       ▼
  Verificar: mismo hash dentro de ventana de 15 minutos?
       │
       ├── SI → Incrementar duplicate_count, saltar insert
       │
       └── NO → Verificar: mismo topic_key + project + scope?
                    │
                    ├── SI → Upsert (actualizar existente, incrementar revision_count)
                    │
                    └── NO → Insertar nueva memoria
```

La ventana de 15 minutos maneja el caso comun: un agente llamando `remember` multiples veces en la misma sesion con esencialmente el mismo contenido. El upsert por topic key maneja el caso de evolucion: el mismo concepto siendo refinado a lo largo de multiples sesiones.

### Upserts por topic key

Los topic keys son el mecanismo para **conocimiento que evoluciona**. En vez de crear una nueva memoria cada vez que aprendes mas sobre un tema, el topic key asegura que la memoria existente se actualice:

```
Sesion 1:
  remember(
    title: "Auth architecture",
    topic_key: "architecture/auth",
    content: "Using JWT with refresh tokens. HS256 signing."
  )
  → Crea nueva memoria (revision 1)

Sesion 2:
  remember(
    title: "Auth architecture",
    topic_key: "architecture/auth",
    content: "Using JWT with refresh tokens. Switched to RS256 for key rotation support."
  )
  → Actualiza memoria existente (revision 2), preserva historial
```

Reglas:
- Mismo `topic_key` + `project` + `scope` = actualizar existente
- Diferente `topic_key` = nueva memoria (nunca sobreescribe temas no relacionados)
- Sin `topic_key` = siempre crea nueva memoria

### Busqueda hibrida (Recall)

Cuando consultas memorias, DevAI usa busqueda hibrida combinando similitud semantica y filtrado por metadata:

```
  recall(query: "authentication architecture", project: "myapp")
       │
       ├──► Busqueda semantica
       │    Embeddear consulta → buscar vectores de memoria en LanceDB
       │    Retorna: memorias rankeadas por similitud coseno
       │
       ├──► Filtrado por metadata
       │    Filtrar por: project, type, scope, tags
       │
       └──► Merge + ranking
            Score de relevancia combinado
            Retornar top-K resultados con contenido COMPLETO
```

Los vectores de memoria se almacenan en la misma instancia de LanceDB que los vectores de codigo, pero con campos de metadata distintos (`memory_type`, `memory_scope`, `memory_tags`) que habilitan filtrado preciso.

La decision de diseno clave: **recall retorna contenido completo, no resumenes truncados**. Las memorias estan estructuradas para ser concisas al momento de escritura, asi pueden retornarse completas al momento de lectura. Sin "buscar y despues traer" en dos pasos — una sola llamada te da todo.

## Cuando se usa

- **MCP `remember` tool**: Los agentes de IA guardan memorias estructuradas
- **MCP `recall` tool**: Los agentes de IA consultan memorias por lenguaje natural + filtros
- **MCP `memory_context` tool**: Obtener contexto enriquecido con memorias para un tema
- **MCP `memory_stats` tool**: Inspeccionar la salud y tamano del memory store
- **Context builder**: Automaticamente incluye memorias relevantes al ensamblar contexto

## Ejemplo: Ciclo de vida de una decision de arquitectura

### Guardando

Un agente de IA (o humano via CLI) completa una discusion de arquitectura y la guarda:

```
remember(
  title: "Chose event sourcing for order management",
  type: "decision",
  scope: "shared",
  project: "ecommerce",
  topic_key: "architecture/order-management",
  content: """
    What: Adopted event sourcing pattern for the order management domain.
    Why: Need full audit trail for compliance. CQRS read models give us
         flexible querying without denormalization trade-offs.
    Where: services/orders/, events/order_events.py, projections/
    Learned: Event store requires careful schema versioning. Using
             upcasters for backward compatibility.
  """,
  tags: ["event-sourcing", "cqrs", "orders"],
  files: ["services/orders/aggregate.py", "events/order_events.py"]
)
```

### Recordando (semanas despues, otra sesion)

Una nueva sesion de agente necesita modificar el sistema de ordenes:

```
recall(query: "order management architecture", project: "ecommerce")

Resultado:
  ┌─────────────────────────────────────────────────────────────┐
  │ Title: Chose event sourcing for order management            │
  │ Type: decision | Scope: shared | Revisions: 3              │
  │                                                             │
  │ What: Adopted event sourcing pattern for the order          │
  │       management domain.                                    │
  │ Why: Need full audit trail for compliance...                │
  │ Where: services/orders/, events/order_events.py...          │
  │ Learned: Event store requires careful schema versioning...  │
  │                                                             │
  │ Tags: event-sourcing, cqrs, orders                         │
  │ Files: services/orders/aggregate.py, events/order_events.py │
  └─────────────────────────────────────────────────────────────┘
```

El agente ahora sabe: este es un sistema event-sourced, usa CQRS, tiene una preocupacion de versionado de schemas, y los archivos relevantes estan en `services/orders/`. Puede proceder con la modificacion sin re-descubrir nada de esto.

### Actualizando (el tema evoluciona)

Mas adelante, se completa una migracion de event store basado en archivos a uno respaldado por base de datos:

```
remember(
  title: "Migrated order event store to PostgreSQL",
  type: "decision",
  topic_key: "architecture/order-management",
  project: "ecommerce",
  content: """
    What: Migrated event store from file-based to PostgreSQL with
          pg_partman for time-based partitioning.
    Why: File store hit performance wall at ~1M events.
    Where: services/orders/event_store.py, migrations/
    Learned: Partitioning by month keeps query performance under 50ms
             up to ~100M events.
  """
)
→ Actualiza memoria existente (revision 4), preserva continuidad
```

## Modelo mental

Pensa en la memoria de DevAI como el **cuaderno compartido del equipo** — pero uno que es buscable por significado, deduplica automaticamente, y siempre esta disponible para cada agente de IA trabajando en el proyecto.

El historial de chat es como una pizarra: util durante la reunion, borrada despues. Los comentarios en codigo son como notas adhesivas: anotan un solo punto pero no pueden capturar el razonamiento detras de una decision a nivel de sistema. La memoria de DevAI es el cuaderno donde escribis "elegimos X porque Y, y ojo con Z" — y seis meses despues, cualquier agente (o humano) puede preguntar "por que elegimos X?" y obtener la respuesta completa.
