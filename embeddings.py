"""Embedding provider for semantic clustering.

Default: sentence-transformers (local, no API key, model cached on first use).
The default model is BAAI/bge-base-en-v1.5 — 768-dim, ~440MB, top-tier general
retrieval/clustering quality. First call downloads to ~/.cache/huggingface/.

To swap models: set EMBED_MODEL env var (e.g. all-MiniLM-L6-v2 for the lighter
~80MB option, or BAAI/bge-large-en-v1.5 for the bigger 1024-dim variant).

NOTE: embeddings from different models are not comparable. If you switch after
populating research.db, re-embed all rows or accept that pre-switch clusters
won't link to post-switch ones.
"""
import os
import numpy as np


_MODEL = None
_PROVIDER = os.getenv("EMBED_PROVIDER", "local")
_MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-base-en-v1.5")


def _load_local():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(_MODEL_NAME)


def get_model():
    global _MODEL
    if _MODEL is None:
        if _PROVIDER == "local":
            _MODEL = _load_local()
        else:
            raise ValueError(f"Unknown EMBED_PROVIDER: {_PROVIDER!r}")
    return _MODEL


def embed(text: str) -> bytes:
    """Embed text; returns float32 bytes for SQLite BLOB storage."""
    text = (text or "").strip()
    if not text:
        return b""
    vec = get_model().encode(text, normalize_embeddings=True)
    return np.asarray(vec, dtype=np.float32).tobytes()


def cosine(a: bytes, b: bytes) -> float:
    """Cosine sim between two normalized embeddings."""
    if not a or not b:
        return 0.0
    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    if va.shape != vb.shape:
        return 0.0
    return float(np.dot(va, vb))


def canonical_text(title: str, summary: str, key_insight: str) -> str:
    """The text we embed for clustering. Title + summary + insight captures
    'what the idea is' better than any single field."""
    return f"{title}\n\n{summary}\n\n{key_insight}".strip()
