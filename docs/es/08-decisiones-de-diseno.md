> 🌐 [English version](../08-design-decisions.md)

# Decisiones de Diseño

Registros de Decisiones de Arquitectura (ADRs) para DevAI. Cada entrada documenta qué se decidió, por qué y qué tradeoffs se aceptaron.

Leé esto si sos contribuidor y te preguntás "¿por qué está construido así?" Estas son las respuestas.

---

## ADR-01: Arquitectura Híbrida Go + Python

**Decisión**: Dividir DevAI en un CLI/servidor MCP en Go y un servicio ML en Python.

**Contexto**: DevAI necesita tanto capacidades de CLI/servidor de alto rendimiento como acceso al ecosistema ML de Python (tree-sitter, sentence-transformers, LanceDB, PyTorch). Ningún lenguaje solo cubre ambos bien.

**Opciones Consideradas**:
- **Python puro**: Simple. Un solo proceso. Pero los CLIs en Python son lentos para arrancar (200-500ms), el servidor MCP sería más pesado de lo necesario, y la distribución es dolorosa (virtualenvs, infierno de dependencias de pip).
- **Go puro**: CLI rápido, distribución fácil (binario único). Pero el ecosistema ML de Go es inmaduro — sin buenos bindings de tree-sitter, sin equivalente de sentence-transformers, los modelos de embedding necesitarían CGO o servicios externos.
- **Híbrido Go + Python**: Go maneja CLI, protocolo MCP y gestión de procesos. Python maneja ML — parseo, embedding, búsqueda vectorial. Comunicación via JSON-RPC sobre stdio.

**Elección**: Híbrido. Go para la capa de interfaz, Python para la capa de inteligencia. Se comunican por JSON-RPC sobre pipes de stdio.

**Tradeoffs**:
- Dos lenguajes para mantener, dos sistemas de build
- Complejidad en la gestión de procesos (Go debe iniciar, monitorear y reiniciar el servicio Python)
- Overhead de serialización en cada llamada (despreciable en la práctica — JSON-RPC es rápido para los tamaños de payload involucrados)
- Los contribuidores necesitan saber ambos lenguajes

**Estado**: Vigente. La separación ha demostrado ser correcta — Go nos da arranque rápido y distribución fácil (binario único + wheel de Python), Python nos da el ecosistema ML completo.

---

## ADR-02: JSON-RPC 2.0 sobre Stdio

**Decisión**: Usar JSON-RPC 2.0 sobre pipes de stdio para la comunicación Go-Python.

**Contexto**: El proceso Go necesita llamar funciones de Python y obtener resultados estructurados de vuelta. Se necesita un protocolo que sea simple, de bajo overhead y que no requiera puertos de red.

**Opciones Consideradas**:
- **gRPC**: Type-safe, serialización binaria rápida, soporte de streaming. Pero requiere compilación de protobuf, agrega complejidad al build, y es overkill para un canal local proceso-a-proceso.
- **HTTP/REST**: Familiar, debuggeable. Pero requiere un puerto (posibles conflictos), el overhead de HTTP es innecesario para IPC local, y agrega una dependencia de servidor web al lado Python.
- **Python embebido (CGO)**: Sin IPC necesario — llamar a Python directamente desde Go. Pero CGO es frágil, la compilación cruzada se rompe, y el GIL de Python hace la concurrencia dolorosa.
- **JSON-RPC sobre stdio**: Simple. El proceso Go inicia Python como subproceso, escribe JSON a stdin, lee JSON de stdout. Sin puertos, sin sockets, sin descubrimiento de servicios.

**Elección**: JSON-RPC 2.0 sobre stdio. El protocolo es una spec de una sola página. La implementación son ~100 líneas de cada lado.

**Tradeoffs**:
- Sin streaming (solo request-response). No se necesita para los casos de uso actuales.
- Overhead de parseo JSON vs protocolos binarios. Irrelevante — los payloads son chicos, y el trabajo real (embedding, búsqueda) empequeñece el costo de serialización.
- El debugging requiere inspección de logs (no hay un endpoint al que hacerle curl). Aceptable para una herramienta local.
- Conexión única — sin requests paralelos sobre un pipe. Resuelto con encolamiento de requests en el cliente Go.

**Estado**: Vigente. Simple, confiable, configuración cero.

---

## ADR-03: LanceDB como Vector Store Predeterminado

