# Context Builder

> 🇪🇸 [Leer en español](../es/03-conceptos-fundamentales/constructor-de-contexto.md)

## What It Is

The context builder is a token-budget-aware assembly engine. Given a natural language query and a token budget, it gathers the most relevant code and memories, deduplicates and ranks them, and produces a single markdown document that fits within the budget. It's the bridge between DevAI's indexes and an LLM's context window.

## Why It Exists

LLMs have finite context windows. Sending "everything potentially relevant" wastes tokens on low-value content and crowds out the code that actually matters. Sending too little leaves the LLM guessing.

The context builder solves this by acting as an **intelligent librarian**: it knows what's in the library (code index + memories), understands what you're asking for (semantic search), and assembles a reading list that fits your time budget (token limit) — prioritized by relevance.

| Approach | Result |
|---|---|
| **Dump entire files** | Blows the context window. 3 files = 12k tokens of noise for 200 tokens of signal. |
| **Send search results raw** | No deduplication. No memory enrichment. No budget awareness. May include 15 chunks from the same file. |
| **Context builder** | Memory-enriched, deduplicated, budget-fitted, ranked by relevance. Every token earns its place. |

## How It Works Internally

### The Algorithm

```
  Query: "refactor the payment module"
  Budget: 8000 tokens (default, configurable)
       │
       ▼
  ┌─────────────────────────────────────┐
  │  STEP 1: Memory Enrichment          │
  │  Search memories for query           │
  │  Take up to 5 relevant memories      │
  │  Extract file hints from memories    │
  │  Budget consumed: ~800 tokens        │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────┐
  │  STEP 2: Code Search                │
  │  Semantic search with remaining      │
  │  budget (~7200 tokens)               │
  │  Limit: 30 chunks max               │
  │  File hints from memories boost      │
  │  ranking                             │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────┐
  │  STEP 3: Dedup by File              │
  │  Multiple chunks from same file?     │
  │  Keep only the highest-scoring one   │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────┐
  │  STEP 4: Filter Tombstones          │
  │  Remove chunks deleted in higher-    │
  │  priority branches (overlay aware)   │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────┐
  │  STEP 5: Assemble Markdown          │
  │  Memory summaries first              │
  │  Then code chunks with file:line     │
  │  Stop when budget exhausted          │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  Final output: structured markdown
  (fits within 8000 tokens)
```

### Step by Step

#### Step 1: Memory Enrichment

The builder queries DevAI's memory store for memories relevant to the query. Up to 5 memories are selected, ranked by semantic similarity.

Memories serve two purposes:
1. **Direct context**: Architecture decisions, past bug fixes, and conventions related to the query are included in the output.
2. **File hints**: File paths mentioned in memories boost the relevance of code chunks from those files in Step 2.

For example, if a memory says "Payment module uses hexagonal architecture, see `services/payment/ports.py`", then chunks from that file get a ranking boost in the code search.

#### Step 2: Code Search

The remaining token budget (total minus memory tokens consumed) is allocated to code search. The builder runs a semantic search against the code index with a hard limit of 30 chunks.

Results are ranked by a combined score: semantic similarity to the query + file hint boost from memories.

#### Step 3: Dedup by File

It's common for multiple chunks from the same file to appear in search results — the file-level chunk, the class-level chunk, and a function-level chunk might all be relevant. The builder keeps only the **highest-scoring chunk per file** to maximize diversity.

This ensures the assembled context covers more of the codebase rather than deep-diving into a single file.

#### Step 4: Filter Tombstones

In branch-aware scenarios, files deleted in a feature branch should not appear in context. The builder checks for tombstones (deletion markers in branch overlays) and removes those chunks.

#### Step 5: Assemble Markdown

The final output is assembled as a structured markdown document:

1. Memory summaries come first (prefixed with `[memory]`)
2. Code chunks follow, ordered by relevance score
3. Each code chunk includes its file path and line range
4. Assembly stops when the next chunk would exceed the budget

### Token Budget Management

