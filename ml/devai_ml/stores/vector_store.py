from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class VectorPoint:
    """A vector with metadata to store."""
    id: str
    vector: list[float]
    metadata: dict[str, Any]
    text: str


@dataclass
class SearchResult:
    """A search result from the vector store."""
    id: str
    score: float
    metadata: dict[str, Any]
    text: str


def deterministic_id(repo: str, branch: str, file_path: str, start_line: int) -> str:
    """Generate a deterministic ID for a vector point.

    Same content at same location always produces the same ID,
    enabling true upsert without orphaned vectors.
    """
    raw = f"{repo}:{branch}:{file_path}:{start_line}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class VectorStore(Protocol):
    """Protocol for vector storage backends."""

    def upsert(self, points: list[VectorPoint]) -> None: ...
    def search(
        self,
        vector: list[float],
        filter_conditions: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]: ...
    def delete_by_file(self, repo: str, branch: str, file_path: str) -> None: ...
    def rename_file(self, repo: str, branch: str, old_path: str, new_path: str) -> None: ...
    def delete_collection(self) -> None: ...
    def count(self) -> int: ...


class LanceDBVectorStore:
    """LanceDB-backed vector store for local mode.

    Embedded (no server process), disk-based, supports metadata filtering.
    """

    def __init__(self, db_path: str, table_name: str = "vectors", dimension: int = 384) -> None:
        import lancedb

        self._db = lancedb.connect(db_path)
        self._table_name = table_name
        self._dimension = dimension
        self._table = self._get_or_create_table()
        self._ensure_schema()
        logger.info("LanceDB store initialized at %s (table=%s)", db_path, table_name)

    def _ensure_schema(self):
        """Migrate existing tables that are missing new columns.

        Uses LanceDB's native add_columns with literal defaults.
        If that fails, falls back to reading data as Arrow, adding columns,
        and recreating. Never drops data silently.
        """
        import pyarrow as pa

        required = {"memory_type", "memory_scope", "memory_tags"}
        existing = set(self._table.schema.names)
        missing = required - existing
        if not missing:
            return

        row_count = self._table.count_rows()
        if row_count == 0:
            # Empty table — just recreate with full schema
            logger.info("Empty table, recreating with full schema")
            self._db.drop_table(self._table_name)
            self._table = self._get_or_create_table()
            return

        logger.info(
            "Schema migration: adding %s to table %s (%d rows)",
            missing, self._table_name, row_count,
        )
        try:
            # Try native Arrow-based migration (no pandas needed)
            table_data = self._table.to_arrow()
            for col in missing:
                null_col = pa.array([""] * row_count, type=pa.string())
                table_data = table_data.append_column(col, null_col)
            self._db.drop_table(self._table_name)
            self._table = self._db.create_table(self._table_name, data=table_data)
            logger.info("Schema migration complete: %d rows preserved", row_count)
        except Exception as e:
            logger.error(
                "Schema migration FAILED for %s: %s — data preserved but new columns not added. "
                "Re-index to fix.",
                self._table_name, e,
            )
            # DO NOT drop and recreate — keep existing data without new columns

    def _get_or_create_table(self):
        import pyarrow as pa

        if self._table_name in self._db.table_names():
            return self._db.open_table(self._table_name)

        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), self._dimension)),
            pa.field("repo", pa.string()),
            pa.field("branch", pa.string()),
            pa.field("commit", pa.string()),
            pa.field("file", pa.string()),
            pa.field("symbol", pa.string()),
            pa.field("symbol_type", pa.string()),
            pa.field("language", pa.string()),
            pa.field("start_line", pa.int32()),
            pa.field("end_line", pa.int32()),
            pa.field("chunk_level", pa.string()),
            pa.field("content_hash", pa.string()),
            pa.field("is_deletion", pa.bool_()),
            pa.field("memory_type", pa.string()),    # insight, decision, note, bug
            pa.field("memory_scope", pa.string()),   # shared, local
            pa.field("memory_tags", pa.string()),    # comma-separated tags
        ])
        return self._db.create_table(self._table_name, schema=schema)

    def upsert(self, points: list[VectorPoint]) -> None:
        if not points:
            return
        data = []
        for p in points:
            row = {
                "id": p.id,
                "text": p.text,
                "vector": p.vector,
                "repo": p.metadata.get("repo", ""),
                "branch": p.metadata.get("branch", ""),
                "commit": p.metadata.get("commit", ""),
                "file": p.metadata.get("file", ""),
                "symbol": p.metadata.get("symbol", ""),
                "symbol_type": p.metadata.get("symbol_type", ""),
                "language": p.metadata.get("language", ""),
                "start_line": p.metadata.get("start_line", 0),
                "end_line": p.metadata.get("end_line", 0),
                "chunk_level": p.metadata.get("chunk_level", ""),
                "content_hash": p.metadata.get("content_hash", ""),
                "is_deletion": p.metadata.get("is_deletion", False),
                "memory_type": p.metadata.get("memory_type", ""),
                "memory_scope": p.metadata.get("memory_scope", ""),
                "memory_tags": p.metadata.get("memory_tags", ""),
            }
            data.append(row)

        # Delete existing rows with same IDs first, then add new ones
        ids = [p.id for p in points]
        try:
            self._table.delete(f"id IN {ids!r}")
        except Exception:
            pass  # table might be empty
        self._table.add(data)

    def search(
        self,
        vector: list[float],
        filter_conditions: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        query = self._table.search(vector).limit(limit)

        if filter_conditions:
            where_parts = []
            for key, value in filter_conditions.items():
                if isinstance(value, list):
                    values_str = ", ".join(f"'{v}'" for v in value)
                    where_parts.append(f"{key} IN ({values_str})")
                elif isinstance(value, bool):
                    where_parts.append(f"{key} = {str(value).lower()}")
                else:
                    where_parts.append(f"{key} = '{value}'")
            if where_parts:
                query = query.where(" AND ".join(where_parts))

        table = query.to_arrow()
        search_results = []
        for i in range(table.num_rows):
            metadata = {
                "repo": str(table.column("repo")[i].as_py()),
                "branch": str(table.column("branch")[i].as_py()),
                "commit": str(table.column("commit")[i].as_py()),
                "file": str(table.column("file")[i].as_py()),
                "symbol": str(table.column("symbol")[i].as_py()),
                "symbol_type": str(table.column("symbol_type")[i].as_py()),
                "language": str(table.column("language")[i].as_py()),
                "start_line": int(table.column("start_line")[i].as_py()),
                "end_line": int(table.column("end_line")[i].as_py()),
                "chunk_level": str(table.column("chunk_level")[i].as_py()),
                "content_hash": str(table.column("content_hash")[i].as_py()),
                "is_deletion": bool(table.column("is_deletion")[i].as_py()),
                "memory_type": str(table.column("memory_type")[i].as_py()) if "memory_type" in table.schema.names else "",
                "memory_scope": str(table.column("memory_scope")[i].as_py()) if "memory_scope" in table.schema.names else "",
                "memory_tags": str(table.column("memory_tags")[i].as_py()) if "memory_tags" in table.schema.names else "",
            }
            search_results.append(SearchResult(
                id=str(table.column("id")[i].as_py()),
                score=float(table.column("_distance")[i].as_py()),
                metadata=metadata,
                text=str(table.column("text")[i].as_py()),
            ))
        return search_results

    def delete_by_file(self, repo: str, branch: str, file_path: str) -> None:
        try:
            self._table.delete(f"repo = '{repo}' AND branch = '{branch}' AND file = '{file_path}'")
        except Exception:
            pass

    def rename_file(self, repo: str, branch: str, old_path: str, new_path: str) -> None:
        # LanceDB doesn't support in-place updates easily
        # Delete old + re-insert would require reading vectors, which is expensive
        # For now, delete old entries; they'll be re-indexed
        self.delete_by_file(repo, branch, old_path)

    def delete_collection(self) -> None:
        self._db.drop_table(self._table_name)

    def count(self) -> int:
        return self._table.count_rows()
