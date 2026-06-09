# Embedding models, summarizer & tuning

A practical guide to the available embedding models, the token-budget strategies,
which configuration fits which hardware, and the behaviors verified empirically.

> **Why this matters**: the embedding model determines retrieval quality and the
> dimension of the vector store; the summarizer + token budget determine how much
> of each result reaches the LLM. Picking the wrong combination either loses
> relevant memories, corrupts identifiers, or burns CPU you don't have.

---

## 1. Available embedding models

The registry lives in `ml/devai_ml/embeddings/local.py` (`MODEL_REGISTRY`). All
run locally via `sentence-transformers`. Select with `embeddings.model` in
`config.yaml`, or with `devai model use <key>`.

| Key | Model | Dims | Size | Speed | Language | Best for |
|-----|-------|------|------|-------|----------|----------|
| `minilm-l6` | all-MiniLM-L6-v2 | 384 | 22 MB | very fast | 🇬🇧 English | resource-constrained machines, English code/text |
| `minilm-l12` | all-MiniLM-L12-v2 | 384 | 33 MB | fast | 🇬🇧 English | slightly better than L6, still lightweight |
| `bge-small` | BAAI/bge-small-en-v1.5 | 384 | 33 MB | fast | 🇬🇧 English | better English retrieval than MiniLM |
| `bge-base` | BAAI/bge-base-en-v1.5 | 768 | 110 MB | medium | 🇬🇧 English | top English precision, large repos |
| `ml-minilm` | paraphrase-multilingual-MiniLM-L12-v2 | 384 | 470 MB | fast | 🌍 50+ langs | **fast multilingual**, small machines with non-English content |
| `ml-mpnet` | paraphrase-multilingual-mpnet-base-v2 | 768 | 1.1 GB | medium | 🌍 50+ langs | **best multilingual quality** (torch), machines with a decent CPU or a GPU |
| `ml-granite` | granite-embedding-97m-multilingual-r2 (**ONNX int8**) | 384 | 94 MB | **very fast** | 🌍 multilingual | **best multilingual on CPU**: top recall + fastest indexing + half the storage |
| `ml-granite-lg` | granite-embedding-311m-multilingual-r2 (**ONNX int8**) | 768 | 299 MB | medium | 🌍 multilingual | larger 768-dim sibling; `ml-granite` matches/beats it on CPU — use only if you need 768 dims |

> 🔹 **`ml-granite` / `ml-granite-lg` are loaded through the ONNX backend**
> (`onnx/model_quint8_avx2.onnx`). The backend (`optimum`) is installed
> automatically by `devai setup`; if you installed the package by hand, run
> `pip install 'devai-ml[onnx]'`. Needs an x86 CPU with **AVX2**. No
> `query:`/`passage:` prefixes required.

### Which one to pick

- **Non-English / mixed content on CPU** → **`ml-granite`** is now the best default:
  it beats `ml-mpnet` on recall while indexing ~6x faster and using half the vector
  storage (see the benchmark below). Fall back to `ml-mpnet` (torch) only if your
  CPU lacks AVX2 or you can't install the `onnx` extra; `ml-minilm` if you want the
  lightest torch multilingual option. None of these need prefixes.
- **English only** → `bge-base` (best) or `minilm-l6` (lightest).
- **Avoid the `e5` family**: they underperform here because the local provider
  doesn't add the `query:`/`passage:` prefixes those models require.

### Benchmark (measured, CPU-only)

Domain corpus of 49 documents / 40 queries (Spanish technical content), measured
on a CPU-only machine. Indexing throughput uses sustained batches of
`DEVAI_EMBED_BATCH_SIZE` (default 16; see the RAM-guard note under §1).

| Model | Backend | Dims | Recall@1 | MRR | Index speed (texts/s) | RAM peak | Disk |
|-------|---------|------|----------|-----|----------------------|----------|------|
| `ml-mpnet` (previous default) | torch | 768 | 87.5% | 0.921 | 17.5 | 1248 MB | 1060 MB |
| e5-base (third-party quant) | ONNX int8 | 768 | 82.5% | 0.906 | 22.3 | 559 MB | 270 MB |
| granite-97m | torch | 384 | 95.0% | 0.975 | 9.3 | 841 MB | 186 MB |
| **`ml-granite` (granite-97m)** | **ONNX int8** | **384** | **95.0%** | **0.975** | **58.7** | 822 MB | **94 MB** |
| granite-311m | ONNX int8 | 768 | 92.5% | 0.963 | 15.0 | 1177 MB | 299 MB |

