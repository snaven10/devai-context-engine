"""Memory chunking + blended retrieval scoring.

Works around the embedding context-window limit. Multilingual sentence-encoders
such as paraphrase-multilingual-mpnet-base-v2 cap their input at 128 tokens, so
only the first ~106 body tokens of a long memory ever reach the single "whole"
vector. Anything buried deeper is invisible to semantic recall.

Strategy (validated A/B against whole-only and chunk-max):

  - INDEX TIME: a long memory keeps its existing whole/intro vector
    (chunk_level="memory") AND gains extra body-window vectors
    (chunk_level="memory_chunk"), each with the title prepended so no chunk is
    context-less. Short memories get NO extra chunks → behaviour is unchanged.

  - RECALL TIME: the per-memory score BLENDS the intro signal with the best body
    chunk:  score = alpha*intro_sim + (1-alpha)*max_chunk_sim.
    The blend (not a plain max) is what makes this safe: chunking multiplies the
    surface a memory can match on, which also raises the noise floor — an
    irrelevant memory now has more chances at a fluke chunk hit. Requiring BOTH a
    decent global match (intro) AND a good local match (chunk) suppresses that
    floor. A plain max does not: it rewards a single fluke chunk. alpha=0.5
    measured best (recovers buried content, zero loss on intro queries).
"""
from __future__ import annotations

import re
from typing import Any

DEFAULT_OVERLAP = 30
DEFAULT_MAX_CHUNKS = 40
DEFAULT_BLEND_ALPHA = 0.5

_CHUNK_ID_RE = re.compile(r"_c\d+$")


def parent_vector_id(vector_id: str) -> str:
    """Map a chunk id back to its memory id. `mem_<h>_c3` -> `mem_<h>`."""
    return _CHUNK_ID_RE.sub("", vector_id)


def body_chunks(
    title: str,
    content: str,
    tokenizer: Any,
    max_seq_length: int,
    overlap: int = DEFAULT_OVERLAP,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
) -> list[str]:
    """Extra body-window chunk texts BEYOND the intro window (title prepended).

    Returns [] when the content fits in a single window (short memory) — the
    existing whole/intro vector already covers it, so no extra vectors are made.
    This keeps short memories at exactly one vector (no added storage, no added
    noise floor).

    Windows slide over the *body* tokens; the title is prepended to every window
    so each chunk carries the memory's identifying terms. The first window is
    skipped (the intro vector already represents it).
    """
    title = (title or "").strip()
    content = content or ""
    if tokenizer is None or max_seq_length <= 0:
        return []
    title_ids = tokenizer.encode(title, add_special_tokens=False)
    body_ids = tokenizer.encode(content, add_special_tokens=False)
    budget = max_seq_length - len(title_ids) - 4   # room for title + special tokens
    if budget < 24:                                # pathologically long title: clamp it
        title_ids = title_ids[: max(max_seq_length // 4, 8)]
        budget = max(max_seq_length - len(title_ids) - 4, 8)
    if len(body_ids) <= budget:                    # fits the intro window → no extras
        return []
    step = max(budget - overlap, 1)
    chunks: list[str] = []
    # start at `step` → skip the first window (covered by the intro vector)
    for i in range(step, len(body_ids), step):
        window = body_ids[i:i + budget]
        if not window:
            break
        chunks.append((title + "\n" + tokenizer.decode(window)).strip())
        if len(chunks) >= max_chunks:
            break
    return chunks


def dist_to_sim(distance: float) -> float:
    """LanceDB squared-L2 distance of normalized vectors -> cosine similarity.

    For unit vectors, ||a-b||^2 = 2 - 2cos, so cos = 1 - d/2. Clamped to [-1, 1]
    so a different metric can't produce out-of-range values (the transform stays
    monotonic decreasing either way, which is all the ranking needs).
    """
    sim = 1.0 - distance / 2.0
    return max(-1.0, min(1.0, sim))


def blend_hits(
    results: list[Any],
    alpha: float = DEFAULT_BLEND_ALPHA,
) -> list[tuple[str, float, Any]]:
    """Group mixed memory/chunk search hits by parent memory and blend them.

    Each result must expose `.id`, `.score` (LanceDB distance, lower = closer),
    `.metadata` (carrying `chunk_level`) and `.text`.

    Returns ``[(parent_id, blend_sim, intro_result_or_None)]`` sorted by
    blend_sim descending. ``intro_result`` is the ``chunk_level=="memory"`` hit
    when it was in the pool, else ``None`` (the caller fetches full text from
    SQLite for the reranker).

    Blend per memory:  ``alpha*intro_sim + (1-alpha)*best_sim`` where
      - ``intro_sim`` is the intro (whole) hit's similarity; if the intro vector
        missed the fetch pool it is floored to the worst similarity observed
        (it provably ranked below the cutoff). This is what penalizes a memory
        that only matched on a fluke chunk — exactly the noise-floor fix.
      - ``best_sim`` is the max similarity over ALL of the memory's hits.
    A short memory has only its intro hit → ``best_sim == intro_sim`` → the blend
    collapses to ``intro_sim`` (no penalty, identical to pre-chunking ranking).
    """
    if not results:
        return []
    sims = [dist_to_sim(float(r.score)) for r in results]
    floor = min(sims)  # worst similarity in the pool — proxy for "missed the cutoff"

    intro_sim: dict[str, float] = {}
    best_sim: dict[str, float] = {}
    intro_res: dict[str, Any] = {}
    order: list[str] = []
    for r, sim in zip(results, sims):
        pid = parent_vector_id(r.id)
        if pid not in best_sim:
            best_sim[pid] = sim
            order.append(pid)
        else:
            best_sim[pid] = max(best_sim[pid], sim)
        if r.metadata.get("chunk_level") == "memory" and r.id == pid:
            intro_sim[pid] = sim
            intro_res[pid] = r

    blended: list[tuple[str, float, Any]] = []
    for pid in order:
        isim = intro_sim.get(pid, floor)
        score = alpha * isim + (1.0 - alpha) * best_sim[pid]
        blended.append((pid, score, intro_res.get(pid)))
    blended.sort(key=lambda t: t[1], reverse=True)
    return blended
