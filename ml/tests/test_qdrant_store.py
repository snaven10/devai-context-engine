"""Integration tests for QdrantVectorStore against a real Qdrant instance.

Requires Qdrant running on localhost:6334 (docker compose up -d qdrant).
Covers: upsert, search, delete_by_file, rename_file, delete_collection,
count, scroll_all, health_check, collection auto-creation, filter
translation, UUID5 ID mapping, and error handling.
"""

from __future__ import annotations

import uuid

import pytest
from qdrant_client import QdrantClient

from devai_ml.stores.qdrant_store import (
    BATCH_SIZE,
    DEVAI_UUID_NAMESPACE,
    PAYLOAD_FIELDS,
    QdrantVectorStore,
)
from devai_ml.stores.vector_store import SearchResult, VectorPoint

QDRANT_URL = "localhost"
QDRANT_PORT = 6334
DIM = 4
TEST_COLLECTION = "devai_test_integration"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_collection():
    """Delete test collection before and after each test."""
    client = QdrantClient(url=QDRANT_URL, port=QDRANT_PORT, prefer_grpc=True)
    try:
        client.delete_collection(TEST_COLLECTION)
    except Exception:
        pass
    yield
    try:
        client.delete_collection(TEST_COLLECTION)
    except Exception:
        pass


def _make_store(dim: int = DIM) -> QdrantVectorStore:
    return QdrantVectorStore(
        url=QDRANT_URL,
        port=QDRANT_PORT,
        collection_name=TEST_COLLECTION,
        dimension=dim,
    )


def _make_point(
    pid: str = "abc123",
    dim: int = DIM,
    repo: str = "my-repo",
    branch: str = "main",
    file: str = "src/foo.py",
    text: str = "hello world",
) -> VectorPoint:
    return VectorPoint(
        id=pid,
        vector=[0.1] * dim,
        metadata={
            "repo": repo,
            "branch": branch,
            "commit": "deadbeef",
            "file": file,
            "symbol": "func",
            "symbol_type": "function",
            "language": "python",
            "start_line": 1,
            "end_line": 10,
            "chunk_level": "symbol",
            "content_hash": "ch123",
            "is_deletion": False,
            "memory_type": "",
            "memory_scope": "",
            "memory_tags": "",
            "indexed_at": "2025-01-01T00:00:00Z",
        },
        text=text,
    )


# ---------------------------------------------------------------------------
# UUID5 ID mapping
# ---------------------------------------------------------------------------


class TestLanceIdToUuid:
    def test_deterministic(self):
        id1 = QdrantVectorStore.lance_id_to_uuid("abc123")
        id2 = QdrantVectorStore.lance_id_to_uuid("abc123")
        assert id1 == id2

    def test_different_ids_differ(self):
        id1 = QdrantVectorStore.lance_id_to_uuid("abc123")
        id2 = QdrantVectorStore.lance_id_to_uuid("def456")
        assert id1 != id2

    def test_returns_valid_uuid(self):
        result = QdrantVectorStore.lance_id_to_uuid("test-id")
        parsed = uuid.UUID(result)
        assert parsed.version == 5

    def test_uses_devai_namespace(self):
        expected = str(uuid.uuid5(DEVAI_UUID_NAMESPACE, "test-id"))
        assert QdrantVectorStore.lance_id_to_uuid("test-id") == expected


# ---------------------------------------------------------------------------
# Collection auto-creation
# ---------------------------------------------------------------------------


class TestCollectionAutoCreation:
    def test_creates_collection_on_first_upsert(self):
        store = _make_store()
        client = QdrantClient(url=QDRANT_URL, port=QDRANT_PORT, prefer_grpc=True)

        assert not client.collection_exists(TEST_COLLECTION)
        store.upsert([_make_point()])
        assert client.collection_exists(TEST_COLLECTION)

    def test_skips_creation_if_exists(self):
        """Upserting twice doesn't fail — collection already exists on second call."""
        store = _make_store()
        store.upsert([_make_point(pid="p1")])
        store.upsert([_make_point(pid="p2")])
        assert store.count() == 2

    def test_dimension_mismatch_raises(self):
        store_dim4 = _make_store(dim=4)
        store_dim4.upsert([_make_point(dim=4)])

        store_dim8 = QdrantVectorStore(
            url=QDRANT_URL,
            port=QDRANT_PORT,
            collection_name=TEST_COLLECTION,
            dimension=8,
        )
        with pytest.raises(ValueError, match="4.*8"):
            store_dim8.upsert([_make_point(dim=8)])


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


