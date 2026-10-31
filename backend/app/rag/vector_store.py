"""Vector stores: ChromaDB (preferred) with an embedded NumPy fallback."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from app.config import get_settings
from app.rag.chunker import Chunk

log = logging.getLogger(__name__)


class NumpyVectorStore:
    backend = "numpy"

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self._matrix: np.ndarray | None = None
        self._docs: list[dict] | None = None

    def index(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        # docs.json is written by the indexer (shared with the lexical index)
        self.dir.mkdir(parents=True, exist_ok=True)
        np.save(self.dir / "vectors.npy", vectors.astype(np.float32))
        self._matrix = vectors
        self._docs = [{"id": c.id, "text": c.text, "metadata": c.metadata} for c in chunks]

    def _load(self) -> None:
        if self._matrix is None:
            self._matrix = np.load(self.dir / "vectors.npy")
            self._docs = json.loads((self.dir / "docs.json").read_text(encoding="utf-8"))

    def query(self, vector: np.ndarray, k: int) -> list[dict]:
        self._load()
        if self._matrix is None or len(self._matrix) == 0:
            return []
        scores = self._matrix @ vector
        top = np.argsort(-scores)[:k]
        return [{**self._docs[i], "score": float(scores[i])} for i in top]

    def by_path(self, path: str) -> list[dict]:
        self._load()
        return [{**d, "score": 0.0} for d in self._docs or [] if d["metadata"]["path"] == path]

    def all_docs(self) -> list[dict]:
        self._load()
        return self._docs or []

    def delete(self) -> None:
        for name in ("vectors.npy", "docs.json"):
            (self.dir / name).unlink(missing_ok=True)


class ChromaVectorStore:
    backend = "chroma"

    def __init__(self, repo_id: str, directory: Path) -> None:
        import chromadb

        s = get_settings()
        if s.chroma_host:
            self.client = chromadb.HttpClient(host=s.chroma_host, port=s.chroma_port)
        else:
            self.client = chromadb.PersistentClient(path=str(directory))
        self.name = f"repo_{repo_id}"

    def index(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        try:
            self.client.delete_collection(self.name)
        except Exception:  # noqa: BLE001 - collection may not exist
            pass
        col = self.client.create_collection(self.name, metadata={"hnsw:space": "cosine"})
        for i in range(0, len(chunks), 1000):
            batch = chunks[i:i + 1000]
            col.add(
                ids=[c.id for c in batch],
                documents=[c.text for c in batch],
                metadatas=[{k: v for k, v in c.metadata.items() if v is not None} for c in batch],
                embeddings=vectors[i:i + 1000].tolist(),
            )

    def query(self, vector: np.ndarray, k: int) -> list[dict]:
        col = self.client.get_collection(self.name)
        res = col.query(query_embeddings=[vector.tolist()], n_results=k, include=["documents", "metadatas", "distances"])
        return [
            {"id": i, "text": d, "metadata": m, "score": 1.0 - float(dist)}
            for i, d, m, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0])
        ]

    def by_path(self, path: str) -> list[dict]:
        col = self.client.get_collection(self.name)
        res = col.get(where={"path": path}, include=["documents", "metadatas"])
        return [{"id": i, "text": d, "metadata": m, "score": 0.0} for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])]

    def delete(self) -> None:
        try:
            self.client.delete_collection(self.name)
        except Exception:  # noqa: BLE001
            pass


def open_store(repo_id: str, rag_dir: Path, backend: str | None = None):
    """Open the store for a repo. backend=None picks the configured default (used when indexing)."""
    s = get_settings()
    want_chroma = backend == "chroma" or (backend is None and s.use_chroma)
    if want_chroma:
        try:
            return ChromaVectorStore(repo_id, rag_dir / "chroma")
        except Exception as exc:  # noqa: BLE001
            if backend == "chroma":
                raise
            log.info("ChromaDB unavailable (%s); using embedded NumPy vector store", exc)
    return NumpyVectorStore(rag_dir)
