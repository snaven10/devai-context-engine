from __future__ import annotations

import logging
from typing import Any

from .base import EmbeddingProvider

logger = logging.getLogger(__name__)


def create_provider(config: dict[str, Any]) -> EmbeddingProvider:
    """Create an embedding provider from configuration.

    Config format:
        provider: local | openai | voyage | custom
        model: model key or name
        device: cpu | cuda (for local only)
        api_key: API key (for API providers)
        endpoint: URL (for custom provider)
        dimension: int (for custom provider)
    """
    provider_type = config.get("provider", "local")

    if provider_type == "local":
        from .local import LocalEmbedding
        return LocalEmbedding(
            model_key=config.get("model", "minilm-l6"),
            device=config.get("device", "cpu"),
            offline=config.get("offline", "auto"),
        )

    if provider_type == "openai":
        from .openai_embed import OpenAIEmbedding
        return OpenAIEmbedding(
            model_key=config.get("model", "small"),
            api_key=config.get("api_key"),
        )

    if provider_type == "voyage":
        from .voyage_embed import VoyageEmbedding
        return VoyageEmbedding(
            model_key=config.get("model", "code-3"),
            api_key=config.get("api_key"),
        )

    if provider_type == "custom":
        from .custom import CustomEmbedding
        return CustomEmbedding(
            endpoint=config["endpoint"],
            dimension=config["dimension"],
            model_name=config.get("model", "custom"),
            api_key=config.get("api_key"),
        )

    raise ValueError(f"Unknown embedding provider: {provider_type}")
