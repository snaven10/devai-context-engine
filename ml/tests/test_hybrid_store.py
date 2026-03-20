"""Tests for HybridVectorStore with mocked local + shared backends.

Covers: write-through, read-local-first, graceful degradation, retry queue,
flush_retry_queue, health_status, search fallback, delete_collection.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from devai_ml.stores.hybrid_store import HybridVectorStore
from devai_ml.stores.vector_store import SearchResult, VectorPoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_point(pid: str = "p1", dim: int = 4) -> VectorPoint:
    return VectorPoint(
        id=pid,
        vector=[0.1] * dim,
        metadata={"repo": "r", "branch": "main", "file": "f.py"},
        text="test text",
    )


def _make_search_result(rid: str = "r1") -> SearchResult:
    return SearchResult(id=rid, score=0.9, metadata={}, text="result text")


def _make_hybrid(
    *,
    shared_healthy: bool = True,
    local: MagicMock | None = None,
    shared: MagicMock | None = None,
    max_retry_queue: int = 10_000,
) -> HybridVectorStore:
    """Create a HybridVectorStore with mocked backends, bypassing health thread."""
    local = local or MagicMock()
    shared = shared or MagicMock()
    shared.health_check.return_value = shared_healthy

    # Patch the health loop thread so it doesn't run in background
    with patch.object(HybridVectorStore, "_health_loop"):
        store = HybridVectorStore(
            local=local,
            shared=shared,
            max_retry_queue=max_retry_queue,
            health_interval=9999,  # effectively never
        )
    return store


# ---------------------------------------------------------------------------
# Write-through
# ---------------------------------------------------------------------------


class TestWriteThrough:
    def test_upsert_calls_both_stores(self):
        local = MagicMock()
        shared = MagicMock()
        store = _make_hybrid(local=local, shared=shared)

        points = [_make_point()]
        store.upsert(points)

        local.upsert.assert_called_once_with(points)
        shared.upsert.assert_called_once_with(points)

    def test_delete_by_file_calls_both(self):
        local = MagicMock()
        shared = MagicMock()
        store = _make_hybrid(local=local, shared=shared)

        store.delete_by_file("r", "main", "f.py")

        local.delete_by_file.assert_called_once_with("r", "main", "f.py")
        shared.delete_by_file.assert_called_once_with("r", "main", "f.py")

    def test_rename_file_calls_both(self):
        local = MagicMock()
        shared = MagicMock()
        store = _make_hybrid(local=local, shared=shared)

        store.rename_file("r", "main", "old.py", "new.py")

        local.rename_file.assert_called_once_with("r", "main", "old.py", "new.py")
        shared.rename_file.assert_called_once_with("r", "main", "old.py", "new.py")

    def test_delete_collection_calls_both(self):
        local = MagicMock()
        shared = MagicMock()
        store = _make_hybrid(local=local, shared=shared)

        store.delete_collection()

        local.delete_collection.assert_called_once()
        shared.delete_collection.assert_called_once()


# ---------------------------------------------------------------------------
# Read-local-first
# ---------------------------------------------------------------------------


class TestReadLocalFirst:
    def test_search_returns_local_results(self):
        local = MagicMock()
        shared = MagicMock()
        local_results = [_make_search_result("local-1")]
        local.search.return_value = local_results
        store = _make_hybrid(local=local, shared=shared)

        results = store.search([0.1] * 4, limit=5)

        assert results == local_results
        shared.search.assert_not_called()

    def test_search_fallback_to_shared_when_local_empty(self):
        local = MagicMock()
        shared = MagicMock()
        local.search.return_value = []
        shared_results = [_make_search_result("shared-1")]
        shared.search.return_value = shared_results
        store = _make_hybrid(local=local, shared=shared, shared_healthy=True)

        results = store.search([0.1] * 4, limit=5)

        assert results == shared_results
        shared.search.assert_called_once()

    def test_search_no_fallback_when_shared_unhealthy(self):
        local = MagicMock()
        shared = MagicMock()
        local.search.return_value = []
        store = _make_hybrid(local=local, shared=shared, shared_healthy=False)

        results = store.search([0.1] * 4)

        assert results == []
        shared.search.assert_not_called()

    def test_search_fallback_error_returns_empty(self):
        local = MagicMock()
        shared = MagicMock()
        local.search.return_value = []
        shared.search.side_effect = RuntimeError("timeout")
        store = _make_hybrid(local=local, shared=shared, shared_healthy=True)

        results = store.search([0.1] * 4)

        assert results == []

    def test_count_returns_local(self):
        local = MagicMock()
        shared = MagicMock()
        local.count.return_value = 500
        shared.count.return_value = 600
        store = _make_hybrid(local=local, shared=shared)

        assert store.count() == 500

    def test_scroll_all_returns_local(self):
        local = MagicMock()
        shared = MagicMock()
        local_points = [_make_point("p1")]
        local.scroll_all.return_value = local_points
        store = _make_hybrid(local=local, shared=shared)

        results = store.scroll_all("r", "main")

        assert results == local_points
        shared.scroll_all.assert_not_called()


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_shared_failure_upsert_succeeds(self):
        local = MagicMock()
        shared = MagicMock()
        shared.upsert.side_effect = RuntimeError("connection refused")
        store = _make_hybrid(local=local, shared=shared)

        points = [_make_point()]
        store.upsert(points)  # Should NOT raise

        local.upsert.assert_called_once_with(points)

    def test_shared_failure_queues_retry(self):
        local = MagicMock()
        shared = MagicMock()
        shared.upsert.side_effect = RuntimeError("connection refused")
        store = _make_hybrid(local=local, shared=shared)

        store.upsert([_make_point()])

        assert len(store._retry_queue) == 1
        method_name, args, kwargs = store._retry_queue[0]
        assert method_name == "upsert"

    def test_unhealthy_shared_skips_write_and_queues(self):
        local = MagicMock()
        shared = MagicMock()
        store = _make_hybrid(local=local, shared=shared, shared_healthy=False)

        store.upsert([_make_point()])

        # Shared write should be SKIPPED (not attempted)
        shared.upsert.assert_not_called()
        # But should be queued
        assert len(store._retry_queue) == 1

    def test_delete_collection_shared_failure_succeeds(self):
        local = MagicMock()
        shared = MagicMock()
        shared.delete_collection.side_effect = RuntimeError("oops")
        store = _make_hybrid(local=local, shared=shared)

        store.delete_collection()  # Should NOT raise

        local.delete_collection.assert_called_once()


# ---------------------------------------------------------------------------
# Retry queue
# ---------------------------------------------------------------------------


class TestRetryQueue:
    def test_queue_operations_on_failure(self):
        local = MagicMock()
        shared = MagicMock()
        shared.upsert.side_effect = RuntimeError("fail")
        shared.delete_by_file.side_effect = RuntimeError("fail")
        store = _make_hybrid(local=local, shared=shared)

        store.upsert([_make_point()])
        store.delete_by_file("r", "main", "f.py")

        assert len(store._retry_queue) == 2

    def test_queue_overflow_drops_oldest(self):
        local = MagicMock()
        shared = MagicMock()
        store = _make_hybrid(
            local=local, shared=shared, shared_healthy=False, max_retry_queue=3
        )

        for i in range(5):
            store.upsert([_make_point(pid=f"p{i}")])

        # deque maxlen=3 keeps only the last 3
        assert len(store._retry_queue) == 3

    def test_flush_replays_queued_ops(self):
        local = MagicMock()
        shared = MagicMock()
        store = _make_hybrid(local=local, shared=shared, shared_healthy=False)

        # Queue some ops
        store.upsert([_make_point("p1")])
        store.upsert([_make_point("p2")])
        assert len(store._retry_queue) == 2

        # Now mark shared as healthy and flush
        store._shared_healthy = True
        shared.upsert.reset_mock()
        store.flush_retry_queue()

        assert shared.upsert.call_count == 2
        assert len(store._retry_queue) == 0

    def test_flush_stops_on_first_failure_requeues_rest(self):
        local = MagicMock()
        shared = MagicMock()
        store = _make_hybrid(local=local, shared=shared, shared_healthy=False)

        # Queue 3 operations
        store.upsert([_make_point("p1")])
        store.upsert([_make_point("p2")])
        store.upsert([_make_point("p3")])
        assert len(store._retry_queue) == 3

        # First replay succeeds, second fails
        call_count = 0

        def fail_on_second(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("fail again")

        shared.upsert.side_effect = fail_on_second
        store.flush_retry_queue()

        # 2 remaining ops should be re-queued (the failed one + the 3rd)
        assert len(store._retry_queue) == 2


# ---------------------------------------------------------------------------
# Health status
# ---------------------------------------------------------------------------


class TestHealthStatus:
    def test_healthy_status(self):
        store = _make_hybrid(shared_healthy=True)
        status = store.health_status()
        assert status["local"] is True
        assert status["shared"] is True
        assert status["retry_queue_size"] == 0
        assert status["mode"] == "hybrid"

    def test_unhealthy_status(self):
        store = _make_hybrid(shared_healthy=False)
        status = store.health_status()
        assert status["shared"] is False

    def test_status_includes_queue_size(self):
        shared = MagicMock()
        store = _make_hybrid(shared=shared, shared_healthy=False)
        store.upsert([_make_point()])
        status = store.health_status()
        assert status["retry_queue_size"] == 1


# ---------------------------------------------------------------------------
# Health check transitions
# ---------------------------------------------------------------------------


class TestHealthCheckTransitions:
    def test_recovery_flushes_queue(self):
        local = MagicMock()
        shared = MagicMock()
        store = _make_hybrid(local=local, shared=shared, shared_healthy=False)

        # Queue an op while unhealthy
        store.upsert([_make_point("p1")])
        assert len(store._retry_queue) == 1

        # Simulate Qdrant coming back
        shared.health_check.return_value = True
        shared.upsert.reset_mock()
        store._perform_health_check()

        assert store._shared_healthy is True
        # Queue should have been flushed
        assert shared.upsert.call_count == 1
        assert len(store._retry_queue) == 0

    def test_degradation_sets_flag(self):
        local = MagicMock()
        shared = MagicMock()
        store = _make_hybrid(local=local, shared=shared, shared_healthy=True)

        # Simulate Qdrant going down
        shared.health_check.return_value = False
        store._perform_health_check()

        assert store._shared_healthy is False
