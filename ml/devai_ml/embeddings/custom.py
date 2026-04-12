from __future__ import annotations

import json
import logging
import urllib.request


logger = logging.getLogger(__name__)


class CustomEmbedding:
    """Custom HTTP endpoint embedding provider.

    Sends POST requests to a user-specified endpoint.
    Expected API contract:
        POST {endpoint}/embed
        Body: {"texts": ["..."], "model": "..."}
        Response: {"vectors": [[0.1, ...], ...], "dimension": 384}
    """

    def __init__(
        self,
        endpoint: str,
        dimension: int,
        model_name: str = "custom",
        api_key: str | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._dim = dimension
        self._model = model_name
        self._api_key = api_key
        logger.info(
            "Custom embedding initialized: endpoint=%s, dim=%d, model=%s",
            endpoint, dimension, model_name,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        body = json.dumps({"texts": texts, "model": self._model}).encode()

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        req = urllib.request.Request(
            f"{self._endpoint}/embed",
            data=body,
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())

        vectors = data["vectors"]
        if vectors and len(vectors[0]) != self._dim:
            logger.warning(
                "Dimension mismatch: expected %d, got %d",
                self._dim, len(vectors[0]),
            )
        return vectors

    def embed_single(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def dimension(self) -> int:
        return self._dim

    def model_name(self) -> str:
        return self._model
