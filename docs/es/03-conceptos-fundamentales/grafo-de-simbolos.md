> 🌐 [English version](../../03-core-concepts/symbol-graph.md)

# Grafo de Simbolos

## Que es

El grafo de simbolos es un mapa persistente de cada relacion en tu codebase — quien llama a quien, quien importa que, quien hereda de donde. Es una lista de adyacencia respaldada por SQLite que te permite recorrer la red de dependencias de tu codigo en milisegundos.

## Por que existe

La busqueda responde "donde esta el codigo que hace X?" El grafo de simbolos responde una pregunta diferente, igualmente critica: **"que esta conectado a X?"**

Encontraste la funcion `processPayment` via busqueda. Ahora necesitas saber:
- Quien la llama? (analisis de impacto)
- Que llama ella? (entender comportamiento)
- Que implementa la interfaz `PaymentProcessor`? (encontrar implementaciones concretas)
- Que importa este modulo? (radio de explosion de un cambio)

Sin el grafo, estas haciendo `grep` manual por nombres de funciones y rezando para no perderte un call site escondido detras de un alias, un decorator o dispatch dinamico. El grafo te da la verdad estructural extraida del AST.

## Como se construye

El grafo de simbolos se construye durante la indexacion, como subproducto del mismo parseo tree-sitter AST que produce los chunks de busqueda:

```
  Archivo fuente
       │
       ▼
  Tree-sitter AST Parse
       │
       ├──► Chunks de busqueda (→ embeddings → LanceDB)
       │
       └──► Extraccion de simbolos (→ aristas del grafo → SQLite)
              │
              ├── Identificar declaraciones (funciones, clases, etc.)
              ├── Identificar referencias (llamadas, imports, etc.)
              └── Crear aristas entre simbolos
```

### Lenguajes soportados

Las gramaticas de tree-sitter proveen parseo completo del AST para **25+ lenguajes**: Python, Go, TypeScript, JavaScript, Java, Rust, C, C++, C#, Ruby, PHP, Swift, Kotlin, y mas.

Para lenguajes sin soporte de tree-sitter (HTML, CSS, JSON, YAML, etc.), un parser de fallback raw extrae lo que puede — tipicamente solo simbolos a nivel de archivo y relaciones de import.

### Almacenamiento: Lista de adyacencia en SQLite

El grafo se almacena en una tabla `graph_edges`:

```sql
CREATE TABLE graph_edges (
    source_symbol  TEXT NOT NULL,  -- nombre completamente calificado
    target_symbol  TEXT NOT NULL,  -- nombre completamente calificado
    edge_kind      TEXT NOT NULL,  -- calls, imports, inherits, etc.
    source_file    TEXT,
    target_file    TEXT,
    source_line    INTEGER,
    target_line    INTEGER
);
```

SQLite se eligio deliberadamente por sobre una base de datos de grafos. El grafo cabe en un solo archivo, no requiere infraestructura, y maneja codebases de hasta ~2M lineas sin despeinarse. Las consultas corren en <10ms.

## Tipos de simbolos

Cada nodo en el grafo es un simbolo con un nombre completamente calificado (FQN):

| Tipo | Ejemplo de FQN |
|---|---|
| `function` | `auth/utils.py::verify_token` |
| `method` | `auth/middleware.py::AuthMiddleware.authenticate` |
| `class` | `auth/middleware.py::AuthMiddleware` |
| `struct` | `models/user.go::User` |
| `interface` | `services/payment.go::PaymentProcessor` |
| `enum` | `types/status.ts::OrderStatus` |
| `constant` | `config/defaults.py::MAX_RETRIES` |
| `variable` | `config/settings.py::db_connection_string` |
| `type_alias` | `types/common.ts::UserId` |

El formato del nombre completamente calificado es `file::SymbolName` o `file::Class.method`. Esto garantiza unicidad — dos clases llamadas `Config` en archivos diferentes son nodos distintos.

## Tipos de aristas

Las aristas representan relaciones entre simbolos:

### `calls`

La funcion A invoca a la funcion B.

```
  processOrder ──calls──► validatePayment
  processOrder ──calls──► calculateTax
  processOrder ──calls──► sendConfirmation
```

### `imports`

El modulo A importa un simbolo del modulo B.

```
  handlers/order.py ──imports──► services/payment.py::processPayment
  handlers/order.py ──imports──► models/order.py::Order
```

### `inherits`

La clase A extiende a la clase B.

```
  AdminUser ──inherits──► User
  PremiumUser ──inherits──► User
```

### `implements`

Un tipo concreto implementa una interfaz (Go, Java, TypeScript).

```
  StripeProcessor ──implements──► PaymentProcessor
  PayPalProcessor ──implements──► PaymentProcessor
```

