"""End-to-end ingestion pipeline: clone → scan → parse → graph → index → analyze."""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath

from app.analysis.architecture import compute_architecture, compute_overview
from app.analysis.purpose import compute_purpose
from app.analysis.report import build_report
from app.analysis.scoring import compute_scores
from app.analysis.security import security_report
from app.analysis.testgen import test_insights
from app.config import get_settings
from app.db.database import session_scope
from app.db.models import Repository
from app.graph.builder import build_graph
from app.graph.neo4j_store import get_mirror
from app.ingestion.git_ops import CloneError, clone_repository, head_commit, mine_history, parse_repo_url
from app.ingestion.infra import discover_infrastructure
from app.ingestion.manifests import collect_dependencies, detect_frameworks
from app.ingestion.scanner import scan_repository
from app.llm.provider import get_llm
from app.parsing.registry import PARSEABLE, parse_config, parse_source, parser_backend
from app.rag import retriever as rag_retriever
from app.rag.retriever import build_index
from app.services.context import (
    RepoContext, artifacts_dir, invalidate_context, put_context, rag_dir, source_dir, write_artifact,
)

log = logging.getLogger(__name__)
_SERVER_ENTRY = ("Spring Boot", "ASP.NET Core", "FastAPI", "Flask", "Django", "NestJS", "HTTP server", "Go main()", ".NET Main()", "Java main()")


def _update(repo_id: str, **fields) -> None:
    with session_scope() as db:
        repo = db.get(Repository, repo_id)
        if repo is not None:
            for k, v in fields.items():
                setattr(repo, k, v)


def run_pipeline(repo_id: str, token: str | None = None) -> None:
    settings = get_settings()
    started = time.time()
    with session_scope() as db:
        repo = db.get(Repository, repo_id)
        if repo is None:
            return
        url, branch, source, name = repo.url, repo.branch, repo.source, repo.name
    root = source_dir(repo_id)
    try:
        invalidate_context(repo_id)
        rag_retriever.invalidate(repo_id)
        _update(repo_id, status="running", stage="cloning", progress=3, error=None)
        if source == "git":
            ref = parse_repo_url(url, settings.allowed_git_hosts)
            sha = clone_repository(ref, root, branch, token or settings.github_token, settings.clone_depth, settings.clone_timeout_s)
        else:
            sha = head_commit(root)
        _update(repo_id, commit_sha=sha, stage="scanning", progress=12)

        scan = scan_repository(root, settings.max_file_size_kb, settings.max_files)
        deps = collect_dependencies(root, scan.files)
        infra = discover_infrastructure(root, scan.files)
        frameworks = detect_frameworks(scan.files, deps, set(infra["kinds"]))
        _update(repo_id, stage="parsing", progress=20)

        parsed, config_parsed = {}, {}
        parse_targets = [f for f in scan.files if f.category in ("source", "test") and f.language in PARSEABLE]
        for i, f in enumerate(parse_targets):
            text = (root / f.path).read_text(encoding="utf-8", errors="ignore")
            parsed[f.path] = parse_source(f.path, f.language, text)
            if i % 400 == 0:
                _update(repo_id, progress=20 + int(25 * i / max(1, len(parse_targets))))
        for f in scan.files:
            if f.category in ("config", "infra") and f.language in ("YAML", "Properties", "JSON", "XML", "INI", "TOML", "Config"):
                cp = parse_config(f.path, f.language, (root / f.path).read_text(encoding="utf-8", errors="ignore"))
                if cp.db_access or cp.http_targets:
                    config_parsed[f.path] = cp

        _classify_modules(scan, parsed, infra)
        history = mine_history(root)
        _update(repo_id, stage="building knowledge graph", progress=50)
        graph = build_graph(name, scan.files, scan.modules, parsed, config_parsed, infra)
        artifacts_dir(repo_id).mkdir(parents=True, exist_ok=True)
        graph.save(artifacts_dir(repo_id) / "graph.json")
        neo4j_synced = get_mirror().sync(repo_id, graph)

        write_artifact(repo_id, "scan", scan.to_dict())
        write_artifact(repo_id, "parsed", {p: pf.to_dict() for p, pf in parsed.items()})
        write_artifact(repo_id, "dependencies", deps)
        write_artifact(repo_id, "infra", infra)
        write_artifact(repo_id, "history", history)
        write_artifact(repo_id, "frameworks", frameworks)
        write_artifact(repo_id, "meta", {"name": name, "url": url})

        _update(repo_id, stage="indexing embeddings", progress=62)
        rag_manifest = build_index(repo_id, root, scan.files, parsed, rag_dir(repo_id))

        _update(repo_id, stage="analyzing", progress=80)
        ctx = RepoContext(repo_id=repo_id, name=name, root=root, files=scan.files, modules=scan.modules, parsed=parsed,
                          graph=graph, dependencies=deps, infra=infra, history=history, frameworks=frameworks)
        put_context(ctx)
        overview = compute_overview(ctx)
        overview["pipeline"] = {
            "parser": parser_backend(), "parsers_used": _parser_counts(parsed), "rag": rag_manifest,
            "neo4j_synced": neo4j_synced, "duration_s": None, "skipped_files": scan.skipped,
        }
        architecture = compute_architecture(ctx)
        _update(repo_id, stage="security analysis", progress=88)
        security = security_report(ctx)
        tests = test_insights(ctx)
        scores = compute_scores(ctx, architecture, security, tests)
        purpose = compute_purpose(ctx, overview, architecture, security, tests, scores)
        purpose["ai_brief"] = _ai_brief(ctx.name, overview, architecture, purpose)
        write_artifact(repo_id, "report", build_report(ctx, overview, architecture, purpose, security, tests))
        overview["pipeline"]["duration_s"] = round(time.time() - started, 1)
        _update(repo_id, overview=overview, architecture=architecture, security=security, scores=scores, test_insights=tests,
                purpose=purpose, status="ready", stage="ready", progress=100)
        log.info("Repository %s analyzed in %.1fs", repo_id, time.time() - started)
    except (CloneError, ValueError) as exc:
        _update(repo_id, status="failed", stage="failed", error=str(exc)[:2000])
    except Exception as exc:  # noqa: BLE001
        log.exception("Pipeline failed for %s", repo_id)
        _update(repo_id, status="failed", stage="failed", error=f"{type(exc).__name__}: {str(exc)[:1500]}")


