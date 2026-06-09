from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Safety cap on chars fed to the encoder per text. This is NOT the model's context
# limit (granite handles 32k tokens) — it's a RAM guard. The ONNX-exported models do
# not cap the sequence themselves, and the raw parser can emit a single non-code chunk
# (minified json/sql/md — observed up to ~2.8M chars ≈ 700k tokens) that makes the
# O(N^2) attention explode the ONNX arena to ~20GB on CPU and OOM-kill the indexer.
# Default 4096 chars ≈ 1024 tokens = large_function_threshold, so NO code chunk is ever
# truncated (the AST chunker keeps those ≤1024 tok); only oversized raw blobs get clipped.
# The STORED chunk text is untouched — only the vector is computed on the prefix, and the
# reranker reads the full text anyway. Raise it if you have RAM/GPU headroom, lower it on
# tight memory, or set 0 to disable. Tune the batch via DEVAI_EMBED_BATCH_SIZE.
_DEFAULT_EMBED_MAX_CHARS = 4096
_DEFAULT_EMBED_BATCH_SIZE = 16


def _embed_max_chars() -> int:
    try:
        return int(os.environ.get("DEVAI_EMBED_MAX_CHARS", _DEFAULT_EMBED_MAX_CHARS))
    except ValueError:
        return _DEFAULT_EMBED_MAX_CHARS


def _embed_batch_size() -> int:
    try:
        return max(1, int(os.environ.get("DEVAI_EMBED_BATCH_SIZE", _DEFAULT_EMBED_BATCH_SIZE)))
    except ValueError:
        return _DEFAULT_EMBED_BATCH_SIZE


class ModelInfo:
    """Metadata for an embedding model.

    backend / onnx_file control how sentence-transformers loads the model:
        - backend="torch" (default): plain SentenceTransformer(name).
        - backend="onnx": loads an ONNX graph via the onnx backend; onnx_file
          selects the weight inside the repo (e.g. "onnx/model_quint8_avx2.onnx").
    """
    __slots__ = ("name", "dimension", "size_mb", "speed", "quality",
                 "desc_en", "desc_es", "backend", "onnx_file")

    def __init__(self, name: str, dimension: int, size_mb: int, speed: str,
                 quality: str, desc_en: str, desc_es: str,
                 backend: str = "torch", onnx_file: str | None = None) -> None:
        self.name = name
        self.dimension = dimension
        self.size_mb = size_mb
        self.speed = speed       # "fast", "medium", "slow"
        self.quality = quality   # "good", "better", "best"
        self.desc_en = desc_en
        self.desc_es = desc_es
        self.backend = backend       # "torch" or "onnx"
        self.onnx_file = onnx_file   # path to onnx weight when backend="onnx"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dimension": self.dimension,
            "size_mb": self.size_mb,
            "speed": self.speed,
            "quality": self.quality,
            "desc_en": self.desc_en,
            "desc_es": self.desc_es,
            "backend": self.backend,
            "onnx_file": self.onnx_file,
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
    # Multilingual models (50+ languages incl. Spanish). No query/passage prefix
    # required — drop-in with the plain encode() call. Use these when content is
    # non-English (e.g. Spanish code comments + memories).
    "ml-minilm": ModelInfo(
        name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        dimension=384,
        size_mb=470,
        speed="fast",
        quality="better",
        desc_en="Multilingual MiniLM (50+ langs). Fast, good for Spanish/mixed-language code and memories. No prefix needed.",
        desc_es="MiniLM multilingue (50+ idiomas). Rapido, bueno para codigo y memorias en espanol o mezcla. No requiere prefijo.",
    ),
    "ml-mpnet": ModelInfo(
        name="sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
        dimension=768,
        size_mb=1110,
        speed="medium",
        quality="best",
        desc_en="Multilingual MPNet base, 768 dims (50+ langs). Best multilingual quality for Spanish content. 2x storage, slower on CPU. No prefix needed.",
        desc_es="MPNet base multilingue, 768 dims (50+ idiomas). Mejor calidad multilingue para contenido en espanol. 2x almacenamiento, mas lento en CPU. No requiere prefijo.",
    ),
    # ONNX (int8) models. Loaded through the onnx backend for fast CPU inference.
    # Require an x86 CPU with AVX2 (the quint8_avx2 weight) + optimum installed.
    "ml-granite": ModelInfo(
        name="ibm-granite/granite-embedding-97m-multilingual-r2",
        dimension=384,
        size_mb=94,
        speed="fast",
        quality="best",
        backend="onnx",
        onnx_file="onnx/model_quint8_avx2.onnx",
        desc_en="Granite 97M multilingual in ONNX int8. Best quality/speed on CPU: 95% top-1 recall, ~6x faster indexing than torch, half the storage (384 dims). No prefix needed. Needs AVX2 + optimum.",
        desc_es="Granite 97M multilingue en ONNX int8. La mejor relacion calidad/velocidad en CPU: 95% de recall en primer lugar, ~6x mas rapido al indexar que torch, mitad de almacenamiento (384 dims). No requiere prefijo. Requiere AVX2 + optimum.",
    ),
    "ml-granite-lg": ModelInfo(
        name="ibm-granite/granite-embedding-311m-multilingual-r2",
        dimension=768,
        size_mb=299,
        speed="medium",
        quality="best",
        backend="onnx",
        onnx_file="onnx/model_quint8_avx2.onnx",
        desc_en="Granite 311M multilingual in ONNX int8, 768 dims. Larger sibling of ml-granite. In CPU tests the smaller ml-granite (97M) matched or beat it on recall while indexing ~4x faster — prefer ml-granite unless you specifically need 768-dim vectors. Needs AVX2 + optimum.",
        desc_es="Granite 311M multilingue en ONNX int8, 768 dims. Hermano mayor de ml-granite. En las pruebas en CPU el ml-granite (97M) lo iguala o supera en recall indexando ~4x mas rapido — preferir ml-granite salvo que necesites vectores de 768 dims. Requiere AVX2 + optimum.",
    ),
}

