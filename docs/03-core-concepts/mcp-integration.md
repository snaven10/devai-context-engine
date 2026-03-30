# MCP Integration

> 🇪🇸 [Leer en español](../es/03-conceptos-fundamentales/integracion-mcp.md)

## What It Is

DevAI exposes its capabilities — search, memory, symbol graph, context building — as tools via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). MCP is an open standard that lets AI applications (Claude Code, Cursor, Windsurf, etc.) discover and call external tools through a unified interface. DevAI implements an MCP server that turns your codebase intelligence into tools any MCP-compatible client can use.

## Why It Exists

Without MCP, every AI tool integration is bespoke. You'd need a VS Code extension, a JetBrains plugin, a CLI wrapper, and a custom API — each with its own protocol, authentication, and maintenance burden.

MCP standardizes this. DevAI implements one server. Any MCP client can use it. Today that's Claude Code and Cursor. Tomorrow it's whatever ships next. Zero integration work per client.

## How It Works

### Architecture

```
  ┌──────────────────┐     stdio      ┌──────────────────┐
  │   MCP Client     │◄──(stdin/──────►│   DevAI MCP      │
  │  (Claude Code,   │   stdout)      │   Server (Go)    │
  │   Cursor, etc.)  │               │                  │
  └──────────────────┘               └────────┬─────────┘
                                              │
                                     JSON-RPC │
                                              │
                                     ┌────────▼─────────┐
                                     │   DevAI ML       │
                                     │   Server (Python) │
                                     │                  │
                                     │  - Embeddings    │
                                     │  - LanceDB       │
                                     │  - SQLite        │
                                     │  - Tree-sitter   │
                                     └──────────────────┘
```