def _parser_counts(parsed: dict) -> dict:
    out: dict[str, int] = {}
    for pf in parsed.values():
        out[pf.parser] = out.get(pf.parser, 0) + 1
    return out


def _ai_brief(name: str, overview: dict, architecture: dict, purpose: dict) -> str | None:
    llm = get_llm()
    if llm is None:
        return None
    facts = {
        "summary": overview.get("summary"), "readme": purpose.get("readme_purpose"), "type": purpose["project_type"],
        "domains": purpose["domains"], "features": purpose["key_features"], "tech": overview.get("tech_stack"),
        "architecture": architecture.get("style"), "flows": architecture.get("business_flow_summary"),
        "modules": [m["summary"] for m in purpose["main_functionality"][:10]],
        "readiness": {k: purpose["enterprise_readiness"][k] for k in ("score", "level")},
        "suggestions": purpose["suggestions"],
    }
    prompt = (
        f"Write an executive brief for the repository '{name}' for an enterprise architecture review board. Sections: "
        "1) What it is and what problem it solves, 2) Who would use it and typical use cases, 3) Main functionality, "
        "4) How an enterprise could adopt or reuse it, 5) What to develop next, 6) Risks. Be concrete and concise (max 350 words).\n\n"
        f"FACTS (JSON):\n{json.dumps(facts, default=str)[:14000]}"
    )
    try:
        return llm.complete("You are a principal software architect.", [("user", prompt)], 1200)
    except Exception as exc:  # noqa: BLE001
        log.warning("AI brief generation failed: %s", exc)
        return None


def _classify_modules(scan, parsed: dict, infra: dict) -> None:
    by_module: dict[str, list] = {}
    for f in scan.files:
        by_module.setdefault(f.module, []).append(f)
    compose_contexts = {(s.get("build_context") or ".") for s in infra.get("compose_services", []) if s.get("build_context") is not None}
    dockerfile_dirs = {str(PurePosixPath(d).parent) for d in infra.get("dockerfiles", [])}
    for m in scan.modules:
        files = by_module.get(m.name, [])
        entries = [f"{f.path} ({parsed[f.path].entry_point})" for f in files
                   if f.path in parsed and parsed[f.path].entry_point and f.category == "source"]
        m.entry_points = entries[:20]
        has_endpoints = any(parsed[f.path].endpoints for f in files if f.path in parsed)
        server_entry = any(any(s in e for s in _SERVER_ENTRY) for e in entries)
        containerized = m.path in compose_contexts or m.path in dockerfile_dirs
        if m.kind == "frontend":
            continue
        logical = m.manifest is None and m.path != "."
        if logical and not containerized:
            m.kind = "component"  # feature package inside a deployable unit
        elif has_endpoints or server_entry or containerized:
            m.kind = "service"
        elif entries:
            m.kind = "application"
        elif m.kind == "component" and m.manifest:
            m.kind = "library"


class JobRunner:
    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=get_settings().pipeline_workers, thread_name_prefix="pipeline")
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, repo_id: str, token: str | None = None) -> bool:
        with self._lock:
            if repo_id in self._active:
                return False
            self._active.add(repo_id)

        def _run() -> None:
            try:
                run_pipeline(repo_id, token)
            finally:
                with self._lock:
                    self._active.discard(repo_id)

        self._pool.submit(_run)
        return True

    def is_active(self, repo_id: str) -> bool:
        with self._lock:
            return repo_id in self._active


runner = JobRunner()
