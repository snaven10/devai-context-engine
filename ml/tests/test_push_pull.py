"""Tests for push_index, pull_index, and sync_index handlers in MLService.

Mocks the MLService internals (vector_store, embedding, storage config)
so no real LanceDB or Qdrant is needed.  Covers: push/pull/sync happy paths,
error on local-only mode, repo+branch filtering, empty stores, conflict
resolution (last-write-wins by indexed_at), and additive-only sync.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from devai_ml.stores.vector_store import VectorPoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_point(
    pid: str = "p1",
    dim: int = 4,
    repo: str = "my-repo",
    branch: str = "main",
    content_hash: str = "hash1",
    indexed_at: str = "2025-06-01T00:00:00Z",
    text: str = "hello",
) -> VectorPoint:
    return VectorPoint(
        id=pid,
        vector=[0.1] * dim,
        metadata={
            "repo": repo,
            "branch": branch,
            "commit": "deadbeef",
            "file": "src/foo.py",
            "symbol": "func",
            "symbol_type": "function",
            "language": "python",
            "start_line": 1,
            "end_line": 10,
            "chunk_level": "symbol",
            "content_hash": content_hash,
            "is_deletion": False,
            "memory_type": "",
            "memory_scope": "",
            "memory_tags": "",
            "indexed_at": indexed_at,
        },
        text=text,
    )


def _make_service(
    *,
    local_store: MagicMock | None = None,
    embedding: MagicMock | None = None,
) -> MagicMock:
    """Build a minimal MLService mock with the real handler methods attached.

    We import the unbound methods from the MLService class and bind them
    to our mock so the actual handler logic executes against mocked stores.
    """
    from devai_ml.server import MLService

    svc = MagicMock(spec=MLService)
    svc._vector_store = local_store or MagicMock()
    svc._embedding = embedding or MagicMock()
    svc._embedding.dimension.return_value = 4

    # Bind real handler methods so business logic runs for real
    svc._handle_push_index = MLService._handle_push_index.__get__(svc, MLService)
    svc._handle_pull_index = MLService._handle_pull_index.__get__(svc, MLService)
    svc._handle_sync_index = MLService._handle_sync_index.__get__(svc, MLService)

    return svc


def _storage_config(mode: str = "hybrid"):
    """Create a StorageConfig with the given mode."""
    from devai_ml.stores.factory import StorageConfig

    return StorageConfig(
        mode=mode,
        local_db_path="/tmp/test-lance",
        qdrant_url="localhost:6334",
        qdrant_api_key=None,
        collection_name="devai_test",
        dimension=4,
    )


@contextmanager
def _patch_handler_deps(storage_mode: str, qdrant_instance: MagicMock):
    """Patch the lazy imports used inside push/pull/sync handlers.

    The handlers do:
        from .stores.qdrant_store import QdrantVectorStore
        from .stores.factory import _parse_qdrant_url

    After the first import, these become attributes on the devai_ml.server
    module. But before that they don't exist, so we use create=True.

    We also patch create_storage_config_from_env which is a top-level import
    in the server module.
    """
    config = _storage_config(storage_mode)
    qdrant_cls = MagicMock(return_value=qdrant_instance)

    with patch("devai_ml.server.create_storage_config_from_env", return_value=config), \
         patch("devai_ml.stores.qdrant_store.QdrantVectorStore", qdrant_cls), \
         patch("devai_ml.stores.factory._parse_qdrant_url", return_value=("localhost", 6334)):
        yield


# ---------------------------------------------------------------------------
# test_push_index
# ---------------------------------------------------------------------------


class TestPushIndex:
    def test_push_reads_local_writes_shared(self):
        """Push should scroll_all from local store and upsert to shared (Qdrant)."""
        local = MagicMock()
        points = [_make_point("p1"), _make_point("p2")]
        local.scroll_all.return_value = points
        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()

        with _patch_handler_deps("shared", qdrant_instance):
            result = svc._handle_push_index({"repo": "my-repo", "branch": "main"})

        local.scroll_all.assert_called_once_with("my-repo", "main")
        qdrant_instance.upsert.assert_called_once_with(points)
        assert result["pushed"] == 2
        assert result["errors"] == 0
        assert result["repo"] == "my-repo"
        assert result["branch"] == "main"

    def test_push_requires_shared_store(self):
        """Push must fail when DEVAI_STORAGE_MODE is 'local'."""
        svc = _make_service()
        qdrant_instance = MagicMock()

        with _patch_handler_deps("local", qdrant_instance):
            with pytest.raises(ValueError, match="Cannot push.*local"):
                svc._handle_push_index({"repo": "my-repo"})

    def test_push_requires_repo_param(self):
        """Push without repo parameter raises ValueError."""
        svc = _make_service()

        with pytest.raises(ValueError, match="requires 'repo'"):
            svc._handle_push_index({})

    def test_push_with_branch_filter(self):
        """Push should pass branch to scroll_all for filtering."""
        local = MagicMock()
        local.scroll_all.return_value = [_make_point(branch="feature")]
        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()

        with _patch_handler_deps("shared", qdrant_instance):
            result = svc._handle_push_index({"repo": "my-repo", "branch": "feature"})

        local.scroll_all.assert_called_once_with("my-repo", "feature")
        assert result["branch"] == "feature"

    def test_push_empty_local_store(self):
        """Push with no local vectors should return pushed=0 gracefully."""
        local = MagicMock()
        local.scroll_all.return_value = []
        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()

        with _patch_handler_deps("shared", qdrant_instance):
            result = svc._handle_push_index({"repo": "my-repo"})

        assert result["pushed"] == 0
        assert result["total_local"] == 0
        qdrant_instance.upsert.assert_not_called()

    def test_push_reports_count(self):
        """Push result should include total_local count matching pushed."""
        local = MagicMock()
        local.scroll_all.return_value = [_make_point(f"p{i}") for i in range(5)]
        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()

        with _patch_handler_deps("shared", qdrant_instance):
            result = svc._handle_push_index({"repo": "my-repo"})

        assert result["pushed"] == 5
        assert result["total_local"] == 5

    def test_push_handles_upsert_error(self):
        """If Qdrant upsert fails, errors should be counted."""
        local = MagicMock()
        local.scroll_all.return_value = [_make_point("p1")]
        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.upsert.side_effect = RuntimeError("connection refused")

        with _patch_handler_deps("shared", qdrant_instance):
            result = svc._handle_push_index({"repo": "my-repo"})

        assert result["errors"] == 1
        assert result["pushed"] == 0


# ---------------------------------------------------------------------------
# test_pull_index
# ---------------------------------------------------------------------------


class TestPullIndex:
    def test_pull_reads_shared_writes_local(self):
        """Pull should scroll_all from Qdrant and upsert to local store."""
        local = MagicMock()
        points = [_make_point("p1"), _make_point("p2")]
        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = points

        with _patch_handler_deps("shared", qdrant_instance):
            result = svc._handle_pull_index({"repo": "my-repo", "branch": "main"})

        qdrant_instance.scroll_all.assert_called_once_with("my-repo", "main")
        local.upsert.assert_called_once_with(points)
        assert result["pulled"] == 2
        assert result["errors"] == 0

    def test_pull_requires_shared_store(self):
        """Pull must fail when DEVAI_STORAGE_MODE is 'local'."""
        svc = _make_service()
        qdrant_instance = MagicMock()

        with _patch_handler_deps("local", qdrant_instance):
            with pytest.raises(ValueError, match="Cannot pull.*local"):
                svc._handle_pull_index({"repo": "my-repo"})

    def test_pull_requires_repo_param(self):
        """Pull without repo parameter raises ValueError."""
        svc = _make_service()

        with pytest.raises(ValueError, match="requires 'repo'"):
            svc._handle_pull_index({})

    def test_pull_with_branch_filter(self):
        """Pull should pass branch to scroll_all for filtering."""
        local = MagicMock()
        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = [_make_point(branch="develop")]

        with _patch_handler_deps("shared", qdrant_instance):
            result = svc._handle_pull_index({"repo": "my-repo", "branch": "develop"})

        qdrant_instance.scroll_all.assert_called_once_with("my-repo", "develop")
        assert result["branch"] == "develop"

    def test_pull_empty_shared_store(self):
        """Pull with no shared vectors should return pulled=0 gracefully."""
        local = MagicMock()
        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = []

        with _patch_handler_deps("shared", qdrant_instance):
            result = svc._handle_pull_index({"repo": "my-repo"})

        assert result["pulled"] == 0
        assert result["total_remote"] == 0
        local.upsert.assert_not_called()

    def test_pull_reports_count(self):
        """Pull result should include total_remote count matching pulled."""
        local = MagicMock()
        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = [_make_point(f"p{i}") for i in range(7)]

        with _patch_handler_deps("shared", qdrant_instance):
            result = svc._handle_pull_index({"repo": "my-repo"})

        assert result["pulled"] == 7
        assert result["total_remote"] == 7

    def test_pull_handles_upsert_error(self):
        """If local upsert fails, errors should be counted."""
        local = MagicMock()
        local.upsert.side_effect = RuntimeError("disk full")
        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = [_make_point("p1")]

        with _patch_handler_deps("shared", qdrant_instance):
            result = svc._handle_pull_index({"repo": "my-repo"})

        assert result["errors"] == 1
        assert result["pulled"] == 0


# ---------------------------------------------------------------------------
# test_sync_index
# ---------------------------------------------------------------------------


class TestSyncIndex:
    def test_sync_bidirectional_merge(self):
        """Sync pushes local-only points to shared and pulls shared-only to local."""
        local = MagicMock()
        local_point = _make_point("p1", content_hash="h1")
        shared_point = _make_point("p2", content_hash="h2")
        local.scroll_all.return_value = [local_point]

        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = [shared_point]

        with _patch_handler_deps("hybrid", qdrant_instance):
            result = svc._handle_sync_index({"repo": "my-repo", "branch": "main"})

        # p1 pushed to shared
        qdrant_instance.upsert.assert_called_once()
        pushed_points = qdrant_instance.upsert.call_args[0][0]
        assert len(pushed_points) == 1
        assert pushed_points[0].id == "p1"

        # p2 pulled to local
        local.upsert.assert_called_once()
        pulled_points = local.upsert.call_args[0][0]
        assert len(pulled_points) == 1
        assert pulled_points[0].id == "p2"

        assert result["pushed"] == 1
        assert result["pulled"] == 1

    def test_sync_conflict_resolution_last_write_wins(self):
        """When same ID exists in both with different hashes, newer indexed_at wins."""
        local = MagicMock()
        local_point = _make_point(
            "conflict-1", content_hash="old-hash", indexed_at="2025-01-01T00:00:00Z",
        )
        shared_point = _make_point(
            "conflict-1", content_hash="new-hash", indexed_at="2025-06-01T00:00:00Z",
        )
        local.scroll_all.return_value = [local_point]

        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = [shared_point]

        with _patch_handler_deps("hybrid", qdrant_instance):
            result = svc._handle_sync_index({"repo": "my-repo"})

        # Shared is newer -> pulled to local
        local.upsert.assert_called_once()
        pulled_point = local.upsert.call_args[0][0][0]
        assert pulled_point.metadata["content_hash"] == "new-hash"

        # Nothing pushed to shared
        qdrant_instance.upsert.assert_not_called()

        assert result["conflicts"] == 1
        assert result["pulled"] == 1
        assert result["pushed"] == 0
        assert result["resolution"] == "last-write-wins"

    def test_sync_conflict_local_wins_when_newer(self):
        """When local has a newer indexed_at, it pushes to shared."""
        local = MagicMock()
        local_point = _make_point(
            "conflict-1", content_hash="new-hash", indexed_at="2025-06-01T00:00:00Z",
        )
        shared_point = _make_point(
            "conflict-1", content_hash="old-hash", indexed_at="2025-01-01T00:00:00Z",
        )
        local.scroll_all.return_value = [local_point]

        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = [shared_point]

        with _patch_handler_deps("hybrid", qdrant_instance):
            result = svc._handle_sync_index({"repo": "my-repo"})

        # Local is newer -> pushed to shared
        qdrant_instance.upsert.assert_called_once()
        pushed_point = qdrant_instance.upsert.call_args[0][0][0]
        assert pushed_point.metadata["content_hash"] == "new-hash"

        assert result["conflicts"] == 1
        assert result["pushed"] == 1
        assert result["pulled"] == 0

    def test_sync_requires_shared_store(self):
        """Sync must fail when DEVAI_STORAGE_MODE is 'local'."""
        svc = _make_service()
        qdrant_instance = MagicMock()

        with _patch_handler_deps("local", qdrant_instance):
            with pytest.raises(ValueError, match="Cannot sync.*local"):
                svc._handle_sync_index({"repo": "my-repo"})

    def test_sync_requires_repo_param(self):
        """Sync without repo parameter raises ValueError."""
        svc = _make_service()

        with pytest.raises(ValueError, match="requires 'repo'"):
            svc._handle_sync_index({})

    def test_sync_reports_counts_both_directions(self):
        """Sync result should include pushed, pulled, and conflicts counts."""
        local = MagicMock()
        local.scroll_all.return_value = [
            _make_point("local-only-1"),
            _make_point("local-only-2"),
        ]

        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = [
            _make_point("shared-only-1"),
            _make_point("shared-only-2"),
            _make_point("shared-only-3"),
        ]

        with _patch_handler_deps("hybrid", qdrant_instance):
            result = svc._handle_sync_index({"repo": "my-repo"})

        assert result["pushed"] == 2
        assert result["pulled"] == 3
        assert result["conflicts"] == 0

    def test_sync_is_additive_only(self):
        """Sync should never call delete methods — it only upserts."""
        local = MagicMock()
        local.scroll_all.return_value = [_make_point("p1")]

        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = [_make_point("p2")]

        with _patch_handler_deps("hybrid", qdrant_instance):
            svc._handle_sync_index({"repo": "my-repo"})

        # Verify no delete calls on either store
        local.delete_by_file.assert_not_called()
        local.delete_collection.assert_not_called()
        qdrant_instance.delete_by_file.assert_not_called()
        qdrant_instance.delete_collection.assert_not_called()

    def test_sync_same_content_hash_no_conflict(self):
        """Points with same ID and same content_hash should not count as conflicts."""
        local = MagicMock()
        local_point = _make_point("same-1", content_hash="identical")
        shared_point = _make_point("same-1", content_hash="identical")
        local.scroll_all.return_value = [local_point]

        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = [shared_point]

        with _patch_handler_deps("hybrid", qdrant_instance):
            result = svc._handle_sync_index({"repo": "my-repo"})

        assert result["conflicts"] == 0
        assert result["pushed"] == 0
        assert result["pulled"] == 0
        local.upsert.assert_not_called()
        qdrant_instance.upsert.assert_not_called()

    def test_sync_empty_both_stores(self):
        """Sync with empty stores on both sides should be a no-op."""
        local = MagicMock()
        local.scroll_all.return_value = []

        svc = _make_service(local_store=local)

        qdrant_instance = MagicMock()
        qdrant_instance.scroll_all.return_value = []

        with _patch_handler_deps("hybrid", qdrant_instance):
            result = svc._handle_sync_index({"repo": "my-repo"})

        assert result["pushed"] == 0
        assert result["pulled"] == 0
        assert result["conflicts"] == 0