**Takeaways:**
- **The ONNX backend is the unlock.** The same granite-97m goes from 9.3 → 58.7
  texts/s (**6.3x**) by switching torch → ONNX int8, with **identical quality**
  (int8 quantization did not degrade the 97M model).
- `ml-granite` wins on every axis vs the previous `ml-mpnet` default: higher recall
  (95% vs 87.5%), fastest indexing, half the dimension (smaller vector store), and
  the smallest disk footprint — at comparable RAM.
- **Quantization is model-sensitive.** On the larger granite-311m, ONNX int8 *did*
  drop quality (97.5% → 92.5%), so the small model is the sweet spot on CPU.
- The e5 family stays the worst here (no prefix support) — confirming the warning above.

> Projected indexing time at this throughput: ~50k chunks take **~14 min** with
> `ml-granite` vs **~48 min** with `ml-mpnet` (and ~5 h with granite-311m in torch).

### Chunk size vs the model's context window

The chunker (`semantic_chunker.py`) sizes chunks in **tokens** (`DEVAI_MAX_CHUNK_TOKENS`,
default **512**), splitting by AST so a symbol is never cut mid-body. But the embedder
only embeds up to its `max_seq_length`:

| Model | `max_seq_length` | vs the 512-token chunk |
|-------|------------------|------------------------|
| `ml-mpnet` | **128 tokens** | chunks were **truncated** — the tail of large chunks never reached the vector |
| `ml-granite` | **32768 tokens** | the full chunk is embedded, no truncation |

So `ml-mpnet` had a latent mismatch: it emits 512-token chunks but only embeds the
first 128. `ml-granite` removes that waste.

**Does a bigger chunk improve recall?** Measured on a real repo (137 files, 12 ground-truth
queries) through the **real pipeline (vector fetch → flashrank rerank → top-k)**:

| chunk size | chunks | Recall@1 | Recall@3 |
|------------|--------|----------|----------|
| 256 | 603 | 58% | 67% |
| 512 | 582 | 58% | 67% |
| 1024 | 570 | 58% | 67% |

**Recall is the same across 256–1024.** Chunk size is not the lever it appears to be.
The reason: the **reranker reads the full chunk text** (stored untruncated), not the
embedding — so as long as the right chunk lands in the fetch window, the reranker
recovers it even when the embedder truncated it. (This is also why `ml-mpnet` is not
as crippled in practice as its 128-token window suggests.)

**Recommendation:** pick chunk size by **efficiency, not recall**. Keep **512** (default)
or raise to **1024** (fewer chunks → smaller, faster index; whole method in one chunk).
**256 adds nothing** — more storage, same recall. With `ml-granite` (32768) no size is
ever truncated. *(Caveat: measured on 12 queries — the "all equal" signal is robust,
but fine differences would need a 50+ query harness.)*

#### RAM guard: `DEVAI_EMBED_MAX_CHARS` (CPU OOM protection)

The model's 32768-token window is *not* a RAM limit. The ONNX-exported encoders do not
cap the input sequence themselves, and the **raw parser** (non-AST files: minified
`json`/`sql`/`md`, lockfiles, bundles) can emit a **single chunk of hundreds of thousands
of tokens** — observed up to ~2.8M chars. On CPU the O(N²) attention on such a blob
explodes the ONNX arena to **~20 GB** and the OS OOM-kills the indexer mid-repo.

`embed()` therefore caps each text to **`DEVAI_EMBED_MAX_CHARS` (default 4096 ≈ 1024
tokens)** before encoding. 4096 = `large_function_threshold`, so **no code chunk is ever
truncated** (the AST chunker keeps those ≤1024 tokens) — only oversized raw blobs get
clipped. The **stored** chunk text is untouched; only the *vector* is computed on the
prefix, and the reranker reads the full text anyway, so recall is unaffected for code.

| Env | Default | Effect |
|-----|---------|--------|
| `DEVAI_EMBED_MAX_CHARS` | `4096` | Chars per text fed to the encoder. `0` disables. Raise with RAM/GPU headroom; lower (e.g. `2048` → ~1–3 GB peak) on tight memory. |
| `DEVAI_EMBED_BATCH_SIZE` | `16` | Encoder batch. A big chunk pads the whole batch, so smaller batches also cut the peak. |

