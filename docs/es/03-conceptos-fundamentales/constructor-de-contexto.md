> 🌐 [English version](../../03-core-concepts/context-builder.md)

# Constructor de Contexto

## Que es

El constructor de contexto es un motor de ensamblado consciente del presupuesto de tokens. Dada una consulta en lenguaje natural y un presupuesto de tokens, reune el codigo y las memorias mas relevantes, los deduplica y rankea, y produce un unico documento markdown que cabe dentro del presupuesto. Es el puente entre los indices de DevAI y la ventana de contexto de un LLM.

## Por que existe

Los LLMs tienen ventanas de contexto finitas. Enviar "todo lo potencialmente relevante" desperdicia tokens en contenido de bajo valor y desplaza el codigo que realmente importa. Enviar muy poco deja al LLM adivinando.

El constructor de contexto resuelve esto actuando como un **bibliotecario inteligente**: sabe que hay en la biblioteca (indice de codigo + memorias), entiende lo que estas pidiendo (busqueda semantica), y arma una lista de lectura que entra en tu presupuesto de tiempo (limite de tokens) — priorizada por relevancia.

| Enfoque | Resultado |
|---|---|
| **Volcar archivos enteros** | Revienta la ventana de contexto. 3 archivos = 12k tokens de ruido por 200 tokens de senal. |
| **Enviar resultados de busqueda crudos** | Sin deduplicacion. Sin enriquecimiento de memoria. Sin consciencia de presupuesto. Puede incluir 15 chunks del mismo archivo. |
| **Constructor de contexto** | Enriquecido con memorias, deduplicado, ajustado al presupuesto, rankeado por relevancia. Cada token se gana su lugar. |

## Como funciona internamente

### El algoritmo

```
  Consulta: "refactorizar el modulo de pagos"
  Presupuesto: 8000 tokens (default, configurable)
       │
       ▼
  ┌─────────────────────────────────────┐
  │  PASO 1: Enriquecimiento con       │
  │          memorias                   │
  │  Buscar memorias para la consulta   │
  │  Tomar hasta 5 memorias relevantes  │
  │  Extraer pistas de archivos         │
  │  de las memorias                    │
  │  Presupuesto consumido: ~800 tokens │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────┐
  │  PASO 2: Busqueda de codigo         │
  │  Busqueda semantica con presupuesto │
  │  restante (~7200 tokens)            │
  │  Limite: 30 chunks maximo           │
  │  Las pistas de archivos de memorias │
  │  mejoran el ranking                 │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────┐
  │  PASO 3: Dedup por archivo          │
  │  Multiples chunks del mismo archivo?│
  │  Quedarse solo con el de mayor score│
  └──────────────┬──────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────┐
  │  PASO 4: Filtrar tombstones         │
  │  Remover chunks eliminados en       │
  │  branches de mayor prioridad        │
  │  (consciente de overlays)           │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────┐
  │  PASO 5: Ensamblar markdown         │
  │  Resumenes de memoria primero       │
  │  Luego chunks de codigo con         │
  │  archivo:linea                      │
  │  Parar cuando se agote el           │
  │  presupuesto                        │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  Salida final: markdown estructurado
  (cabe dentro de 8000 tokens)
```

### Paso a paso

#### Paso 1: Enriquecimiento con memorias

El constructor consulta el memory store de DevAI buscando memorias relevantes a la consulta. Se seleccionan hasta 5 memorias, rankeadas por similitud semantica.

Las memorias sirven para dos propositos:
1. **Contexto directo**: Decisiones de arquitectura, bugfixes pasados y convenciones relacionadas con la consulta se incluyen en la salida.
2. **Pistas de archivos**: Las rutas de archivos mencionadas en las memorias mejoran la relevancia de los chunks de codigo de esos archivos en el Paso 2.

Por ejemplo, si una memoria dice "Payment module uses hexagonal architecture, see `services/payment/ports.py`", entonces los chunks de ese archivo reciben un boost de ranking en la busqueda de codigo.

#### Paso 2: Busqueda de codigo

El presupuesto de tokens restante (total menos tokens consumidos por memorias) se asigna a la busqueda de codigo. El constructor ejecuta una busqueda semantica contra el indice de codigo con un limite duro de 30 chunks.

Los resultados se rankean por un score combinado: similitud semantica con la consulta + boost de pistas de archivos desde las memorias.

#### Paso 3: Dedup por archivo

