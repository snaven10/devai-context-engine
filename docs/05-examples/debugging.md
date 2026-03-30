# Debugging a Bug in a Large Codebase

> 🇪🇸 [Leer en español](../es/05-ejemplos/depuracion.md)

A step-by-step walkthrough of an AI agent using DevAI's MCP tools to track down and diagnose a production bug — from vague report to root cause in seven tool calls.

---

## Scenario

A user reports: **"Payments are being processed twice in production."** The codebase has 200+ files across multiple services. No one knows where the duplication originates.

The AI agent has access to DevAI's 14 MCP tools. Here's exactly how it finds the bug.

---

## Step 1 — Check Memory for Prior Context

Before touching code, the agent checks whether this issue (or something related) has been investigated before.

**Tool call:**
```json
{
  "tool": "recall",
  "arguments": {
    "query": "payment processing duplicate",
    "project": "payments-api",
    "limit": 5
  }
}
```

**What DevAI does internally:**
Searches the memory store — vector similarity over past `remember` entries scoped to this project.

**Response:**
```json
{
  "memories": []
}
```

**What the agent learns:** No prior investigation exists. This is a fresh bug. Time to dig into the code.

---

## Step 2 — Search for Payment Processing Code

The agent starts broad: find where payment processing logic lives.

**Tool call:**
```json
{
  "tool": "search",
  "arguments": {
    "query": "payment processing handler",
    "limit": 10
  }
}
```

**What DevAI does internally:**
Embeds the query, performs vector similarity search against the code index, ranks results by semantic relevance (not just keyword matching).

**Response:**
```json
{
  "results": [
    {
      "file": "services/payment.py",
      "line": 45,
      "symbol": "PaymentService.process",
      "snippet": "def process(self, payment_id: str, amount: Decimal, idempotency_key: str) -> PaymentResult:",
      "score": 0.94
    },
    {
      "file": "services/payment.py",
      "line": 112,
      "symbol": "PaymentService._charge_provider",
      "snippet": "def _charge_provider(self, charge_request: ChargeRequest) -> ProviderResponse:",
      "score": 0.81
    },
    {
      "file": "api/webhooks.py",
      "line": 18,
      "symbol": "handle_payment_webhook",
      "snippet": "async def handle_payment_webhook(request: Request) -> Response:",
      "score": 0.78
    }
  ]
}
```

**What the agent learns:** The main entry point is `PaymentService.process()` at line 45 of `services/payment.py`. It accepts an `idempotency_key` parameter — so the system was designed with dedup in mind. The question is whether callers are using it correctly.

---

## Step 3 — Read the Full Symbol Definition

The agent pulls the complete implementation of the processing function.

**Tool call:**
```json
{
  "tool": "read_symbol",
  "arguments": {
    "name": "PaymentService.process"
  }
}
```

**What DevAI does internally:**
Resolves the symbol via the code graph (tree-sitter AST), extracts the full function body including decorators, docstrings, and nested calls.

**Response:**
```json
{
  "symbol": "PaymentService.process",
  "kind": "method",
  "file": "services/payment.py",
  "start_line": 45,
  "end_line": 89,
  "code": "def process(self, payment_id: str, amount: Decimal, idempotency_key: str) -> PaymentResult:\n    \"\"\"Process a payment. Checks idempotency key to prevent duplicates.\"\"\"\n    existing = self._check_idempotency(idempotency_key)\n    if existing:\n        logger.info(f\"Duplicate detected for key={idempotency_key}, returning cached result\")\n        return existing\n\n    payment = self.repo.get(payment_id)\n    if payment.status == PaymentStatus.COMPLETED:\n        raise PaymentAlreadyProcessed(payment_id)\n\n    charge = ChargeRequest(\n        payment_id=payment_id,\n        amount=amount,\n        currency=payment.currency,\n        provider=payment.provider,\n    )\n    result = self._charge_provider(charge)\n\n    payment.status = PaymentStatus.COMPLETED\n    payment.transaction_id = result.transaction_id\n    self.repo.save(payment)\n\n    self._store_idempotency(idempotency_key, PaymentResult(...))\n    return PaymentResult(success=True, transaction_id=result.transaction_id)"
}
```

**What the agent learns:** The function itself is CORRECT. It checks idempotency keys, guards against re-processing completed payments, and stores results. The bug is not here. The duplication must come from how this function is called.

---