**Decisión**: Usar LanceDB como backend de almacenamiento vectorial predeterminado.

**Contexto**: Se necesita una base de datos vectorial que funcione localmente, requiera cero configuración y maneje la escala de codebases típicos (10K-100K archivos, 50K-500K chunks).

**Opciones Consideradas**:
- **ChromaDB**: Popular, nativo de Python. Pero agrega una dependencia pesada (SQLite + DuckDB + su propia capa de embedding), y tuvo problemas de estabilidad en versiones tempranas. Tiene opiniones fuertes sobre embedding — nosotros queremos controlar eso.
- **Qdrant**: Grado de producción, excelente rendimiento. Pero requiere un servidor corriendo (Docker o binario). No es adecuado como predeterminado para una herramienta CLI que debería funcionar con cero configuración. Soportado como backend opcional para uso compartido/de equipo.
- **FAISS**: Biblioteca de búsqueda vectorial de Facebook. Rápida, bien probada. Pero sin persistencia out of the box — vos manejás la serialización. Sin filtrado de metadata. Bajo nivel.
- **LanceDB**: Embebido (sin servidor), basado en archivos (fácil de inspeccionar/borrar/respaldar), soporta filtrado de metadata, buen rendimiento hasta millones de vectores. Basado en Apache Arrow — almacenamiento columnar eficiente.

**Elección**: LanceDB como predeterminado. Qdrant soportado como backend opcional para deployments compartidos.

**Tradeoffs**:
- LanceDB es más joven que FAISS/Qdrant — menos probado en batalla a escala extrema
- El almacenamiento basado en archivos significa que no hay escrituras concurrentes desde múltiples procesos (no es un problema — DevAI es de un solo usuario por repo)
- Comunidad más chica que ChromaDB (pero API más limpia y menos sorpresas)

**Estado**: Vigente. LanceDB ha sido confiable. El modelo basado en archivos encaja perfecto — `.devai/vectors/` son solo archivos que podés borrar y reconstruir.

---

## ADR-04: SQLite para Datos Estructurados

**Decisión**: Usar SQLite para el grafo de código y las memorias persistentes.

**Contexto**: Se necesita almacenamiento estructurado para relaciones (llamadas a funciones, imports, jerarquías de clases) y memorias de usuario (decisiones, descubrimientos, resúmenes de sesión). Debe funcionar localmente con cero configuración.

**Opciones Consideradas**:
- **PostgreSQL**: Full-featured, genial para equipos. Pero requiere un servidor corriendo. Absurdo para datos locales de una herramienta CLI.
- **Solo en memoria**: Lo más rápido posible. Pero los datos se pierden al reiniciar — inaceptable para memorias, y reconstruir el grafo de código es costoso.
- **SQLite**: Embebido, configuración cero, base de datos de un solo archivo. Maneja millones de filas. Cumple ACID. Disponible en todos lados.

**Elección**: SQLite. Dos bases de datos: `graph.db` para relaciones de código, `memory.db` para memorias persistentes.

**Tradeoffs**:
- Sin escrituras concurrentes (el modo WAL ayuda pero no elimina). Está bien — herramienta de un solo usuario.
- Sin acceso por red (no se puede compartir un archivo SQLite entre máquinas fácilmente). Los deployments compartidos usan Qdrant para vectores; grafo/memoria se mantienen locales.
- Las migraciones de esquema deben manejarse manualmente. Aceptable a la escala actual.

**Estado**: Vigente. SQLite es la herramienta correcta para este trabajo. Va a seguir siendo el predeterminado para almacenamiento local.

---

## ADR-05: IDs de Vector Determinísticos

**Decisión**: Generar IDs de vector determinísticamente a partir de la identidad del contenido (ruta de archivo + rango del chunk + branch), no aleatoriamente.

**Contexto**: Cuando un archivo cambia, sus chunks necesitan actualizarse en el vector store. Se necesita saber qué chunks existentes reemplazar.

**Opciones Consideradas**:
- **UUIDs**: Simple, sin colisiones. Pero no podés buscar "el chunk de las líneas 10-50 de main.go" sin un índice secundario. Las actualizaciones requieren delete-by-metadata + insert (dos operaciones, propenso a race conditions).
- **Auto-increment**: Peor todavía — ninguna forma de correlacionar chunks entre reindexaciones.
- **Determinístico (hash de ruta + rango + branch)**: El mismo chunk lógico siempre tiene el mismo ID. El upsert es una sola operación. Sin vectores huérfanos después de reindexar.

