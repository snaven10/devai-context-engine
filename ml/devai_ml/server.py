from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .chunking.semantic_chunker import SemanticChunker
from .embeddings.factory import create_provider
from .embeddings.base import EmbeddingProvider
from .parsers.registry import ParserRegistry
from .pipeline.orchestrator import IndexPipeline
from .stores.graph_store import SQLiteGraphStore
from .stores.index_state import IndexStateStore
from .stores.memory_store import Memory, MemoryStore
from .stores.vector_store import LanceDBVectorStore

logger = logging.getLogger(__name__)


class MLService:
    """DevAI ML Service — handles embedding, parsing, and indexing.

    Communicates via JSON-RPC over stdio. Will be upgraded to gRPC
    once proto stubs are generated.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self._config = config

        # Initialize components
        logger.info("Initializing ML service...")
        self._embedding = create_provider(config.get("embeddings", {}))
        self._parser_registry = ParserRegistry()
        self._chunker = SemanticChunker()

        # Storage path resolution (priority order):
        #   1. DEVAI_STATE_DIR env var
        #   2. config.state_dir (from CLI --state-dir or config file)
        #   3. ~/.local/share/devai/state/ (XDG default)
        import os
        xdg_default = str(Path.home() / ".local" / "share" / "devai" / "state")
        state_dir = Path(
            os.environ.get("DEVAI_STATE_DIR")
            or config.get("state_dir")
            or xdg_default
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        logger.info("State directory: %s", state_dir)

        self._vector_store = LanceDBVectorStore(
            db_path=str(state_dir / "vectors"),
            dimension=self._embedding.dimension(),
        )
        self._graph_store = SQLiteGraphStore(
            db_path=str(state_dir / "index.db"),
        )
        self._index_store = IndexStateStore(
            db_path=str(state_dir / "index.db"),
        )
        self._memory_store = MemoryStore(
            db_path=str(state_dir / "index.db"),
        )

        self._pipeline = IndexPipeline(
            parser_registry=self._parser_registry,
            chunker=self._chunker,
            embedding=self._embedding,
            vector_store=self._vector_store,
            graph_store=self._graph_store,
            index_store=self._index_store,
        )

        logger.info(
            "ML service ready (model=%s, dim=%d, languages=%d)",
            self._embedding.model_name(),
            self._embedding.dimension(),
            len(self._parser_registry.supported_languages()),
        )

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle a JSON-RPC request."""
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        try:
            result = self._dispatch(method, params)
            return {"jsonrpc": "2.0", "result": result, "id": req_id}
        except Exception as e:
            logger.error("Error handling %s: %s", method, e, exc_info=True)
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": str(e)},
                "id": req_id,
            }

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Route method to handler."""
        handlers = {
            "embed": self._handle_embed,
            "embed_batch": self._handle_embed_batch,
            "search": self._handle_search,
            "parse_file": self._handle_parse_file,
            "index_repo": self._handle_index_repo,
            "health": self._handle_health,
            "read_symbol": self._handle_read_symbol,
            "get_references": self._handle_get_references,
            "remember": self._handle_remember,
            "recall": self._handle_recall,
            "memory_context": self._handle_memory_context,
            "memory_update": self._handle_memory_update,
            "memory_stats": self._handle_memory_stats,
            "get_branch_context": self._handle_get_branch_context,
            "get_session_history": self._handle_get_session_history,
            "index_status": self._handle_index_status,
        }

        handler = handlers.get(method)
        if handler is None:
            raise ValueError(f"Unknown method: {method}")
        return handler(params)

    def _handle_embed(self, params: dict) -> dict:
        """Generate embedding for a single text."""
        text = params["text"]
        vector = self._embedding.embed_single(text)
        return {
            "vector": vector,
            "dimension": self._embedding.dimension(),
            "model": self._embedding.model_name(),
        }

    def _handle_embed_batch(self, params: dict) -> dict:
        """Generate embeddings for multiple texts."""
        texts = params["texts"]
        vectors = self._embedding.embed(texts)
        return {
            "vectors": vectors,
            "dimension": self._embedding.dimension(),
            "model": self._embedding.model_name(),
            "count": len(vectors),
        }

    def _handle_search(self, params: dict) -> dict:
        """Semantic search: embed query → search LanceDB → return results."""
        query = params["query"]
        limit = params.get("limit", 10)
        branch = params.get("branch")
        language = params.get("language")

        # Embed the query
        vector = self._embedding.embed_single(query)

        # Build filter
        filters = {}
        if branch:
            filters["branch"] = branch
        if language:
            filters["language"] = language

        # Search vector store
        results = self._vector_store.search(
            vector=vector,
            filter_conditions=filters if filters else None,
            limit=limit,
        )

        # Deduplicate by file + start_line (handles duplicate vectors from
        # re-indexing with inconsistent repo paths)
        seen: set[str] = set()
        deduped = []
        for r in results:
            key = f"{r.metadata.get('file', '')}:{r.metadata.get('start_line', 0)}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append({
                "score": round(r.score, 4),
                "file": r.metadata.get("file", ""),
                "symbol": r.metadata.get("symbol", ""),
                "symbol_type": r.metadata.get("symbol_type", ""),
                "language": r.metadata.get("language", ""),
                "start_line": r.metadata.get("start_line", 0),
                "end_line": r.metadata.get("end_line", 0),
                "chunk_level": r.metadata.get("chunk_level", ""),
                "branch": r.metadata.get("branch", ""),
                "text": r.text[:500] if r.text else "",
            })

        return {
            "query": query,
            "count": len(deduped),
            "results": deduped,
        }

    def _handle_parse_file(self, params: dict) -> dict:
        """Parse a file and return symbols, imports, edges."""
        file_path = params.get("file_path", "")
        content = params.get("content")
        language = params.get("language")

        if content:
            parser = self._parser_registry.get_parser(file_path)
            if parser is None:
                return {"error": f"No parser for {file_path}", "symbols": [], "imports": [], "edges": []}
            result = parser.parse_source(content, file_path)
        else:
            parser = self._parser_registry.get_parser(file_path)
            if parser is None:
                return {"error": f"No parser for {file_path}", "symbols": [], "imports": [], "edges": []}
            result = parser.parse_file(file_path)

        return {
            "file_path": result.file_path,
            "language": result.language,
            "symbols": [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                    "signature": s.signature,
                    "parent": s.parent,
                    "visibility": s.visibility,
                }
                for s in result.symbols
            ],
            "imports": [
                {"module": i.module, "alias": i.alias, "names": list(i.names), "line": i.line}
                for i in result.imports
            ],
            "edges": [
                {"source": e.source, "target": e.target, "kind": e.kind, "line": e.line}
                for e in result.edges
            ],
        }

    def _handle_index_repo(self, params: dict) -> dict:
        """Index a repository."""
        repo_path = params["repo_path"].rstrip("/")
        branch = params.get("branch")
        incremental = params.get("incremental", True)

        result = self._pipeline.index_repo(repo_path, branch, incremental)
        return {
            "repo_path": result.repo_path,
            "branch": result.branch,
            "commit": result.commit,
            "files_processed": result.files_processed,
            "files_skipped": result.files_skipped,
            "chunks_created": result.chunks_created,
            "symbols_found": result.symbols_found,
            "edges_found": result.edges_found,
            "duration_seconds": round(result.duration_seconds, 2),
            "errors": result.errors,
        }

    def _get_indexed_repos(self) -> list[str]:
        """Get all repo paths that have been indexed by reading index_state."""
        conn = self._index_store._conn
        rows = conn.execute("SELECT DISTINCT repo_path FROM index_state").fetchall()
        return [r[0] for r in rows]

    def _get_repo_branches(self, repo_path: str) -> list[str]:
        """Get all indexed branches for a repo."""
        conn = self._index_store._conn
        rows = conn.execute(
            "SELECT DISTINCT branch FROM index_state WHERE repo_path = ?",
            (repo_path,),
        ).fetchall()
        return [r[0] for r in rows]

    def _handle_read_symbol(self, params: dict) -> dict:
        """Find a symbol by name using vector search, then return its code."""
        name = params["name"]
        branch = params.get("branch")
        repo = params.get("repo")

        # Search for the symbol in vectors
        vector = self._embedding.embed_single(name)
        filters = {"symbol_type": ["function", "method", "class", "struct", "interface", "enum"]}
        if branch:
            filters["branch"] = branch

        results = self._vector_store.search(
            vector=vector,
            filter_conditions=filters,
            limit=5,
        )

        # Find best match by symbol name
        best = None
        for r in results:
            sym_name = r.metadata.get("symbol", "")
            if sym_name.lower() == name.lower():
                best = r
                break
            if name.lower() in sym_name.lower() and best is None:
                best = r

        if best is None and results:
            best = results[0]

        if best is None:
            return {"symbol": name, "found": False, "error": "Symbol not found"}

        # Try to read the actual source code from disk
        file_path = best.metadata.get("file", "")
        start_line = best.metadata.get("start_line", 0)
        end_line = best.metadata.get("end_line", 0)
        code = best.text

        if file_path and start_line > 0:
            # Try to read from actual repo files
            import os
            for candidate_repo in self._get_indexed_repos():
                full_path = os.path.join(candidate_repo, file_path)
                if os.path.exists(full_path):
                    try:
                        with open(full_path) as f:
                            lines = f.readlines()
                            if start_line <= len(lines):
                                end = min(end_line, len(lines))
                                code = "".join(lines[start_line - 1:end])
                    except Exception:
                        pass
                    break

        return {
            "symbol": best.metadata.get("symbol", name),
            "found": True,
            "file": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "language": best.metadata.get("language", ""),
            "symbol_type": best.metadata.get("symbol_type", ""),
            "branch": best.metadata.get("branch", ""),
            "code": code,
            "score": round(best.score, 4),
        }

    def _handle_get_references(self, params: dict) -> dict:
        """Find all references to a symbol using the graph store.

        Strategy:
        1. Search edges by symbol name (LIKE match on source/target)
        2. Find the file where the symbol is defined (via vector store)
        3. Search edges whose target contains that file path (catches imports)
        4. Deduplicate and return
        """
        symbol = params["symbol"]
        repo_filter = params.get("repo", "")
        branch_filter = params.get("branch", "")

        # Step 1: Find the file where this symbol is defined
        symbol_file = None
        vector = self._embedding.embed_single(symbol)
        results = self._vector_store.search(
            vector=vector,
            filter_conditions={"symbol": symbol} if symbol else None,
            limit=1,
        )
        if results:
            symbol_file = results[0].metadata.get("file", "")

        all_refs = []
        seen = set()  # dedup key: (file, line, kind)

        for repo_path in self._get_indexed_repos():
            repo_path = repo_path.rstrip("/")
            if repo_filter and repo_filter.rstrip("/") not in repo_path:
                continue
            branches = [branch_filter] if branch_filter else self._get_repo_branches(repo_path)
            for br in branches:
                # Search by symbol name
                edges = self._graph_store.find_references(repo_path, br, symbol)
                for e in edges:
                    key = (e.source_file, e.line, e.kind)
                    if key not in seen:
                        seen.add(key)
                        all_refs.append({
                            "source": e.source,
                            "target": e.target,
                            "kind": e.kind,
                            "file": e.source_file,
                            "line": e.line,
                        })

                # Search by file path of symbol definition (catches import edges)
                # Use basename without extension because imports use relative paths
                # e.g. symbol is in "apps/shell/.../admin-nui-local.component.ts"
                #      but import target is "./admin-nui-local/admin-nui-local.component"
                if symbol_file:
                    import os
                    file_basename = os.path.splitext(os.path.basename(symbol_file))[0]
                    file_edges = self._graph_store.find_references(repo_path, br, file_basename)
                    for e in file_edges:
                        key = (e.source_file, e.line, e.kind)
                        if key not in seen:
                            seen.add(key)
                            all_refs.append({
                                "source": e.source,
                                "target": e.target,
                                "kind": e.kind,
                                "file": e.source_file,
                                "line": e.line,
                            })

        return {"symbol": symbol, "count": len(all_refs), "references": all_refs[:200]}

    def _handle_remember(self, params: dict) -> dict:
        """Save a structured memory entry — Engram-quality storage.

        Supports:
        - title: searchable short title
        - content or text: the memory content (structured with What/Why/Where/Learned)
        - type: insight, decision, note, bug, architecture, pattern, discovery
        - scope: shared (team) or local (personal)
        - project: project context
        - topic_key: stable key for upserts (e.g. "architecture/auth-model")
        - tags: comma-separated
        - files: comma-separated file paths
        - repo, branch: git context
        """
        content = params.get("content") or params.get("text", "")
        if not content:
            return {"saved": False, "error": "content or text is required"}

        title = params.get("title", "")
        if not title:
            # Auto-generate title from first line or first 80 chars
            first_line = content.split("\n")[0].strip()
            if first_line.startswith("**What**:"):
                title = first_line.replace("**What**:", "").strip()[:80]
            else:
                title = first_line[:80]

        memory = Memory(
            title=title,
            content=content,
            memory_type=params.get("type", "note"),
            scope=params.get("scope", "shared"),
            project=params.get("project", ""),
            topic_key=params.get("topic_key"),
            tags=params.get("tags", ""),
            author=params.get("author", ""),
            repo=params.get("repo", ""),
            branch=params.get("branch", ""),
            files=params.get("files", ""),
        )

        # Embed content for semantic search
        vector = self._embedding.embed_single(f"{title} {content}")
        from .stores.vector_store import VectorPoint
        vector_id = f"mem_{memory.normalized_hash[:24]}"

        point = VectorPoint(
            id=vector_id,
            vector=vector,
            metadata={
                "repo": memory.repo,
                "branch": memory.branch,
                "commit": "",
                "file": "",
                "symbol": memory.title,
                "symbol_type": memory.memory_type,
                "language": "",
                "start_line": 0,
                "end_line": 0,
                "chunk_level": "memory",
                "content_hash": memory.normalized_hash[:16],
                "is_deletion": False,
                "memory_type": memory.memory_type,
                "memory_scope": memory.scope,
                "memory_tags": memory.tags,
            },
            text=f"{title}\n{content}" if title else content,
        )
        self._vector_store.upsert([point])

        # Save to SQLite with rich metadata
        memory.vector_id = vector_id
        saved = self._memory_store.save(memory)

        return {
            "saved": True,
            "id": saved.id,
            "title": saved.title,
            "type": saved.memory_type,
            "scope": saved.scope,
            "topic_key": saved.topic_key,
            "revision_count": saved.revision_count,
            "duplicate_count": saved.duplicate_count,
            "is_update": saved.revision_count > 1 or saved.duplicate_count > 1,
        }

    def _handle_recall(self, params: dict) -> dict:
        """Search memories using hybrid: semantic vector search + SQLite metadata.

        Combines vector similarity with rich metadata from SQLite.
        """
        query = params["query"]
        scope = params.get("scope")
        mem_type = params.get("type")
        project = params.get("project", "")
        limit = int(params.get("limit", 10))

        # Semantic search via vector store
        vector = self._embedding.embed_single(query)
        filters = {"chunk_level": "memory"}
        if scope:
            filters["memory_scope"] = scope
        if mem_type:
            filters["memory_type"] = mem_type

        vector_results = self._vector_store.search(
            vector=vector, filter_conditions=filters, limit=limit,
        )

        # Enrich with SQLite metadata
        memories = []
        for vr in vector_results:
            # Try to find the rich metadata in SQLite
            vid = vr.id
            row = self._memory_store._conn.execute(
                "SELECT * FROM memories WHERE vector_id = ? AND deleted_at IS NULL",
                (vid,),
            ).fetchone()

            if row:
                mem = self._memory_store._row_to_memory(row)
                memories.append({
                    "id": mem.id,
                    "title": mem.title,
                    "content": mem.content,
                    "type": mem.memory_type,
                    "scope": mem.scope,
                    "project": mem.project,
                    "topic_key": mem.topic_key,
                    "tags": mem.tags,
                    "files": mem.files,
                    "revision_count": mem.revision_count,
                    "duplicate_count": mem.duplicate_count,
                    "created_at": mem.created_at,
                    "updated_at": mem.updated_at,
                    "score": round(vr.score, 4),
                })
            else:
                # Fallback: vector-only memory (no SQLite entry)
                memories.append({
                    "id": None,
                    "title": "",
                    "content": vr.text,
                    "type": vr.metadata.get("memory_type", ""),
                    "scope": vr.metadata.get("memory_scope", ""),
                    "project": "",
                    "topic_key": None,
                    "tags": vr.metadata.get("memory_tags", ""),
                    "files": "",
                    "revision_count": 0,
                    "duplicate_count": 0,
                    "created_at": "",
                    "updated_at": "",
                    "score": round(vr.score, 4),
                })

        return {"query": query, "count": len(memories), "memories": memories}

    def _handle_memory_context(self, params: dict) -> dict:
        """Get recent memories without search — like Engram's mem_context."""
        project = params.get("project", "")
        scope = params.get("scope", "")
        limit = int(params.get("limit", 20))

        memories = self._memory_store.get_recent(project=project, scope=scope, limit=limit)
        return {
            "count": len(memories),
            "memories": [
                {
                    "id": m.id,
                    "title": m.title,
                    "content": m.content[:200] + "..." if len(m.content) > 200 else m.content,
                    "type": m.memory_type,
                    "scope": m.scope,
                    "project": m.project,
                    "topic_key": m.topic_key,
                    "tags": m.tags,
                    "revision_count": m.revision_count,
                    "created_at": m.created_at,
                    "updated_at": m.updated_at,
                }
                for m in memories
            ],
        }

    def _handle_memory_update(self, params: dict) -> dict:
        """Update an existing memory by ID."""
        memory_id = int(params["id"])
        fields = {k: v for k, v in params.items() if k != "id"}
        updated = self._memory_store.update(memory_id, **fields)
        if updated:
            return {"updated": True, "id": updated.id, "revision_count": updated.revision_count}
        return {"updated": False, "error": "Memory not found"}

    def _handle_memory_stats(self, params: dict) -> dict:
        """Memory statistics."""
        return self._memory_store.stats()

    def _handle_get_branch_context(self, params: dict) -> dict:
        """Get current branch info + index stats from SQLite."""
        branch = params.get("branch")
        repo = params.get("repo")

        repos_info = []
        for repo_path in self._get_indexed_repos():
            if repo and repo != repo_path:
                continue
            branches = self._get_repo_branches(repo_path)
            for br in branches:
                record = self._index_store.get_last_indexed(repo_path, br)
                if record:
                    repos_info.append({
                        "repo": repo_path,
                        "branch": br,
                        "last_commit": record.last_commit,
                        "model": record.model_name,
                        "files": record.file_count,
                        "symbols": record.symbol_count,
                        "chunks": record.chunk_count,
                        "indexed_at": record.indexed_at,
                    })

        # Filter by branch if specified
        if branch:
            repos_info = [r for r in repos_info if r["branch"] == branch]

        return {
            "count": len(repos_info),
            "repos": repos_info,
        }

    def _handle_get_session_history(self, params: dict) -> dict:
        """Get session history — for now return index operations from index_state."""
        limit = params.get("limit", 20)

        # Since we don't have a dedicated session table yet,
        # return indexing events from index_state as history
        events = []
        for repo_path in self._get_indexed_repos():
            branches = self._get_repo_branches(repo_path)
            for br in branches:
                record = self._index_store.get_last_indexed(repo_path, br)
                if record:
                    events.append({
                        "timestamp": record.indexed_at,
                        "event_type": "index",
                        "tool": "index_repo",
                        "summary": f"Indexed {repo_path} ({br}): {record.file_count} files, {record.symbol_count} symbols",
                        "repo": repo_path,
                        "branch": br,
                    })

        # Sort by timestamp descending
        events.sort(key=lambda e: e["timestamp"], reverse=True)
        return {
            "count": len(events[:limit]),
            "events": events[:limit],
        }

    def _handle_index_status(self, params: dict) -> dict:
        """Get index status for all repos or a specific one."""
        repo_filter = params.get("repo")

        repos = []
        for repo_path in self._get_indexed_repos():
            if repo_filter and repo_filter != repo_path:
                continue
            branches = self._get_repo_branches(repo_path)
            for br in branches:
                record = self._index_store.get_last_indexed(repo_path, br)
                if record:
                    repos.append({
                        "repo": repo_path,
                        "name": repo_path.rstrip("/").split("/")[-1],
                        "branch": br,
                        "last_commit": record.last_commit,
                        "model": record.model_name,
                        "dimension": record.model_dimension,
                        "files": record.file_count,
                        "symbols": record.symbol_count,
                        "chunks": record.chunk_count,
                        "indexed_at": record.indexed_at,
                        "status": "indexed",
                    })

        return {
            "count": len(repos),
            "repos": repos,
        }

    def _handle_health(self, params: dict) -> dict:
        """Health check."""
        return {
            "status": "serving",
            "model_loaded": self._embedding.model_name(),
            "model_dimension": self._embedding.dimension(),
            "languages_supported": self._parser_registry.supported_languages(),
        }