## Step 4 — Find All Callers

The agent traces who calls `PaymentService.process()`.

**Tool call:**
```json
{
  "tool": "get_references",
  "arguments": {
    "symbol": "PaymentService.process"
  }
}
```

**What DevAI does internally:**
Traverses the code graph to find all call sites — imports, direct calls, and indirect references across the entire indexed repository.

**Response:**
```json
{
  "symbol": "PaymentService.process",
  "references": [
    {
      "file": "api/webhooks.py",
      "line": 23,
      "context": "result = payment_service.process(payment_id=data['id'], amount=data['amount'], idempotency_key=data['idempotency_key'])",
      "kind": "call"
    },
    {
      "file": "api/retry_handler.py",
      "line": 67,
      "context": "result = payment_service.process(payment_id=job.payment_id, amount=job.amount, idempotency_key=str(uuid4()))",
      "kind": "call"
    },
    {
      "file": "tests/test_payment.py",
      "line": 34,
      "context": "result = service.process('pay_123', Decimal('50.00'), 'idem_key_1')",
      "kind": "call"
    }
  ]
}
```

**What the agent learns:** There it is. Two production call sites:

- `api/webhooks.py:23` — passes the original `idempotency_key` from the webhook payload. Correct.
- `api/retry_handler.py:67` — generates a **new UUID** as the idempotency key on every retry. This defeats the entire dedup mechanism.

When a payment webhook arrives and the initial processing times out, the retry handler picks it up — but with a fresh idempotency key, so `PaymentService.process()` treats it as a new payment.

---

## Step 5 — Build Full Context

The agent assembles comprehensive context to confirm the theory and understand the retry flow end to end.

**Tool call:**
```json
{
  "tool": "build_context",
  "arguments": {
    "query": "payment webhook retry logic",
    "max_tokens": 6000
  }
}
```

**What DevAI does internally:**
Combines semantic search results, code graph relationships, dependency analysis, and relevant memories into a single coherent context package optimized for AI consumption.

**Response (abbreviated):**
```json
{
  "context": {
    "primary_files": [
      {
        "file": "api/retry_handler.py",
        "relevant_code": "class RetryHandler:\n    def process_failed_jobs(self):\n        for job in self.queue.get_pending():\n            if job.attempts < self.max_retries:\n                job.attempts += 1\n                result = payment_service.process(\n                    payment_id=job.payment_id,\n                    amount=job.amount,\n                    idempotency_key=str(uuid4())  # BUG: new key each retry\n                )\n                ..."
      },
      {
        "file": "api/webhooks.py",
        "relevant_code": "async def handle_payment_webhook(request):\n    data = await request.json()\n    try:\n        result = payment_service.process(\n            payment_id=data['id'],\n            amount=data['amount'],\n            idempotency_key=data['idempotency_key']  # Correct: uses original key\n        )\n    except TimeoutError:\n        retry_queue.enqueue(RetryJob(\n            payment_id=data['id'],\n            amount=data['amount'],\n            # NOTE: original idempotency_key is NOT stored in RetryJob\n        ))\n        ..."
      },
      {
        "file": "models/retry_job.py",
        "relevant_code": "class RetryJob:\n    payment_id: str\n    amount: Decimal\n    attempts: int = 0\n    created_at: datetime = field(default_factory=utcnow)"
      }
    ],
    "dependencies": ["services/payment.py", "models/retry_job.py"],
    "related_memories": []
  }
}
```

**What the agent learns:** The full picture is now clear:

1. Webhook arrives → `webhooks.py` calls `process()` with the correct idempotency key.
2. If processing times out → the job is enqueued in the retry queue.
3. The `RetryJob` model does NOT store the original `idempotency_key`.
4. The retry handler generates `str(uuid4())` as a new key for each attempt.
5. Each retry is treated as a distinct payment → duplicate charges.

---

## Step 6 — Identify Root Cause

The agent now has full clarity. The root cause is a two-part defect:

1. **`models/retry_job.py`** — The `RetryJob` dataclass does not include an `idempotency_key` field, so the original key is lost when a job is enqueued.
2. **`api/retry_handler.py:67`** — Compensates for the missing field by generating a new UUID, which bypasses dedup entirely.

**The fix:**
- Add `idempotency_key: str` to `RetryJob`.
- Pass `data['idempotency_key']` when creating the `RetryJob` in `webhooks.py`.
- Use `job.idempotency_key` instead of `str(uuid4())` in `retry_handler.py`.

