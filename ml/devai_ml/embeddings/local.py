from __future__ import annotations

import logging
from sentence_transformers import SentenceTransformer
from .base import EmbeddingProvider

logger = logging.getLogger(__name__)

MODELS = {
    "minilm-l6": ("all-MiniLM-L6-v2", 384),
    "minilm-l12": ("all-MiniLM-L12-v2", 384),
    "bge-small": ("BAAI/bge-small-en-v1.5", 384),
    "bge-base": ("BAAI/bge-base-en-v1.5", 768),
}


class LocalEmbedding:
    """Local embedding provider using sentence-transformers."""

    def __init__(self, model_key: str = "minilm-l6", device: str = "cpu") -> None:
        if model_key not in MODELS:
            raise ValueError(f"Unknown model: {model_key}. Available: {list(MODELS.keys())}")
        name, dim = MODELS[model_key]
        logger.info("Loading embedding model: %s (dim=%d, device=%s)", name, dim, device)
        self._model = SentenceTransformer(name, device=device)
        self._dim = dim
        self._name = name
        self._key = model_key
        logger.info("Model loaded successfully")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings = self._model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return embeddings.tolist()

    def embed_single(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def dimension(self) -> int:
        return self._dim

    def model_name(self) -> str:
        return self._name