**Elección**: IDs determinísticos. Fórmula: `hash(repo + file_path + chunk_start + chunk_end + branch)`.

**Tradeoffs**:
- Si los límites de chunking cambian (ej., una función crece), los IDs de chunks viejos quedan huérfanos. Resuelto con limpieza durante la reindexación incremental.
- Las colisiones de hash son teóricamente posibles. La truncación de SHA-256 las hace despreciables.
- Generación de ID ligeramente más compleja vs `uuid4()`. Vale la pena por la semántica limpia de upsert.

**Estado**: Vigente. Esta decisión elimina toda una clase de bugs de consistencia de datos.

---

## ADR-06: Overlay de Branch, No Copias

**Decisión**: Manejar branches superponiendo cambios específicos del branch sobre el índice principal, no manteniendo índices separados por branch.

**Contexto**: Los desarrolladores cambian de branch frecuentemente. Mantener un índice completo por branch multiplicaría el almacenamiento y el tiempo de indexación.

**Opciones Consideradas**:
- **Índice por branch**: Cada branch obtiene su propio vector store completo. Aislamiento limpio. Pero el almacenamiento crece linealmente con los branches, e indexar un branch nuevo significa reindexación completa.
- **Ignorar branches**: Indexar solo el HEAD actual. Simple. Pero cambiar de branch invalida los resultados de búsqueda hasta que la reindexación se complete.
- **Overlay de branch**: Mantener un índice base (branch main/default). Cuando estás en un feature branch, superponer archivos cambiados encima. La búsqueda combina resultados base + overlay.

**Elección**: Overlay de branch. El índice base cubre la mayoría del código (archivos sin cambios). Solo los diffs específicos del branch se indexan por separado.

**Tradeoffs**:
- La combinación de búsqueda agrega complejidad — hay que manejar "archivo eliminado en branch" y "chunk reemplazado en branch" correctamente
- Un índice base desactualizado (si main avanza) puede retornar resultados ligeramente desactualizados para archivos sin cambios. Aceptable — la búsqueda de código es aproximada de todos modos.
- Se necesita limpieza del overlay cuando los branches se mergean/eliminan

**Estado**: Vigente. Reduce dramáticamente el almacenamiento y hace que cambiar de branch sea casi instantáneo.

---

## ADR-07: Chunking Consciente del AST

**Decisión**: Usar ASTs de tree-sitter para crear chunks semánticamente significativos, no divisiones arbitrarias por líneas o tokens.

**Contexto**: La calidad de la búsqueda de código depende fuertemente de la calidad de los chunks. Un chunk debería ser una unidad coherente de código — una función, un método de clase, una definición de tipo. Dividir a la mitad de una función destruye el significado semántico.

**Opciones Consideradas**:
- **Basado en líneas (N líneas fijas)**: Simple. Pero parte funciones por la mitad, mezcla código no relacionado en un chunk, produce embeddings pobres.
- **Basado en tokens (N tokens fijos)**: Mejor que líneas para modelos de embedding. Mismo problema fundamental — sin conciencia de la estructura del código.
- **A nivel de archivo (un chunk por archivo)**: Preserva todo el contexto. Pero archivos grandes exceden los límites del modelo de embedding, y la búsqueda retorna archivos enteros en vez de secciones relevantes.
- **Consciente del AST**: Parsear el código, identificar límites naturales (funciones, clases, bloques), dividir en chunks siguiendo esos límites. Cada chunk es una unidad semántica completa.

**Elección**: Chunking consciente del AST via tree-sitter. Las funciones y métodos se convierten en chunks individuales. Las clases se dividen por método con contexto de clase preservado. El código de nivel superior se agrupa por bloques lógicos.

**Tradeoffs**:
- Requiere un parser por cada lenguaje (gramáticas de tree-sitter). Actualmente más de 25 lenguajes soportados. Los lenguajes no soportados recurren a chunking basado en líneas.
- Los errores de parseo en código roto pueden producir chunks pobres. Tree-sitter es tolerante a errores, así que esto es raro.
- Más complejo que dividir por líneas. Vale la pena — la mejora en calidad de búsqueda es dramática.
- Los tamaños de chunk varían (una función utilitaria de 5 líneas vs un método de 200 líneas). Los chunks grandes se dividen en sub-límites lógicos.

