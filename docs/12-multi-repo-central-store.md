# Multi-Repo Central Store

> 🇪🇸 [Leer en español](es/12-multi-repo-store-central.md)

How to configure multiple repositories to feed a **single shared DevAI index** via post-commit
hooks, while one MCP server reads that unified store for all of them.

---

## 1. Mental Model

By default every `devai init` creates its own isolated index inside the repo. The multi-repo
topology inverts this: you designate **one central store** and tell every repo to write into it.

```
   $WORKSPACE/repo-a/   $WORKSPACE/repo-b/   $WORKSPACE/repo-c/
        |                     |                     |
   post-commit hook      post-commit hook      post-commit hook
    (devai index          (devai index          (devai index
     --incremental)        --incremental)        --incremental)
        |                     |                     |
        +----------+----------+----------+----------+
                   |
          CENTRAL STORE
          $WORKSPACE/.devai/state/
           ├── index.db         (graph + index_state + memory SQLite)
           ├── vectors/         (LanceDB embedding vectors)
           └── config.yaml      (shared model config)
                   |
        +----------+
        |
   devai server mcp
   (one MCP process, reads the shared store)
        |
   AI Agent (Claude Code, Cursor, …)
```

All three repos contribute their symbols, memories, and embeddings to the same database.
The AI agent sees the full picture through a single MCP connection.

---

## 2. Recipe

### 2.1 Pick (or create) the central store path

Choose a path that is **not inside any individual repo** to avoid accidental git-tracking:

```bash
# Option A — dedicated workspace root (recommended for monorepos)
export CENTRAL="$WORKSPACE/.devai/state"

# Option B — user-level state shared across all projects
export CENTRAL="$HOME/.local/share/devai/state"

mkdir -p "$CENTRAL"
```

### 2.2 Initialize each repo

Run `devai init` inside every repo that will contribute to the shared store. As of the
recent default-change, `init` no longer pins a per-repo `state_dir` in `config.yaml`, so the
file is created without a `state_dir` line.

```bash
cd $WORKSPACE/repo-a && devai init
cd $WORKSPACE/repo-b && devai init
cd $WORKSPACE/repo-c && devai init
```

