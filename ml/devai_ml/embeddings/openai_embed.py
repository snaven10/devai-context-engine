from __future__ import annotations

import logging


logger = logging.getLogger(__name__)

MODELS = {
    "small": ("text-embedding-3-small", 1536),
    "large": ("text-embedding-3-large", 3072),
    "ada": ("text-embedding-ada-002", 1536),
}


class OpenAIEmbedding:
    """OpenAI API embedding provider."""

    def __init__(self, model_key: str = "small", api_key: str | None = None) -> None:
        if model_key not in MODELS:
            raise ValueError(f"Unknown model: {model_key}. Available: {list(MODELS.keys())}")

        name, dim = MODELS[model_key]
        self._name = name
        self._dim = dim
        self._key = model_key

        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package required. Install with: pip install devai-ml[api]"
            )

        self._client = OpenAI(api_key=api_key)
        logger.info("OpenAI embedding initialized: %s (dim=%d)", name, dim)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # OpenAI batch limit is 2048 inputs
        all_embeddings: list[list[float]] = []
        batch_size = 512

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = self._client.embeddings.create(
                model=self._name,
                input=batch,
            )
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    def embed_single(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def dimension(self) -> int:
        return self._dim

    def model_name(self) -> str:
        return self._name
