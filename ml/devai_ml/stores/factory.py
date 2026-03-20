"""Storage backend factory for DevAI vector stores.

Reads configuration from environment variables and returns the appropriate
VectorStore implementation based on the storage mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class StorageConfig:
    """Configuration for storage backend selection."""

    mode: str = "local"  # local | shared | hybrid
    local_db_path: str = ""  # path to LanceDB directory
    qdrant_url: str = "localhost:6334"  # Qdrant gRPC endpoint (host:port)
    qdrant_api_key: str | None = None  # optional, for Qdrant Cloud
    collection_name: str | None = None  # override auto-generated name
    dimension: int = 384  # embedding dimension


def create_storage_config_from_env() -> StorageConfig:
    """Read storage config from environment variables.

    Env vars:
        DEVAI_STORAGE_MODE: local | shared | hybrid (default: local)
        DEVAI_LOCAL_DB_PATH: path to LanceDB directory
        DEVAI_QDRANT_URL: Qdrant gRPC endpoint host:port (default: localhost:6334)
        DEVAI_QDRANT_API_KEY: optional Qdrant API key
    """
    return StorageConfig(
        mode=os.environ.get("DEVAI_STORAGE_MODE", "local").lower(),
        local_db_path=os.environ.get("DEVAI_LOCAL_DB_PATH", ""),
        qdrant_url=os.environ.get("DEVAI_QDRANT_URL", "localhost:6334"),
        qdrant_api_key=os.environ.get("DEVAI_QDRANT_API_KEY") or None,
    )


def create_vector_store(config: StorageConfig):
    """Factory: returns the appropriate VectorStore for the configured mode.

    Args:
        config: StorageConfig with mode and backend-specific settings.

    Returns:
        A VectorStore implementation (LanceDBVectorStore, QdrantVectorStore,
        or HybridVectorStore).

    Raises:
        ValueError: If mode is unknown, or required config is missing.
    """
    from .vector_store import LanceDBVectorStore

    if config.mode == "local":
        return LanceDBVectorStore(
            db_path=config.local_db_path,
            dimension=config.dimension,
        )
    elif config.mode == "shared":
        if not config.qdrant_url:
            raise ValueError("shared mode requires DEVAI_QDRANT_URL to be set")
        from .qdrant_store import QdrantVectorStore

        host, port = _parse_qdrant_url(config.qdrant_url)
        return QdrantVectorStore(
            url=host,
            port=port,
            api_key=config.qdrant_api_key,
            collection_name=config.collection_name or "devai_default",
            dimension=config.dimension,
        )
    elif config.mode == "hybrid":
        if not config.local_db_path:
            raise ValueError("hybrid mode requires DEVAI_LOCAL_DB_PATH to be set")
        if not config.qdrant_url:
            raise ValueError("hybrid mode requires DEVAI_QDRANT_URL to be set")
        from .qdrant_store import QdrantVectorStore
        from .hybrid_store import HybridVectorStore

        local = LanceDBVectorStore(
            db_path=config.local_db_path,
            dimension=config.dimension,
        )
        host, port = _parse_qdrant_url(config.qdrant_url)
        shared = QdrantVectorStore(
            url=host,
            port=port,
            api_key=config.qdrant_api_key,
            collection_name=config.collection_name or "devai_default",
            dimension=config.dimension,
        )
        return HybridVectorStore(local=local, shared=shared)
    else:
        raise ValueError(
            f"unknown storage mode: {config.mode}. Valid modes: local, shared, hybrid"
        )


def _parse_qdrant_url(url: str) -> tuple[str, int]:
    """Parse host:port from Qdrant URL string.

    Handles both plain ``host:port`` and scheme-prefixed URLs like
    ``http://host:port``.  The scheme is stripped before extracting the
    host so that gRPC clients receive a clean hostname.

    Returns:
        (host, port) tuple. Defaults to port 6334 if not specified.
    """
    # Strip scheme prefix (http://, https://, etc.) if present
    cleaned = url
    if "://" in cleaned:
        cleaned = cleaned.split("://", 1)[1]

    if ":" in cleaned:
        parts = cleaned.rsplit(":", 1)
        try:
            return parts[0], int(parts[1])
        except ValueError:
            return cleaned, 6334
    return cleaned, 6334
