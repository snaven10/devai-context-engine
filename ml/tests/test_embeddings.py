"""Tests for the local embedding provider — model registry + ONNX backend wiring."""
from __future__ import annotations

import builtins

import pytest
from unittest.mock import patch

from devai_ml.embeddings.local import (
    MODEL_REGISTRY,
    LocalEmbedding,
    _embed_batch_size,
    _embed_max_chars,
    _model_is_cached,
)


# --- Registry metadata --------------------------------------------------------

def test_registry_has_granite_onnx_entry():
    info = MODEL_REGISTRY["ml-granite"]
    assert info.backend == "onnx"
    assert info.dimension == 384
    assert info.onnx_file == "onnx/model_quint8_avx2.onnx"
    assert info.name == "ibm-granite/granite-embedding-97m-multilingual-r2"


def test_registry_has_granite_lg_onnx_entry():
    info = MODEL_REGISTRY["ml-granite-lg"]
    assert info.backend == "onnx"
    assert info.dimension == 768
    assert info.onnx_file == "onnx/model_quint8_avx2.onnx"
    assert info.name == "ibm-granite/granite-embedding-311m-multilingual-r2"


def test_torch_models_keep_default_backend():
    # every pre-existing model stays on the torch backend, onnx_file unset
    for key in ("minilm-l6", "minilm-l12", "bge-small", "bge-base", "ml-minilm", "ml-mpnet"):
        info = MODEL_REGISTRY[key]
        assert info.backend == "torch"
        assert info.onnx_file is None


def test_to_dict_exposes_backend_fields():
    d = MODEL_REGISTRY["ml-granite"].to_dict()
    assert d["backend"] == "onnx"
    assert d["onnx_file"] == "onnx/model_quint8_avx2.onnx"
    # torch model serializes the same shape with safe defaults
    d2 = MODEL_REGISTRY["ml-mpnet"].to_dict()
    assert d2["backend"] == "torch"
    assert d2["onnx_file"] is None


# --- LocalEmbedding construction ---------------------------------------------

def test_unknown_model_raises():
    with pytest.raises(ValueError, match="Unknown model"):
        LocalEmbedding("does-not-exist")


@patch("devai_ml.embeddings.local._model_is_cached", return_value=True)
@patch("sentence_transformers.SentenceTransformer")
def test_torch_model_loads_without_backend_kwarg(mock_st, _cached):
    """Retro-compat: torch models must NOT receive backend/model_kwargs."""
    LocalEmbedding("ml-mpnet")
    _, kwargs = mock_st.call_args
    assert kwargs.get("device") == "cpu"
    assert "backend" not in kwargs
    assert "model_kwargs" not in kwargs


@patch("devai_ml.embeddings.local._model_is_cached", return_value=True)
@patch("sentence_transformers.SentenceTransformer")
def test_onnx_model_passes_backend_and_file(mock_st, _cached):
    emb = LocalEmbedding("ml-granite")
    args, kwargs = mock_st.call_args
    assert args[0] == "ibm-granite/granite-embedding-97m-multilingual-r2"
    assert kwargs.get("backend") == "onnx"
    assert kwargs.get("model_kwargs") == {"file_name": "onnx/model_quint8_avx2.onnx"}
    assert emb.dimension() == 384


