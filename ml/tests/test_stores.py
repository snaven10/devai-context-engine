from __future__ import annotations

import os
import tempfile
import pytest

from devai_ml.stores.graph_store import SQLiteGraphStore, StoredEdge
from devai_ml.stores.index_state import IndexStateStore, IndexRecord, FileRecord
from devai_ml.stores.vector_store import deterministic_id


class TestSQLiteGraphStore:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = SQLiteGraphStore(self.tmp.name)

    def teardown_method(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_add_and_get_callers(self):
        edge = StoredEdge(
            source="a.py::main",
            target="b.py::helper",
            kind="calls",
            source_file="a.py",
            target_file="b.py",
            line=10,
            repo="/repo",
            branch="main",
        )
        self.store.add_edges([edge])
        callers = self.store.get_callers("/repo", "main", "b.py::helper")
        assert len(callers) == 1
        assert callers[0].source == "a.py::main"

    def test_add_and_get_callees(self):
        edge = StoredEdge(
            source="a.py::main",
            target="b.py::helper",
            kind="calls",
            source_file="a.py",
            target_file="b.py",
            line=10,
            repo="/repo",
            branch="main",
        )
        self.store.add_edges([edge])
        callees = self.store.get_callees("/repo", "main", "a.py::main")
        assert len(callees) == 1
        assert callees[0].target == "b.py::helper"

    def test_remove_file(self):
        edges = [
            StoredEdge("a::f1", "b::f2", "calls", "a.py", "b.py", 1, "/r", "main"),
            StoredEdge("a::f3", "c::f4", "calls", "a.py", "c.py", 2, "/r", "main"),
        ]
        self.store.add_edges(edges)
        self.store.remove_file("/r", "main", "a.py")
        assert self.store.get_callees("/r", "main", "a::f1") == []

    def test_rename_file(self):
        edge = StoredEdge("a::f", "b::g", "calls", "a.py", "b.py", 1, "/r", "main")
        self.store.add_edges([edge])
        self.store.rename_file("/r", "main", "a.py", "renamed.py")
        callees = self.store.get_callees("/r", "main", "a::f")
        assert callees[0].source_file == "renamed.py"

    def test_branch_isolation(self):
        edge1 = StoredEdge("a::f", "b::g", "calls", "a.py", "b.py", 1, "/r", "main")
        edge2 = StoredEdge("a::f", "c::h", "calls", "a.py", "c.py", 2, "/r", "feature")
        self.store.add_edges([edge1, edge2])
        main_callees = self.store.get_callees("/r", "main", "a::f")
        feature_callees = self.store.get_callees("/r", "feature", "a::f")
        assert len(main_callees) == 1
        assert len(feature_callees) == 1
        assert main_callees[0].target != feature_callees[0].target

    def test_get_dependencies(self):
        edges = [
            StoredEdge("a.py::main", "os", "imports", "a.py", "os.py", 1, "/r", "main"),
            StoredEdge("a.py::main", "sys", "imports", "a.py", "sys.py", 2, "/r", "main"),
        ]
        self.store.add_edges(edges)
        deps = self.store.get_dependencies("/r", "main", "a.py")
        assert len(deps) == 2


class TestIndexStateStore:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = IndexStateStore(self.tmp.name)

    def teardown_method(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_save_and_get(self):
        record = IndexRecord(
            repo_path="/repo",
            branch="main",
            last_commit="abc123",
            model_name="all-MiniLM-L6-v2",
            model_dimension=384,
            file_count=10,
            symbol_count=50,
        )
        self.store.save(record)
        result = self.store.get_last_indexed("/repo", "main")
        assert result is not None
        assert result.last_commit == "abc123"
        assert result.file_count == 10

    def test_get_nonexistent(self):
        result = self.store.get_last_indexed("/nonexistent", "main")
        assert result is None

    def test_file_hash_tracking(self):
        self.store.save_file("/repo", "main", FileRecord(
            file_path="a.py",
            content_hash="hash123",
            language="python",
            symbol_count=5,
        ))
        h = self.store.get_file_hash("/repo", "main", "a.py")
        assert h == "hash123"

    def test_file_removal(self):
        self.store.save_file("/repo", "main", FileRecord("a.py", "h1", "python"))
        self.store.remove_file("/repo", "main", "a.py")
        assert self.store.get_file_hash("/repo", "main", "a.py") is None

    def test_file_rename(self):
        self.store.save_file("/repo", "main", FileRecord("old.py", "h1", "python"))
        self.store.rename_file("/repo", "main", "old.py", "new.py")
        assert self.store.get_file_hash("/repo", "main", "old.py") is None
        assert self.store.get_file_hash("/repo", "main", "new.py") == "h1"

    def test_branch_lineage(self):
        self.store.set_branch_lineage("/repo", "feature-auth", "develop")
        self.store.set_branch_lineage("/repo", "develop", "main")
        lineage = self.store.get_branch_lineage("/repo", "feature-auth")
        assert lineage == ["feature-auth", "develop", "main"]

    def test_get_all_file_hashes(self):
        self.store.save_file("/repo", "main", FileRecord("a.py", "h1", "python"))
        self.store.save_file("/repo", "main", FileRecord("b.py", "h2", "python"))
        hashes = self.store.get_all_file_hashes("/repo", "main")
        assert len(hashes) == 2
        assert hashes["a.py"] == "h1"

    def test_stats(self):
        record = IndexRecord("/repo", "main", "abc", "model", 384, 10, 50, 100)
        self.store.save(record)
        stats = self.store.get_stats("/repo", "main")
        assert stats["indexed"] is True
        assert stats["files"] == 10
        assert stats["symbols"] == 50


class TestDeterministicId:
    def test_same_input_same_output(self):
        id1 = deterministic_id("repo", "main", "a.py", 10)
        id2 = deterministic_id("repo", "main", "a.py", 10)
        assert id1 == id2

    def test_different_input_different_output(self):
        id1 = deterministic_id("repo", "main", "a.py", 10)
        id2 = deterministic_id("repo", "main", "a.py", 11)
        assert id1 != id2

    def test_length(self):
        id1 = deterministic_id("repo", "main", "a.py", 10)
        assert len(id1) == 32
