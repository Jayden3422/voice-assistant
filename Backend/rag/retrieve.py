"""Retrieve relevant chunks from the FAISS knowledge base."""

import hashlib
import json
import logging
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

STORE_DIR = Path(__file__).resolve().parent.parent / "rag_store"
_retrieval_cache: dict[str, list[dict]] = {}
_faiss_index = None
_faiss_meta: list[dict] | None = None
_faiss_index_mtime: float | None = None
_faiss_meta_mtime: float | None = None


def _query_hash(query: str, top_k: int) -> str:
    return hashlib.sha256(f"{query}::{top_k}".encode()).hexdigest()[:16]


async def retrieve(
    query: str,
    client,
    *,
    top_k: int = 5,
    model: str | None = None,
) -> list[dict]:
    """
    Retrieve top-K chunks from the knowledge base.
    Returns list of {doc, chunk, score, text}.
    """
    import faiss

    cache_key = _query_hash(query, top_k)
    if cache_key in _retrieval_cache:
        logger.info("Retrieval cache hit for query hash %s", cache_key)
        return _retrieval_cache[cache_key]

    index_path = STORE_DIR / "kb.index"
    meta_path = STORE_DIR / "kb_meta.json"

    if not index_path.exists() or not meta_path.exists():
        logger.warning("FAISS index not found at %s. Run ingest first.", STORE_DIR)
        return []

    model = model or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    # Embed query
    resp = await client.embeddings.create(model=model, input=[query])
    q_vec = np.array([resp.data[0].embedding], dtype="float32")
    faiss.normalize_L2(q_vec)

    # Load index and metadata with caching
    global _faiss_index, _faiss_meta, _faiss_index_mtime, _faiss_meta_mtime
    index_mtime = index_path.stat().st_mtime
    meta_mtime = meta_path.stat().st_mtime
    if (
        _faiss_index is None
        or _faiss_meta is None
        or _faiss_index_mtime != index_mtime
        or _faiss_meta_mtime != meta_mtime
    ):
        _faiss_index = faiss.read_index(str(index_path))
        with open(meta_path, "r", encoding="utf-8") as f:
            _faiss_meta = json.load(f)
        _faiss_index_mtime = index_mtime
        _faiss_meta_mtime = meta_mtime
        _retrieval_cache.clear()

    index = _faiss_index
    meta = _faiss_meta

    actual_k = min(top_k, index.ntotal)
    if actual_k == 0:
        return []

    scores, indices = index.search(q_vec, actual_k)

    results = []
    for rank in range(actual_k):
        idx = int(indices[0][rank])
        if idx < 0:
            continue
        score = float(scores[0][rank])
        m = meta[idx]
        results.append({
            "doc": m["doc"],
            "chunk": m["chunk_index"],
            "score": round(score, 4),
            "text": m["text"],
        })

    _retrieval_cache[cache_key] = results
    logger.info("Retrieved %d chunks for query (len=%d)", len(results), len(query))
    return results