class TestUpsert:
    def test_upsert_empty_list_noop(self):
        store = _make_store()
        store.upsert([])
        assert store.count() == 0

    def test_upsert_persists_points(self):
        store = _make_store()
        store.upsert([_make_point(pid="p1"), _make_point(pid="p2")])
        assert store.count() == 2

    def test_upsert_is_idempotent(self):
        """Upserting the same point twice should not create duplicates."""
        store = _make_store()
        point = _make_point(pid="same-id")
        store.upsert([point])
        store.upsert([point])
        assert store.count() == 1

    def test_upsert_uuid5_id(self):
        """Points are stored with UUID5-mapped IDs."""
        store = _make_store()
        store.upsert([_make_point(pid="myid")])

        expected_uuid = QdrantVectorStore.lance_id_to_uuid("myid")
        client = QdrantClient(url=QDRANT_URL, port=QDRANT_PORT, prefer_grpc=True)
        results, _ = client.scroll(
            collection_name=TEST_COLLECTION, limit=10, with_payload=True
        )
        assert len(results) == 1
        assert str(results[0].id) == expected_uuid

    def test_upsert_payload_contains_lance_id_and_text(self):
        store = _make_store()
        store.upsert([_make_point(pid="myid", text="hello")])

        client = QdrantClient(url=QDRANT_URL, port=QDRANT_PORT, prefer_grpc=True)
        results, _ = client.scroll(
            collection_name=TEST_COLLECTION, limit=10, with_payload=True
        )
        payload = results[0].payload
        assert payload["_lance_id"] == "myid"
        assert payload["_text"] == "hello"

    def test_upsert_payload_has_all_metadata_fields(self):
        store = _make_store()
        store.upsert([_make_point()])

        client = QdrantClient(url=QDRANT_URL, port=QDRANT_PORT, prefer_grpc=True)
        results, _ = client.scroll(
            collection_name=TEST_COLLECTION, limit=10, with_payload=True
        )
        payload = results[0].payload
        for field in PAYLOAD_FIELDS:
            assert field in payload, f"Missing payload field: {field}"

    def test_upsert_batching(self):
        """Points beyond BATCH_SIZE are split into multiple batches transparently."""
        store = _make_store()
        n = BATCH_SIZE + 5
        points = [_make_point(pid=f"id-{i}") for i in range(n)]
        store.upsert(points)
        assert store.count() == n


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_returns_results_with_lance_id(self):
        store = _make_store()
        store.upsert([_make_point(pid="original-id", text="hello")])

        results = store.search([0.1] * DIM, limit=5)

        assert len(results) == 1
        assert results[0].id == "original-id"
        assert results[0].text == "hello"
        assert results[0].score > 0

    def test_search_with_scalar_filter(self):
        store = _make_store()
        store.upsert([
            _make_point(pid="p1", repo="repo-a", branch="main"),
            _make_point(pid="p2", repo="repo-b", branch="main"),
        ])

        results = store.search(
            [0.1] * DIM,
            filter_conditions={"repo": "repo-a"},
            limit=10,
        )

        assert len(results) == 1
        assert results[0].id == "p1"

    def test_search_with_list_filter(self):
        store = _make_store()
        store.upsert([
            _make_point(pid="py1", file="a.py"),
            _make_point(pid="go1", file="b.go"),
            _make_point(pid="rs1", file="c.rs"),
        ])

        results = store.search(
            [0.1] * DIM,
            filter_conditions={"file": ["a.py", "b.go"]},
            limit=10,
        )

        ids = {r.id for r in results}
        assert ids == {"py1", "go1"}

    def test_search_no_collection_returns_empty(self):
        store = _make_store()
        # Don't upsert — collection doesn't exist
        results = store.search([0.1] * DIM)
        assert results == []


# ---------------------------------------------------------------------------
# delete_by_file
# ---------------------------------------------------------------------------