> Measured (CPU, 8 cores, granite int8): with `MAX_CHARS=2048` the per-repo RAM peak drops
> from **~20.9 GB (OOM) → ~1–3 GB**; 4096 stays comfortably under a 20 GB cgroup cap.
> On constrained hosts, pair this with a hard `systemd-run --user --scope -p MemoryMax=…`
> so a runaway never takes the whole box (incl. WSL) down.

> ⚠️ **Changing the model changes the vector dimension** (384 ↔ 768). The vector
> store is incompatible across dimensions → it **forces a full re-index**. See §6.

---

## 2. The response pipeline: rerank → token budget

When you call `recall` or `search`, the flow is:

```
vector search (top_k_fetch)  →  reranker  →  token budget (fit)  →  response
```

1. **Reranker** (`DEVAI_RERANK_*`): defaults to `flashrank`
   (ms-marco-MiniLM-L-12-v2). Reorders by relevance and trims to `limit`.
   The default model is **English** — it reorders well but yields lower scores on
   cross-lingual queries (an English query against a non-English memory ranks #1
   correctly but with a score near ~0.37). For non-English content, set
   **`DEVAI_RERANK_MODEL=ms-marco-MultiBERT-L-12`** — a multilingual flashrank
   model (same ONNX/CPU speed, ~150 ms for 15 candidates). Measured: the same
   cross-lingual query jumps from **~0.37 → ~0.99**. No re-index needed — the
   reranker runs at query time only. Other flashrank options:
   `ms-marco-TinyBERT-L-2-v2` (fastest), `ms-marco-MiniLM-L-12-v2` (default,
   English), `ms-marco-MultiBERT-L-12` (multilingual).

2. **Token budget** (`DEVAI_TOKEN_*` + `DEVAI_SUMMARIZER_*`): fits the content
   under `DEVAI_MAX_OUTPUT_TOKENS`. This is where drop/summarize/truncate happens.

### The per-item budget formula

```
per_item_budget = max(DEVAI_MAX_OUTPUT_TOKENS / limit, 128)
```

Each memory that **fits** its slice is returned **verbatim**; one that exceeds it
is processed by the strategy. With `MAX_OUTPUT_TOKENS=8000`:

| `limit` | slice/item | effect |
|---------|------------|--------|
| 4 | 2000 tok | almost everything verbatim |
| 8 | 1000 tok | medium verbatim, large summarized |
| 12 | 666 tok | many summarized |
| 18 | 444 tok | almost all summarized |

**Rule of thumb**: a memory stays verbatim ⟺ `memory_size ≤ 8000 / limit`. A
600-token memory is verbatim up to `limit ≤ 13`; a 2000-token one, up to `limit ≤ 4`.

---

## 3. Token-budget strategies (`DEVAI_TOKEN_STRATEGY`)

| Strategy | What it does | CPU cost | Drops items | Recommendation |
|----------|--------------|----------|-------------|----------------|
| `drop` | discards whole items from worst-ranked down until it fits | **zero** | **YES** ❌ | avoid for memories — hides relevant results |
| `soft_truncate` | cuts each large item at a sentence boundary (keeps the head) | **zero** | no | good for small machines / browsing |
| `hard_truncate` | cuts at an exact char count | zero | no | rarely |
| `summarize` | summarizes each large item with the summarizer | depends on summarizer | no | **recommended** with `extractive` |

> **The original bug**: with `drop` + `MAX_OUTPUT_TOKENS=4000`, one or two large
> memories filled the budget and the rest were **silently dropped**
> (`items_dropped: 9`) → you'd conclude "that memory doesn't exist" when it did.
> Any strategy other than `drop` keeps `output_count == input_count`.

---

## 4. Summarizers (`DEVAI_SUMMARIZER_PROVIDER`)