# Backward-compatible tuple format: (name, dimension)
MODELS = {k: (v.name, v.dimension) for k, v in MODEL_REGISTRY.items()}


def _model_is_cached(model_name: str, onnx_file: str | None = None) -> bool:
    """Check if a HuggingFace model is already downloaded in the local cache.

    When onnx_file is given, also require that specific ONNX weight to be present
    in a snapshot — a metadata-only snapshot does not count as cached for ONNX.
    """
    cache_dir = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    # HF cache uses models--{org}--{name} or models--{name} directory format
    safe_name = model_name.replace("/", "--")
    for prefix in (f"models--{safe_name}", f"models--sentence-transformers--{safe_name}"):
        candidate = cache_dir / prefix
        if not candidate.exists():
            continue
        snapshots = candidate / "snapshots"
        if not (snapshots.exists() and any(snapshots.iterdir())):
            continue
        if onnx_file and not any((snap / onnx_file).exists() for snap in snapshots.iterdir()):
            continue
        return True
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
        if model_key not in MODEL_REGISTRY:
            raise ValueError(f"Unknown model: {model_key}. Available: {list(MODEL_REGISTRY.keys())}")
        info = MODEL_REGISTRY[model_key]
        name, dim = info.name, info.dimension
        cached = _model_is_cached(name, info.onnx_file)

        # Backend-specific load kwargs. Torch models pass only device (unchanged
        # behaviour); ONNX models add backend + the selected onnx weight.
        st_kwargs: dict = {"device": device}
        if info.backend == "onnx":
            try:
                import optimum.onnxruntime  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    f"Model '{model_key}' uses the ONNX backend but 'optimum' is not "
                    f"installed. Install it with: pip install 'devai-ml[onnx]'"
                ) from exc
            st_kwargs["backend"] = "onnx"
            st_kwargs["model_kwargs"] = {"file_name": info.onnx_file}

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
            logger.info("Loading embedding model: %s (cached, dim=%d, backend=%s)", name, dim, info.backend)
            os.environ["HF_HUB_OFFLINE"] = "1"
            from sentence_transformers import SentenceTransformer
            try:
                self._model = SentenceTransformer(name, **st_kwargs)
            finally:
                os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            action = "Updating" if cached else "Downloading"
            logger.info("%s embedding model: %s (dim=%d, device=%s, backend=%s)", action, name, dim, device, info.backend)
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(name, **st_kwargs)

        self._dim = dim
        self._name = name
        self._key = model_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Cap each text so a single huge chunk can't blow up the ONNX attention
        # arena (see module-level note). The model only attends ~512 tokens anyway,
        # so this drops no signal the encoder would have used.
        max_chars = _embed_max_chars()
        if max_chars > 0:
            texts = [t[:max_chars] for t in texts]
        embeddings = self._model.encode(
            texts,
            batch_size=_embed_batch_size(),
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
