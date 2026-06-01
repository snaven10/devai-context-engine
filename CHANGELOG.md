# Changelog

All notable changes to DevAI are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/). This project is in **alpha** — versions are tagged
`vX.Y.Z-alpha` and APIs may change without notice.

> **How to use this file:** land changes under **[Unreleased]** as you merge PRs, then on release move that
> block under a new `## [vX.Y.Z-alpha]` heading with the date.

---

## [Unreleased]

### Added
- **Blend-scored chunking for long memories** — works around the embedding context-window limit (mpnet
  caps inputs at 128 tokens, hiding buried content in long memories from semantic recall). A long memory
  now keeps its intro/whole vector (`chunk_level="memory"`) **and** gains title-prepended body-window
  vectors (`chunk_level="memory_chunk"`); short memories stay single-vector. Recall blends the two signals
  — `alpha*intro_sim + (1-alpha)*max_chunk_sim` (A/B-validated `alpha=0.5`); the blend, not a plain max,
  suppresses the noise floor chunking introduces, and the reranker still does final precision over full
  memory text. New `reindex_memories` RPC migrates existing memories. Tunable via `DEVAI_MEMORY_CHUNKING`
  (on by default), `DEVAI_MEMORY_BLEND_ALPHA`, `DEVAI_MEMORY_CHUNK_MAX_CHUNKS`, `DEVAI_MEMORY_CHUNK_OVERLAP`.
- Documented the **multilingual reranker** option: set `DEVAI_RERANK_MODEL=ms-marco-MultiBERT-L-12` (a
  flashrank ONNX model) so cross-lingual queries score correctly — measured ~0.37 → ~0.99 on an
  English-query/Spanish-memory case, with no re-index (the reranker runs at query time). Updated
  `docs/09-models-and-tuning.md` and `docs/11-configuration.md` (EN + ES).

<!-- Template:
### Added
### Changed
### Fixed
### Removed
-->

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
