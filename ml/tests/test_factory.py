"""Tests for the store factory: create_storage_config_from_env and create_vector_store.

Covers all modes (local, shared, hybrid), env var parsing, validation errors,
case-insensitive mode, and default values.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from devai_ml.stores.factory import (
    StorageConfig,
    _parse_qdrant_url,
    create_storage_config_from_env,
    create_vector_store,
)


# ---------------------------------------------------------------------------
# StorageConfig defaults
# ---------------------------------------------------------------------------


class TestStorageConfigDefaults:
    def test_default_values(self):
        cfg = StorageConfig()
        assert cfg.mode == "local"
        assert cfg.local_db_path == ""
        assert cfg.qdrant_url == "localhost:6334"
        assert cfg.qdrant_api_key is None
        assert cfg.collection_name is None
        assert cfg.dimension == 384


# ---------------------------------------------------------------------------
# create_storage_config_from_env
# ---------------------------------------------------------------------------


class TestCreateStorageConfigFromEnv:
    def test_defaults_when_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = create_storage_config_from_env()
        assert cfg.mode == "local"
        assert cfg.qdrant_url == "localhost:6334"
        assert cfg.qdrant_api_key is None
        assert cfg.dimension == 384

    def test_reads_all_env_vars(self):
        env = {
            "DEVAI_STORAGE_MODE": "shared",
            "DEVAI_LOCAL_DB_PATH": "/tmp/db",
            "DEVAI_QDRANT_URL": "qdrant.example.com:6334",
            "DEVAI_QDRANT_API_KEY": "secret-key",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = create_storage_config_from_env()
        assert cfg.mode == "shared"
        assert cfg.local_db_path == "/tmp/db"
        assert cfg.qdrant_url == "qdrant.example.com:6334"
        assert cfg.qdrant_api_key == "secret-key"

    def test_case_insensitive_mode(self):
        with patch.dict(os.environ, {"DEVAI_STORAGE_MODE": "SHARED"}, clear=True):
            cfg = create_storage_config_from_env()
        assert cfg.mode == "shared"

    def test_mixed_case_mode(self):
        with patch.dict(os.environ, {"DEVAI_STORAGE_MODE": "Hybrid"}, clear=True):
            cfg = create_storage_config_from_env()
        assert cfg.mode == "hybrid"

    def test_empty_api_key_becomes_none(self):
        with patch.dict(os.environ, {"DEVAI_QDRANT_API_KEY": ""}, clear=True):
            cfg = create_storage_config_from_env()
        assert cfg.qdrant_api_key is None

    def test_default_mode_is_local(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = create_storage_config_from_env()
        assert cfg.mode == "local"


# ---------------------------------------------------------------------------
# _parse_qdrant_url
# ---------------------------------------------------------------------------


class TestParseQdrantUrl:
    def test_host_and_port(self):
        assert _parse_qdrant_url("qdrant.example.com:6334") == ("qdrant.example.com", 6334)

    def test_host_only(self):
        assert _parse_qdrant_url("qdrant.example.com") == ("qdrant.example.com", 6334)

    def test_custom_port(self):
        assert _parse_qdrant_url("localhost:9999") == ("localhost", 9999)

    def test_invalid_port_defaults(self):
        assert _parse_qdrant_url("localhost:notaport") == ("localhost:notaport", 6334)

    def test_http_scheme_stripped(self):
        assert _parse_qdrant_url("http://qdrant:6334") == ("qdrant", 6334)

    def test_https_scheme_stripped(self):
        assert _parse_qdrant_url("https://qdrant.example.com:6334") == ("qdrant.example.com", 6334)

    def test_scheme_with_no_port(self):
        assert _parse_qdrant_url("http://qdrant.example.com") == ("qdrant.example.com", 6334)


# ---------------------------------------------------------------------------
# create_vector_store — local mode
# ---------------------------------------------------------------------------


class TestCreateVectorStoreLocal:
    @patch("devai_ml.stores.vector_store.LanceDBVectorStore")
    def test_returns_lancedb(self, MockLanceDB):
        cfg = StorageConfig(mode="local", local_db_path="/tmp/db")
        store = create_vector_store(cfg)
        MockLanceDB.assert_called_once_with(db_path="/tmp/db", dimension=384)
        assert store == MockLanceDB.return_value


# ---------------------------------------------------------------------------
# create_vector_store — shared mode
# ---------------------------------------------------------------------------


class TestCreateVectorStoreShared:
    @patch("devai_ml.stores.qdrant_store.QdrantVectorStore")
    def test_returns_qdrant(self, MockQdrant):
        cfg = StorageConfig(
            mode="shared",
            qdrant_url="qdrant.example.com:6334",
            collection_name="devai_test",
        )
        store = create_vector_store(cfg)
        MockQdrant.assert_called_once_with(
            url="qdrant.example.com",
            port=6334,
            api_key=None,
            collection_name="devai_test",
            dimension=384,
        )
        assert store == MockQdrant.return_value

    def test_shared_without_url_raises(self):
        cfg = StorageConfig(mode="shared", qdrant_url="")
        with pytest.raises(ValueError, match="DEVAI_QDRANT_URL"):
            create_vector_store(cfg)


# ---------------------------------------------------------------------------
# create_vector_store — hybrid mode
# ---------------------------------------------------------------------------


class TestCreateVectorStoreHybrid:
    @patch("devai_ml.stores.hybrid_store.HybridVectorStore")
    @patch("devai_ml.stores.qdrant_store.QdrantVectorStore")
    @patch("devai_ml.stores.vector_store.LanceDBVectorStore")
    def test_returns_hybrid(self, MockLanceDB, MockQdrant, MockHybrid):
        cfg = StorageConfig(
            mode="hybrid",
            local_db_path="/tmp/db",
            qdrant_url="qdrant:6334",
        )
        store = create_vector_store(cfg)
        MockLanceDB.assert_called_once()
        MockQdrant.assert_called_once()
        MockHybrid.assert_called_once_with(
            local=MockLanceDB.return_value,
            shared=MockQdrant.return_value,
        )
        assert store == MockHybrid.return_value

    def test_hybrid_without_local_path_raises(self):
        cfg = StorageConfig(mode="hybrid", local_db_path="", qdrant_url="qdrant:6334")
        with pytest.raises(ValueError, match="DEVAI_LOCAL_DB_PATH"):
            create_vector_store(cfg)

    def test_hybrid_without_qdrant_url_raises(self):
        cfg = StorageConfig(mode="hybrid", local_db_path="/tmp/db", qdrant_url="")
        with pytest.raises(ValueError, match="DEVAI_QDRANT_URL"):
            create_vector_store(cfg)


# ---------------------------------------------------------------------------
# create_vector_store — unknown mode
# ---------------------------------------------------------------------------


class TestCreateVectorStoreUnknown:
    def test_unknown_mode_raises(self):
        cfg = StorageConfig(mode="distributed")
        with pytest.raises(ValueError, match="distributed"):
            create_vector_store(cfg)

    def test_error_lists_valid_modes(self):
        cfg = StorageConfig(mode="magic")
        with pytest.raises(ValueError, match="local, shared, hybrid"):
            create_vector_store(cfg)
