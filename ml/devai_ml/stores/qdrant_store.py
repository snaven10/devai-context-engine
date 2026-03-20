"""Qdrant-backed vector store for shared mode.

Implements the VectorStore Protocol using the sync qdrant_client via gRPC.
ID mapping uses UUID5 with a fixed DevAI namespace for deterministic,
stateless conversion from LanceDB string IDs to Qdrant UUIDs.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from .vector_store import SearchResult, VectorPoint

logger = logging.getLogger(__name__)

# Fixed namespace for deterministic UUID5 generation (AD-05)
DEVAI_UUID_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

# Metadata fields stored in Qdrant payload — mirrors LanceDB schema exactly
PAYLOAD_FIELDS = [
    "repo",
    "branch",
    "commit",
    "file",
    "symbol",
    "symbol_type",
    "language",
    "start_line",
    "end_line",
    "chunk_level",
    "content_hash",
    "is_deletion",
    "memory_type",
    "memory_scope",
    "memory_tags",
    "indexed_at",
]

# Fields that should be stored/returned as integers
_INT_FIELDS = {"start_line", "end_line"}

# Fields that should be stored/returned as booleans
_BOOL_FIELDS = {"is_deletion"}

# Batch size for upsert and scroll operations (REQ-VS-008)
BATCH_SIZE = 1000


class QdrantVectorStore:
    """Qdrant-backed vector store for shared mode.

    Implements the VectorStore Protocol. Uses sync qdrant_client via gRPC.
    Collection is auto-created on first upsert if it does not exist.
    """

    def __init__(
        self,
        url: str = "localhost",
        port: int = 6334,
        api_key: str | None = None,
        collection_name: str = "devai_default",
        dimension: int = 384,
        timeout: int = 30,
    ) -> None:
        from qdrant_client import QdrantClient

        self._client = QdrantClient(
            url=url,
            port=port,
            api_key=api_key,
            prefer_grpc=True,
            timeout=timeout,
        )
        self._collection_name = collection_name
        self._dimension = dimension
        self._collection_exists = False
        logger.info(
            "QdrantVectorStore initialized (url=%s:%d, collection=%s)",
            url,
            port,
            collection_name,
        )

    # ------------------------------------------------------------------
    # ID mapping
    # ------------------------------------------------------------------

    @staticmethod
    def lance_id_to_uuid(lance_id: str) -> str:
        """Convert LanceDB string ID to deterministic UUID5 string.

        Uses a fixed DevAI namespace so the same lance_id always produces
        the same UUID, enabling idempotent upserts without state.
        """
        return str(uuid.uuid5(DEVAI_UUID_NAMESPACE, lance_id))

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def _ensure_collection(self, dimension: int | None = None) -> None:
        """Auto-create collection if it doesn't exist (REQ-VS-014).

        Validates dimension matches if collection already exists (REQ-VS-015).
        """
        if self._collection_exists:
            return

        from qdrant_client.models import Distance, VectorParams

        dim = dimension or self._dimension
        try:
            if not self._client.collection_exists(self._collection_name):
                self._client.create_collection(
                    collection_name=self._collection_name,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
                logger.info(
                    "Created Qdrant collection %s (dim=%d)",
                    self._collection_name,
                    dim,
                )
            else:
                # Validate dimension matches existing collection
                info = self._client.get_collection(self._collection_name)
                existing_dim = info.config.params.vectors.size
                if existing_dim != dim:
                    raise ValueError(
                        f"Dimension mismatch: collection {self._collection_name} has "
                        f"dimension {existing_dim}, but got vectors of dimension {dim}"
                    )
        except ValueError:
            raise
        except Exception as e:
            logger.error("Failed to ensure collection %s: %s", self._collection_name, e)
            raise

        self._collection_exists = True

    # ------------------------------------------------------------------
    # Filter translation
    # ------------------------------------------------------------------

    def _build_filter(self, filter_conditions: dict[str, Any]) -> Any:
        """Translate LanceDB-style filter dict to Qdrant Filter (REQ-VS-007).

        Scalar values -> MatchValue, list values -> MatchAny.
        """
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            MatchAny,
            MatchValue,
        )

        must = []
        for key, value in filter_conditions.items():
            if isinstance(value, list):
                must.append(FieldCondition(key=key, match=MatchAny(any=value)))
            else:
                must.append(FieldCondition(key=key, match=MatchValue(value=value)))
        return Filter(must=must)

    # ------------------------------------------------------------------
    # Payload helpers
    # ------------------------------------------------------------------

    def _point_to_payload(self, point: VectorPoint) -> dict[str, Any]:
        """Build Qdrant payload from VectorPoint.

        Stores _lance_id (REQ-VS-006) and _text (REQ-VS-016) alongside
        all metadata fields (REQ-VS-005).
        """
        payload: dict[str, Any] = {
            "_lance_id": point.id,
            "_text": point.text,
        }
        for field in PAYLOAD_FIELDS:
            if field in _INT_FIELDS:
                payload[field] = int(point.metadata.get(field, 0) or 0)
            elif field in _BOOL_FIELDS:
                payload[field] = bool(point.metadata.get(field, False))
            else:
                payload[field] = point.metadata.get(field, "")
        return payload

    @staticmethod
    def _payload_to_metadata(payload: dict[str, Any]) -> dict[str, Any]:
        """Reconstruct metadata dict from Qdrant payload."""
        metadata: dict[str, Any] = {}
        for field in PAYLOAD_FIELDS:
            raw = payload.get(field, "")
            if field in _INT_FIELDS:
                metadata[field] = int(raw or 0)
            elif field in _BOOL_FIELDS:
                metadata[field] = bool(raw)
            else:
                metadata[field] = str(raw) if raw is not None else ""
        return metadata

    # ------------------------------------------------------------------
    # VectorStore Protocol — upsert
    # ------------------------------------------------------------------

    def upsert(self, points: list[VectorPoint]) -> None:
        """Upsert vector points into Qdrant (REQ-VS-008).

        Auto-creates collection on first call. Batches points in groups
        of BATCH_SIZE with wait=True for durability.
        """
        if not points:
            return

        from qdrant_client.models import PointStruct

        dim = len(points[0].vector)
        self._ensure_collection(dim)

        for i in range(0, len(points), BATCH_SIZE):
            batch = points[i : i + BATCH_SIZE]
            qdrant_points = [
                PointStruct(
                    id=self.lance_id_to_uuid(p.id),
                    vector=p.vector,
                    payload=self._point_to_payload(p),
                )
                for p in batch
            ]
            try:
                self._client.upsert(
                    collection_name=self._collection_name,
                    points=qdrant_points,
                    wait=True,
                )
            except Exception as e:
                logger.error(
                    "Failed to upsert batch %d-%d to %s: %s",
                    i,
                    i + len(batch),
                    self._collection_name,
                    e,
                )
                raise

        logger.debug("Upserted %d points to %s", len(points), self._collection_name)

    # ------------------------------------------------------------------
    # VectorStore Protocol — search
    # ------------------------------------------------------------------

    def search(
        self,
        vector: list[float],
        filter_conditions: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Search for similar vectors with optional filters (REQ-VS-009).

        Returns SearchResult objects with the original LanceDB string ID
        restored from the _lance_id payload field.
        """
        try:
            if not self._client.collection_exists(self._collection_name):
                return []
        except Exception as e:
            logger.error("Failed to check collection existence: %s", e)
            return []

        query_filter = self._build_filter(filter_conditions) if filter_conditions else None

        try:
            response = self._client.query_points(
                collection_name=self._collection_name,
                query=vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            hits = response.points
        except Exception as e:
            logger.error("Search failed on %s: %s", self._collection_name, e)
            return []

        results = []
        for hit in hits:
            payload = hit.payload or {}
            metadata = self._payload_to_metadata(payload)
            results.append(
                SearchResult(
                    id=payload.get("_lance_id", str(hit.id)),
                    score=hit.score,
                    metadata=metadata,
                    text=payload.get("_text", ""),
                )
            )
        return results

    # ------------------------------------------------------------------
    # VectorStore Protocol — delete_by_file
    # ------------------------------------------------------------------

    def delete_by_file(self, repo: str, branch: str, file_path: str) -> None:
        """Delete all points matching repo+branch+file (REQ-VS-010)."""
        try:
            if not self._client.collection_exists(self._collection_name):
                return
            self._client.delete(
                collection_name=self._collection_name,
                points_selector=self._build_filter(
                    {"repo": repo, "branch": branch, "file": file_path}
                ),
            )
            logger.debug(
                "Deleted points for %s/%s/%s from %s",
                repo,
                branch,
                file_path,
                self._collection_name,
            )
        except Exception as e:
            logger.error("delete_by_file failed: %s", e)

    # ------------------------------------------------------------------
    # VectorStore Protocol — rename_file
    # ------------------------------------------------------------------

    def rename_file(
        self, repo: str, branch: str, old_path: str, new_path: str
    ) -> None:
        """Rename file path in-place using scroll + set_payload (REQ-VS-011).

        Unlike LanceDB's delete-and-reindex approach, Qdrant supports
        in-place payload updates, so we scroll matching points and update
        their 'file' field without touching vectors.
        """
        try:
            if not self._client.collection_exists(self._collection_name):
                return
        except Exception as e:
            logger.error("rename_file collection check failed: %s", e)
            return

        scroll_filter = self._build_filter(
            {"repo": repo, "branch": branch, "file": old_path}
        )
        offset = None
        total_updated = 0

        while True:
            try:
                results, next_offset = self._client.scroll(
                    collection_name=self._collection_name,
                    scroll_filter=scroll_filter,
                    limit=BATCH_SIZE,
                    offset=offset,
                )
            except Exception as e:
                logger.error("rename_file scroll failed: %s", e)
                break

            if not results:
                break

            point_ids = [p.id for p in results]
            try:
                self._client.set_payload(
                    collection_name=self._collection_name,
                    payload={"file": new_path},
                    points=point_ids,
                )
                total_updated += len(point_ids)
            except Exception as e:
                logger.error("rename_file set_payload failed: %s", e)
                break

            if next_offset is None:
                break
            offset = next_offset

        if total_updated > 0:
            logger.debug(
                "Renamed %d points from %s to %s in %s",
                total_updated,
                old_path,
                new_path,
                self._collection_name,
            )

    # ------------------------------------------------------------------
    # VectorStore Protocol — delete_collection
    # ------------------------------------------------------------------

    def delete_collection(self) -> None:
        """Drop the Qdrant collection (REQ-VS-012).

        No-op if the collection does not exist.
        """
        try:
            self._client.delete_collection(self._collection_name)
            self._collection_exists = False
            logger.info("Deleted Qdrant collection %s", self._collection_name)
        except Exception:
            # Collection may not exist — that's fine
            self._collection_exists = False

    # ------------------------------------------------------------------
    # VectorStore Protocol — count
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Return the number of points in the collection (REQ-VS-013)."""
        try:
            if not self._client.collection_exists(self._collection_name):
                return 0
            return self._client.count(self._collection_name).count
        except Exception as e:
            logger.error("count failed on %s: %s", self._collection_name, e)
            return 0

    # ------------------------------------------------------------------
    # VectorStore Protocol — scroll_all
    # ------------------------------------------------------------------

    def scroll_all(self, repo: str, branch: str) -> list[VectorPoint]:
        """Scroll all points for a repo+branch (REQ-PP-005).

        Uses Qdrant's scroll API with pagination to avoid loading
        all points into memory at once. Returns VectorPoint objects
        with original LanceDB string IDs restored from _lance_id.
        """
        try:
            if not self._client.collection_exists(self._collection_name):
                return []
        except Exception as e:
            logger.error("scroll_all collection check failed: %s", e)
            return []

        scroll_filter = self._build_filter({"repo": repo, "branch": branch})
        all_points: list[VectorPoint] = []
        offset = None

        while True:
            try:
                results, next_offset = self._client.scroll(
                    collection_name=self._collection_name,
                    scroll_filter=scroll_filter,
                    limit=BATCH_SIZE,
                    offset=offset,
                    with_vectors=True,
                )
            except Exception as e:
                logger.error("scroll_all failed: %s", e)
                break

            for p in results:
                payload = p.payload or {}
                metadata = self._payload_to_metadata(payload)
                all_points.append(
                    VectorPoint(
                        id=payload.get("_lance_id", str(p.id)),
                        vector=list(p.vector) if p.vector else [],
                        metadata=metadata,
                        text=payload.get("_text", ""),
                    )
                )

            if next_offset is None or not results:
                break
            offset = next_offset

        logger.debug(
            "Scrolled %d points for %s/%s from %s",
            len(all_points),
            repo,
            branch,
            self._collection_name,
        )
        return all_points

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self, timeout: float = 5.0) -> bool:
        """Check if Qdrant is reachable (AD-08).

        Uses get_collections() as a lightweight probe with a 5s timeout.
        Returns True if Qdrant responds, False otherwise.
        """
        try:
            self._client.get_collections()
            return True
        except Exception:
            return False
