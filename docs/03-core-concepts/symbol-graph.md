# Symbol Graph

> 🇪🇸 [Leer en español](../es/03-conceptos-fundamentales/grafo-de-simbolos.md)

## What It Is

The symbol graph is a persistent map of every relationship in your codebase — who calls whom, who imports what, who inherits from where. It's a SQLite-backed adjacency list that lets you traverse your code's dependency web in milliseconds.

## Why It Exists

Search answers "where is the code that does X?" The symbol graph answers a different, equally critical question: **"what is connected to X?"**

You found the `processPayment` function via search. Now you need to know:
- Who calls it? (impact analysis)
- What does it call? (understanding behavior)
- What implements the `PaymentProcessor` interface? (finding concrete implementations)
- What imports this module? (blast radius of a change)

Without the graph, you're doing manual `grep` for function names and hoping you don't miss a call site hidden behind an alias, a decorator, or dynamic dispatch. The graph gives you structural truth extracted from the AST.

## How It's Built

The symbol graph is constructed during indexing, as a byproduct of the same tree-sitter AST parse that produces search chunks:

```
  Source file
       │
       ▼
  Tree-sitter AST Parse
       │
       ├──► Search chunks (→ embeddings → LanceDB)
       │
       └──► Symbol extraction (→ graph edges → SQLite)
              │
              ├── Identify declarations (functions, classes, etc.)
              ├── Identify references (calls, imports, etc.)
              └── Create edges between symbols
```

### Supported Languages

Tree-sitter grammars provide full AST parsing for **25+ languages**: Python, Go, TypeScript, JavaScript, Java, Rust, C, C++, C#, Ruby, PHP, Swift, Kotlin, and more.

For languages without tree-sitter support (HTML, CSS, JSON, YAML, etc.), a raw parser fallback extracts what it can — typically just file-level symbols and import relationships.

### Storage: SQLite Adjacency List

The graph is stored in a `graph_edges` table:

```sql
CREATE TABLE graph_edges (
    source_symbol  TEXT NOT NULL,  -- fully qualified name
    target_symbol  TEXT NOT NULL,  -- fully qualified name
    edge_kind      TEXT NOT NULL,  -- calls, imports, inherits, etc.
    source_file    TEXT,
    target_file    TEXT,
    source_line    INTEGER,
    target_line    INTEGER
);
```

SQLite was chosen deliberately over a graph database. The graph fits in a single file, requires zero infrastructure, and handles codebases up to ~2M lines without breaking a sweat. Queries run in <10ms.

## Symbol Types

Every node in the graph is a symbol with a fully qualified name:

| Type | Example FQN |
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

The fully qualified name format is `file::SymbolName` or `file::Class.method`. This ensures uniqueness — two classes named `Config` in different files are distinct nodes.

## Edge Types

Edges represent relationships between symbols:

### `calls`

Function A invokes function B.

```
  processOrder ──calls──► validatePayment
  processOrder ──calls──► calculateTax
  processOrder ──calls──► sendConfirmation
```

### `imports`

Module A imports a symbol from module B.

```
  handlers/order.py ──imports──► services/payment.py::processPayment
  handlers/order.py ──imports──► models/order.py::Order
```

### `inherits`

Class A extends class B.

```
  AdminUser ──inherits──► User
  PremiumUser ──inherits──► User
```

### `implements`

A concrete type implements an interface (Go, Java, TypeScript).

```
  StripeProcessor ──implements──► PaymentProcessor
  PayPalProcessor ──implements──► PaymentProcessor
```

### `references`

A symbol is referenced (read, assigned, passed as argument) without being called or imported.

```
  createOrder ──references──► OrderStatus.PENDING
  middleware ──references──► AUTH_CONFIG
```

## Operations

### `get_callers(symbol)` — Who calls this?

**Use case:** Impact analysis. Before changing a function, know every call site.

```
get_callers("auth/middleware.py::AuthMiddleware.authenticate")

Results:
  handlers/api.py::handle_request        (line 45)
  handlers/websocket.py::on_connect       (line 12)
  tests/test_auth.py::test_valid_token    (line 23)
```

### `get_callees(symbol)` — What does this call?

**Use case:** Understanding behavior. See every function a method depends on without reading the implementation.

```
get_callees("services/order.py::processOrder")

Results:
  services/payment.py::validatePayment    (line 67)
  services/tax.py::calculateTax           (line 89)
  services/email.py::sendConfirmation     (line 112)
  models/order.py::Order.save             (line 34)
```

### `get_dependents(symbol)` — What depends on this?

**Use case:** Blast radius. What breaks if this symbol changes or disappears?

```
get_dependents("models/user.py::User")

Results:
  auth/middleware.py::AuthMiddleware       (inherits)
  handlers/profile.py::get_profile        (imports)
  handlers/admin.py::list_users           (imports)
  services/notification.py::notify_user   (references)
```

### `get_dependencies(symbol)` — What does this depend on?

**Use case:** Understanding a module's footprint. What does it pull in?

```
get_dependencies("services/order.py::processOrder")

Results:
  services/payment.py::validatePayment    (calls)
  services/tax.py::calculateTax           (calls)
  models/order.py::Order                  (imports)
  config/settings.py::TAX_RATE            (references)
```

## How It Complements Search

Search and the symbol graph solve different problems and are most powerful together:

```
  "How does payment processing work?"
       │
       ▼
  SEARCH (semantic)
       │  Finds: processPayment(), PaymentProcessor, StripeClient
       │
       ▼
  SYMBOL GRAPH (structural)
       │  get_callers(processPayment)    → who triggers payments
       │  get_callees(processPayment)    → what it orchestrates
       │  implements(PaymentProcessor)   → concrete implementations
       │
       ▼
  FULL PICTURE
       Order handler → processPayment → [validate, charge, notify]
                                              │
                                    StripeClient / PayPalClient
```

Search gives you the entry points. The graph gives you the connections. Together, they give an AI agent (or a human) enough structural understanding to reason about the codebase without reading every file.

## When It Is Used

- **MCP `get_references` tool**: Returns all call sites and usages of a symbol
- **MCP `read_symbol` tool**: Uses the graph to locate symbol definitions
- **Context builder**: Follows graph edges to include related code in assembled context
- **Impact analysis**: Before refactoring, understand what depends on the code being changed

## Example: Investigating a Bug in `processPayment`

```
1. SEARCH: "payment processing error handling"
   → Finds services/payment.py::processPayment (score: 0.91)

2. READ SYMBOL: processPayment
   → Full function body (47 lines)

3. GET CALLERS: processPayment
   → handlers/checkout.py::checkout (line 34)
   → handlers/retry.py::retry_failed_payment (line 12)
   → workers/subscription.py::renew_subscription (line 78)

4. GET CALLEES: processPayment
   → stripe_client.charge()
   → order.update_status()
   → audit_log.record()

   NOW you know:
   - 3 entry points that trigger this code
   - 3 downstream operations that could fail
   - The exact call chain to trace the bug
```

## Mental Model

Think of the symbol graph as a **city map**. Search is like asking "where's the nearest hospital?" — it finds locations. The graph is like the road network — it shows you how to get there, what's connected, and which roads will be affected if you close an intersection.

Every function is an intersection. Every call is a road. The graph lets you answer "if I close this road, what routes break?" — which is exactly the question you need answered before every refactor.
