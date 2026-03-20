"""Hybrid vector store: write-through to local + shared, read-local-first.

Wraps a LanceDBVectorStore (local) and QdrantVectorStore (shared) to provide
dual-backend storage with graceful degradation. Local is always authoritative;
shared failures are logged, queued for retry, and never block operations.

Architecture decisions:
    AD-07: In-memory deque for retry queue (bounded, best-effort)
    AD-08: Health check via QdrantVectorStore.health_check()
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

from .vector_store import SearchResult, VectorPoint

logger = logging.getLogger(__name__)


class HybridVectorStore:
    """Dual-backend store: write-through to local + shared, read-local-first.

    Implements the VectorStore Protocol. Local store (LanceDB) is authoritative
    for count and scroll operations. Shared store (Qdrant) receives write-through
    copies and serves as fallback when local returns zero search results.

    Graceful degradation:
        - If shared is unreachable, operates in local-only mode (REQ-GD-002)
        - Failed shared writes are queued for retry (REQ-HM-004)
        - Periodic health checks restore shared connectivity (REQ-GD-003)
    """

    def __init__(
        self,
        local: Any,
        shared: Any,
        max_retry_queue: int = 10_000,
        health_interval: int = 60,
    ) -> None:
        self._local = local
        self._shared = shared
        self._retry_queue: deque[tuple[str, tuple, dict]] = deque(
            maxlen=max_retry_queue,
        )
        self._max_retry_queue = max_retry_queue
        self._shared_healthy = True
        self._lock = threading.Lock()
        self._health_interval = health_interval

        # Initial health check (REQ-GD-001)
        self._perform_health_check()

        # Start periodic health checker (REQ-GD-003)
        self._health_thread = threading.Thread(
            target=self._health_loop,
            daemon=True,
            name="hybrid-store-health",
        )
        self._health_thread.start()

        logger.info(
            "HybridVectorStore initialized (shared_healthy=%s, retry_queue_max=%d)",
            self._shared_healthy,
            max_retry_queue,
        )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def _perform_health_check(self) -> bool:
        """Ping shared store and update health status.

        Uses QdrantVectorStore.health_check() (AD-08) with 5s timeout.
        On recovery, flushes the retry queue (REQ-GD-005).
        """
        try:
            healthy = self._shared.health_check(timeout=5.0)
        except Exception:
            healthy = False

        was_healthy = self._shared_healthy
        self._shared_healthy = healthy

        if not healthy and was_healthy:
            logger.warning(
                "Qdrant unreachable - operating in degraded mode (local-only)"
            )
        elif healthy and not was_healthy:
            queue_size = len(self._retry_queue)
            if queue_size > 0:
                logger.info(
                    "Qdrant connection restored - flushing %d queued writes",
                    queue_size,
                )
            else:
                logger.info("Qdrant connection restored")
            self.flush_retry_queue()

        return healthy

    def _health_loop(self) -> None:
        """Periodic health check loop (REQ-GD-003).

        Runs every health_interval seconds in a daemon thread.
        """
        while True:
            time.sleep(self._health_interval)
            try:
                self._perform_health_check()
            except Exception as e:
                logger.error("Health check loop error: %s", e)

    # ------------------------------------------------------------------
    # Retry queue
    # ------------------------------------------------------------------

    def _enqueue_retry(self, method_name: str, args: tuple, kwargs: dict) -> None:
        """Add a failed operation to the retry queue (REQ-HM-004).

        Bounded deque automatically drops oldest entry on overflow.
        """
        with self._lock:
            if len(self._retry_queue) >= self._max_retry_queue:
                logger.warning("Retry queue overflow - dropping oldest entry")
            self._retry_queue.append((method_name, args, kwargs))

    def flush_retry_queue(self) -> None:
        """Attempt to replay all queued operations to shared store.

        Called explicitly by sync command or on health recovery.
        Stops on first failure and re-queues remaining operations.
        """
        with self._lock:
            pending = list(self._retry_queue)
            self._retry_queue.clear()

        failed_from = None
        for i, (method_name, args, kwargs) in enumerate(pending):
            try:
                getattr(self._shared, method_name)(*args, **kwargs)
            except Exception as e:
                logger.warning(
                    "Retry failed for %s: %s — re-queuing %d remaining operations",
                    method_name,
                    e,
                    len(pending) - i,
                )
                failed_from = i
                break

        # Re-queue anything that wasn't replayed
        if failed_from is not None:
            with self._lock:
                for item in pending[failed_from:]:
                    self._retry_queue.append(item)

    # ------------------------------------------------------------------
    # Shared write helper
    # ------------------------------------------------------------------

    def _shared_write(
        self, method_name: str, *args: Any, **kwargs: Any
    ) -> None:
        """Attempt a write on shared store; queue on failure (REQ-HM-003).

        If shared is known-unhealthy (REQ-GD-004), skips the attempt
        entirely and queues immediately to avoid timeout delays.
        """
        if not self._shared_healthy:
            self._enqueue_retry(method_name, args, kwargs)
            return

        try:
            getattr(self._shared, method_name)(*args, **kwargs)
        except Exception as e:
            logger.warning("Shared write failed: %s", e)
            self._enqueue_retry(method_name, args, kwargs)

    # ------------------------------------------------------------------
    # VectorStore Protocol — upsert
    # ------------------------------------------------------------------

    def upsert(self, points: list[VectorPoint]) -> None:
        """Write-through: upsert to local first, then shared (REQ-HM-002).

        Local write must succeed. Shared failure is logged and queued.
        """
        self._local.upsert(points)
        self._shared_write("upsert", points)

    # ------------------------------------------------------------------
    # VectorStore Protocol — search
    # ------------------------------------------------------------------

    def search(
        self,
        vector: list[float],
        filter_conditions: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Read-local-first with shared fallback (REQ-HM-005).

        Searches local first. If local returns zero results AND shared
        is healthy, falls back to shared store.
        """
        results = self._local.search(vector, filter_conditions, limit)

        if not results and self._shared_healthy:
            try:
                results = self._shared.search(vector, filter_conditions, limit)
            except Exception as e:
                logger.warning("Shared search fallback failed: %s", e)
                results = []

        return results

    # ------------------------------------------------------------------
    # VectorStore Protocol — delete_by_file
    # ------------------------------------------------------------------

    def delete_by_file(self, repo: str, branch: str, file_path: str) -> None:
        """Delete from both stores (REQ-HY-006)."""
        self._local.delete_by_file(repo, branch, file_path)
        self._shared_write("delete_by_file", repo, branch, file_path)

    # ------------------------------------------------------------------
    # VectorStore Protocol — rename_file
    # ------------------------------------------------------------------

    def rename_file(
        self, repo: str, branch: str, old_path: str, new_path: str
    ) -> None:
        """Rename on both stores."""
        self._local.rename_file(repo, branch, old_path, new_path)
        self._shared_write("rename_file", repo, branch, old_path, new_path)

    # ------------------------------------------------------------------
    # VectorStore Protocol — delete_collection
    # ------------------------------------------------------------------

    def delete_collection(self) -> None:
        """Delete collection from both stores (REQ-HM-007).

        Local deletion must succeed. Shared failure is logged but
        does not fail the operation.
        """
        self._local.delete_collection()
        self._shared_write("delete_collection")

    # ------------------------------------------------------------------
    # VectorStore Protocol — count
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Return local count — local is authoritative (REQ-HM-006)."""
        return self._local.count()

    # ------------------------------------------------------------------
    # VectorStore Protocol — scroll_all
    # ------------------------------------------------------------------

    def scroll_all(self, repo: str, branch: str) -> list[VectorPoint]:
        """Scroll from local store — local is primary (REQ-HY-007)."""
        return self._local.scroll_all(repo, branch)

    # ------------------------------------------------------------------
    # Health status
    # ------------------------------------------------------------------

    def health_status(self) -> dict[str, Any]:
        """Report health of both backends (REQ-HY-005).

        Returns a dict with local/shared health and retry queue size.
        """
        return {
            "local": True,  # local is always available (embedded)
            "shared": self._shared_healthy,
            "retry_queue_size": len(self._retry_queue),
            "mode": "hybrid",
        }
