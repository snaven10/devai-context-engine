from __future__ import annotations

import logging


logger = logging.getLogger(__name__)

MODELS = {
    "code-3": ("voyage-code-3", 1024),
    "3-lite": ("voyage-3-lite", 512),
    "3": ("voyage-3", 1024),
}


class VoyageEmbedding:
    """Voyage AI embedding provider — optimized for code."""

    def __init__(self, model_key: str = "code-3", api_key: str | None = None) -> None:
        if model_key not in MODELS:
            raise ValueError(f"Unknown model: {model_key}. Available: {list(MODELS.keys())}")

        name, dim = MODELS[model_key]
        self._name = name
        self._dim = dim
        self._key = model_key

        try:
            import voyageai
        except ImportError:
            raise ImportError(
                "voyageai package required. Install with: pip install devai-ml[api]"
            )

        self._client = voyageai.Client(api_key=api_key)
        logger.info("Voyage embedding initialized: %s (dim=%d)", name, dim)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # Voyage batch limit is 128
        all_embeddings: list[list[float]] = []
        batch_size = 128

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            result = self._client.embed(
                batch,
                model=self._name,
                input_type="document",
            )
            all_embeddings.extend(result.embeddings)

        return all_embeddings

    def embed_single(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def dimension(self) -> int:
        return self._dim

    def model_name(self) -> str:
        return self._name