---

## Step 7 — Save the Finding to Memory

The agent persists the diagnosis so future sessions (and other team members) can find it.

**Tool call:**
```json
{
  "tool": "remember",
  "arguments": {
    "content": "Duplicate payment bug: retry_handler.py generates a new UUID as idempotency_key on every retry (line 67), bypassing the dedup check in PaymentService.process(). Root cause: RetryJob model does not store the original idempotency_key from the webhook payload. The webhook handler in webhooks.py correctly passes the original key, but when it enqueues a RetryJob on timeout, the key is lost. Fix: add idempotency_key field to RetryJob, pass it during enqueue, and use it in retry_handler.py instead of uuid4().",
    "type": "bugfix",
    "topic_key": "bugfix/duplicate-payments",
    "tags": ["payments", "idempotency", "retry", "production-bug"],
    "files": ["api/retry_handler.py", "api/webhooks.py", "services/payment.py", "models/retry_job.py"]
  }
}
```

**What DevAI does internally:**
Stores the memory entry with vector embeddings for future semantic recall. Tags and `topic_key` enable exact lookups. The `files` field links the memory to specific source files for cross-referencing.

**Response:**
```json
{
  "status": "saved",
  "id": "mem_a8f3c2e1",
  "topic_key": "bugfix/duplicate-payments"
}
```

**What the agent learns:** The finding is persisted. Any future `recall` query about "duplicate payments", "idempotency", or "retry handler bug" will surface this entry — across sessions, across agents.

---

## Full Debugging Flow

```
  User Report                          DevAI MCP Tools                    Agent Reasoning
  ──────────                          ───────────────                    ───────────────

  "Payments are
   processed twice"
        │
        ▼
  ┌─────────────┐     recall("payment duplicate")
  │ Check       │────────────────────────────────►  No prior context.
  │ Memory      │◄──── [] empty                     Start fresh.
  └─────┬───────┘
        │
        ▼
  ┌─────────────┐     search("payment processing")
  │ Search      │────────────────────────────────►  Found main entry
  │ Codebase    │◄──── PaymentService.process()     point at line 45.
  └─────┬───────┘      services/payment.py:45
        │
        ▼
  ┌─────────────┐     read_symbol("PaymentService
  │ Read        │      .process")
  │ Symbol      │────────────────────────────────►  Function has dedup.
  └─────┬───────┘◄──── full function code           Bug is NOT here.
        │
        ▼
  ┌─────────────┐     get_references("PaymentService
  │ Find        │      .process")
  │ Callers     │────────────────────────────────►  Two callers found.
  └─────┬───────┘◄──── webhooks.py:23               retry_handler uses
        │              retry_handler.py:67           uuid4() — suspect!
        ▼
  ┌─────────────┐     build_context("payment
  │ Build       │      webhook retry logic")
  │ Context     │────────────────────────────────►  RetryJob missing
  └─────┬───────┘◄──── assembled code + deps        idempotency_key.
        │                                           Root cause found.
        ▼
  ┌─────────────┐
  │ Root Cause  │  retry_handler.py:67 generates new UUID per retry,
  │ Identified  │  bypassing idempotency. RetryJob model lacks the
  └─────┬───────┘  idempotency_key field entirely.
        │
        ▼
  ┌─────────────┐     remember(type="bugfix",
  │ Save        │      topic_key="bugfix/
  │ Finding     │      duplicate-payments")
  └─────────────┘────────────────────────────────►  Persisted for
                 ◄──── saved: mem_a8f3c2e1          future sessions.
```

---

## What DevAI Provided

| Capability | Tool Used | Value |
|---|---|---|
| **Institutional memory** | `recall` | Confirmed no prior investigation existed |
| **Semantic search** | `search` | Found the payment entry point across 200+ files in one call |
| **Symbol resolution** | `read_symbol` | Retrieved the full function to confirm dedup logic was correct |
| **Reference graph** | `get_references` | Identified all callers — pinpointed the broken call site |
| **Context assembly** | `build_context` | Pulled together retry handler, webhook handler, and RetryJob model into a single coherent view |
| **Persistent memory** | `remember` | Saved the root cause so no one has to re-investigate this |

**Total tool calls: 7.** From vague bug report to confirmed root cause with a documented fix, without reading a single file manually.