**Estado**: Vigente. Esta es una de las diferenciaciones core de DevAI. El chunking consciente del AST produce resultados de búsqueda significativamente mejores que la división naive.

---

## ADR-08: Indexación Incremental via Git Diff

**Decisión**: Usar `git diff` para determinar qué archivos cambiaron, y solo reindexar esos archivos.

**Contexto**: Reindexar completamente un repo grande toma minutos. Los desarrolladores cambian unos pocos archivos por commit. Reindexar todo en cada cambio es un desperdicio.

**Opciones Consideradas**:
- **Reindexación completa cada vez**: Simple, siempre correcto. Pero 5-10 minutos para un repo grande en cada guardado es inaceptable.
- **File watcher (fsnotify)**: Tiempo real, captura cada cambio. Pero ruidoso (archivos temporales del editor, artefactos de build), pierde cambios hechos fuera del editor, y no maneja cambios de branch.
- **Git diff**: Preciso. Sabe exactamente qué cambió. Funciona entre cambios de branch (diff entre HEAD actual y último commit indexado). Maneja renombramientos, eliminaciones y movimientos correctamente.

**Elección**: Git diff como mecanismo de detección de cambios. El indexador almacena el SHA del último commit indexado y hace diff contra él.

**Tradeoffs**:
- Solo funciona en repos git. Los directorios sin git requieren reindexación completa. Aceptable — DevAI está construido para flujos de trabajo de desarrollo.
- Los cambios no commiteados requieren hacer diff contra el working tree (ligeramente más complejo que diff commit-a-commit). Implementado.
- No puede detectar cambios a archivos fuera del repo (configuración externa, bibliotecas compartidas). Por diseño — esos no son parte del repo.

**Estado**: Vigente. Esto hace que DevAI sea práctico para uso continuo de desarrollo.

---

## ADR-09: Interfaz MCP Agnóstica al Agente

**Decisión**: Exponer las capacidades de DevAI como herramientas MCP (Model Context Protocol), no un protocolo custom.

**Contexto**: DevAI necesita ser usable por agentes de IA (Claude, GPT, Copilot, agentes custom). Cada plataforma de agente tiene su propio mecanismo de integración. Construir integraciones custom para cada uno es insostenible.

**Opciones Consideradas**:
- **Protocolo custom**: Máximo control sobre la interfaz. Pero cada agente necesita un adapter custom. N agentes = N adapters para mantener.
- **API HTTP**: Universal, cualquier cliente puede llamarla. Pero los agentes necesitan schemas de herramientas específicos para saber qué hay disponible. HTTP es demasiado bajo nivel — terminás construyendo una capa de herramientas encima de todos modos.
- **MCP (Model Context Protocol)**: Protocolo estandarizado para exponer herramientas a agentes de IA. Los agentes que soportan MCP pueden usar DevAI inmediatamente. Schema-first — las herramientas se auto-describen.

**Elección**: MCP. DevAI expone 14 herramientas via MCP. Cualquier agente compatible con MCP puede usarlas sin código de integración custom.

**Tradeoffs**:
- MCP todavía está evolucionando — los cambios de spec pueden requerir actualizaciones
- No todos los agentes soportan MCP todavía (pero la adopción está creciendo rápido)
- El schema de herramientas de MCP es menos expresivo que un SDK custom de TypeScript/Python
- Los agentes que no soportan MCP necesitan un wrapper (pero ese wrapper es más delgado que una integración custom completa)

**Estado**: Vigente. La adopción de MCP se está acelerando. Fue la apuesta correcta.

---

## ADR-10: Deduplicación de Memoria via Content Hashing

**Decisión**: Deduplicar memorias automáticamente usando content hashing, no limpieza manual.

**Contexto**: Los agentes de IA almacenan memorias frecuentemente — resúmenes de sesión, decisiones, descubrimientos. Sin deduplicación, el almacén de memoria se llena con entradas casi idénticas. La limpieza manual es irreal (no se puede confiar en que los agentes manejen su propia higiene de memoria).

