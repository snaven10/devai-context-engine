from __future__ import annotations

import logging
import os
from pathlib import Path
from .base import EmbeddingProvider

logger = logging.getLogger(__name__)

MODELS = {
    "minilm-l6": ("all-MiniLM-L6-v2", 384),
    "minilm-l12": ("all-MiniLM-L12-v2", 384),
    "bge-small": ("BAAI/bge-small-en-v1.5", 384),
    "bge-base": ("BAAI/bge-base-en-v1.5", 768),
}


def _model_is_cached(model_name: str) -> bool:
    """Check if a HuggingFace model is already downloaded in the local cache."""
    cache_dir = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    # HF cache uses models--{org}--{name} or models--{name} directory format
    safe_name = model_name.replace("/", "--")
    candidate = cache_dir / f"models--{safe_name}"
    if candidate.exists():
        # Verify it has actual model files (not just metadata)
        snapshots = candidate / "snapshots"
        return snapshots.exists() and any(snapshots.iterdir())
    # Also check sentence-transformers namespaced variant
    candidate_st = cache_dir / f"models--sentence-transformers--{safe_name}"
    if candidate_st.exists():
        snapshots = candidate_st / "snapshots"
        return snapshots.exists() and any(snapshots.iterdir())
    return False


class LocalEmbedding:
    """Local embedding provider using sentence-transformers."""

    def __init__(self, model_key: str = "minilm-l6", device: str = "cpu") -> None:
        if model_key not in MODELS:
            raise ValueError(f"Unknown model: {model_key}. Available: {list(MODELS.keys())}")
        name, dim = MODELS[model_key]

        # Use cached model without HTTP check when available (faster startup)
        cached = _model_is_cached(name)
        if cached:
            logger.info("Loading embedding model: %s (cached, dim=%d)", name, dim)
            # Set offline mode BEFORE importing sentence_transformers to prevent
            # HTTP requests to HuggingFace Hub and silence auth warnings
            os.environ["HF_HUB_OFFLINE"] = "1"
            from sentence_transformers import SentenceTransformer
            try:
                self._model = SentenceTransformer(name, device=device)
            finally:
                os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            logger.info("Downloading embedding model: %s (dim=%d, device=%s)", name, dim, device)
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(name, device=device)

        self._dim = dim
        self._name = name
        self._key = model_key

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