class TestDeleteByFile:
    def test_delete_by_file_removes_matching(self):
        store = _make_store()
        store.upsert([
            _make_point(pid="p1", file="src/foo.py"),
            _make_point(pid="p2", file="src/bar.py"),
        ])
        assert store.count() == 2

        store.delete_by_file("my-repo", "main", "src/foo.py")

        assert store.count() == 1
        results = store.search([0.1] * DIM, filter_conditions={"file": "src/bar.py"})
        assert len(results) == 1
        assert results[0].id == "p2"

    def test_delete_by_file_no_collection_noop(self):
        store = _make_store()
        # Should not raise
        store.delete_by_file("my-repo", "main", "src/foo.py")

    def test_delete_by_file_no_match_noop(self):
        store = _make_store()
        store.upsert([_make_point(pid="p1", file="src/foo.py")])
        store.delete_by_file("my-repo", "main", "nonexistent.py")
        assert store.count() == 1


# ---------------------------------------------------------------------------
# scroll_all (pagination)
# ---------------------------------------------------------------------------


class TestScrollAll:
    def test_scroll_all_returns_vector_points(self):
        store = _make_store()
        store.upsert([_make_point(pid="lance-1", text="text1")])

        results = store.scroll_all("my-repo", "main")

        assert len(results) == 1
        assert results[0].id == "lance-1"
        assert results[0].text == "text1"
        assert len(results[0].vector) == DIM

    def test_scroll_all_filters_by_repo_branch(self):
        store = _make_store()
        store.upsert([
            _make_point(pid="p1", repo="repo-a", branch="main"),
            _make_point(pid="p2", repo="repo-a", branch="dev"),
            _make_point(pid="p3", repo="repo-b", branch="main"),
        ])

        results = store.scroll_all("repo-a", "main")

        assert len(results) == 1
        assert results[0].id == "p1"

    def test_scroll_all_no_collection_returns_empty(self):
        store = _make_store()
        assert store.scroll_all("r", "main") == []

    def test_scroll_all_empty_collection_returns_empty(self):
        store = _make_store()
        store.upsert([_make_point(pid="p1")])
        # Scroll for a different repo
        assert store.scroll_all("nonexistent-repo", "main") == []


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_healthy(self):
        store = _make_store()
        assert store.health_check() is True

    def test_unhealthy(self):
        """Use a non-routable IP to guarantee connection failure."""
        store = QdrantVectorStore(
            url="192.0.2.1",  # TEST-NET-1, RFC 5737 — guaranteed unreachable
            port=6334,
            collection_name="doesnt_matter",
            dimension=DIM,
            timeout=2,
        )
        assert store.health_check() is False


# ---------------------------------------------------------------------------
# rename_file
# ---------------------------------------------------------------------------


class TestRenameFile:
    def test_rename_updates_payload(self):
        store = _make_store()
        store.upsert([_make_point(pid="p1", file="old.py")])

        store.rename_file("my-repo", "main", "old.py", "new.py")

        results = store.search([0.1] * DIM, filter_conditions={"file": "new.py"})
        assert len(results) == 1
        assert results[0].id == "p1"

    def test_rename_no_matching_points_noop(self):
        store = _make_store()
        store.upsert([_make_point(pid="p1", file="keep.py")])
        store.rename_file("my-repo", "main", "nonexistent.py", "new.py")
        assert store.count() == 1

    def test_rename_no_collection_noop(self):
        store = _make_store()
        # Should not raise
        store.rename_file("r", "main", "old.py", "new.py")


# ---------------------------------------------------------------------------
# delete_collection
# ---------------------------------------------------------------------------


class TestDeleteCollection:
    def test_delete_removes_collection(self):
        store = _make_store()
        store.upsert([_make_point()])

        client = QdrantClient(url=QDRANT_URL, port=QDRANT_PORT, prefer_grpc=True)
        assert client.collection_exists(TEST_COLLECTION)

        store.delete_collection()

        assert not client.collection_exists(TEST_COLLECTION)

    def test_delete_nonexistent_no_error(self):
        store = _make_store()
        # Should not raise
        store.delete_collection()


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------


class TestCount:
    def test_count_returns_value(self):
        store = _make_store()
        store.upsert([_make_point(pid="p1"), _make_point(pid="p2")])
        assert store.count() == 2

    def test_count_no_collection_returns_zero(self):
        store = _make_store()
        assert store.count() == 0

    def test_count_after_delete_reflects_change(self):
        store = _make_store()
        store.upsert([
            _make_point(pid="p1", file="a.py"),
            _make_point(pid="p2", file="b.py"),
        ])
        assert store.count() == 2
        store.delete_by_file("my-repo", "main", "a.py")
        assert store.count() == 1