Es comun que multiples chunks del mismo archivo aparezcan en los resultados de busqueda — el chunk a nivel de archivo, el de nivel de clase y uno a nivel de funcion podrian ser todos relevantes. El constructor se queda solo con el **chunk de mayor score por archivo** para maximizar la diversidad.

Esto asegura que el contexto ensamblado cubra mas del codebase en vez de profundizar en un solo archivo.

#### Paso 4: Filtrar tombstones

En escenarios conscientes de branches, los archivos eliminados en un feature branch no deberian aparecer en el contexto. El constructor verifica tombstones (marcadores de eliminacion en overlays de branches) y remueve esos chunks.

#### Paso 5: Ensamblar markdown

La salida final se ensambla como un documento markdown estructurado:

1. Los resumenes de memoria van primero (con prefijo `[memory]`)
2. Los chunks de codigo siguen, ordenados por score de relevancia
3. Cada chunk de codigo incluye su ruta de archivo y rango de lineas
4. El ensamblado se detiene cuando el siguiente chunk excederia el presupuesto

### Gestion del presupuesto de tokens

El presupuesto usa una heuristica simple: **4 caracteres ~ 1 token**. Esto es intencionalmente conservador (la mayoria de los tokenizers promedian 3.5-4.2 chars/token para codigo) para evitar pasarse del presupuesto.

```
Presupuesto: 8000 tokens = ~32,000 caracteres

Asignacion de memoria:   hasta 20% del presupuesto (1,600 tokens)
Asignacion de codigo:    80% restante (6,400 tokens)
Margen de seguridad:     incorporado en la relacion 4:1
```

El presupuesto es configurable por llamada. El default es 8000 tokens — suficiente para contexto significativo sin dominar una conversacion de 100k tokens.

## Formato de salida

El contexto ensamblado es un documento markdown:

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

Este formato esta disenado para consumo por LLMs: las memorias proveen contexto de alto nivel, los chunks de codigo proveen detalle de implementacion, y las referencias archivo:linea le permiten al LLM sugerir ediciones precisas.

## Cuando se usa

- **MCP `build_context` tool**: Interfaz principal para que los agentes de IA obtengan contexto enriquecido
- **MCP `memory_context` tool**: Variante enfocada en memoria (solo memorias, sin codigo)
- **Workflows de agentes**: Cada vez que un agente necesita entender un tema antes de escribir codigo

## Ejemplo: Construyendo contexto para "refactorizar el modulo de pagos"

```
build_context(query: "refactor the payment module", max_tokens: 8000)

Internamente:
  1. Busqueda de memorias → 3 resultados:
     - "Payment module uses hexagonal architecture" (decision)
     - "Fixed race condition in PaymentGateway" (bugfix)
     - "Payment adapters must implement idempotency" (pattern)
     → ~600 tokens consumidos

  2. Busqueda de codigo (presupuesto: 7400 tokens, limite: 30 chunks) → 18 resultados
     Archivos con boost: services/payment/domain.py, services/payment/ports.py

  3. Dedup → 12 archivos unicos conservados

  4. Filtro de tombstones → 12 quedan (ninguno eliminado)

  5. Ensamblar → 11 chunks caben dentro del presupuesto
     Salida final: 7,850 tokens

La salida incluye:
  - 3 resumenes de memoria (arquitectura, bug pasado, convencion)
  - 11 chunks de codigo de:
    services/payment/domain.py
    services/payment/ports.py
    services/payment/adapters/stripe.py
    services/payment/adapters/paypal.py
    services/payment/validator.py
    tests/test_payment.py
    config/payment.py
    ...
```

Un LLM que recibe este contexto tiene todo lo que necesita para razonar sobre el refactoring: el patron de arquitectura en uso, un bug pasado para evitar reintroducir, una convencion a seguir, y el codigo fuente relevante — todo dentro de 8k tokens.

## Modelo mental

El constructor de contexto es un **bibliotecario inteligente**. Entras y decis "necesito entender el procesamiento de pagos". El bibliotecario no te da todos los libros de la biblioteca — eso tardaria semanas en leer. No te da solo un libro — podria faltar contexto critico.

En cambio, piensa: "hay un registro de decision sobre la arquitectura (memoria), un post-mortem sobre una race condition (memoria), y aca estan los archivos fuente mas relevantes (chunks de codigo) — priorizados, deduplicados, y dimensionados para que entren en tu tiempo de lectura (presupuesto de tokens)."

Cada token en la salida se gano su lugar. Nada es relleno.