| Provider | Type | Local | Verdict |
|----------|------|-------|---------|
| `noop` | none | ✅ | with `strategy=summarize` it falls back to truncation — useless |
| **`extractive`** | extractive (picks sentences by similarity to the query) | ✅ | **recommended**: reuses the embedding model, never corrupts identifiers, finds buried content |
| `flan-t5` | abstractive (generates text) | ✅ | **do NOT use for code/non-English**: corrupts identifiers (e.g. a `getStatusById` symbol comes out `getStatuById`) and words, 512-token input limit, slow. Patched for transformers 5.x but still not recommended |
| `openai` | abstractive cloud | ❌ | blocked by `require_local=true` (data exfiltration guard) |

**`extractive` is the right choice** for a code-memory tool:
- Preserves identifiers **verbatim** (it picks whole sentences, never splits words).
- It is **query-focused**: it surfaces the sentences relevant to your query, even
  when they sit at the end of a long memory.
- It reuses the already-loaded embedding model → no extra download.

---

## 5. Recommended configuration by hardware

The heaviest CPU factor is the **embedding model** (ml-mpnet 768d is ~5x slower
than minilm-l6 on CPU). The summarize strategy is secondary (`extractive` adds
~0.5–1 s per recall to embed sentences; `soft_truncate` is free).

### 🖥️ CPU machine, non-English content — RECOMMENDED
```jsonc
DEVAI_EMBEDDING_MODEL    = "ml-granite"        // 384d multilingual, ONNX int8
DEVAI_EMBEDDING_DEVICE   = "cpu"
DEVAI_TOKEN_STRATEGY     = "summarize"
DEVAI_SUMMARIZER_PROVIDER= "extractive"
DEVAI_MAX_OUTPUT_TOKENS  = "8000"
```
> Best quality **and** fastest indexing on CPU (see the benchmark in §1). The ONNX
> backend ships with `devai setup`; needs an AVX2 CPU. If your CPU lacks AVX2,
> use `ml-mpnet` (best torch quality) or `ml-minilm` (lightest) below.

### 🖥️ Small / no-GPU (or weak GPU) machine, non-English content
```jsonc
DEVAI_EMBEDDING_MODEL    = "ml-minilm"        // 384d multilingual, fast
DEVAI_EMBEDDING_DEVICE   = "cpu"
DEVAI_TOKEN_STRATEGY     = "soft_truncate"    // zero extra CPU, drops nothing
DEVAI_MAX_OUTPUT_TOKENS  = "6000"
DEVAI_RERANK_PROVIDER    = "flashrank"
```
> **Should `drop` and `summarize` be off on a small PC?** `drop`: yes, always off
> (it loses memories — never worth it). As for `summarize`: on a small machine
> prefer `soft_truncate` instead — it keeps **all** memories and spends **no**
> extra CPU (no sentence embedding). Use `summarize`+`extractive` only if you can
> afford ~1 s more per recall in exchange for query-focused summaries.

### 🖥️ Powerful / GPU machine, non-English content
```jsonc
DEVAI_EMBEDDING_MODEL    = "ml-mpnet"         // 768d multilingual, best quality
DEVAI_EMBEDDING_DEVICE   = "cpu"              // or "cuda" with a good GPU
DEVAI_TOKEN_STRATEGY     = "summarize"
DEVAI_SUMMARIZER_PROVIDER= "extractive"
DEVAI_MAX_OUTPUT_TOKENS  = "8000"
```

### 🖥️ English-only content
```jsonc
DEVAI_EMBEDDING_MODEL    = "bge-base"   // or "minilm-l6" on a small machine
DEVAI_TOKEN_STRATEGY     = "summarize"
DEVAI_SUMMARIZER_PROVIDER= "extractive"
```

### Measured cost (CPU only, no GPU — old Maxwell laptop GPU, CPU fallback)
- `ml-granite` (ONNX int8): ~58 texts/sec in batch — the fastest of the multilingual
  models; ~50k chunks in ~14 min. Half the vector dimension (384) → smaller store.
- `ml-mpnet`: ~225 ms per memory embed; ~17–27 chunks/sec in batch; ~50k chunks ~48 min.
- Full re-index of a large repo (~1500 files, ~7000 chunks, 58k edges): ~2 h with
  `ml-mpnet`, proportionally faster with `ml-granite`.
- Typical recall: ~1–2 s. (`minilm-l6` was ~5x faster than `ml-mpnet`.)

---

## 6. Verified behaviors

An empirical test battery over real memories with `ml-mpnet` + `extractive`:

