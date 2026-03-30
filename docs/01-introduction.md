# Introduction

> Back to [README](../README.md)
> 🇪🇸 [Leer en español](es/01-introduccion.md)

---

## What is DevAI?

DevAI is a **Context Engine for AI agents**. It gives AI coding assistants — Claude Code, Cursor, Copilot, custom agents — structured, semantic understanding of your codebase instead of forcing them to work through a keyhole of individual file reads.

It is not a search tool. It is not a linter. It is not another indexer you have to babysit. DevAI is the layer between your code and your AI agent that transforms raw source files into navigable, queryable, persistent knowledge.

**DevAI is to AI agents what an IDE is to humans.** An IDE gives you project-wide search, go-to-definition, find-all-references, and persistent workspace state. Without it, you are `cat`-ing files in a terminal. That is exactly what AI agents do today — they read files one at a time, lose context between turns, and cannot navigate code structurally. DevAI fixes that.

---

## The Problem

AI coding agents are powerful but blind. They operate under hard constraints that make large codebases painful:

- **Keyhole vision.** Agents see one file at a time. They cannot hold an entire module in working memory, let alone trace a call chain across packages.
- **No structural awareness.** `grep` finds text. It does not know that `handleAuth` is a method on `AuthMiddleware` that implements `http.Handler` and is called from three route files.
- **Amnesia.** Every session starts from zero. The agent that spent 20 minutes understanding your authentication flow yesterday remembers nothing today.
- **Context waste.** Without targeted retrieval, agents dump entire files into their context window. Half the tokens go to irrelevant code. The important parts get truncated.

These are not minor inconveniences. They are the reason AI agents produce shallow, incorrect, or incomplete code on anything beyond trivial changes.

---

## Core Capabilities

DevAI provides five capabilities that eliminate these constraints:

### 1. Semantic Search

Find code by *meaning*, not keywords. Ask for "authentication middleware" and get the actual auth handler, even if it is called `verifyToken` in a file named `security.go`. Powered by sentence-transformers (MiniLM-L6, 384-dimensional vectors) with LanceDB or Qdrant as the vector store.

### 2. Symbol Graph

Full structural navigation built from tree-sitter AST parsing across 25+ languages. Go-to-definition, find-all-references, caller/callee relationships — stored as SQLite adjacency lists. The agent can trace `UserService.Create` → `Repository.Insert` → `db.Exec` in one query.

### 3. Persistent Memory

Agents can `remember` decisions, discoveries, and conventions — and `recall` them in future sessions. Memories are deduplicated by content hash, support topic-key upserts for evolving knowledge, and persist in SQLite. No more re-explaining your architecture every session.

### 4. Context Building

Token-budget-aware assembly of search results, symbol definitions, memory entries, and dependency information into a single coherent context block. The agent asks for "everything about the payment flow" and gets a curated, right-sized response — not a raw dump.

### 5. MCP Integration

14 tools exposed via the Model Context Protocol over stdio. Any MCP-compatible agent can call `search`, `read_symbol`, `get_references`, `build_context`, `remember`, `recall`, and more. Zero configuration when auto-setup is used.

---

## Quick Start in 5 Minutes

### Install

```bash
curl -fsSL https://raw.githubusercontent.com/snaven10/devai-context-engine/main/scripts/install.sh | bash
```

Downloads the Go binary and a portable Python environment. No system Python required.

### Initialize a repository

```bash
cd your-repo
devai init
```

Creates a `.devai/` directory with configuration and state. Detects your project's languages automatically.

### Index the codebase

```bash
devai index
```

Parses all source files with tree-sitter, generates semantic chunks, computes embeddings, and builds the symbol graph. Incremental — subsequent runs only process `git diff` changes.

### Search from the CLI

```bash
devai search "authentication middleware"
```

Returns ranked results with file paths, symbol names, and relevance scores.

### Connect to Claude Code

```bash
devai server configure claude
```

Auto-writes the MCP server entry to Claude Code's configuration. The agent can now call all 14 DevAI tools directly. No manual JSON editing.

---

## How It Works (30-Second Version)

```
Your Code ──▶ Tree-sitter AST ──▶ Semantic Chunks ──▶ Embeddings ──▶ Vector DB
                    │                                                (LanceDB/Qdrant)
                    ▼
              Symbol Graph ──▶ SQLite (adjacency lists)

AI Agent ──MCP──▶ DevAI Go Server ──JSON-RPC──▶ Python ML Service
                                                    │
                                        ┌───────────┼───────────┐
                                        ▼           ▼           ▼
                                    Vector DB    Symbol DB    Memory DB
                                        │           │           │
                                        └───────────┼───────────┘
                                                    ▼
                                            Assembled Context ──▶ Agent
```

**Go** handles the CLI, TUI, MCP server, and process management. **Python** handles embeddings, AST parsing, chunking, vector storage, and memory. They communicate over JSON-RPC via stdio — no network dependency, no ports to configure.

Indexing is incremental. After the first full index, DevAI uses `git diff` to detect changed files and only reprocesses those. Branch overlays let you search across branch lineage without duplicating data.

---

## Documentation Map

| Document | What it covers |
|----------|---------------|
| [Introduction](01-introduction.md) | You are here — overview, quick start, mental model |
| [Setup](setup.md) | Installation options, configuration, environment variables |
| [Architecture](architecture.md) | Go + Python hybrid design, storage layers, data flow |
| [Agent Workflow](04-agent-workflow.md) | How AI agents interact with DevAI, tool selection patterns |
| [MCP Tools Reference](mcp-tools.md) | All 14 tools with parameters, examples, and response schemas |
| [Features](features.md) | Detailed capability breakdown — search, graph, memory, branches |
| [API](api.md) | JSON-RPC protocol between Go and Python |
| [Schemas](schemas.md) | Database schemas, config file formats, state structures |

---

> **DevAI is in alpha.** APIs, CLI flags, and storage formats may change between versions. See the [README](../README.md) for current status.
