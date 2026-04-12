from __future__ import annotations

import logging
import os
from pathlib import Path
from .base import EmbeddingProvider

logger = logging.getLogger(__name__)

class ModelInfo:
    """Metadata for an embedding model."""
    __slots__ = ("name", "dimension", "size_mb", "speed", "quality", "desc_en", "desc_es")

    def __init__(self, name: str, dimension: int, size_mb: int, speed: str,
                 quality: str, desc_en: str, desc_es: str) -> None:
        self.name = name
        self.dimension = dimension
        self.size_mb = size_mb
        self.speed = speed       # "fast", "medium", "slow"
        self.quality = quality   # "good", "better", "best"
        self.desc_en = desc_en
        self.desc_es = desc_es

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dimension": self.dimension,
            "size_mb": self.size_mb,
            "speed": self.speed,
            "quality": self.quality,
            "desc_en": self.desc_en,
            "desc_es": self.desc_es,
        }


MODEL_REGISTRY: dict[str, ModelInfo] = {
    "minilm-l6": ModelInfo(
        name="all-MiniLM-L6-v2",
        dimension=384,
        size_mb=22,
        speed="fast",
        quality="good",
        desc_en="Lightweight 6-layer model. Fastest startup and inference. Good for general code search on resource-constrained machines.",
        desc_es="Modelo ligero de 6 capas. El mas rapido en inicio e inferencia. Bueno para busqueda general de codigo en maquinas con pocos recursos.",
    ),
    "minilm-l12": ModelInfo(
        name="all-MiniLM-L12-v2",
        dimension=384,
        size_mb=33,
        speed="fast",
        quality="better",
        desc_en="12-layer model with better semantic understanding than L6. Good balance between speed and quality for general text and code.",
        desc_es="Modelo de 12 capas con mejor comprension semantica que L6. Buen balance entre velocidad y calidad para texto general y codigo.",
    ),
    "bge-small": ModelInfo(
        name="BAAI/bge-small-en-v1.5",
        dimension=384,
        size_mb=33,
        speed="fast",
        quality="better",
        desc_en="BGE small model trained on diverse retrieval corpus. Better accuracy than MiniLM for semantic search. Recommended for most projects.",
        desc_es="Modelo BGE pequeno entrenado en corpus diverso de recuperacion. Mejor precision que MiniLM para busqueda semantica. Recomendado para la mayoria de proyectos.",
    ),
    "bge-base": ModelInfo(
        name="BAAI/bge-base-en-v1.5",
        dimension=768,
        size_mb=110,
        speed="medium",
        quality="best",
        desc_en="BGE base model with 768 dimensions. State-of-the-art quality for code search and complex queries. Uses 2x storage. Best for large codebases where precision matters.",
        desc_es="Modelo BGE base con 768 dimensiones. Calidad de vanguardia para busqueda de codigo y consultas complejas. Usa 2x almacenamiento. Mejor para grandes repositorios donde la precision importa.",
    ),
}

# Backward-compatible tuple format: (name, dimension)
MODELS = {k: (v.name, v.dimension) for k, v in MODEL_REGISTRY.items()}


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


def list_available_models() -> dict[str, tuple[str, int]]:
    """Return the available model registry (legacy tuple format)."""
    return dict(MODELS)


def list_models_detailed() -> dict[str, ModelInfo]:
    """Return the full model registry with metadata."""
    return dict(MODEL_REGISTRY)


class LocalEmbedding:
    """Local embedding provider using sentence-transformers.

    Args:
        model_key: Key from MODELS registry (e.g. "minilm-l6").
        device: "cpu" or "cuda".
        offline: Controls network access for model loading.
            - "auto" (default): offline when cached, online when not.
            - True: always offline (fail if not cached).
            - False: always online (check HF Hub for updates).
    """

    def __init__(self, model_key: str = "minilm-l6", device: str = "cpu",
                 offline: str | bool = "auto") -> None:
        if model_key not in MODELS:
            raise ValueError(f"Unknown model: {model_key}. Available: {list(MODELS.keys())}")
        name, dim = MODELS[model_key]
        cached = _model_is_cached(name)

        # Resolve offline mode
        if offline == "auto":
            use_offline = cached
        else:
            use_offline = bool(offline)

        if use_offline:
            if not cached:
                raise RuntimeError(
                    f"Model {name} not cached and offline=true. "
                    f"Run 'devai model update' to download it first."
                )
            logger.info("Loading embedding model: %s (cached, dim=%d)", name, dim)
            os.environ["HF_HUB_OFFLINE"] = "1"
            from sentence_transformers import SentenceTransformer
            try:
                self._model = SentenceTransformer(name, device=device)
            finally:
                os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            action = "Updating" if cached else "Downloading"
            logger.info("%s embedding model: %s (dim=%d, device=%s)", action, name, dim, device)
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