| Test | What was measured | Result |
|------|-------------------|--------|
| Content at the END | query targeting the last paragraph | `summarize`/extractive **finds it** ✅; `soft_truncate` misses it ❌ |
| Verbatim threshold | budget sweep | verbatim if `budget ≥ memory size`; summarized below that |
| Minimum budget (60 tok) | extreme compression | coherent, **identifiers intact, zero corruption** |
| 3 strategies | drop/summarize/soft_truncate | drop = all-or-nothing; summarize = compresses the relevant part; soft = linear |
| Cross-lingual query | query in language A, memory in language B | correct #1 match — score ~0.37 with the English reranker, ~0.99 with `ms-marco-MultiBERT-L-12` |
| Code (`search`) | — | forces `drop` automatically — **code is never summarized** (avoids corrupting identifiers) |

**Conclusions**:
- `extractive` surfaces relevant content even when buried deep in a long memory
  → it is the correct strategy for targeted recall.
- Cross-lingual retrieval works thanks to the multilingual model.
- `summarize`/`soft_truncate` never lose memories (`output_count == input_count`).

### Usage cheat sheet

| You want… | Configure / use |
|-----------|-----------------|
| The exact detail of a specific thing | `limit 3-5` → full verbatim |
| To explore a broad topic | `limit 12-18` → many results to the point, none lost |
| To query in another language | nothing — `ml-mpnet`/`ml-minilm` bridge it |
| Always surface the relevant bit even if buried | `summarize` + `extractive` |

---

## 7. Gotchas when migrating models (learned in production)

1. **`config.yaml` overrides the env var.** The Go CLI (`devai index`) and the MCP
   read `embeddings.model` from `config.yaml` and pass it to Python, **overriding**
   `DEVAI_EMBEDDING_MODEL`. **Each repo has its own `.devai/config.yaml`**, plus
   one at the workspace root and one in `state/`. Changing only the env is not
   enough: run `devai model use <key>` in EACH repo, or edit every `config.yaml`.
   (The template default lives in `cmd/devai/cmd/init.go`.)

2. **Wiping `vectors/` is not enough — clear `file_state`.** The re-index checks a
   per-file hash in the `file_state` table (in `index.db`) and **skips** matches,
   even when the vectors no longer exist. `--incremental=false` does NOT bypass
   the hash check. You must `DELETE FROM file_state` (and `index_state`) to force
   a re-embed. **`index.db` holds the memories and the graph → do NOT delete it**,
   only those two tables. Memories are re-embedded with a standalone script that
   reads them from SQLite and re-embeds `f"{title} {content}"` (there is no native
   re-embed command).

3. **The idle watchdog (1800 s) kills a long re-index.** `index_repo` is a single
   long RPC call; the watchdog measures "idle" as time since the last *new*
   request, not CPU activity. A large repo with a heavy model takes > 30 min → the
   watchdog kills the ML service (`reading response: EOF`). For re-indexing set
   `DEVAI_ML_IDLE_TIMEOUT_SEC=0`.

### Full model-switch procedure
```bash
# 1. switch the model in EVERY config.yaml
for r in repoA repoB ...; do (cd "$r" && devai model use ml-mpnet); done
# 2. stop the MCP / ML service (release the LanceDB)
# 3. wipe the vector store (keeps index.db = memories + graph)
rm -rf "$DEVAI_STATE_DIR/vectors"
# 4. clear file_state + index_state in index.db (NOT memories)
#    sqlite3 index.db "DELETE FROM file_state; DELETE FROM index_state;"
# 5. re-index each repo with the watchdog disabled
for r in repoA repoB ...; do
  (cd "$r" && DEVAI_ML_IDLE_TIMEOUT_SEC=0 devai index --incremental=false)
done
# 6. re-embed memories with the new model (standalone script)
# 7. reconnect the MCP
```

---

## 8. Where each configuration lives

| File | Read by | Purpose |
|------|---------|---------|
| `<repo>/.devai/config.yaml` | CLI `devai index` (from that repo) | model + excludes when indexing that repo |
| `<workspace>/.devai/config.yaml` | MCP (cwd = root) | model for the MCP service |
| `<workspace>/.devai/state/config.yaml` | shared-state resolution | shared `state_dir` |
| `.mcp.json` (client env) | MCP at runtime | strategy, summarizer, max_tokens, rerank, idle timeout |

**They must all use the SAME model**, or gotcha #1 reappears.
