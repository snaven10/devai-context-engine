# Configuration

How DevAI is configured: the `config.yaml` files, how the MCP server is wired into AI clients
(Claude Code, Cursor, …), the environment variables, and the git auto-index hooks.

> For embedding-model choices, summarizer/token-budget strategies, and hardware-based tuning, see
> [Models & Tuning](09-models-and-tuning.md). This page is the **configuration mechanics** reference.

---

## 1. `config.yaml` — project configuration

`devai init` creates `.devai/config.yaml` in the repo. The CLI (`devai index`, `devai server …`) and the
MCP server read it to resolve the model, state directory, storage mode and excludes.

### 1.1 Full schema

```yaml
project:
  name: my-repo                 # human-friendly alias
  path: /full/path/to/repo

state_dir: /full/path/to/.devai/state   # where vectors + graph + memory live
language: en                    # en | es  (affects model descriptions / TUI)

embeddings:
  provider: local               # local | openai | voyage | custom
  model: minilm-l6              # registry key — see Models & Tuning
  offline: auto                 # auto (cache, no Hub check) | true | false

storage:
  mode: local                   # local | shared | hybrid
  qdrant_url: localhost:6334    # only for shared / hybrid
  qdrant_api_key: ""            # only for shared / hybrid
  local_db_path: ""             # override the LanceDB path (optional)

indexing:
  exclude:                      # glob patterns skipped during indexing
    - "node_modules/**"
    - "vendor/**"
    - ".git/**"
    - "dist/**"
    - "build/**"
    - "*.min.js"
    - "*.lock"

runtime:
  python_path: ""               # explicit python binary (optional; auto-detected)
```

### 1.2 The three locations (and how they're found)

DevAI looks for `.devai/config.yaml` by **walking up** from the current directory (`FindConfigFile`). In a
multi-repo workspace you typically end up with several:

| File | Read when | Drives |
|------|-----------|--------|
| `<repo>/.devai/config.yaml` | `devai index` run **inside that repo** | model + excludes for that repo |
| `<workspace>/.devai/config.yaml` | `devai server mcp` run from the workspace root | model for the MCP service |
| `<workspace>/.devai/state/config.yaml` | shared-state resolution | the shared `state_dir` |

> **Keep the `embeddings.model` identical across all of them.** They are independent files; if one drifts,
> tools that run from that directory will index/query with the wrong (or empty-dimension) model.

### 1.3 Precedence: `config.yaml` wins over the env var