### `references`

Un simbolo es referenciado (leido, asignado, pasado como argumento) sin ser llamado o importado.

```
  createOrder ──references──► OrderStatus.PENDING
  middleware ──references──► AUTH_CONFIG
```

## Operaciones

### `get_callers(symbol)` — Quien llama a esto?

**Caso de uso:** Analisis de impacto. Antes de cambiar una funcion, conoce cada call site.

```
get_callers("auth/middleware.py::AuthMiddleware.authenticate")

Resultados:
  handlers/api.py::handle_request        (linea 45)
  handlers/websocket.py::on_connect       (linea 12)
  tests/test_auth.py::test_valid_token    (linea 23)
```

### `get_callees(symbol)` — Que llama esto?

**Caso de uso:** Entender comportamiento. Ve cada funcion de la que depende un metodo sin leer la implementacion.

```
get_callees("services/order.py::processOrder")

Resultados:
  services/payment.py::validatePayment    (linea 67)
  services/tax.py::calculateTax           (linea 89)
  services/email.py::sendConfirmation     (linea 112)
  models/order.py::Order.save             (linea 34)
```

### `get_dependents(symbol)` — Que depende de esto?

**Caso de uso:** Radio de explosion. Que se rompe si este simbolo cambia o desaparece?

```
get_dependents("models/user.py::User")

Resultados:
  auth/middleware.py::AuthMiddleware       (inherits)
  handlers/profile.py::get_profile        (imports)
  handlers/admin.py::list_users           (imports)
  services/notification.py::notify_user   (references)
```

### `get_dependencies(symbol)` — De que depende esto?

**Caso de uso:** Entender la huella de un modulo. Que arrastra consigo?

```
get_dependencies("services/order.py::processOrder")

Resultados:
  services/payment.py::validatePayment    (calls)
  services/tax.py::calculateTax           (calls)
  models/order.py::Order                  (imports)
  config/settings.py::TAX_RATE            (references)
```

## Como complementa a la busqueda

La busqueda y el grafo de simbolos resuelven problemas diferentes y son mas poderosos juntos:

```
  "Como funciona el procesamiento de pagos?"
       │
       ▼
  BUSQUEDA (semantica)
       │  Encuentra: processPayment(), PaymentProcessor, StripeClient
       │
       ▼
  GRAFO DE SIMBOLOS (estructural)
       │  get_callers(processPayment)    → quien dispara pagos
       │  get_callees(processPayment)    → que orquesta
       │  implements(PaymentProcessor)   → implementaciones concretas
       │
       ▼
  PANORAMA COMPLETO
       Order handler → processPayment → [validate, charge, notify]
                                              │
                                    StripeClient / PayPalClient
```

La busqueda te da los puntos de entrada. El grafo te da las conexiones. Juntos, le dan a un agente de IA (o a un humano) suficiente comprension estructural para razonar sobre el codebase sin leer cada archivo.

## Cuando se usa

- **MCP `get_references` tool**: Devuelve todos los call sites y usos de un simbolo
- **MCP `read_symbol` tool**: Usa el grafo para localizar definiciones de simbolos
- **Context builder**: Sigue las aristas del grafo para incluir codigo relacionado en el contexto ensamblado
- **Analisis de impacto**: Antes de refactorizar, entender que depende del codigo que se va a cambiar

## Ejemplo: Investigando un bug en `processPayment`

```
1. BUSQUEDA: "payment processing error handling"
   → Encuentra services/payment.py::processPayment (score: 0.91)

2. READ SYMBOL: processPayment
   → Cuerpo completo de la funcion (47 lineas)

3. GET CALLERS: processPayment
   → handlers/checkout.py::checkout (linea 34)
   → handlers/retry.py::retry_failed_payment (linea 12)
   → workers/subscription.py::renew_subscription (linea 78)

4. GET CALLEES: processPayment
   → stripe_client.charge()
   → order.update_status()
   → audit_log.record()

   AHORA sabes:
   - 3 puntos de entrada que disparan este codigo
   - 3 operaciones downstream que podrian fallar
   - La cadena de llamadas exacta para rastrear el bug
```

## Modelo mental

Pensa en el grafo de simbolos como un **mapa de la ciudad**. La busqueda es como preguntar "donde esta el hospital mas cercano?" — encuentra ubicaciones. El grafo es como la red de calles — te muestra como llegar, que esta conectado y que rutas se ven afectadas si cerras una interseccion.

Cada funcion es una interseccion. Cada llamada es una calle. El grafo te permite responder "si cierro esta calle, que rutas se rompen?" — que es exactamente la pregunta que necesitas responder antes de cada refactor.
