"""Loads and caches per-repository analysis artifacts (scan, parsed files, graph, dependencies, infra, history)."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings
from app.graph.store import KnowledgeGraph
from app.ingestion.scanner import ModuleInfo, SourceFile
from app.parsing.models import ParsedFile


def repo_dir(repo_id: str) -> Path:
    if not repo_id.isalnum():
        raise ValueError("invalid repository id")
    return get_settings().workspace_dir / repo_id


def source_dir(repo_id: str) -> Path:
    return repo_dir(repo_id) / "source"


def artifacts_dir(repo_id: str) -> Path:
    return repo_dir(repo_id) / "artifacts"


def rag_dir(repo_id: str) -> Path:
    return repo_dir(repo_id) / "rag"


def write_artifact(repo_id: str, name: str, data) -> None:
    d = artifacts_dir(repo_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(data, default=str), encoding="utf-8")


def read_artifact(repo_id: str, name: str, default=None):
    p = artifacts_dir(repo_id) / f"{name}.json"
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


@dataclass
class RepoContext:
    repo_id: str
    name: str
    root: Path
    files: list[SourceFile]
    modules: list[ModuleInfo]
    parsed: dict[str, ParsedFile]
    graph: KnowledgeGraph
    dependencies: list[dict]
    infra: dict
    history: dict
    frameworks: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.file_info = {f.path: f for f in self.files}

    def read(self, path: str, max_chars: int | None = None) -> str:
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root.resolve()) or not target.is_file():
            return ""
        text = target.read_text(encoding="utf-8", errors="ignore")
        return text[:max_chars] if max_chars else text

    def module_of(self, path: str) -> str | None:
        info = self.file_info.get(path)
        return info.module if info else None


_cache: dict[str, RepoContext] = {}
_lock = threading.Lock()


def load_context(repo_id: str, name: str = "") -> RepoContext:
    with _lock:
        if repo_id in _cache:
            return _cache[repo_id]
    scan = read_artifact(repo_id, "scan")
    if scan is None:
        raise FileNotFoundError("Repository has not been analyzed yet")
    ctx = RepoContext(
        repo_id=repo_id,
        name=name or read_artifact(repo_id, "meta", {}).get("name", repo_id),
        root=source_dir(repo_id),
        files=[SourceFile(**f) for f in scan["files"]],
        modules=[ModuleInfo(**m) for m in scan["modules"]],
        parsed={p: ParsedFile.from_dict(d) for p, d in read_artifact(repo_id, "parsed", {}).items()},
        graph=KnowledgeGraph.load(artifacts_dir(repo_id) / "graph.json"),
        dependencies=read_artifact(repo_id, "dependencies", []),
        infra=read_artifact(repo_id, "infra", {}),
        history=read_artifact(repo_id, "history", {}),
        frameworks=read_artifact(repo_id, "frameworks", []),
    )
    with _lock:
        if len(_cache) >= 6:
            _cache.pop(next(iter(_cache)))
        _cache[repo_id] = ctx
    return ctx


def put_context(ctx: RepoContext) -> None:
    with _lock:
        _cache[ctx.repo_id] = ctx


def invalidate_context(repo_id: str) -> None:
    with _lock:
        _cache.pop(repo_id, None)