def serve_stdio(config: dict[str, Any] | None = None) -> None:
    """Run the ML service over stdin/stdout JSON-RPC."""
    # Silence HuggingFace warnings and progress bars before any imports trigger them
    import os
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,  # logs go to stderr, JSON-RPC goes to stdout
    )
    # Silence noisy libraries — nobody wants 30 lines of HTTP HEAD requests
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)
    logging.getLogger("torch").setLevel(logging.WARNING)

    service = MLService(config)

    # Signal ready
    sys.stderr.write("DEVAI_ML_READY\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = service.handle_request(request)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError as e:
            error_response = {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {e}"},
                "id": None,
            }
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()


def main() -> None:
    """Entry point for the devai-ml command."""
    import argparse

    parser = argparse.ArgumentParser(description="DevAI ML Service")
    parser.add_argument("--config", type=str, help="Path to config YAML file")
    parser.add_argument("--state-dir", type=str, default=None, help="State directory (default: ~/.local/share/devai/state/)")
    parser.add_argument("--model", type=str, default="minilm-l6", help="Embedding model key")
    parser.add_argument("--device", type=str, default="cpu", help="Device (cpu/cuda)")
    args = parser.parse_args()

    config = {
        "embeddings": {
            "provider": "local",
            "model": args.model,
            "device": args.device,
        },
    }

    # --state-dir CLI flag takes priority over config file
    if args.state_dir:
        config["state_dir"] = args.state_dir

    if args.config:
        import yaml
        with open(args.config) as f:
            file_config = yaml.safe_load(f)
            if file_config:
                config.update(file_config)

    serve_stdio(config)


if __name__ == "__main__":
    main()
