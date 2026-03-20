"""DevAI storage backends.

Public API:
    StorageConfig — configuration dataclass for backend selection
    create_vector_store — factory function returning the appropriate VectorStore
    create_storage_config_from_env — reads config from DEVAI_* env vars
"""

from .factory import StorageConfig, create_storage_config_from_env, create_vector_store

__all__ = [
    "StorageConfig",
    "create_storage_config_from_env",
    "create_vector_store",
]