@patch("devai_ml.embeddings.local._model_is_cached", return_value=True)
def test_onnx_without_optimum_raises_clear_error(_cached):
    """If optimum is missing, the error must tell the user what to install."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("optimum"):
            raise ImportError("simulated: optimum not installed")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(RuntimeError, match=r"devai-ml\[onnx\]"):
            LocalEmbedding("ml-granite")


# --- Cache detection ----------------------------------------------------------

def test_cache_check_requires_onnx_file(tmp_path, monkeypatch):
    """A snapshot without the onnx weight must NOT count as cached for ONNX."""
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    snap = tmp_path / "hub" / "models--ibm-granite--granite-embedding-97m-multilingual-r2" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}")  # metadata only, no onnx

    # torch-style check (no onnx_file) passes; onnx check fails until weight exists
    assert _model_is_cached("ibm-granite/granite-embedding-97m-multilingual-r2") is True
    assert _model_is_cached(
        "ibm-granite/granite-embedding-97m-multilingual-r2",
        onnx_file="onnx/model_quint8_avx2.onnx",
    ) is False

    onnx_path = snap / "onnx"
    onnx_path.mkdir()
    (onnx_path / "model_quint8_avx2.onnx").write_bytes(b"fake")
    assert _model_is_cached(
        "ibm-granite/granite-embedding-97m-multilingual-r2",
        onnx_file="onnx/model_quint8_avx2.onnx",
    ) is True


# --- Embed RAM guard (DEVAI_EMBED_MAX_CHARS / _BATCH_SIZE) ---------------------

def test_embed_char_cap_defaults_and_env(monkeypatch):
    monkeypatch.delenv("DEVAI_EMBED_MAX_CHARS", raising=False)
    assert _embed_max_chars() == 4096
    monkeypatch.setenv("DEVAI_EMBED_MAX_CHARS", "1000")
    assert _embed_max_chars() == 1000
    monkeypatch.setenv("DEVAI_EMBED_MAX_CHARS", "0")  # disabled
    assert _embed_max_chars() == 0
    monkeypatch.setenv("DEVAI_EMBED_MAX_CHARS", "garbage")  # falls back to default
    assert _embed_max_chars() == 4096


def test_embed_batch_size_defaults_and_floor(monkeypatch):
    monkeypatch.delenv("DEVAI_EMBED_BATCH_SIZE", raising=False)
    assert _embed_batch_size() == 16
    monkeypatch.setenv("DEVAI_EMBED_BATCH_SIZE", "8")
    assert _embed_batch_size() == 8
    monkeypatch.setenv("DEVAI_EMBED_BATCH_SIZE", "0")  # never below 1
    assert _embed_batch_size() == 1


def _make_granite_with_capturing_encode():
    """Build a LocalEmbedding whose encode() records its inputs."""
    captured = {}

    class _Vec:
        def tolist(self):
            return [[0.0]]

    def _encode(texts, **kwargs):
        captured["texts"] = list(texts)
        captured["batch_size"] = kwargs.get("batch_size")
        return _Vec()

    with patch("devai_ml.embeddings.local._model_is_cached", return_value=True), \
         patch("sentence_transformers.SentenceTransformer"):
        emb = LocalEmbedding("ml-granite")
    emb._model.encode = _encode
    return emb, captured


def test_embed_truncates_oversized_text(monkeypatch):
    monkeypatch.setenv("DEVAI_EMBED_MAX_CHARS", "10")
    emb, captured = _make_granite_with_capturing_encode()
    emb.embed(["x" * 500])
    assert captured["texts"] == ["x" * 10]  # clipped to the cap


def test_embed_does_not_touch_short_text(monkeypatch):
    monkeypatch.setenv("DEVAI_EMBED_MAX_CHARS", "4096")
    emb, captured = _make_granite_with_capturing_encode()
    emb.embed(["def f(): pass"])
    assert captured["texts"] == ["def f(): pass"]  # untouched


def test_embed_cap_disabled_with_zero(monkeypatch):
    monkeypatch.setenv("DEVAI_EMBED_MAX_CHARS", "0")
    emb, captured = _make_granite_with_capturing_encode()
    big = "y" * 5000
    emb.embed([big])
    assert captured["texts"] == [big]  # not clipped when disabled


def test_embed_passes_batch_size_from_env(monkeypatch):
    monkeypatch.setenv("DEVAI_EMBED_BATCH_SIZE", "4")
    emb, captured = _make_granite_with_capturing_encode()
    emb.embed(["a", "b"])
    assert captured["batch_size"] == 4
