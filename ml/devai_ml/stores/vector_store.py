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
        logger.info("LanceDB store initialized at %s (table=%s)", db_path, table_name)

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

        results = query.to_pandas()
        search_results = []
        for _, row in results.iterrows():
            metadata = {
                "repo": row.get("repo", ""),
                "branch": row.get("branch", ""),
                "commit": row.get("commit", ""),
                "file": row.get("file", ""),
                "symbol": row.get("symbol", ""),
                "symbol_type": row.get("symbol_type", ""),
                "language": row.get("language", ""),
                "start_line": int(row.get("start_line", 0)),
                "end_line": int(row.get("end_line", 0)),
                "chunk_level": row.get("chunk_level", ""),
                "content_hash": row.get("content_hash", ""),
                "is_deletion": bool(row.get("is_deletion", False)),
            }
            search_results.append(SearchResult(
                id=row["id"],
                score=float(row.get("_distance", 0.0)),
                metadata=metadata,
                text=row.get("text", ""),
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
