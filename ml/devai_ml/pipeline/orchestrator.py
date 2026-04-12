from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from ..chunking.semantic_chunker import SemanticChunker
from ..embeddings.base import EmbeddingProvider
from ..parsers.registry import ParserRegistry
from ..stores.graph_store import SQLiteGraphStore, StoredEdge
from ..stores.index_state import FileRecord, IndexRecord, IndexStateStore
from ..stores.vector_store import LanceDBVectorStore, VectorPoint, deterministic_id
from .git_state import FileChange, FileStatus, GitStateDetector

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    """Result of an indexing operation."""
    repo_path: str
    branch: str
    commit: str
    files_processed: int
    files_skipped: int
    chunks_created: int
    symbols_found: int
    edges_found: int
    duration_seconds: float
    errors: list[str]


class IndexPipeline:
    """Git-aware indexing pipeline.

    Orchestrates: git diff -> parse -> chunk -> embed -> store
    """

    def __init__(
        self,
        parser_registry: ParserRegistry,
        chunker: SemanticChunker,
        embedding: EmbeddingProvider,
        vector_store: LanceDBVectorStore,
        graph_store: SQLiteGraphStore,
        index_store: IndexStateStore,
    ) -> None:
        self.parser_registry = parser_registry
        self.chunker = chunker
        self.embedding = embedding
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.index_store = index_store

    @staticmethod
    def _repo_name(repo_path: str) -> str:
        """Extract short repo name from full path (basename)."""
        return Path(repo_path).name

    def index_repo(
        self,
        repo_path: str,
        branch: str | None = None,
        incremental: bool = True,
    ) -> IndexResult:
        """Index a repository, optionally incrementally."""
        start_time = time.monotonic()

        repo_name = self._repo_name(repo_path)
        git = GitStateDetector(repo_path)
        state = git.get_state()
        branch = branch or state.branch

        last = self.index_store.get_last_indexed(repo_name, branch)

        # Determine what to index
        full_reindex = False
        if last is None:
            logger.info("First index for %s/%s", repo_path, branch)
            full_reindex = True
        elif last.model_name != self.embedding.model_name():
            logger.warning(
                "Embedding model changed (%s -> %s), forcing full reindex",
                last.model_name, self.embedding.model_name(),
            )
            full_reindex = True
        elif not incremental:
            logger.info("Forced full reindex for %s/%s", repo_path, branch)
            full_reindex = True

        if full_reindex:
            changes = git.compute_diff(None, state.current_commit)
        else:
            changes = git.compute_diff(last.last_commit, state.current_commit)

        # Optionally include dirty (uncommitted) changes
        # dirty = git.get_dirty_changes()
        # changes.extend(dirty)

        logger.info(
            "Indexing %s/%s: %d files to process (incremental=%s)",
            repo_path, branch, len(changes), not full_reindex,
        )

        # Process changes
        files_processed = 0
        files_skipped = 0
        total_chunks = 0
        total_symbols = 0
        total_edges = 0
        errors: list[str] = []

        for change in changes:
            try:
                result = self._process_change(change, repo_name, branch, state.current_commit, git)
                if result:
                    files_processed += 1
                    total_chunks += result["chunks"]
                    total_symbols += result["symbols"]
                    total_edges += result["edges"]
                else:
                    files_skipped += 1
            except Exception as e:
                logger.error("Error processing %s: %s", change.path, e)
                errors.append(f"{change.path}: {e}")

        # Update index state
        self.index_store.save(IndexRecord(
            repo_path=repo_name,
            branch=branch,
            last_commit=state.current_commit,
            model_name=self.embedding.model_name(),
            model_dimension=self.embedding.dimension(),
            file_count=files_processed,
            symbol_count=total_symbols,
            chunk_count=total_chunks,
        ))

        duration = time.monotonic() - start_time
        logger.info(
            "Indexing complete: %d files, %d chunks, %d symbols, %d edges in %.1fs",
            files_processed, total_chunks, total_symbols, total_edges, duration,
        )

        return IndexResult(
            repo_path=repo_name,
            branch=branch,
            commit=state.current_commit,
            files_processed=files_processed,
            files_skipped=files_skipped,
            chunks_created=total_chunks,
            symbols_found=total_symbols,
            edges_found=total_edges,
            duration_seconds=duration,
            errors=errors,
        )

    def _process_change(
        self,
        change: FileChange,
        repo_name: str,
        branch: str,
        commit: str,
        git: GitStateDetector,
    ) -> dict | None:
        """Process a single file change. Returns stats dict or None if skipped."""

        if change.status == FileStatus.DELETED:
            self.vector_store.delete_by_file(repo_name, branch, change.path)
            self.graph_store.remove_file(repo_name, branch, change.path)
            self.index_store.remove_file(repo_name, branch, change.path)
            logger.debug("Deleted index for %s", change.path)
            return {"chunks": 0, "symbols": 0, "edges": 0}

        if change.status == FileStatus.RENAMED:
            self.vector_store.rename_file(repo_name, branch, change.old_path, change.path)
            self.graph_store.rename_file(repo_name, branch, change.old_path, change.path)
            self.index_store.rename_file(repo_name, branch, change.old_path, change.path)
            # Also re-parse in case content changed with the rename
            # Fall through to parse_and_store

        # For ADDED, MODIFIED, and RENAMED (after metadata update)
        return self._parse_and_store(change.path, repo_name, branch, commit, git)

    def _parse_and_store(
        self,
        file_path: str,
        repo_name: str,
        branch: str,
        commit: str,
        git: GitStateDetector,
    ) -> dict | None:
        """Parse a file, chunk it, embed it, and store results."""
        parser = self.parser_registry.get_parser(file_path)
        if parser is None:
            logger.debug("No parser for %s, skipping", file_path)
            return None

        # Read file content
        content = git.get_file_content(file_path)
        if content is None:
            logger.warning("Could not read %s", file_path)
            return None

        # Check if content actually changed (hash comparison)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        existing_hash = self.index_store.get_file_hash(repo_name, branch, file_path)
        if existing_hash == content_hash:
            logger.debug("Content unchanged for %s, skipping", file_path)
            return None

        # Delete old vectors for this file (before re-indexing)
        self.vector_store.delete_by_file(repo_name, branch, file_path)
        self.graph_store.remove_file(repo_name, branch, file_path)

        # Parse
        parse_result = parser.parse_source(content, file_path)

        # Chunk
        chunks = self.chunker.chunk(parse_result)

        # Embed
        if chunks:
            texts = [c.text for c in chunks]
            vectors = self.embedding.embed(texts)

            # Store vectors
            points = []
            for chunk, vector in zip(chunks, vectors):
                point_id = deterministic_id(repo_name, branch, file_path, chunk.start_line)
                points.append(VectorPoint(
                    id=point_id,
                    vector=vector,
                    metadata={
                        "repo": repo_name,
                        "branch": branch,
                        "commit": commit,
                        "file": file_path,
                        "symbol": chunk.symbol_name or "",
                        "symbol_type": chunk.symbol_type or "",
                        "language": chunk.language,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "chunk_level": chunk.level,
                        "content_hash": chunk.content_hash,
                        "is_deletion": False,
                    },
                    text=chunk.text,
                ))
            self.vector_store.upsert(points)

        # Store graph edges
        if parse_result.edges:
            stored_edges = [
                StoredEdge(
                    source=e.source,
                    target=e.target,
                    kind=e.kind,
                    source_file=file_path,
                    target_file=None,  # resolved later
                    line=e.line,
                    repo=repo_name,
                    branch=branch,
                )
                for e in parse_result.edges
            ]
            self.graph_store.add_edges(stored_edges)

        # Update file state
        self.index_store.save_file(repo_name, branch, FileRecord(
            file_path=file_path,
            content_hash=content_hash,
            language=parse_result.language,
            symbol_count=len(parse_result.symbols),
            chunk_count=len(chunks),
        ))

        logger.debug(
            "Indexed %s: %d chunks, %d symbols, %d edges",
            file_path, len(chunks), len(parse_result.symbols), len(parse_result.edges),
        )

        return {
            "chunks": len(chunks),
            "symbols": len(parse_result.symbols),
            "edges": len(parse_result.edges),
        }
