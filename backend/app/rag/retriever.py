"""RAG indexing and hybrid (vector + BM25) retrieval."""
from __future__ import annotations

import json
import logging
import math
import threading
from collections import Counter, defaultdict
from pathlib import Path

from app.ingestion.scanner import SourceFile
from app.parsing.models import ParsedFile
from app.rag.chunker import Chunk, chunk_file
from app.rag.embeddings import get_embedder, tokenize
from app.rag.vector_store import open_store

log = logging.getLogger(__name__)
INDEXABLE = {"source", "test", "doc", "config", "build", "infra"}


def build_index(repo_id: str, root: Path, files: list[SourceFile], parsed: dict[str, ParsedFile], rag_dir: Path,
                max_chunks: int = 60000) -> dict:
    rag_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Chunk] = []
    priority = {"doc": 0, "source": 1, "config": 2, "build": 2, "infra": 2, "test": 3}
    for sf in sorted((f for f in files if f.category in INDEXABLE), key=lambda f: priority.get(f.category, 4)):
        try:
            text = (root / sf.path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        chunks.extend(chunk_file(sf, text, parsed.get(sf.path)))
        if len(chunks) >= max_chunks:
            log.warning("Chunk cap %s reached for %s", max_chunks, repo_id)
            chunks = chunks[:max_chunks]
            break

    seen: set[str] = set()
    chunks = [c for c in chunks if not (c.id in seen or seen.add(c.id))]
    (rag_dir / "docs.json").write_text(
        json.dumps([{"id": c.id, "text": c.text, "metadata": c.metadata} for c in chunks]), encoding="utf-8"
    )
    embedder = get_embedder()
    vectors = embedder.embed_documents([c.text for c in chunks])
    store = open_store(repo_id, rag_dir)
    try:
        store.index(chunks, vectors)
    except Exception as exc:  # noqa: BLE001
        if store.backend == "numpy":
            raise
        log.warning("Chroma indexing failed (%s); falling back to NumPy store", exc)
        store = open_store(repo_id, rag_dir, backend="numpy")
        store.index(chunks, vectors)
    manifest = {"backend": store.backend, "embedder": embedder.name, "chunks": len(chunks)}
    (rag_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class Retriever:
    K1, B = 1.4, 0.75

    def __init__(self, repo_id: str, rag_dir: Path) -> None:
        manifest = json.loads((rag_dir / "manifest.json").read_text(encoding="utf-8"))
        self.manifest = manifest
        self.embedder = get_embedder(manifest["embedder"])
        if getattr(self.embedder, "name", "") != manifest["embedder"]:
            self.embedder = None  # index built with an embedder that is no longer configured → lexical only
        self.store = open_store(repo_id, rag_dir, manifest["backend"])
        self.docs: list[dict] = json.loads((rag_dir / "docs.json").read_text(encoding="utf-8"))
        self.by_id = {d["id"]: i for i, d in enumerate(self.docs)}
        self._build_bm25()

    def _build_bm25(self) -> None:
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []
        for i, d in enumerate(self.docs):
            tf = Counter(tokenize(d["text"]))
            self.lengths.append(sum(tf.values()))
            for tok, c in tf.items():
                self.postings[tok].append((i, c))
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0
        self.n_docs = len(self.docs)

    def _bm25(self, query: str, k: int) -> list[int]:
        scores: dict[int, float] = defaultdict(float)
        for tok in set(tokenize(query)):
            plist = self.postings.get(tok)
            if not plist:
                continue
            idf = math.log(1 + (self.n_docs - len(plist) + 0.5) / (len(plist) + 0.5))
            for i, tf in plist:
                denom = tf + self.K1 * (1 - self.B + self.B * self.lengths[i] / self.avg_len)
                scores[i] += idf * tf * (self.K1 + 1) / denom
        return [i for i, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:k]]

    def search(self, query: str, k: int = 8, boost_paths: set[str] | None = None, exclude_tests: bool = False) -> list[dict]:
        fused: dict[str, float] = defaultdict(float)
        if self.embedder is not None:
            try:
                for rank, hit in enumerate(self.store.query(self.embedder.embed_query(query), 40)):
                    fused[hit["id"]] += 1.0 / (60 + rank)
            except Exception as exc:  # noqa: BLE001
                log.warning("Vector query failed: %s", exc)
        for rank, idx in enumerate(self._bm25(query, 40)):
            fused[self.docs[idx]["id"]] += 1.0 / (60 + rank)
        if boost_paths:
            for d in self.docs:
                if d["metadata"]["path"] in boost_paths:
                    fused[d["id"]] += 0.01
        results = []
        per_file: Counter = Counter()
        for doc_id, score in sorted(fused.items(), key=lambda kv: -kv[1]):
            idx = self.by_id.get(doc_id)
            if idx is None:
                continue
            d = self.docs[idx]
            meta = d["metadata"]
            if exclude_tests and meta.get("category") == "test":
                continue
            if per_file[meta["path"]] >= 2:
                continue
            per_file[meta["path"]] += 1
            results.append({**meta, "id": doc_id, "text": d["text"], "score": round(score, 5)})
            if len(results) >= k:
                break
        return results

    def chunks_for(self, path: str, line: int | None = None) -> list[dict]:
        out = [{**d["metadata"], "id": d["id"], "text": d["text"]} for d in self.docs if d["metadata"]["path"] == path]
        if line is not None:
            out = [c for c in out if c["start_line"] <= line <= c["end_line"]] or out[:1]
        return out


_cache: dict[str, Retriever] = {}
_lock = threading.Lock()


def get_retriever(repo_id: str, rag_dir: Path) -> Retriever:
    with _lock:
        r = _cache.get(repo_id)
        if r is None:
            r = Retriever(repo_id, rag_dir)
            if len(_cache) > 8:
                _cache.pop(next(iter(_cache)))
            _cache[repo_id] = r
        return r


def invalidate(repo_id: str) -> None:
    with _lock:
        _cache.pop(repo_id, None)
