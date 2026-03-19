from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding providers. All providers must implement this."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        ...

    def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text. Default implementation."""
        ...

    def dimension(self) -> int:
        """Return the embedding dimension."""
        ...

    def model_name(self) -> str:
        """Return the model identifier."""
        ...
