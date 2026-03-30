> 🌐 [English version](../../05-examples/debugging.md)

# Depuración de un Bug en un Codebase Grande

Un recorrido paso a paso de un agente de IA usando las herramientas MCP de DevAI para rastrear y diagnosticar un bug en producción — desde un reporte vago hasta la causa raíz en siete llamadas a herramientas.

---

## Escenario

Un usuario reporta: **"Los pagos se están procesando dos veces en producción."** El codebase tiene más de 200 archivos distribuidos entre múltiples servicios. Nadie sabe de dónde viene la duplicación.

El agente de IA tiene acceso a las 14 herramientas MCP de DevAI. Así es exactamente cómo encuentra el bug.

---

## Paso 1 — Verificar la Memoria en Busca de Contexto Previo

Antes de tocar código, el agente verifica si este problema (o algo relacionado) fue investigado antes.

**Llamada a herramienta:**
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

**Lo que DevAI hace internamente:**
Busca en el almacén de memoria — similitud vectorial sobre entradas previas de `remember` asociadas a este proyecto.

**Respuesta:**
```json
{
  "memories": []
}
```

**Lo que el agente aprende:** No existe ninguna investigación previa. Es un bug nuevo. Hora de meterse en el código.

---

## Paso 2 — Buscar el Código de Procesamiento de Pagos

El agente empieza de forma amplia: encontrar dónde vive la lógica de procesamiento de pagos.

**Llamada a herramienta:**
```json
{
  "tool": "search",
  "arguments": {
    "query": "payment processing handler",
    "limit": 10
  }
}
```

**Lo que DevAI hace internamente:**
Genera el embedding de la consulta, realiza una búsqueda por similitud vectorial contra el índice de código, y rankea los resultados por relevancia semántica (no solo coincidencia de palabras clave).

**Respuesta:**
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

**Lo que el agente aprende:** El punto de entrada principal es `PaymentService.process()` en la línea 45 de `services/payment.py`. Acepta un parámetro `idempotency_key` — así que el sistema fue diseñado con deduplicación en mente. La pregunta es si los llamadores lo están usando correctamente.

---

## Paso 3 — Leer la Definición Completa del Símbolo

El agente extrae la implementación completa de la función de procesamiento.

**Llamada a herramienta:**
```json
{
  "tool": "read_symbol",
  "arguments": {
    "name": "PaymentService.process"
  }
}
```

**Lo que DevAI hace internamente:**
Resuelve el símbolo a través del grafo de código (AST de tree-sitter), extrae el cuerpo completo de la función incluyendo decoradores, docstrings y llamadas anidadas.

**Respuesta:**
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

**Lo que el agente aprende:** La función en sí es CORRECTA. Verifica claves de idempotencia, protege contra el reprocesamiento de pagos completados, y almacena los resultados. El bug no está acá. La duplicación tiene que venir de cómo se llama a esta función.

---

## Paso 4 — Encontrar Todos los Llamadores

El agente rastrea quién llama a `PaymentService.process()`.

**Llamada a herramienta:**
```json
{
  "tool": "get_references",
  "arguments": {
    "symbol": "PaymentService.process"
  }
}
```

**Lo que DevAI hace internamente:**
Recorre el grafo de código para encontrar todos los sitios de llamada — imports, llamadas directas y referencias indirectas a lo largo de todo el repositorio indexado.

**Respuesta:**
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

**Lo que el agente aprende:** Ahí está. Dos sitios de llamada en producción:

- `api/webhooks.py:23` — pasa el `idempotency_key` original del payload del webhook. Correcto.
- `api/retry_handler.py:67` — genera un **nuevo UUID** como clave de idempotencia en cada reintento. Esto anula completamente el mecanismo de deduplicación.

Cuando llega un webhook de pago y el procesamiento inicial expira por timeout, el handler de reintentos lo toma — pero con una clave de idempotencia nueva, así que `PaymentService.process()` lo trata como un pago nuevo.

---

## Paso 5 — Construir Contexto Completo

El agente arma un contexto comprensivo para confirmar la teoría y entender el flujo de reintentos de punta a punta.

**Llamada a herramienta:**
```json
{
  "tool": "build_context",
  "arguments": {
    "query": "payment webhook retry logic",
    "max_tokens": 6000
  }
}
```

**Lo que DevAI hace internamente:**
Combina resultados de búsqueda semántica, relaciones del grafo de código, análisis de dependencias y memorias relevantes en un único paquete de contexto coherente optimizado para consumo por IA.

**Respuesta (abreviada):**
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

**Lo que el agente aprende:** El panorama completo queda claro:

1. Llega el webhook → `webhooks.py` llama a `process()` con la clave de idempotencia correcta.
2. Si el procesamiento expira por timeout → el trabajo se encola en la cola de reintentos.
3. El modelo `RetryJob` NO almacena el `idempotency_key` original.
4. El handler de reintentos genera `str(uuid4())` como una clave nueva para cada intento.
5. Cada reintento se trata como un pago distinto → cobros duplicados.