- **Transport**: stdio (stdin/stdout). The MCP client spawns the DevAI server as a child process and communicates via standard I/O.
- **Go MCP server**: Built with the [mark3labs/mcp-go](https://github.com/mark3labs/mcp-go) library. Handles MCP protocol negotiation, tool discovery, and request routing.
- **Python ML server**: Performs the actual work — embedding, searching, indexing, memory operations. The Go server communicates with it via JSON-RPC.

### Handler Pattern

Every MCP tool follows the same internal flow:

```
  MCP request (from client)
       │
       ▼
  Parse arguments (validate types, apply defaults)
       │
       ▼
  Call ML server via JSON-RPC
       │
       ▼
  Format response (structured text for LLM consumption)
       │
       ▼
  Return MCP result (to client)
```

## Tool Reference

DevAI exposes 14 tools via MCP. Three additional operations (`push_index`, `pull_index`, `sync_index`) are CLI-only.

### Code Intelligence

| Tool | Description | Key Parameters |
|---|---|---|
| `search` | Semantic code search across indexed repositories | `query` (string), `repo` (string, optional), `branch` (string, optional), `language` (string, optional), `symbol_type` (string, optional), `limit` (int, default 10) |
| `read_file` | Read file contents with optional line range | `path` (string), `start_line` (int, optional), `end_line` (int, optional) |
| `read_symbol` | Get the full definition of a function, class, or type | `name` (string), `repo` (string, optional) |
| `get_references` | Find all call sites and usages of a symbol | `symbol` (string), `repo` (string, optional) |
| `build_context` | Assemble token-budget-aware context from code + memories | `query` (string), `max_tokens` (int, default 8000), `repo` (string, optional) |

### Indexing

| Tool | Description | Key Parameters |
|---|---|---|
| `index_repo` | Index or re-index a repository | `path` (string), `branch` (string, optional), `full` (bool, default false) |
| `index_status` | Check indexing status for a repository | `path` (string) |

### Branch Context

| Tool | Description | Key Parameters |
|---|---|---|
| `get_branch_context` | Get context about the current branch and its changes | `path` (string), `branch` (string, optional) |
| `switch_context` | Switch the active branch context for search | `path` (string), `branch` (string) |

### Memory

| Tool | Description | Key Parameters |
|---|---|---|
| `remember` | Save a structured memory | `title` (string), `content` (string), `type` (string), `project` (string), `scope` (string, default "shared"), `topic_key` (string, optional), `tags` (list, optional), `files` (list, optional) |
| `recall` | Search memories by natural language query | `query` (string), `project` (string), `type` (string, optional), `scope` (string, optional), `limit` (int, default 10) |
| `memory_context` | Get memory-enriched context for a topic | `query` (string), `project` (string) |
| `memory_stats` | Get statistics about the memory store | `project` (string, optional) |

### Session

| Tool | Description | Key Parameters |
|---|---|---|
| `get_session_history` | Retrieve session interaction history | `session_id` (string, optional), `limit` (int, default 20) |

### CLI-Only (Not Exposed via MCP)

| Command | Description |
|---|---|
| `devai push-index` | Push local index to remote storage |
| `devai pull-index` | Pull index from remote storage |
| `devai sync-index` | Bidirectional index sync |

These are CLI-only because they involve potentially destructive operations (overwriting indexes) and long-running transfers that don't fit the request-response MCP model.

## Configuration

### Auto-Configure for Claude Code

```bash
devai server configure claude
```

This writes the MCP server configuration to Claude Code's config file (`~/.claude.json` or project-level `.mcp.json`), registering DevAI as an available MCP server with the correct binary path and arguments.

### Auto-Configure for Cursor

```bash
devai server configure cursor
```

Same as above, but writes to Cursor's MCP configuration location.

### Manual Configuration

For other MCP clients, add this to your MCP config:

```json
{
  "mcpServers": {
    "devai": {
      "command": "devai",
      "args": ["server", "start"],
      "transport": "stdio"
    }
  }
}
```

The server binary must be in your `PATH`. It will automatically locate and start the Python ML server.

## When It Is Used

MCP integration is the **primary interface** for AI-assisted development workflows. When you use DevAI with Claude Code or Cursor:

1. The editor/CLI spawns the DevAI MCP server
2. The AI agent discovers available tools via MCP protocol
3. During conversation, the agent calls tools as needed:
   - `search` to find relevant code
   - `build_context` to assemble comprehensive context
   - `remember` / `recall` to persist and retrieve knowledge
   - `read_symbol` / `get_references` for precise code navigation
4. Results are returned as structured text that the agent incorporates into its reasoning

## Example: What Happens When Claude Code Calls `search`

```
User (in Claude Code): "Find the retry logic for API calls"

1. TOOL DISCOVERY (already done at session start)
   Claude Code knows DevAI exposes a `search` tool

2. TOOL CALL
   Claude Code sends MCP request:
   {
     "method": "tools/call",
     "params": {
       "name": "search",
       "arguments": {
         "query": "retry logic for API calls",
         "limit": 10
       }
     }
   }

3. GO MCP SERVER
   Receives request via stdin
   Parses arguments: query="retry logic for API calls", limit=10
   Sends JSON-RPC call to Python ML server

4. PYTHON ML SERVER
   Embeds query → 384-dim vector
   Searches LanceDB for nearest chunks
   Returns ranked results with metadata

5. GO MCP SERVER
   Formats results as structured text:

   "Found 7 results:

   [1] services/http/retry.py:12-45 (score: 0.93)
   class RetryPolicy:
       def __init__(self, max_retries=3, backoff_factor=2.0):
           ...

   [2] services/http/client.py:67-89 (score: 0.87)
   async def fetch_with_retry(url, policy=None):
       ..."

   Returns MCP response via stdout

6. CLAUDE CODE
   Receives results
   Incorporates into response to user
   "I found the retry logic in services/http/retry.py..."
```

Total latency: 50-200ms depending on index size. The user sees results inline in the conversation, as if the AI agent just "knew" where the code was.

## Adding New Tools

To add a new MCP tool to DevAI:

1. **Go side** (`internal/mcp/server.go`): Register the tool with its schema and handler function
2. **Python side** (`ml/devai_ml/server.py`): Implement the JSON-RPC method that does the actual work
3. **Test both sides**: The handler parses args and formats output; the ML method implements logic

For detailed instructions, see the [Extending DevAI](../04-extending/adding-tools.md) guide.

## Mental Model

MCP is a **USB port for AI tools**. USB standardized how peripherals connect to computers — before USB, every device needed its own proprietary connector. MCP standardizes how AI agents connect to external capabilities.

DevAI is a device you plug in via MCP. The AI agent (Claude Code, Cursor) is the computer. Once plugged in, the agent can use DevAI's capabilities — search, memory, symbol graph — without knowing anything about embeddings, LanceDB, or tree-sitter. It just calls tools and gets results. Swap in a different MCP client, and everything still works. That's the point.