**Opciones Consideradas**:
- **Solo agregar (append-only)**: Cada llamada a `remember` crea una entrada nueva. Simple. Pero la búsqueda de memoria retorna 15 copias de la misma decisión, ahogando los resultados útiles.
- **Limpieza manual**: Depender del agente o usuario para borrar memorias viejas. En la práctica nunca pasa.
- **Dedup por content hash**: Hashear el contenido de la memoria. Si existe una memoria con el mismo hash, omitir el insert. Combinado con upserts por topic_key (ADR-12) para temas que evolucionan.

**Elección**: Dedup automática via content hashing. Mismo contenido = misma memoria, no un duplicado.

**Tradeoffs**:
- Cambios menores de contenido (espacios, formato) producen hashes diferentes. Aceptable — contenido semánticamente diferente debería almacenarse.
- La computación del hash agrega overhead despreciable
- No se puede almacenar contenido intencionalmente duplicado (no hay caso de uso válido para esto)

**Estado**: Vigente. La calidad de la memoria mejoró dramáticamente después de implementar esto.

---

## ADR-11: Degradación Elegante Híbrida

**Decisión**: Cuando el backend compartido (Qdrant) no está disponible, degradar elegantemente a almacenamiento solo-local en vez de fallar.

**Contexto**: DevAI soporta tanto almacenamiento local (LanceDB/SQLite) como compartido (Qdrant). En configuraciones de equipo, el servidor Qdrant puede estar temporalmente inalcanzable (problemas de red, reinicio del servidor, desconexión de VPN).

**Opciones Consideradas**:
- **Fallar rápido (fail-fast)**: Si está configurado para almacenamiento compartido y no está disponible, lanzar un error. Comportamiento claro. Pero bloquea al desarrollador — no puede usar DevAI en absoluto hasta que el servidor vuelva.
- **Modo solo-local**: Nunca usar almacenamiento compartido. Simple. Pero pierde el compartir conocimiento del equipo que hace valioso al almacenamiento compartido.
- **Degradación elegante**: Intentar almacenamiento compartido. Si no está disponible, caer a local. Loguear un warning. Sincronizar cuando el almacenamiento compartido vuelva.

**Elección**: Degradación elegante. Local siempre funciona. Compartido es best-effort.

**Tradeoffs**:
- Datos locales desactualizados cuando el compartido es actualizado por compañeros. Aceptable — la búsqueda de código es aproximada.
- Sincronizar al reconectar agrega complejidad. Pero la alternativa (bloquear al desarrollador) es peor.
- El fallback silencioso puede confundir usuarios que esperan resultados compartidos. Resuelto logueando warnings.

**Estado**: Vigente. Los desarrolladores nunca deberían ser bloqueados por problemas de infraestructura en una herramienta local-first.

---

## ADR-12: Upserts por Topic Key para Memoria

**Decisión**: Usar upserts basados en topic_key para actualizaciones de memoria. Mismo topic_key = actualizar memoria existente, no crear una nueva.

**Contexto**: Algunas memorias representan conocimiento que evoluciona — decisiones de arquitectura que se refinan, patrones de bugs que acumulan ejemplos, resúmenes de sesión que reemplazan a los anteriores. El modo append-only crea desorden. El historial versionado agrega complejidad sin valor claro.

**Opciones Consideradas**:
- **Historial versionado**: Mantener cada versión de un tema, etiquetada con timestamps. Trail de auditoría completo. Pero la búsqueda de memoria retorna todas las versiones, y los agentes no pueden usar significativamente el historial de versiones.
- **Dedup manual**: Requerir que el llamador haga delete-then-insert. Propenso a errores — si el delete falla o se olvida, los duplicados se acumulan.
- **Upsert por topic key**: Si existe una memoria con el mismo `topic_key`, reemplazar su contenido. Un tema = una entrada de memoria, siempre actual.

**Elección**: Upserts por topic key. `remember(topic_key="architecture/auth-model", ...)` siempre actualiza la misma entrada de memoria.

**Tradeoffs**:
- Sin historial — las versiones anteriores se sobreescriben. Si se necesita historial, el llamador debería usar un topic_key diferente (ej., `architecture/auth-model/v2`). Esto intencionalmente no es automático.
- Las colisiones de topic key entre diferentes proyectos son posibles. Mitigado incluyendo el nombre del proyecto en las consultas.
- Requiere disciplina en el naming de topic_key. Las convenciones documentadas ayudan (ver protocolo de memoria).

**Estado**: Vigente. Combinado con la dedup por content hash (ADR-10), esto mantiene el almacén de memoria limpio y actual sin intervención manual.