> **Keep the same embedding model across all repos.** Mixing models creates vector
> dimension mismatches that corrupt the store — `devai index` now aborts on mismatch, but
> a corrupt store still requires a full re-index to recover. See
> [Configuration §1.3](11-configuration.md#13-precedence-configyaml-wins-over-the-env-var)
> and the [Models & Tuning footgun section](09-models-and-tuning.md#7-gotchas-when-migrating-models-learned-in-production).

### 2.3 Point each repo at the central store

**Path A — via environment variable (recommended for CI / one-off runs):**

```bash
DEVAI_STATE_DIR="$CENTRAL" devai index            # full index
DEVAI_STATE_DIR="$CENTRAL" devai index --incremental  # incremental
```

**Path B — via `config.yaml` (permanent, per-repo):**

Add or set `state_dir` in each repo's `.devai/config.yaml`:

```yaml
state_dir: /abs/path/to/central/state   # shared across all repos

embeddings:
  model: ml-granite   # must be identical in every repo
```

> If you set `state_dir` in `config.yaml`, you no longer need `DEVAI_STATE_DIR` at the
> command line — the CLI reads it from the file. Both approaches are equivalent; choose
> whichever fits your workflow.

**Override the LanceDB path if needed** (only when splitting vectors from the DB):

```bash
export DEVAI_LOCAL_DB_PATH="$CENTRAL/vectors"
```

### 2.4 Install post-commit hooks

Install the auto-index hook in each repo, pointing at the central store. The hook now
embeds the active embedding model and `DEVAI_EMBED_MAX_CHARS` automatically:

```bash
cd $WORKSPACE/repo-a
DEVAI_STATE_DIR="$CENTRAL" devai hooks install

cd $WORKSPACE/repo-b
DEVAI_STATE_DIR="$CENTRAL" devai hooks install

cd $WORKSPACE/repo-c
DEVAI_STATE_DIR="$CENTRAL" devai hooks install
```

The resulting block in each repo's `.git/hooks/post-commit` will look like:

```sh
# >>> DEVAI_AUTO_INDEX >>>
# Auto-index after each commit. Managed by 'devai hooks install/uninstall' — do not edit by hand.
( cd "$(git rev-parse --show-toplevel)" && DEVAI_STATE_DIR="/abs/path/to/central/state" DEVAI_EMBEDDING_MODEL="ml-granite" DEVAI_EMBED_MAX_CHARS="2048" "/abs/path/to/devai" index --incremental ) >/dev/null 2>&1 &
# <<< DEVAI_AUTO_INDEX <<<
```

Note: the entire hook command is a **single line** — no backslash continuations. `DEVAI_EMBEDDING_MODEL` is included when a model was active at install time; if no model was set, it is omitted.

### 2.5 Point the MCP client at the central store

In `.mcp.json` (project-scoped) or `~/.claude.json` (global), set `DEVAI_STATE_DIR`
to the same central path:

```json
{
  "mcpServers": {
    "devai": {
      "command": "/abs/path/to/devai",
      "args": ["server", "mcp"],
      "env": {
        "DEVAI_STATE_DIR": "/abs/path/to/central/state"
      }
    }
  }
}
```

Use `devai server configure --claude --env DEVAI_STATE_DIR="$CENTRAL"` to generate this
entry automatically. After any change, restart / reconnect the MCP in your AI client.

### 2.6 Perform the initial index

Run a full index in each repo to seed the central store:

```bash
for repo in repo-a repo-b repo-c; do
  ( cd "$WORKSPACE/$repo" && DEVAI_STATE_DIR="$CENTRAL" devai index )
done
```

> On large repos or slow CPUs the ML idle watchdog may fire mid-run. Set
> `DEVAI_ML_IDLE_TIMEOUT_SEC=0` for the duration:
> ```bash
> DEVAI_ML_IDLE_TIMEOUT_SEC=0 DEVAI_STATE_DIR="$CENTRAL" devai index
> ```

---

## 3. Worktree Guards

A git worktree shares the parent repo's `.git/hooks/` directory via the `gitdir` pointer.
Without a guard, committing inside a worktree triggers the same post-commit hook and indexes
the worktree as a **phantom** — duplicate symbols with a different working-tree path.

Add a guard at the top of the `# >>> DEVAI_AUTO_INDEX >>>` block (or just before it) to
skip indexing for named worktrees:

```sh
# Skip indexing for worktrees that shadow the parent repo.
# Adjust the suffix pattern to match your worktree directory names.
case "$(git rev-parse --show-toplevel)" in
  *-desp|*-hotfix|*_wt) exit 0 ;;
esac
```

The `case` pattern should match whatever suffix or name you use for worktrees (`*-desp`,
`*_worktree`, `*/worktrees/*`, etc.).

`exit 0` inside the `case` guard terminates the **entire post-commit hook script** immediately
with a clean exit status (so git proceeds normally). This is fine — and the common case — when
the devai auto-index block is the only post-commit logic in the file.

**Warning:** if your hook file contains other post-commit tasks (linters, notification scripts,
etc.), a top-of-file `exit 0` guard will skip those too on matching worktrees. In that case,
guard only the devai block instead of the whole file — for example, wrap the devai line in an
`if` that skips on matching worktrees — so that your other tasks still run.

---

## 4. Known Gotchas

### 4.1 Model must match across all repos (footgun A)

Every repo's `.devai/config.yaml` `embeddings.model` must be identical. A different model
produces vectors of a different dimension; `devai index` aborts on mismatch since the guard
was added, but a partially written store still requires a full re-index. Set the model once
in a shared `config.yaml` at the workspace root if possible, or audit with:

```bash
grep -r "model:" $WORKSPACE/*/.devai/config.yaml 2>/dev/null
```

See [Models & Tuning §7](09-models-and-tuning.md#7-gotchas-when-migrating-models-learned-in-production)
for the migration runbook.

### 4.2 `DEVAI_EMBED_MAX_CHARS` prevents OOM on large chunks

Large or minified files can produce enormous text chunks. Without a cap, the embedding model
allocates the full tensor for each chunk and can exhaust RAM mid-index (especially with
`ml-granite` on CPU).

`devai hooks install` **always** embeds `DEVAI_EMBED_MAX_CHARS` into the hook — it defaults
to `"2048"` when the env var is unset, so you get the OOM guard automatically. To use a
different ceiling, set `DEVAI_EMBED_MAX_CHARS` in your shell **before** running
`devai hooks install`:

```bash
DEVAI_EMBED_MAX_CHARS=4096 DEVAI_STATE_DIR="$CENTRAL" devai hooks install
```

The value is then baked into the hook line and applies to every background index run.

### 4.3 Never commit `.mcp.json`

`.mcp.json` may hold API keys (`DEVAI_API_TOKEN`, embedding API keys, Qdrant keys). Add it
to `.gitignore`:

```bash
echo ".mcp.json" >> .gitignore
```

### 4.4 `config.yaml` beats `DEVAI_EMBEDDING_MODEL`

If a repo's `config.yaml` has `embeddings.model: minilm-l6` but you set
`DEVAI_EMBEDDING_MODEL=ml-granite` in the environment, the CLI uses `minilm-l6`. The
config file always wins over the env var for the model key. Change the file (or run
`devai model use <key>`) to switch models. See
[Configuration §1.3](11-configuration.md#13-precedence-configyaml-wins-over-the-env-var).

---

## 5. Transition Note

Before the recent default changes:

- `devai init` wrote an explicit `state_dir:` into every repo's `config.yaml`, pointing at
  the repo's own `.devai/state/`. Multi-repo sharing required manually overwriting that
  field in each file.
- `devai hooks install` wrote a bare `devai index --incremental` without embedding model or
  embed-cap vars, which meant hooks could use the wrong model after a `devai model use`
  change or run out of RAM on large files.

After the recent defaults:

- `devai init` omits `state_dir` (no per-repo default). The central store is adopted
  automatically when `DEVAI_STATE_DIR` is set or `state_dir` is set in `config.yaml`.
- `devai hooks install` embeds the active model and `DEVAI_EMBED_MAX_CHARS` into the hook
  block, so hooks stay in sync when the model or cap changes.

If you have **existing repos** initialized with the old behavior (explicit per-repo
`state_dir`), override by either:

1. Editing each `.devai/config.yaml` to set `state_dir: /abs/path/to/central/state`, or
2. Always passing `DEVAI_STATE_DIR="$CENTRAL"` on the command line and in the MCP env.

Re-run `DEVAI_STATE_DIR="$CENTRAL" devai hooks install` in each repo after the transition
to regenerate hooks with the correct store path.
