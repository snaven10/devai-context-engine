from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .chunking.memory_chunker import body_chunks, blend_hits
from .chunking.semantic_chunker import SemanticChunker
from .config import DevAIConfig
from .embeddings.factory import create_provider
from .parsers.registry import ParserRegistry
from .pipeline.orchestrator import IndexPipeline
from .retrieval import create_reranker
from .stores.factory import create_storage_config_from_env, create_vector_store
from .stores.graph_store import SQLiteGraphStore
from .stores.index_state import IndexStateStore
from .stores.memory_store import Memory, MemoryStore
from .summarization import create_summarizer
from .util import TokenBudget

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _mem_to_dict(m: Memory) -> dict:
    """Serialize a Memory to the JSON shape returned by recall/memory_context."""
    return {
        "id": m.id,
        "title": m.title,
        "content": m.content,
        "type": m.memory_type,
        "scope": m.scope,
        "project": m.project,
        "topic_key": m.topic_key,
        "tags": m.tags,
        "files": m.files,
        "revision_count": m.revision_count,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }


class MLService:
    """DevAI ML Service — handles embedding, parsing, and indexing.

    Communicates via JSON-RPC over stdio. Will be upgraded to gRPC
    once proto stubs are generated.
    """

    def __init__(self, config: DevAIConfig | dict[str, Any] | None = None) -> None:
        # Accept DevAIConfig directly (v0.8+) OR the legacy dict shape (pre-v0.8).
        if isinstance(config, DevAIConfig):
            cfg = config
        else:
            cfg = DevAIConfig.from_legacy_dict(config)
        self._config = cfg

        # Initialize components from the typed sub-configs.
        self._embedding = create_provider(cfg.embedding.to_legacy_dict())
        self._parser_registry = ParserRegistry()
        self._chunker = SemanticChunker(config=cfg.chunking)
        self._summarizer = create_summarizer(
            cfg.summarizer, embedding_provider=self._embedding,
        )
        self._token_budget = TokenBudget(
            cfg.token_budget, summarizer=self._summarizer,
        )
        self._reranker = create_reranker(cfg.rerank)
        self._rerank_top_k_fetch = cfg.rerank.top_k_fetch

        # Memory chunking — works around the embedding context-window limit for
        # long memories (see chunking/memory_chunker.py). All env-tunable; the
        # defaults (on, alpha=0.5) are the A/B-validated configuration.
        self._mem_chunking = os.environ.get(
            "DEVAI_MEMORY_CHUNKING", "1"
        ).strip().lower() not in ("0", "false", "no", "off", "")
        self._mem_chunk_max = _env_int("DEVAI_MEMORY_CHUNK_MAX_CHUNKS", 40)
        self._mem_chunk_overlap = _env_int("DEVAI_MEMORY_CHUNK_OVERLAP", 30)
        self._mem_blend_alpha = _env_float("DEVAI_MEMORY_BLEND_ALPHA", 0.5)

        # Storage path resolution (priority order):
        #   1. cfg.state_dir (env var DEVAI_STATE_DIR or CLI --state-dir)
        #   2. ~/.local/share/devai/state/ (XDG default)
        xdg_default = Path.home() / ".local" / "share" / "devai" / "state"
        state_dir = cfg.state_dir if cfg.state_dir is not None else xdg_default
        state_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("State directory: %s", state_dir)

        # Vector store: use factory for backend selection (local/shared/hybrid).
        # The factory reads DEVAI_STORAGE_MODE from env. Default is "local"
        # which preserves backward compatibility — existing users see no change.
        storage_config = create_storage_config_from_env()
        # Override local_db_path with resolved state_dir (preserves priority logic above)
        if not storage_config.local_db_path:
            storage_config.local_db_path = str(state_dir / "vectors")
        storage_config.dimension = self._embedding.dimension()
        logger.debug(
            "Storage mode: %s (local_db=%s, qdrant=%s)",
            storage_config.mode,
            storage_config.local_db_path,
            storage_config.qdrant_url if storage_config.mode != "local" else "n/a",
        )
        self._vector_store = create_vector_store(storage_config)
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

        # Ready message is emitted by serve_stdio() after construction

    @staticmethod
    def _repo_name(repo: str) -> str:
        """Normalize repo identifier to short name (basename of path)."""
        if not repo:
            return repo
        return Path(repo.rstrip("/")).name

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
            "reindex_memories": self._handle_reindex_memories,
            "memory_context": self._handle_memory_context,
            "memory_update": self._handle_memory_update,
            "memory_stats": self._handle_memory_stats,
            "memories_by_symbol": self._handle_memories_by_symbol,
            "memories_by_file": self._handle_memories_by_file,
            "symbol_memory_counts": self._handle_symbol_memory_counts,
            "memory_refs": self._handle_memory_refs,
            "impact_analysis": self._handle_impact_analysis,
            "fts_rebuild": self._handle_fts_rebuild,
            "extract_quarkus_routes": self._handle_extract_quarkus_routes,
            "extract_routes": self._handle_extract_routes,
            "search_routes": self._handle_search_routes,
            "routes_for_handler": self._handle_routes_for_handler,
            "backfill_symbol_refs": self._handle_backfill_symbol_refs,
            "backfill_vector_links": self._handle_backfill_vector_links,
            "get_branch_context": self._handle_get_branch_context,
            "get_session_history": self._handle_get_session_history,
            "index_status": self._handle_index_status,
            "push_index": self._handle_push_index,
            "pull_index": self._handle_pull_index,
            "sync_index": self._handle_sync_index,
            "model_list": self._handle_model_list,
            "model_download": self._handle_model_download,
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
        repo = params.get("repo", "")

        # Embed the query
        vector = self._embedding.embed_single(query)

        # Build filter
        filters = {}
        if repo:
            filters["repo"] = self._repo_name(repo)
        if branch:
            filters["branch"] = branch
        if language:
            filters["language"] = language

        # Search vector store. When the reranker is active we fetch wider
        # (top_k_fetch) so the reranker has more material to reorder; the
        # final response is still narrowed to `limit` by the reranker.
        fetch_limit = (
            max(self._rerank_top_k_fetch, limit)
            if self._reranker.is_active() else limit
        )
        results = self._vector_store.search(
            vector=vector,
            filter_conditions=filters if filters else None,
            limit=fetch_limit,
        )
        # Rerank with the cross-encoder before truncating to `limit`.
        results = self._reranker.rerank(query, results, top_k=limit)

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

        # Apply token budget. is_code=True forces DROP strategy — truncating
        # or summarizing code chunks would corrupt identifiers.
        deduped, budget = self._token_budget.fit(
            deduped, content_key="text", is_code=True, query=query,
        )

        return {
            "query": query,
            "count": len(deduped),
            "results": deduped,
            "budget": budget.to_dict(),
            "reranker": self._reranker.model_name(),
        }

    def _handle_parse_file(self, params: dict) -> dict:
        """Parse a file and return symbols, imports, edges."""
        file_path = params.get("file_path", "")
        content = params.get("content")
        _language = params.get("language")  # reserved for future use

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
        _repo = params.get("repo")  # reserved for multi-repo filtering

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

    # --- Memory vector indexing ------------------------------------------------

    def _mem_tokenizer(self) -> tuple[Any, int]:
        """Tokenizer + max_seq_length of the embedding model, when it exposes
        one (local sentence-transformers). Returns (None, 0) for API providers
        — those have large context windows and don't need body chunking."""
        model = getattr(self._embedding, "_model", None)
        if model is None:
            return None, 0
        tok = getattr(model, "tokenizer", None)
        max_seq = int(getattr(model, "max_seq_length", 0) or 0)
        return tok, max_seq

    def _store_memory_vectors(self, memory: Memory) -> tuple[str, int]:
        """Embed a memory and upsert its vector(s): the intro/whole vector
        (chunk_level='memory', unchanged from the original single-vector path)
        plus, for long memories when chunking is enabled, extra body-window
        vectors (chunk_level='memory_chunk'). Returns (vector_id, n_extra_chunks).

        Idempotent: clears any prior vectors for this memory id first, so a
        re-index that produces fewer chunks does not leave orphans.
        """
        from .stores.vector_store import VectorPoint

        title, content = memory.title, memory.content
        vector_id = f"mem_{memory.normalized_hash[:24]}"
        base_meta = {
            "repo": memory.repo, "branch": memory.branch, "commit": "",
            "file": "", "symbol": memory.title, "symbol_type": memory.memory_type,
            "language": "", "start_line": 0, "end_line": 0,
            "content_hash": memory.normalized_hash[:16], "is_deletion": False,
            "memory_type": memory.memory_type, "memory_scope": memory.scope,
            "memory_tags": memory.tags,
        }

        # Intro/whole vector — identical to the original single-vector behaviour.
        points = [VectorPoint(
            id=vector_id,
            vector=self._embedding.embed_single(f"{title} {content}"),
            metadata={**base_meta, "chunk_level": "memory"},
            text=f"{title}\n{content}" if title else content,
        )]

        # Extra body chunks for long memories (token-based, title prepended).
        n_extra = 0
        if self._mem_chunking:
            tok, max_seq = self._mem_tokenizer()
            extra = body_chunks(
                title, content, tok, max_seq,
                overlap=self._mem_chunk_overlap, max_chunks=self._mem_chunk_max,
            )
            if extra:
                vecs = self._embedding.embed(extra)
                for i, (ctext, cvec) in enumerate(zip(extra, vecs), start=1):
                    points.append(VectorPoint(
                        id=f"{vector_id}_c{i}",
                        vector=cvec,
                        metadata={**base_meta, "chunk_level": "memory_chunk"},
                        text=ctext,
                    ))
                n_extra = len(extra)

        # Clear stale vectors for this memory (handles shrinking chunk counts),
        # then upsert the fresh set. Guarded: not every backend implements it —
        # upsert's own delete-by-id still refreshes the points being re-added.
        deleter = getattr(self._vector_store, "delete_memory_vectors", None)
        if callable(deleter):
            deleter(vector_id)
        self._vector_store.upsert(points)
        return vector_id, n_extra

    def _blend_memory_results(self, raw: list, pool: int) -> list:
        """Collapse mixed intro/chunk hits to one SearchResult per memory using
        the intro/chunk blend, then take the top `pool` for the reranker.

        Each returned SearchResult carries the memory's FULL text so the
        cross-encoder reranker scores against complete content (not a chunk).
        When the intro vector missed the search pool (a buried-only match) the
        full text is pulled from SQLite by vector_id.
        """
        from .stores.vector_store import SearchResult

        out: list[SearchResult] = []
        for pid, score, intro in blend_hits(raw, alpha=self._mem_blend_alpha)[:pool]:
            if intro is not None:
                text, meta = intro.text, intro.metadata
            else:
                # NOTE: SQLite columns are scope/tags; the memory_scope /
                # memory_tags names are LanceDB metadata keys, not table columns.
                row = self._memory_store._conn.execute(
                    "SELECT title, content, memory_type, scope, tags "
                    "FROM memories WHERE vector_id = ? AND deleted_at IS NULL",
                    (pid,),
                ).fetchone()
                if row is None:
                    continue
                title = row["title"] or ""
                text = f"{title}\n{row['content']}" if title else (row["content"] or "")
                meta = {
                    "chunk_level": "memory",
                    "memory_type": row["memory_type"],
                    "memory_scope": row["scope"],
                    "memory_tags": row["tags"],
                }
            out.append(SearchResult(id=pid, score=score, metadata=meta, text=text))
        return out

    def _handle_remember(self, params: dict) -> dict:
        """Save a structured memory entry.

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

        # Embed content for semantic search (intro vector + body chunks for
        # long memories — see _store_memory_vectors / chunking/memory_chunker.py).
        vector_id, _ = self._store_memory_vectors(memory)

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

    def _handle_reindex_memories(self, params: dict) -> dict:
        """Re-embed every non-deleted memory, (re)generating intro + chunk
        vectors. Run once after enabling memory chunking (or changing the model).
        Idempotent — safe to re-run. Returns counts."""
        rows = self._memory_store._conn.execute(
            "SELECT * FROM memories WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
        reindexed = 0
        with_chunks = 0
        total_chunks = 0
        for row in rows:
            mem = self._memory_store._row_to_memory(row)
            if not mem.content:
                continue
            try:
                vid, n_extra = self._store_memory_vectors(mem)
            except Exception as e:
                logger.warning("reindex failed for memory #%s: %s", mem.id, e)
                continue
            if vid != mem.vector_id:
                self._memory_store._conn.execute(
                    "UPDATE memories SET vector_id = ? WHERE id = ?", (vid, mem.id),
                )
            reindexed += 1
            if n_extra:
                with_chunks += 1
                total_chunks += n_extra
        self._memory_store._conn.commit()
        logger.info(
            "reindex_memories: %d memories, %d chunked (+%d chunk vectors)",
            reindexed, with_chunks, total_chunks,
        )
        return {
            "reindexed": reindexed,
            "memories_with_chunks": with_chunks,
            "extra_chunk_vectors": total_chunks,
            "chunking_enabled": self._mem_chunking,
            "blend_alpha": self._mem_blend_alpha,
        }

    def _handle_recall(self, params: dict) -> dict:
        """Search memories using hybrid: semantic vector search + SQLite metadata.

        Combines vector similarity with rich metadata from SQLite.
        """
        query = params["query"]
        scope = params.get("scope")
        mem_type = params.get("type")
        _project = params.get("project", "")  # reserved for multi-project filtering
        limit = int(params.get("limit", 10))

        # Semantic search via vector store
        vector = self._embedding.embed_single(query)
        filters: dict[str, Any] = {}
        if scope:
            filters["memory_scope"] = scope
        if mem_type:
            filters["memory_type"] = mem_type

        # How many memories to hand the reranker (fetch-wider-then-rerank).
        rerank_pool = (
            max(self._rerank_top_k_fetch, limit)
            if self._reranker.is_active() else limit
        )

        if self._mem_chunking:
            # Search BOTH the intro vectors and the body-chunk vectors, then
            # collapse to one score per memory via the intro/chunk blend (see
            # chunking/memory_chunker.py). Over-fetch: chunks consume pool slots,
            # so widen to keep enough *distinct* memories in play.
            filters["chunk_level"] = ["memory", "memory_chunk"]
            raw = self._vector_store.search(
                vector=vector, filter_conditions=filters,
                limit=max(rerank_pool * 3, 90),
            )
            vector_results = self._blend_memory_results(raw, rerank_pool)
        else:
            filters["chunk_level"] = "memory"
            vector_results = self._vector_store.search(
                vector=vector, filter_conditions=filters, limit=rerank_pool,
            )
        vector_results = self._reranker.rerank(query, vector_results, top_k=limit)

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

        # Apply token budget. is_code=False — strategy from config (drop /
        # soft_truncate / hard_truncate / summarize) applies.
        memories, budget = self._token_budget.fit(
            memories, content_key="content", is_code=False, query=query,
        )

        return {
            "query": query,
            "count": len(memories),
            "memories": memories,
            "budget": budget.to_dict(),
            "reranker": self._reranker.model_name(),
        }

    def _handle_memory_context(self, params: dict) -> dict:
        """Get recent memories without search."""
        project = params.get("project", "")
        scope = params.get("scope", "")
        limit = int(params.get("limit", 20))

        memories_raw = self._memory_store.get_recent(project=project, scope=scope, limit=limit)
        memories = [
            {
                "id": m.id,
                "title": m.title,
                "content": m.content,
                "type": m.memory_type,
                "scope": m.scope,
                "project": m.project,
                "topic_key": m.topic_key,
                "tags": m.tags,
                "revision_count": m.revision_count,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
            }
            for m in memories_raw
        ]

        # Apply token budget — replaces the previous hardcoded 200-char per-item
        # truncate with the configured strategy (drop default).
        memories, budget = self._token_budget.fit(
            memories, content_key="content", is_code=False,
        )

        return {
            "count": len(memories),
            "memories": memories,
            "budget": budget.to_dict(),
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

    def _handle_memories_by_symbol(self, params: dict) -> dict:
        """Memories that reference a specific code symbol (full id)."""
        symbol = params["symbol"]
        repo = params.get("repo", "")
        branch = params.get("branch", "")
        limit = int(params.get("limit", 20))
        rows = self._memory_store.memories_by_symbol(symbol, repo, branch, limit)
        out = []
        for m, link_sources in rows:
            d = _mem_to_dict(m)
            d["link_sources"] = link_sources
            out.append(d)
        out, budget = self._token_budget.fit(out, content_key="content", is_code=False)
        return {"symbol": symbol, "count": len(out), "memories": out, "budget": budget.to_dict()}

    def _handle_memories_by_file(self, params: dict) -> dict:
        """Memories that reference a file path."""
        file = params["file"]
        limit = int(params.get("limit", 20))
        rows = self._memory_store.memories_by_file(file, limit)
        out = []
        for m, link_sources in rows:
            d = _mem_to_dict(m)
            d["link_sources"] = link_sources
            out.append(d)
        out, budget = self._token_budget.fit(out, content_key="content", is_code=False)
        return {"file": file, "count": len(out), "memories": out, "budget": budget.to_dict()}

    def _handle_extract_routes(self, params: dict) -> dict:
        """Generic dispatcher. Picks the right extractor per framework, scans
        the indexed files of (repo, branch) on disk, and persists every route.

        Frameworks supported today: quarkus, spring, fastapi, flask, express,
        nestjs, angular.
        """
        from pathlib import Path
        from .parsers import (
            quarkus_routes, spring_routes, fastapi_routes, flask_routes,
            express_routes, nest_routes, angular_routes,
        )

        EXTRACTORS = {
            "quarkus":  quarkus_routes,
            "spring":   spring_routes,
            "fastapi":  fastapi_routes,
            "flask":    flask_routes,
            "express":  express_routes,
            "nestjs":   nest_routes,
            "angular":  angular_routes,
        }

        framework = params.get("framework", "")
        if framework not in EXTRACTORS:
            return {
                "error": f"unknown framework '{framework}'",
                "supported": sorted(EXTRACTORS.keys()),
            }
        repo = params.get("repo", "")
        branch = params.get("branch", "")
        if not repo or not branch:
            return {"error": "repo and branch are required"}
        source_root_param = params.get("source_root", "")

        mod = EXTRACTORS[framework]
        exts = tuple(getattr(mod, "EXTENSIONS", ()))
        if not exts:
            return {"error": f"extractor '{framework}' missing EXTENSIONS"}

        # Pull indexed source files for this repo+branch matching the extensions
        like_clauses = " OR ".join(["source_file LIKE ?"] * len(exts))
        like_params = [f"%{e}" for e in exts]
        files = self._graph_store._conn.execute(
            f"""SELECT DISTINCT source_file FROM graph_edges
                WHERE repo = ? AND branch = ? AND ({like_clauses})""",
            (repo, branch, *like_params),
        ).fetchall()
        target_files = [r[0] for r in files]

        # Resolve on-disk repo root
        candidates = []
        if source_root_param:
            candidates.append(Path(source_root_param))
        candidates.append(Path.cwd() / repo)
        candidates.append(Path.cwd().parent / repo)
        state_dir_env = os.environ.get("DEVAI_STATE_DIR", "")
        if state_dir_env:
            candidates.append(Path(state_dir_env).parent.parent / repo)
        roots = [c for c in candidates if c.exists()]
        if not roots:
            return {"error": f"could not locate '{repo}' on disk; pass source_root explicitly",
                    "tried": [str(c) for c in candidates]}
        root = roots[0]

        scanned = 0
        skipped_missing = 0
        all_routes: list[dict] = []

        for rel_path in target_files:
            full = root / rel_path
            if not full.exists():
                skipped_missing += 1
                continue
            try:
                content = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                skipped_missing += 1
                continue
            scanned += 1
            for r in mod.extract(content, rel_path):
                all_routes.append({
                    "framework": r.framework, "http_method": r.http_method,
                    "path": r.path, "handler_class": r.handler_class,
                    "handler_method": r.handler_method,
                    "handler_symbol": r.handler_symbol,
                    "file": r.file, "line": r.line,
                    "repo": repo, "branch": branch,
                })

        inserted = self._graph_store.add_routes(all_routes)
        return {
            "framework": framework, "repo": repo, "branch": branch,
            "source_root": str(root),
            "files_indexed": len(target_files),
            "files_scanned": scanned,
            "files_missing_on_disk": skipped_missing,
            "routes_extracted": len(all_routes),
            "routes_persisted": inserted,
        }

    def _handle_extract_quarkus_routes(self, params: dict) -> dict:
        """LEGACY: kept for backward compatibility. Delegates to extract_routes
        with framework='quarkus'. New code should call extract_routes directly."""
        return self._handle_extract_routes({**params, "framework": "quarkus"})

    def _handle_extract_quarkus_routes_legacy_impl(self, params: dict) -> dict:
        """Old standalone implementation; preserved only because tests reference it.
        Not registered."""
        from pathlib import Path
        from .parsers.quarkus_routes import extract_quarkus_routes

        repo = params.get("repo", "")
        branch = params.get("branch", "")
        if not repo or not branch:
            return {"error": "repo and branch are required"}
        source_root = params.get("source_root", "")

        # Find indexed .java files for this repo+branch via graph_store
        files = self._graph_store._conn.execute(
            """SELECT DISTINCT source_file FROM graph_edges
               WHERE repo = ? AND branch = ? AND source_file LIKE '%.java'""",
            (repo, branch),
        ).fetchall()
        java_files = [r[0] for r in files]

        total_routes = 0
        scanned = 0
        skipped_missing = 0
        all_routes: list[dict] = []

        # Resolve the on-disk root. If source_root not given, try a few sensible
        # candidates: cwd/{repo}, parent-of-state-dir/{repo}.
        candidates = []
        if source_root:
            candidates.append(Path(source_root))
        candidates.append(Path.cwd() / repo)
        candidates.append(Path.cwd().parent / repo)
        state_dir_env = os.environ.get("DEVAI_STATE_DIR", "")
        if state_dir_env:
            candidates.append(Path(state_dir_env).parent.parent / repo)
        roots = [c for c in candidates if c.exists()]
        if not roots:
            return {"error": f"could not locate '{repo}' on disk; pass source_root explicitly",
                    "tried": [str(c) for c in candidates]}
        root = roots[0]

        for rel_path in java_files:
            full = root / rel_path
            if not full.exists():
                skipped_missing += 1
                continue
            try:
                content = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                skipped_missing += 1
                continue
            scanned += 1
            routes = extract_quarkus_routes(content, rel_path)
            for r in routes:
                d = {
                    "framework": r.framework, "http_method": r.http_method,
                    "path": r.path, "handler_class": r.handler_class,
                    "handler_method": r.handler_method, "handler_symbol": r.handler_symbol,
                    "file": r.file, "line": r.line,
                    "repo": repo, "branch": branch,
                }
                all_routes.append(d)
                total_routes += 1

        inserted = self._graph_store.add_routes(all_routes)
        return {
            "repo": repo, "branch": branch,
            "source_root": str(root),
            "java_files_indexed": len(java_files),
            "java_files_scanned": scanned,
            "java_files_missing_on_disk": skipped_missing,
            "routes_extracted": total_routes,
            "routes_persisted": inserted,
        }

    def _handle_search_routes(self, params: dict) -> dict:
        """List routes matching a path substring + optional filters."""
        rows = self._graph_store.search_routes(
            q=params.get("q", ""),
            framework=params.get("framework", ""),
            http_method=params.get("http_method", ""),
            repo=params.get("repo", ""),
            branch=params.get("branch", ""),
            limit=int(params.get("limit", 50)),
        )
        return {"count": len(rows), "routes": rows}

    def _handle_routes_for_handler(self, params: dict) -> dict:
        """Given a handler symbol, return the route(s) it serves."""
        handler = params.get("handler_symbol", "")
        if not handler:
            return {"error": "handler_symbol is required"}
        return {"handler_symbol": handler, "routes": self._graph_store.routes_for_handler(handler)}

    def _handle_fts_rebuild(self, params: dict) -> dict:
        """Rebuild graph_symbols_fts from current graph_edges.
        Run once after upgrading to a build that supports FTS5 symbol search,
        or after a fresh index from scratch."""
        force = bool(params.get("force", False))
        return self._graph_store.populate_fts(force=force)

    def _handle_impact_analysis(self, params: dict) -> dict:
        """Trace upstream callers + downstream callees for a symbol up to `depth`.
        Returns direct neighbors, transitive counts, files affected, hot files."""
        symbol = params.get("symbol", "")
        repo = params.get("repo", "")
        branch = params.get("branch", "")
        depth = int(params.get("depth", 3))
        kind = params.get("kind", "calls")
        if not symbol or not repo or not branch:
            return {"error": "symbol, repo, and branch are required"}
        return self._graph_store.impact_analysis(repo, branch, symbol, depth=depth, kind=kind)

    def _handle_memory_refs(self, params: dict) -> dict:
        """Junction rows for a single memory: which symbols and files it references."""
        memory_id = int(params["id"])
        refs = self._memory_store.memory_refs(memory_id)
        return {"id": memory_id, "count": len(refs), "refs": refs}

    def _handle_symbol_memory_counts(self, params: dict) -> dict:
        """Map {symbol: memory_count} for the heatmap overlay."""
        repo = params.get("repo", "")
        branch = params.get("branch", "")
        return {"counts": self._memory_store.symbol_reference_counts(repo, branch)}

    def _handle_backfill_symbol_refs(self, params: dict) -> dict:
        """Re-extract symbol references for every existing memory.
        Run once after upgrading or after a fresh index."""
        return self._memory_store.backfill_symbol_refs()

    def _handle_backfill_vector_links(self, params: dict) -> dict:
        """Bridge memories that have no explicit file/symbol mentions to code
        via vector similarity.

        For each candidate memory:
          1. Re-embed `title + content` (deterministic, cheap)
          2. Search LanceDB for the top-K nearest CODE chunks
          3. Insert (memory_id, symbol, file) refs with source='vector-similarity'

        Params:
          top_k         (default 5)     — how many nearest code chunks per memory
          only_unlinked (default True)  — skip memories that already have any junction row
          repo          (optional)      — restrict the LanceDB search to a specific repo
        """
        from datetime import datetime, timezone as _tz
        top_k = int(params.get("top_k", 5))
        only_unlinked = bool(params.get("only_unlinked", True))
        repo_filter = params.get("repo", "")

        if only_unlinked:
            rows = self._memory_store._conn.execute(
                """SELECT id, title, content, repo, branch FROM memories m
                   WHERE deleted_at IS NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM memory_symbol_references msr
                       WHERE msr.memory_id = m.id
                     )"""
            ).fetchall()
        else:
            rows = self._memory_store._conn.execute(
                "SELECT id, title, content, repo, branch FROM memories WHERE deleted_at IS NULL"
            ).fetchall()

        now = datetime.now(_tz.utc).isoformat()
        processed = 0
        skipped_empty = 0
        inserted_total = 0
        for r in rows:
            text = ((r["title"] or "") + " " + (r["content"] or "")).strip()
            if not text:
                skipped_empty += 1
                continue

            try:
                vec = self._embedding.embed_single(text)
            except Exception as e:
                logger.warning("embed failed for memory #%d: %s", r["id"], e)
                continue

            # Over-fetch heavily: memory-vs-memory hits dominate top-15 for
            # abstract memories (HU inventories, session summaries). 30x lets
            # us reach into the code chunks before truncating.
            try:
                hits = self._vector_store.search(vector=vec, limit=top_k * 30,
                                                 filter_conditions={"repo": repo_filter} if repo_filter else None)
            except Exception as e:
                logger.warning("vector search failed for memory #%d: %s", r["id"], e)
                continue

            # Keep only code chunks (filter out memory + memory_chunk hits) and cap
            code_hits = [
                h for h in hits
                if not h.metadata.get("chunk_level", "").startswith("memory")
            ][:top_k]

            seen = set()
            for h in code_hits:
                symbol = h.metadata.get("symbol", "") or ""
                file = h.metadata.get("file", "") or ""
                if not symbol and not file:
                    continue
                key = (symbol, file)
                if key in seen:
                    continue
                seen.add(key)
                self._memory_store._conn.execute(
                    """INSERT OR IGNORE INTO memory_symbol_references
                       (memory_id, symbol, file, line, repo, branch, source, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'vector-similarity', ?)""",
                    (r["id"], symbol, file,
                     int(h.metadata.get("start_line", 0)),
                     h.metadata.get("repo", "") or r["repo"] or "",
                     h.metadata.get("branch", "") or r["branch"] or "",
                     now),
                )
                inserted_total += 1
            processed += 1

        self._memory_store._conn.commit()
        return {
            "memories_processed": processed,
            "memories_skipped_empty": skipped_empty,
            "refs_inserted": inserted_total,
            "only_unlinked": only_unlinked,
            "top_k": top_k,
        }

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
        """Get index status for all repos or a specific one.

        Listing strategy:
          1. Pull the canonical (repo, branch) pairs straight from graph_edges
             — that's where the data physically lives.
          2. Enrich each row with the matching index_state record (model,
             commit, indexed_at) when available. Pairs without an index_state
             record are flagged `phantom_branch=true` so callers know the
             metadata is stale.

        This fixes the bug where index_status would skip a branch that has
        edges in graph_edges but no companion row in index_state.
        """
        repo_filter = params.get("repo")

        # Source of truth: graph_edges
        sql = "SELECT repo, branch, COUNT(*) AS edge_count FROM graph_edges"
        params_sql: list[Any] = []
        if repo_filter:
            sql += " WHERE repo = ?"
            params_sql.append(repo_filter)
        sql += " GROUP BY repo, branch ORDER BY repo, branch"
        rows = self._graph_store._conn.execute(sql, params_sql).fetchall()

        repos = []
        for r in rows:
            repo_path = r[0]
            br = r[1]
            edge_count = r[2]
            record = self._index_store.get_last_indexed(repo_path, br)
            entry = {
                "repo": repo_path,
                "name": (repo_path or "").rstrip("/").split("/")[-1],
                "branch": br,
                "edge_count": edge_count,
                "status": "indexed",
                "phantom_branch": False,
            }
            if record:
                entry.update({
                    "last_commit": record.last_commit,
                    "model": record.model_name,
                    "dimension": record.model_dimension,
                    "files": record.file_count,
                    "symbols": record.symbol_count,
                    "chunks": record.chunk_count,
                    "indexed_at": record.indexed_at,
                })
            else:
                # Edges exist but no index_state record → metadata drift.
                entry["phantom_branch"] = True
                entry["last_commit"] = None
                entry["model"] = None
                entry["dimension"] = None
                entry["files"] = None
                entry["symbols"] = None
                entry["chunks"] = None
                entry["indexed_at"] = None
            repos.append(entry)

        return {
            "count": len(repos),
            "phantom_count": sum(1 for r in repos if r["phantom_branch"]),
            "repos": repos,
        }

    def _handle_push_index(self, params: dict) -> dict:
        """Push local vectors to shared Qdrant for a repo+branch.

        Reads all vectors from the local LanceDB store via scroll_all(),
        creates a temporary QdrantVectorStore from env config, and upserts
        in batches of 100. Returns counts for pushed/skipped/errors.

        Params:
            repo (str): Required. Repository path or identifier.
            branch (str): Optional. Git branch (default: "").

        Returns:
            {"pushed": N, "skipped": N, "errors": N}
        """
        repo_raw = params.get("repo")
        if not repo_raw:
            raise ValueError("push_index requires 'repo' parameter")
        repo = self._repo_name(repo_raw)
        branch = params.get("branch", "")

        config = create_storage_config_from_env()
        if config.mode == "local":
            raise ValueError(
                "Cannot push: DEVAI_STORAGE_MODE is 'local'. "
                "Set DEVAI_STORAGE_MODE=shared or hybrid and configure DEVAI_QDRANT_URL."
            )

        from .stores.qdrant_store import QdrantVectorStore
        from .stores.factory import _parse_qdrant_url

        host, port = _parse_qdrant_url(config.qdrant_url)
        collection = config.collection_name or f"devai_{hashlib.sha256(repo.encode()).hexdigest()[:16]}"
        target = QdrantVectorStore(
            url=host,
            port=port,
            api_key=config.qdrant_api_key,
            collection_name=collection,
            dimension=self._embedding.dimension(),
        )

        # Read all local vectors for this repo+branch
        points = self._vector_store.scroll_all(repo, branch)
        total = len(points)
        pushed = 0
        errors = 0
        batch_size = 1000

        # Count per branch for reporting
        branch_counts: dict[str, int] = {}
        for p in points:
            b = p.metadata.get("branch", "")
            branch_counts[b] = branch_counts.get(b, 0) + 1

        for i in range(0, total, batch_size):
            batch = points[i : i + batch_size]
            try:
                target.upsert(batch)
                pushed += len(batch)
            except Exception as e:
                logger.error("Push batch %d failed: %s", i // batch_size + 1, e)
                errors += len(batch)

        return {
            "pushed": pushed,
            "errors": errors,
            "total_local": total,
            "repo": repo,
            "branch": branch or "(all)",
            "branches": branch_counts,
            "collection": collection,
        }

    def _handle_pull_index(self, params: dict) -> dict:
        """Pull vectors from shared Qdrant to local LanceDB for a repo+branch.

        Creates a temporary QdrantVectorStore from env config, reads all vectors
        via scroll_all(), and upserts into the local store in batches of 100.

        Params:
            repo (str): Required. Repository path or identifier.
            branch (str): Optional. Git branch (default: "").

        Returns:
            {"pulled": N, "skipped": N, "errors": N}
        """
        repo_raw = params.get("repo")
        if not repo_raw:
            raise ValueError("pull_index requires 'repo' parameter")
        repo = self._repo_name(repo_raw)
        branch = params.get("branch", "")

        config = create_storage_config_from_env()
        if config.mode == "local":
            raise ValueError(
                "Cannot pull: DEVAI_STORAGE_MODE is 'local'. "
                "Set DEVAI_STORAGE_MODE=shared or hybrid and configure DEVAI_QDRANT_URL."
            )

        from .stores.qdrant_store import QdrantVectorStore
        from .stores.factory import _parse_qdrant_url

        host, port = _parse_qdrant_url(config.qdrant_url)
        collection = config.collection_name or f"devai_{hashlib.sha256(repo.encode()).hexdigest()[:16]}"
        source = QdrantVectorStore(
            url=host,
            port=port,
            api_key=config.qdrant_api_key,
            collection_name=collection,
            dimension=self._embedding.dimension(),
        )

        # Read all shared vectors for this repo+branch
        points = source.scroll_all(repo, branch)
        total = len(points)
        pulled = 0
        errors = 0
        batch_size = 1000

        # Count per branch for reporting
        branch_counts: dict[str, int] = {}
        for p in points:
            b = p.metadata.get("branch", "")
            branch_counts[b] = branch_counts.get(b, 0) + 1

        for i in range(0, total, batch_size):
            batch = points[i : i + batch_size]
            try:
                self._vector_store.upsert(batch)
                pulled += len(batch)
            except Exception as e:
                logger.error("Pull batch %d failed: %s", i // batch_size + 1, e)
                errors += len(batch)

        return {
            "pulled": pulled,
            "errors": errors,
            "total_remote": total,
            "repo": repo,
            "branch": branch or "(all)",
            "branches": branch_counts,
            "collection": collection,
        }

    def _handle_sync_index(self, params: dict) -> dict:
        """Bidirectional sync between local LanceDB and shared Qdrant.

        Compares content_hash values between stores. Points only in local
        are pushed to shared. Points only in shared are pulled to local.
        Conflicts (same ID, different content_hash) resolved by last-write-wins
        using indexed_at timestamps (per AD-10).

        Params:
            repo (str): Required. Repository path or identifier.
            branch (str): Optional. Git branch (default: "").

        Returns:
            {"pushed": N, "pulled": N, "conflicts": N, "resolution": "last-write-wins"}
        """
        repo_raw = params.get("repo")
        if not repo_raw:
            raise ValueError("sync_index requires 'repo' parameter")
        repo = self._repo_name(repo_raw)
        branch = params.get("branch", "")

        config = create_storage_config_from_env()
        if config.mode == "local":
            raise ValueError(
                "Cannot sync: DEVAI_STORAGE_MODE is 'local'. "
                "Set DEVAI_STORAGE_MODE=shared or hybrid and configure DEVAI_QDRANT_URL."
            )

        from .stores.qdrant_store import QdrantVectorStore
        from .stores.factory import _parse_qdrant_url

        host, port = _parse_qdrant_url(config.qdrant_url)
        collection = config.collection_name or f"devai_{hashlib.sha256(repo.encode()).hexdigest()[:16]}"
        shared_store = QdrantVectorStore(
            url=host,
            port=port,
            api_key=config.qdrant_api_key,
            collection_name=collection,
            dimension=self._embedding.dimension(),
        )

        # Fetch all points from both stores
        local_points = self._vector_store.scroll_all(repo, branch)
        shared_points = shared_store.scroll_all(repo, branch)

        # Build lookup maps by ID
        local_by_id = {p.id: p for p in local_points}
        shared_by_id = {p.id: p for p in shared_points}

        local_ids = set(local_by_id.keys())
        shared_ids = set(shared_by_id.keys())

        # Sets
        only_local = local_ids - shared_ids
        only_shared = shared_ids - local_ids
        both = local_ids & shared_ids

        pushed = 0
        pulled = 0
        conflicts = 0
        batch_size = 1000

        # Push local-only points to shared
        to_push = [local_by_id[pid] for pid in only_local]
        for i in range(0, len(to_push), batch_size):
            batch = to_push[i : i + batch_size]
            try:
                shared_store.upsert(batch)
                pushed += len(batch)
            except Exception as e:
                logger.error("Sync push batch failed: %s", e)

        # Pull shared-only points to local
        to_pull = [shared_by_id[pid] for pid in only_shared]
        for i in range(0, len(to_pull), batch_size):
            batch = to_pull[i : i + batch_size]
            try:
                self._vector_store.upsert(batch)
                pulled += len(batch)
            except Exception as e:
                logger.error("Sync pull batch failed: %s", e)

        # Resolve conflicts: same ID, different content
        for pid in both:
            lp = local_by_id[pid]
            sp = shared_by_id[pid]
            local_hash = lp.metadata.get("content_hash", "")
            shared_hash = sp.metadata.get("content_hash", "")
            if local_hash == shared_hash:
                continue  # already in sync

            conflicts += 1
            # Last-write-wins by indexed_at
            local_ts = lp.metadata.get("indexed_at", "")
            shared_ts = sp.metadata.get("indexed_at", "")

            if shared_ts > local_ts:
                # Shared wins — pull to local
                try:
                    self._vector_store.upsert([sp])
                    pulled += 1
                except Exception as e:
                    logger.error("Sync conflict resolution (pull) failed: %s", e)
            else:
                # Local wins (also covers empty timestamps) — push to shared
                try:
                    shared_store.upsert([lp])
                    pushed += 1
                except Exception as e:
                    logger.error("Sync conflict resolution (push) failed: %s", e)

        # Collect branch stats from all points involved
        branch_counts: dict[str, int] = {}
        for p in local_points:
            b = p.metadata.get("branch", "")
            branch_counts[b] = branch_counts.get(b, 0) + 1

        return {
            "pushed": pushed,
            "pulled": pulled,
            "conflicts": conflicts,
            "resolution": "last-write-wins",
            "total_local": len(local_points),
            "total_remote": len(shared_points),
            "repo": repo,
            "branch": branch or "(all)",
            "branches": branch_counts,
            "collection": collection,
        }

    def _handle_health(self, params: dict) -> dict:
        """Health check."""
        return {
            "status": "serving",
            "model_loaded": self._embedding.model_name(),
            "model_dimension": self._embedding.dimension(),
            "languages_supported": self._parser_registry.supported_languages(),
        }

    def _handle_model_list(self, params: dict) -> dict:
        """List available embedding models with cache status and metadata."""
        from .embeddings.local import list_models_detailed, _model_is_cached
        models = list_models_detailed()
        current = self._embedding.model_name()
        lang = params.get("lang", "en")
        result = []
        for key, info in models.items():
            entry = info.to_dict()
            entry["key"] = key
            entry["cached"] = _model_is_cached(info.name)
            entry["description"] = info.desc_es if lang == "es" else info.desc_en
            result.append(entry)
        return {"current": current, "models": result}

    def _handle_model_download(self, params: dict) -> dict:
        """Download/cache a model without switching to it."""
        model_key = params.get("model")
        from .embeddings.local import MODEL_REGISTRY, _model_is_cached
        if model_key not in MODEL_REGISTRY:
            raise ValueError(f"Unknown model: {model_key}. Available: {list(MODEL_REGISTRY.keys())}")
        info = MODEL_REGISTRY[model_key]
        if _model_is_cached(info.name):
            return {"status": "already_cached", "model": info.name}
        logger.info("Downloading model: %s (%d MB)...", info.name, info.size_mb)
        from sentence_transformers import SentenceTransformer
        SentenceTransformer(info.name, device="cpu")
        return {"status": "downloaded", "model": info.name}


def serve_stdio(config: DevAIConfig | dict[str, Any] | None = None) -> None:
    """Run the ML service over stdin/stdout JSON-RPC.

    Accepts DevAIConfig (v0.8+) OR the legacy dict shape (pre-v0.8). When None,
    config is built entirely from env vars.
    """
    # Silence HuggingFace warnings and progress bars before any imports trigger them
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
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)
    logging.getLogger("torch").setLevel(logging.WARNING)

    # Normalize to DevAIConfig. EmbeddingConfig.from_env reads DEVAI_EMBEDDINGS_OFFLINE
    # internally, so the previous manual override here is no longer needed.
    if isinstance(config, DevAIConfig):
        cfg = config
    else:
        cfg = DevAIConfig.from_legacy_dict(config)

    service = MLService(cfg)

    emb = service._embedding
    # Single concise ready line with key info
    logger.info(
        "ML service ready (model=%s, dim=%d, languages=%d)",
        emb.model_name(), emb.dimension(),
        len(service._parser_registry.supported_languages()),
    )

    # Idle watchdog: exit after cfg.idle_timeout.timeout_sec of inactivity.
    # Set to 0 to disable. Default 1800s (30 min). The main thread blocks on
    # stdin so we use os._exit() to terminate immediately from the watchdog.
    idle_timeout = cfg.idle_timeout.timeout_sec

    last_activity = [time.monotonic()]
    activity_lock = threading.Lock()

    def _bump_activity() -> None:
        with activity_lock:
            last_activity[0] = time.monotonic()

    if idle_timeout > 0:
        def _watchdog() -> None:
            check_interval = min(60, max(5, idle_timeout // 4))
            while True:
                time.sleep(check_interval)
                with activity_lock:
                    idle_for = time.monotonic() - last_activity[0]
                if idle_for >= idle_timeout:
                    logger.info(
                        "Idle for %.0fs (timeout=%ds), exiting",
                        idle_for, idle_timeout,
                    )
                    sys.stderr.flush()
                    os._exit(0)

        watchdog_thread = threading.Thread(
            target=_watchdog, name="idle-watchdog", daemon=True,
        )
        watchdog_thread.start()
        logger.info("Idle watchdog active (timeout=%ds)", idle_timeout)
    else:
        logger.info("Idle watchdog disabled (DEVAI_ML_IDLE_TIMEOUT_SEC=0)")

    # Signal ready
    sys.stderr.write("DEVAI_ML_READY\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        _bump_activity()
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
        _bump_activity()


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