The budget uses a simple heuristic: **4 characters ~ 1 token**. This is intentionally conservative (most tokenizers average 3.5-4.2 chars/token for code) to avoid overrunning the budget.

```
Budget: 8000 tokens = ~32,000 characters

Memory allocation:   up to 20% of budget (1,600 tokens)
Code allocation:     remaining 80% (6,400 tokens)
Safety margin:       built into the 4:1 ratio
```

The budget is configurable per call. Default is 8000 tokens — enough for meaningful context without dominating a 100k-token conversation.

## Output Format

The assembled context is a markdown document:

```markdown
[memory] Architecture: Payment module uses hexagonal architecture
with ports and adapters. Domain logic in services/payment/domain.py,
external integrations behind adapter interfaces.

[memory] Bug fix: Fixed race condition in concurrent payment
processing. Root cause was shared mutable state in PaymentGateway
singleton. Solution: request-scoped gateway instances.

---

**services/payment/domain.py:15-67**
```python
class PaymentProcessor:
    def __init__(self, gateway: PaymentGateway, validator: PaymentValidator):
        self.gateway = gateway
        self.validator = validator

    def process(self, order: Order) -> PaymentResult:
        validation = self.validator.validate(order)
        if not validation.is_valid:
            return PaymentResult.failed(validation.errors)
        return self.gateway.charge(order.total, order.payment_method)
```

**services/payment/ports.py:1-34**
```python
from abc import ABC, abstractmethod

class PaymentGateway(ABC):
    @abstractmethod
    def charge(self, amount: Decimal, method: PaymentMethod) -> ChargeResult: ...

    @abstractmethod
    def refund(self, charge_id: str, amount: Decimal) -> RefundResult: ...
```

**services/payment/adapters/stripe.py:8-42**
```python
class StripeGateway(PaymentGateway):
    def __init__(self, api_key: str):
        self.client = stripe.Client(api_key)

    def charge(self, amount: Decimal, method: PaymentMethod) -> ChargeResult:
        # ...
```
```

This format is designed for LLM consumption: memories provide high-level context, code chunks provide implementation detail, and file:line references enable the LLM to suggest precise edits.

## When It Is Used

- **MCP `build_context` tool**: Primary interface for AI agents to get enriched context
- **MCP `memory_context` tool**: Memory-focused variant (memories only, no code)
- **Agent workflows**: Any time an agent needs to understand a topic before writing code

## Example: Building Context for "refactor the payment module"

```
build_context(query: "refactor the payment module", max_tokens: 8000)

Internally:
  1. Memory search → 3 hits:
     - "Payment module uses hexagonal architecture" (decision)
     - "Fixed race condition in PaymentGateway" (bugfix)
     - "Payment adapters must implement idempotency" (pattern)
     → ~600 tokens consumed

  2. Code search (budget: 7400 tokens, limit: 30 chunks) → 18 hits
     Boosted files: services/payment/domain.py, services/payment/ports.py

  3. Dedup → 12 unique files kept

  4. Tombstone filter → 12 remain (none deleted)

  5. Assemble → 11 chunks fit within budget
     Final output: 7,850 tokens

Output includes:
  - 3 memory summaries (architecture, past bug, convention)
  - 11 code chunks from:
    services/payment/domain.py
    services/payment/ports.py
    services/payment/adapters/stripe.py
    services/payment/adapters/paypal.py
    services/payment/validator.py
    tests/test_payment.py
    config/payment.py
    ...
```

An LLM receiving this context has everything it needs to reason about the refactoring: the architecture pattern in use, a past bug to avoid reintroducing, a convention to follow, and the relevant source code — all within 8k tokens.

## Mental Model

The context builder is an **intelligent librarian**. You walk in and say "I need to understand payment processing." The librarian doesn't hand you every book in the library — that would take weeks to read. They don't hand you just one book — that might miss critical context.

Instead, they think: "There's a decision record about the architecture (memory), a post-mortem about a race condition (memory), and here are the most relevant source files (code chunks) — prioritized, deduplicated, and sized to fit your reading time (token budget)."

Every token in the output earned its place. Nothing is filler.
