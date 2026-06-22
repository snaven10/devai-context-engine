# Changelog

All notable changes to DevAI are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/). This project is in **alpha** — versions are tagged
`vX.Y.Z-alpha` and APIs may change without notice.

> **How to use this file:** land changes under **[Unreleased]** as you merge PRs, then on release move that
> block under a new `## [vX.Y.Z-alpha]` heading with the date.

---

## [Unreleased]

### Added
- Installer (`install.sh`) now injects `DEVAI_EMBED_MAX_CHARS` into the MCP client config so the
  embed cap is active from the first index without manual env-var wiring.
- `DEVAI_EMBED_MAX_CHARS` and `DEVAI_EMBED_BATCH_SIZE` documented in `docs/11-configuration.md`
  (EN + ES mirror) with default values, RAM-impact notes, and hook interaction.
- `docs/12-multi-repo-central-store.md` (EN + ES) — step-by-step recipe for pointing multiple
  repos at a single central store: when to do it, which env vars to set, how to verify with
  `devai index --status`, and common pitfalls.

### Changed
- `devai init` no longer writes a per-repo `state_dir` into the generated `config.yaml`. The
  store now resolves to the central default (`~/.local/share/devai/state`) unless the user
  explicitly overrides `DEVAI_STATE_DIR`. Removes the main footgun where each cloned repo got
  its own isolated vector store.
- The auto-index post-commit hook now embeds the active embedding model name and
  `DEVAI_EMBED_MAX_CHARS` (default `2048`) so incremental indexes use the same model/cap as the
  full index without requiring a separate env-var export in the shell profile.
- `install.sh` fails loudly (exit 1 with an explanatory message) when the ML wheel is absent
  from the release assets. Pass `--allow-no-ml` (or set `ALLOW_NO_ML=1`) to proceed with a
  search-only install.

### Fixed
- `devai server configure` (global scope) now writes the MCP entry to `~/.claude.json` instead of
  `~/.claude/settings.json`, which Claude Code silently ignores for MCP servers. Users who configured
  with an older version may have a harmless orphaned `devai` entry under `mcpServers` in
  `~/.claude/settings.json`; it can be removed by hand.
