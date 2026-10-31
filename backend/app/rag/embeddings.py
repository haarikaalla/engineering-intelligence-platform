"""Embedding providers: OpenAI (when configured) or a deterministic local hashing embedder."""
from __future__ import annotations

import logging
import math
import re
import zlib
from collections import Counter

import numpy as np

from app.config import get_settings

log = logging.getLogger(__name__)
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
STOPWORDS = {
    "the", "a", "an", "is", "are", "of", "to", "in", "and", "or", "for", "on", "with", "this", "that", "it", "be",
    "as", "by", "at", "from", "what", "which", "where", "how", "does", "do", "i", "if", "my", "me", "can", "you",
    "self", "return", "public", "private", "import", "new", "var", "let", "const", "def", "function", "void", "class",
}


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in _WORD_RE.findall(text):
        lower = word.lower()
        if lower in STOPWORDS:
            continue
        tokens.append(lower)
        parts = [p.lower() for piece in word.split("_") for p in _CAMEL_RE.findall(piece)]
        if len(parts) > 1:
            tokens.extend(p for p in parts if len(p) > 1 and p not in STOPWORDS)
    return tokens


class LocalHashEmbedder:
    """Signed feature hashing over identifier sub-tokens and bigrams. Deterministic and dependency-free."""

    name = "local-hash-v1"

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = tokenize(text)
        feats = Counter(toks)
        feats.update(f"{a}_{b}" for a, b in zip(toks, toks[1:]))
        for feat, tf in feats.items():
            h = zlib.crc32(feat.encode())
            v[h % self.dim] += (1.0 if (h >> 31) & 1 else -1.0) * (1.0 + math.log(tf))
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.name = f"openai:{model}"

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), 256):
            batch = [t[:8000] for t in texts[i:i + 256]]
            resp = self.client.embeddings.create(model=self.model, input=batch)
            out.extend(d.embedding for d in resp.data)
        arr = np.array(out, dtype=np.float32)
        return arr / np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-9)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


def get_embedder(name: str | None = None):
    """Return the embedder by name (used at query time to match the index) or the configured default."""
    s = get_settings()
    wants_openai = name.startswith("openai:") if name else (
        s.embedding_provider == "openai" or (s.embedding_provider == "auto" and bool(s.openai_api_key))
    )
    if wants_openai and s.openai_api_key:
        try:
            return OpenAIEmbedder(s.openai_api_key, name.split(":", 1)[1] if name else s.openai_embedding_model)
        except Exception as exc:  # noqa: BLE001
            log.warning("OpenAI embeddings unavailable (%s); falling back to local embedder", exc)
    return LocalHashEmbedder()