This is the single most common surprise. The **Go CLI and MCP read `embeddings.model` from the nearest
`config.yaml` and pass it to the Python service, overriding `DEVAI_EMBEDDING_MODEL`.** Setting only the env
var is **not** enough to change the model — change it in `config.yaml` (or with `devai model use <key>`,
which edits the file for you). See the migration runbook in [Models & Tuning](09-models-and-tuning.md#7-gotchas-when-migrating-models-learned-in-production).

Env vars *do* take effect for parameters that have no `config.yaml` field (token budget, summarizer, rerank,
idle timeout — see §3).

---

## 2. MCP configuration — wiring DevAI into AI clients

DevAI talks to AI agents over the **Model Context Protocol** on **stdio**. The client launches
`devai server mcp` as a subprocess and calls the tools over JSON-RPC.

### 2.1 Automatic: `devai server configure`

```bash
devai server configure --all      # Claude Code + Cursor (default)
devai server configure --claude   # only Claude Code
devai server configure --show     # preview without writing
devai server configure --remove   # remove the devai entry
devai server configure --claude --scope project   # write a project .mcp.json instead of the global ~/.claude.json
devai server configure --claude --env DEVAI_EMBEDDING_MODEL=ml-mpnet  # pin tuning vars into the entry
```

It (a) resolves the absolute `devai` binary path, (b) detects the project `config.yaml` + state dir, (c)
writes the MCP server entry into each client config, and (d) generates `.devai/AGENT.md` (tool-usage
instructions for the agent).

| Client | File written | Key |
|--------|--------------|-----|
| Claude Code | `~/.claude.json` | `mcpServers.devai` |
| Cursor / Windsurf | `~/.cursor/mcp.json` | `mcpServers.devai` |

> `--scope project` writes the Claude entry to `<projectRoot>/.mcp.json` (merged non-destructively, Claude Code only).
> `--env KEY=VALUE` is repeatable and merges on top of the defaults (`DEVAI_STATE_DIR`, Qdrant).
>
> **Note on the model and `--env`:** by default `server configure` does *not* write `DEVAI_EMBEDDING_MODEL`
> into `env` — the model is resolved from `config.yaml` (§1.3). When you pass it explicitly (e.g. the
> installer's `--env DEVAI_EMBEDDING_MODEL=…`), it *is* pinned into the entry and acts as the effective
> model until a `config.yaml` exists (which then overrides it again, per §1.3).

The entry it writes:

```json
{
  "type": "stdio",
  "command": "/abs/path/to/devai",
  "args": ["server", "mcp"],
  "env": {
    "DEVAI_STATE_DIR": "/abs/path/.devai/state"
  }
}
```

> **Note — the model is *not* written into `env`.** `server configure` only sets `DEVAI_STATE_DIR` (plus
> `DEVAI_STORAGE_MODE` / Qdrant vars when storage mode is `shared`/`hybrid`). The embedding model is resolved
> from `config.yaml` (§1.3). If you want to pin tuning parameters (summarizer, token strategy, rerank), add
> them to the `env` block manually — see §3.

### 2.2 Project-scoped config: `.mcp.json`

Claude Code also supports a **project-level** `.mcp.json` at the repo/workspace root, which is the right
place to pin per-project tuning. Same structure as the entry above, e.g.:

```json
{
  "mcpServers": {
    "devai": {
      "command": "/abs/path/to/devai",
      "args": ["server", "mcp"],
      "env": {
        "DEVAI_STATE_DIR": "/abs/path/.devai/state",
        "DEVAI_EMBEDDING_MODEL": "ml-mpnet",
        "DEVAI_TOKEN_STRATEGY": "summarize",
        "DEVAI_SUMMARIZER_PROVIDER": "extractive",
        "DEVAI_MAX_OUTPUT_TOKENS": "4000"
      }
    }
  }
}
```

After any change, **restart / reconnect the MCP** in your client for it to take effect.

### 2.3 `.devai/AGENT.md`

`server configure` also drops an `AGENT.md` telling the agent to prefer DevAI tools (`search`,
`build_context`, `read_symbol`, `get_references`, `recall`/`remember`) over manual file reads. Point your
agent's instructions at it, or paste its contents into your project rules.

### 2.4 The installer wizard

`scripts/install.sh` is **TTY-aware**. Run from a terminal it walks you through a short wizard; piped
(`curl … | bash`) it runs non-interactively with defaults + flags and never blocks on a prompt.

| Prompt | Default | Flag |
|--------|---------|------|
| Install directory | `~/.local/share/devai` | `--install-dir DIR` |
| State directory (`DEVAI_STATE_DIR`) | `<install-dir>/state` | `--state-dir DIR` |
| PyTorch CPU or GPU | CPU | `--gpu` |
| Embedding model | `minilm-l6` (or `ml-mpnet`) | `--model KEY` |
| Configure AI client | `claude` (or `cursor` / `both` / `none`) | `--client NAME` |
| Claude config scope | `global` (or `project`) | `--scope SCOPE` |
| Install git auto-index hook | yes | `--hooks` / `--no-hooks` |
| Accept all defaults, no prompts | — | `--yes` (implied when no TTY) |

After installing, the wizard delegates client wiring to `devai server configure` and (optionally) installs
the git hook via `devai hooks install` — it never writes client JSON itself.

---

## 3. Environment Variables

Read by the Python ML service at startup. Useful in the MCP `env` block or your shell. (Names are the
authoritative ones from the service config.)

**Core / paths**

| Variable | Purpose | Default |
|----------|---------|---------|
| `DEVAI_STATE_DIR` | Where vectors/graph/memory live | `~/.local/share/devai/state` |
| `DEVAI_LOCAL_DB_PATH` | Override the LanceDB vectors path | `<state_dir>/vectors` |
| `DEVAI_PYTHON` | Explicit python binary for the ML service | auto-detected |

**Embeddings**

| Variable | Purpose | Default |
|----------|---------|---------|
| `DEVAI_EMBEDDING_MODEL` | Embedding model key *(overridden by `config.yaml`, §1.3)* | `minilm-l6` |
| `DEVAI_EMBEDDING_PROVIDER` | `local` \| `openai` \| `voyage` \| `custom` | `local` |
| `DEVAI_EMBEDDING_DEVICE` | `cpu` \| `cuda` | `cpu` |
| `DEVAI_EMBEDDING_API_KEY` | API key for remote embedding providers | — |
| `DEVAI_EMBEDDINGS_OFFLINE` | `auto` \| `true` \| `false` | `auto` |
| `DEVAI_EMBED_MAX_CHARS` | RAM guard — max chars fed to the encoder per text (NOT the model's context limit). Lower (e.g. `2048`) on low-RAM machines to avoid OOM on minified/large non-code chunks. | `4096` |
| `DEVAI_EMBED_BATCH_SIZE` | Texts per embedding batch. Lower (e.g. `8`) to reduce peak RAM. | `16` |

**Token budget & summarizer**

| Variable | Purpose | Default |
|----------|---------|---------|
| `DEVAI_TOKEN_STRATEGY` | `drop` \| `soft_truncate` \| `hard_truncate` \| `summarize` | `drop` |
| `DEVAI_MAX_OUTPUT_TOKENS` | Token budget for tool responses | `4000` |
| `DEVAI_TOKEN_ENCODING` | Tokenizer encoding name | `cl100k_base` |
| `DEVAI_SUMMARIZER_PROVIDER` | `noop` \| `extractive` \| `flan-t5` \| `openai` | `extractive` |
| `DEVAI_SUMMARIZER_MODEL` | Model id for non-extractive summarizers (e.g. `google/flan-t5-small`) | provider-specific |
| `DEVAI_SUMMARIZER_DEVICE` | `cpu` \| `cuda` for local summarizers | `cpu` |
| `DEVAI_SUMMARIZER_API_KEY` | API key for `openai` summarizer | — |
| `DEVAI_SUMMARIZER_TARGET_TOKENS` | Target length for summaries | `200` |
| `DEVAI_SUMMARIZER_REQUIRE_LOCAL` | Block non-local providers (fail instead of using a remote summarizer) | `true` |

**Rerank**

| Variable | Purpose | Default |
|----------|---------|---------|
| `DEVAI_RERANK_ENABLED` | Toggle reranking on/off | `true` |
| `DEVAI_RERANK_PROVIDER` | `noop` \| `flashrank` | `flashrank` |
| `DEVAI_RERANK_MODEL` | flashrank model; `ms-marco-MultiBERT-L-12` for **multilingual** | `ms-marco-MiniLM-L-12-v2` |
| `DEVAI_RERANK_TOP_K_FETCH` | Candidates pulled before reranking | `15` |
| `DEVAI_RERANK_CACHE_DIR` | Where flashrank model files are cached | `<install>/flashrank` |

**Chunking**

| Variable | Purpose | Default |
|----------|---------|---------|
| `DEVAI_MAX_CHUNK_TOKENS` | Upper bound on a code chunk | `512` |
| `DEVAI_MIN_CHUNK_TOKENS` | Lower bound on a code chunk | `64` |
| `DEVAI_LARGE_FUNCTION_THRESHOLD` | Token size above which a function is split | `1024` |

**Storage & service**

| Variable | Purpose | Default |
|----------|---------|---------|
| `DEVAI_STORAGE_MODE` | `local` \| `shared` \| `hybrid` | `local` |
| `DEVAI_QDRANT_URL` / `DEVAI_QDRANT_API_KEY` | Shared/hybrid Qdrant | — |
| `DEVAI_ML_IDLE_TIMEOUT_SEC` | Idle seconds before the ML service exits (`0` disables) | `1800` |
| `DEVAI_API_TOKEN` | Bearer token for the HTTP server mode (`devai server http`) | — |

> **Not user-configurable env vars** — these strings appear in the codebase but are **not** read from the
> environment, so do **not** treat them as tunables:
> - `DEVAI_ML_READY` — set by the runtime to signal the ML service is up.
> - `DEVAI_AUTO_INDEX` — the begin/end **marker text** of the git post-commit hook block (see §4), not a variable.
> - `DEVAI_UUID_NAMESPACE` — a hardcoded UUID **constant** in the Qdrant store, not an env var.

> Long re-index runs (large repos on CPU) can exceed the idle watchdog. Set `DEVAI_ML_IDLE_TIMEOUT_SEC=0`
> while re-indexing — see [Models & Tuning](09-models-and-tuning.md#7-gotchas-when-migrating-models-learned-in-production).

See [Models & Tuning §3–§4](09-models-and-tuning.md) for the full set of summarizer/token-budget/rerank
variables and their trade-offs.

---

## 4. Git Auto-Index Hooks

`devai hooks install` adds a **git post-commit hook** that re-indexes the repo (incrementally, in the
background) after each commit, so the index never goes stale.

```bash
devai hooks install [repo-path]     # install or update (defaults to current repo)
devai hooks uninstall [repo-path]   # remove only the devai section
```

### What it writes

A **delimited block** in `.git/hooks/post-commit`:

```sh
# >>> DEVAI_AUTO_INDEX >>>
# Auto-index after each commit. Managed by 'devai hooks install/uninstall' — do not edit by hand.
( cd "$(git rev-parse --show-toplevel)" && DEVAI_STATE_DIR="…/.devai/state" "/abs/devai" index --incremental ) >/dev/null 2>&1 &
# <<< DEVAI_AUTO_INDEX <<<
```

- **`cd "$(git rev-parse --show-toplevel)"`** — runs from the repo root so the indexer resolves the real
  repo name.
- **`>/dev/null 2>&1 &`** — backgrounded and silenced; the commit is never blocked or polluted by logs.
- **Coexists with other hooks.** The block is delimited by BEGIN/END markers: re-running `install` replaces
  only that block, and `uninstall` removes only that block — any other post-commit logic is preserved. If
  the file ends up with just a shebang, `uninstall` removes it entirely.

> Tip: combine with `DEVAI_STATE_DIR` pointing at a **shared workspace state** so several repos keep a single
> unified index.

---

## 5. Quick Recap

1. **Model & excludes** → `.devai/config.yaml` (per repo). `config.yaml` beats `DEVAI_EMBEDDING_MODEL`.
2. **Wire into an AI client** → `devai server configure --all` (writes `mcpServers.devai`), or a project
   `.mcp.json` for per-project tuning.
3. **Tuning** (summarizer, token budget, rerank, idle timeout) → env vars in the MCP `env` block.
4. **Keep the index fresh** → `devai hooks install`.
5. **Reconnect the MCP** after changing any client config or env.