- `devai index` now aborts with a clear error when the requested model's output dimension
  mismatches the existing store's dimension, instead of silently appending mismatched vectors
  and corrupting the store. (#footgun-C)

### Docs
- Consolidated install and configuration into a single source of truth; removed the stale
  `DOCS.md` (which contradicted README and `docs/`).
- Corrected README ↔ docs contradictions: `DEVAI_STATE_DIR` default path, `DEVAI_TOKEN_STRATEGY`
  valid values, `DEVAI_MAX_OUTPUT_TOKENS` default, and `server configure --claude` flag name.

---

## [v0.12.0-alpha] — 2026-06-09

### Added
- `DEVAI_EMBED_MAX_CHARS` (default `4096`) and `DEVAI_EMBED_BATCH_SIZE` (default `16`): RAM
  guards for the local embedder. See `docs/09-models-and-tuning.md` §1 "RAM guard".

### Changed
- `devai upgrade` now reinstalls the matching `devai_ml` Python wheel from the release in
  addition to the Go binary, so the CLI and the ML service no longer drift apart on upgrade
  (previously only the source-build fallback refreshed Python). Warns, non-fatally, if the
  wheel asset or venv is missing.

### Fixed
- **Indexer OOM on CPU**: the ONNX-exported encoders (e.g. `ml-granite` int8) don't cap the
  input sequence, so a single oversized non-code chunk from the raw parser (minified
  json/sql/md — up to ~2.8M chars) made the O(N²) attention explode the ONNX arena to ~20 GB
  and OOM-kill the indexer mid-repo. `embed()` now caps each text to `DEVAI_EMBED_MAX_CHARS`
  before encoding (no code chunk is truncated; stored text and reranker input are untouched),
  dropping the per-repo RAM peak from ~20.9 GB to ~1–3 GB.

---

## [v0.11.0-alpha] — 2026-06-08

### Added
- **ONNX embedding backend** for the local provider (`backend="onnx"` via sentence-transformers +
  `optimum`), plus two Granite R2 models: **`ml-granite`** (97M, int8, 384 dims) and **`ml-granite-lg`**
  (311M, int8, 768 dims). On CPU, `ml-granite` beats the previous multilingual default on recall while
  indexing ~6× faster at half the vector storage (benchmark in `docs/09`). `devai setup` installs the
  ONNX backend automatically; no `query:`/`passage:` prefixes required; needs an AVX2 CPU. (#26)
- Interactive `install.sh` wizard (paths, CPU/GPU, model, AI client, scope, git hook) that is
  TTY-aware and falls back to flags + defaults when piped. (#25)
- `devai server configure --scope project` writes a project `.mcp.json`; `--env KEY=VALUE`
  pins tuning vars into the MCP entry. (#25)
- Documented the **multilingual reranker** option: set `DEVAI_RERANK_MODEL=ms-marco-MultiBERT-L-12` (a
  flashrank ONNX model) so cross-lingual queries score correctly — measured ~0.37 → ~0.99 on an
  English-query/Spanish-memory case, with no re-index (the reranker runs at query time). Updated
  `docs/09-models-and-tuning.md` and `docs/11-configuration.md` (EN + ES).

### Changed
- ML requirements: bump `sentence-transformers>=3.2` and add `optimum[onnxruntime]` (CPU + GPU) so the
  ONNX backend installs with `devai setup`. (#26)
- `docs/11-configuration.md` (+ Spanish mirror) now document every `DEVAI_*` variable. (#25)

### Documented
- `docs/09` (EN + ES): chunk size vs the embedder's context window — with the reranker, recall is
  chunk-size-agnostic across 256–1024; pick by efficiency. `ml-granite`'s 32768-token window removes the
  truncation `ml-mpnet` (128) suffered. (#26)

---

## [v0.9.2-alpha] — 2026-05-31

### Fixed
- **`devai hooks install` no longer clobbers existing post-commit hooks.** Re-running install used to
  overwrite the entire `post-commit` file. It now manages a delimited `BEGIN/END` block: install replaces
  only the DevAI block (or appends to a foreign hook), and uninstall removes only that block. The injected
  command also `cd`s to the repo top-level and runs in the background with output suppressed. (#22)

### Added
- **`docs/11-configuration.md`** (EN + ES) — configuration-mechanics reference: `config.yaml` schema, the
  three config locations and precedence (`config.yaml` overrides `DEVAI_EMBEDDING_MODEL`), MCP client wiring
  (`server configure`, project `.mcp.json`), the env-var reference, and the git auto-index hooks. (#22)
- **`docs/10-mcp-token-benchmark.md`** (EN + ES) — real MCP-vs-direct-mode A/B showing ~71% cost savings and
  ~12× fewer tokens on a diagnostic task. (#21)

### Changed
- README: corrected a stale env var name (`DEVAI_ML_MODEL` → `DEVAI_EMBEDDING_MODEL`), added the multilingual
  models to the table, and linked the new docs.

## [v0.9.1-alpha] — 2026-05-29

### Fixed
- **Indexer stored an empty repo name.** `_repo_name` used `Path(repo_path).name`, but `devai index` sends
  `repo_path="."` (run from inside a repo) and `Path(".").name` is `""`, so every `(repo, branch)` pair was
  stored under an empty repo name — breaking repo-filtered tools like `impact_analysis`. Now resolves the
  path first: `Path(".").resolve().name`. (#20)

## [v0.9.0-alpha] — 2026-05-28

### Added
- **Multilingual embedding models** `ml-minilm` (paraphrase-multilingual-MiniLM-L12-v2, 384d) and `ml-mpnet`
  (paraphrase-multilingual-mpnet-base-v2, 768d), for non-English content. (#19)
- **`docs/09-models-and-tuning.md`** (EN + ES) — model comparison, summarizer & token-budget strategies,
  hardware-based config, and a model-migration runbook. (#19)

### Fixed
- **`FlanT5Summarizer` for transformers 5.x** — the `text2text-generation` pipeline task was removed
  upstream, silently degrading the summarizer to truncation. Reworked to use `AutoModelForSeq2SeqLM` directly
  with anti-repetition generation params and 512-token input truncation. (#19)

### Changed
- Docs: corrected the MCP tool count (14 → 21) across all docs; fixed 18 broken internal links and rebuilt
  the intro Documentation Map.

### Notes
- `devai init` default stays `minilm-l6` (fast, English) — multilingual models are opt-in.

---

## Earlier releases

See the [GitHub Releases](https://github.com/snaven10/devai-context-engine/releases) page for `v0.8.0-alpha`
and prior.