---

## Paso 6 — Identificar la Causa Raíz

El agente ahora tiene total claridad. La causa raíz es un defecto de dos partes:

1. **`models/retry_job.py`** — La dataclass `RetryJob` no incluye un campo `idempotency_key`, así que la clave original se pierde cuando un trabajo se encola.
2. **`api/retry_handler.py:67`** — Compensa el campo faltante generando un nuevo UUID, lo que saltea la deduplicación por completo.

**La solución:**
- Agregar `idempotency_key: str` a `RetryJob`.
- Pasar `data['idempotency_key']` al crear el `RetryJob` en `webhooks.py`.
- Usar `job.idempotency_key` en vez de `str(uuid4())` en `retry_handler.py`.

---

## Paso 7 — Guardar el Hallazgo en la Memoria

El agente persiste el diagnóstico para que futuras sesiones (y otros miembros del equipo) puedan encontrarlo.

**Llamada a herramienta:**
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

**Lo que DevAI hace internamente:**
Almacena la entrada de memoria con embeddings vectoriales para recuperación semántica futura. Los tags y el `topic_key` permiten búsquedas exactas. El campo `files` vincula la memoria con archivos fuente específicos para referencia cruzada.

**Respuesta:**
```json
{
  "status": "saved",
  "id": "mem_a8f3c2e1",
  "topic_key": "bugfix/duplicate-payments"
}
```

**Lo que el agente aprende:** El hallazgo queda persistido. Cualquier consulta futura de `recall` sobre "pagos duplicados", "idempotencia" o "bug del retry handler" va a traer esta entrada — entre sesiones, entre agentes.

---

## Flujo Completo de Depuración

```
  Reporte del Usuario                   Herramientas MCP de DevAI          Razonamiento del Agente
  ──────────────────                   ──────────────────────────          ───────────────────────

  "Los pagos se
   procesan dos veces"
        │
        ▼
  ┌─────────────┐     recall("payment duplicate")
  │ Verificar   │────────────────────────────────►  Sin contexto previo.
  │ Memoria     │◄──── [] vacío                     Empezar de cero.
  └─────┬───────┘
        │
        ▼
  ┌─────────────┐     search("payment processing")
  │ Buscar en   │────────────────────────────────►  Punto de entrada
  │ el Codebase │◄──── PaymentService.process()     encontrado en línea 45.
  └─────┬───────┘      services/payment.py:45
        │
        ▼
  ┌─────────────┐     read_symbol("PaymentService
  │ Leer        │      .process")
  │ Símbolo     │────────────────────────────────►  La función tiene dedup.
  └─────┬───────┘◄──── código completo              El bug NO está acá.
        │
        ▼
  ┌─────────────┐     get_references("PaymentService
  │ Encontrar   │      .process")
  │ Llamadores  │────────────────────────────────►  Dos llamadores encontrados.
  └─────┬───────┘◄──── webhooks.py:23               retry_handler usa
        │              retry_handler.py:67           uuid4() — ¡sospechoso!
        ▼
  ┌─────────────┐     build_context("payment
  │ Construir   │      webhook retry logic")
  │ Contexto    │────────────────────────────────►  A RetryJob le falta
  └─────┬───────┘◄──── código + deps ensamblados    idempotency_key.
        │                                           Causa raíz encontrada.
        ▼
  ┌─────────────┐
  │ Causa Raíz  │  retry_handler.py:67 genera un nuevo UUID por reintento,
  │ Identificada│  salteando la idempotencia. El modelo RetryJob no tiene
  └─────┬───────┘  el campo idempotency_key en absoluto.
        │
        ▼
  ┌─────────────┐     remember(type="bugfix",
  │ Guardar     │      topic_key="bugfix/
  │ Hallazgo    │      duplicate-payments")
  └─────────────┘────────────────────────────────►  Persistido para
                 ◄──── saved: mem_a8f3c2e1          futuras sesiones.
```

---

## Lo que DevAI Aportó

| Capacidad | Herramienta Usada | Valor |
|---|---|---|
| **Memoria institucional** | `recall` | Confirmó que no existía investigación previa |
| **Búsqueda semántica** | `search` | Encontró el punto de entrada de pagos en más de 200 archivos en una sola llamada |
| **Resolución de símbolos** | `read_symbol` | Recuperó la función completa para confirmar que la lógica de dedup era correcta |
| **Grafo de referencias** | `get_references` | Identificó todos los llamadores — localizó el sitio de llamada roto |
| **Ensamblado de contexto** | `build_context` | Reunió el retry handler, webhook handler y modelo RetryJob en una sola vista coherente |
| **Memoria persistente** | `remember` | Guardó la causa raíz para que nadie tenga que re-investigar esto |

**Total de llamadas a herramientas: 7.** Desde un reporte de bug vago hasta la causa raíz confirmada con una solución documentada, sin leer un solo archivo manualmente.
