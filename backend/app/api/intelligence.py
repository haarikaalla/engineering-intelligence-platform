from __future__ import annotations

import logging
import re
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.analysis.explain import explain_file, goal_fit
from app.analysis.report import build_report, report_markdown
from app.llm.grounding import grounded_narrative
from app.analysis.impact import analyze_impact
from app.analysis.pr import analyze_pr, fetch_github_pr, parse_unified_diff
from app.analysis.testgen import build_test_prompt, generate_template
from app.api.deps import ChatRequest, GoalFitRequest, ImpactRequest, PRRequest, TestGenRequest, get_ready_repo, require_api_key
from app.config import get_settings
from app.db.database import get_db
from app.db.models import ChatMessage, Repository
from app.llm.agent import run_agent
from app.llm.provider import get_llm
from app.rag.retriever import get_retriever
from app.services.context import RepoContext, load_context, rag_dir, read_artifact, write_artifact

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/repositories/{repo_id}", tags=["intelligence"], dependencies=[Depends(require_api_key)])


def _ctx(repo: Repository) -> RepoContext:
    try:
        return load_context(repo.id, repo.name)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Analysis artifacts missing; re-analyze the repository") from exc


def _record(repo: Repository) -> dict:
    return {"overview": repo.overview, "architecture": repo.architecture, "security": repo.security,
            "scores": repo.scores, "test_insights": repo.test_insights, "purpose": repo.purpose}


# ---------------------------------------------------------------- evidence-backed report
def _report(repo: Repository) -> dict:
    report = read_artifact(repo.id, "report")
    if report is None:
        ctx = _ctx(repo)
        report = build_report(ctx, repo.overview or {}, repo.architecture or {}, repo.purpose or {}, repo.security or {}, repo.test_insights or {})
        write_artifact(repo.id, "report", report)
    return report


@router.get("/report")
async def report(repo: Repository = Depends(get_ready_repo)) -> dict:
    return await run_in_threadpool(_report, repo)


@router.get("/report/markdown", response_class=PlainTextResponse)
async def report_md(repo: Repository = Depends(get_ready_repo)) -> PlainTextResponse:
    rep = await run_in_threadpool(_report, repo)
    safe = re.sub(r"[^\w.\-]+", "_", repo.name)
    return PlainTextResponse(report_markdown(rep), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{safe}-report.md"'})


@router.post("/report/narrative")
async def report_narrative(repo: Repository = Depends(get_ready_repo)) -> dict:
    llm = get_llm()
    if llm is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No LLM configured (set OPENAI_API_KEY or ANTHROPIC_API_KEY). The evidence report above is complete without it.")
    ctx = await run_in_threadpool(_ctx, repo)
    rep = await run_in_threadpool(_report, repo)
    try:
        narrative = await run_in_threadpool(grounded_narrative, ctx, llm, rep)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM call failed: {type(exc).__name__}") from exc
    rep["narrative"] = {**narrative, "model": llm.name}
    write_artifact(repo.id, "report", rep)
    return rep["narrative"]


# ---------------------------------------------------------------- purpose & adoption
@router.get("/purpose")
def purpose(repo: Repository = Depends(get_ready_repo)) -> dict:
    if not repo.purpose:
        raise HTTPException(status.HTTP_409_CONFLICT, "Purpose insights not available; re-analyze the repository")
    return repo.purpose


@router.post("/purpose/fit")
async def purpose_fit(body: GoalFitRequest, repo: Repository = Depends(get_ready_repo)) -> dict:
    ctx = await run_in_threadpool(_ctx, repo)
    try:
        retriever = await run_in_threadpool(get_retriever, repo.id, rag_dir(repo.id))
    except FileNotFoundError:
        retriever = None
    result = await run_in_threadpool(goal_fit, ctx, retriever, body.goal, _record(repo))
    llm = get_llm()
    if llm is not None:
        prompt = (
            f"Our goal: {body.goal}\n\nRepository: {repo.name}\nWhat it does: {(repo.purpose or {}).get('what_it_solves')}\n"
            f"Fit analysis: {result}\n\nAdvise how we can use this repository for our goal: what to reuse as-is, what to adapt, "
            "what to build new, integration approach, risks, and a phased plan. Be specific to the modules/APIs listed. Max 400 words."
        )
        try:
            result["ai_advice"] = await run_in_threadpool(llm.complete, "You are a principal solutions architect.", [("user", prompt)], 1400)
        except Exception as exc:  # noqa: BLE001
            log.warning("Goal-fit advice failed: %s", exc)
    return result


@router.get("/explain")
async def explain(path: str, ai: bool = True, repo: Repository = Depends(get_ready_repo)) -> dict:
    ctx = await run_in_threadpool(_ctx, repo)
    if path not in ctx.file_info:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    result = await run_in_threadpool(explain_file, ctx, path, repo.security)
    llm = get_llm() if ai else None
    if llm is not None:
        content = ctx.read(path, 24000)
        prompt = (
            f"Explain this file to a developer new to the codebase: its responsibility, how it works step by step, key functions, "
            f"collaborators, side effects (DB, network, events) and pitfalls. Structure facts: {result['summary']}\n\nFile {path}:\n```\n{content}\n```"
        )
        try:
            result["ai_explanation"] = await run_in_threadpool(llm.complete, "You are a senior engineer mentoring a new teammate.", [("user", prompt)], 1500)
        except Exception as exc:  # noqa: BLE001
            log.warning("File explanation failed: %s", exc)
    return result


# ---------------------------------------------------------------- overview & scores
@router.get("/overview")
def overview(repo: Repository = Depends(get_ready_repo)) -> dict:
    return {**(repo.overview or {}), "scores": repo.scores}


@router.get("/architecture")
def architecture(repo: Repository = Depends(get_ready_repo)) -> dict:
    return repo.architecture or {}


@router.get("/scores")
def scores(repo: Repository = Depends(get_ready_repo)) -> dict:
    return repo.scores or {}


@router.get("/security")
def security(repo: Repository = Depends(get_ready_repo), severity: str | None = None, category: str | None = None) -> dict:
    data = dict(repo.security or {})
    findings = data.get("findings", [])
    if severity:
        findings = [f for f in findings if f["severity"] == severity]
    if category:
        findings = [f for f in findings if f["category"] == category]
    data["findings"] = findings
    return data


# ---------------------------------------------------------------- files & symbols
@router.get("/files")
def files(repo: Repository = Depends(get_ready_repo), q: str = "", category: str | None = None, limit: int = Query(50, le=500)) -> list[dict]:
    ctx = _ctx(repo)
    needle = q.lower()
    out = []
    for f in ctx.files:
        if category and f.category != category:
            continue
        if f.category not in ("source", "test", "config", "infra", "build"):
            continue
        if needle and needle not in f.path.lower():
            continue
        out.append({"path": f.path, "language": f.language, "category": f.category, "module": f.module, "loc": f.loc})
        if len(out) >= limit:
            break
    return out


@router.get("/file")
def file_content(path: str, repo: Repository = Depends(get_ready_repo)) -> dict:
    ctx = _ctx(repo)
    if path not in ctx.file_info:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    pf = ctx.parsed.get(path)
    return {
        "path": path,
        "language": ctx.file_info[path].language,
        "content": ctx.read(path, 400_000),
        "symbols": [{"name": s.qualified, "kind": s.kind, "line": s.start_line, "end_line": s.end_line, "complexity": s.complexity}
                    for s in (pf.symbols if pf else [])],
        "endpoints": [e.__dict__ for e in (pf.endpoints if pf else [])],
    }


# ---------------------------------------------------------------- knowledge graph
_VIEW_LABELS = {"Repository", "Service", "Module", "Database", "Topic", "Infrastructure"}


@router.get("/graph")
def graph(
    repo: Repository = Depends(get_ready_repo),
    view: str = Query("services", pattern="^(services|hierarchy|focus)$"),
    module: str | None = None,
    node: str | None = None,
    depth: int = Query(1, ge=1, le=3),
    limit: int = Query(350, ge=10, le=2000),
) -> dict:
    ctx = _ctx(repo)
    g = ctx.graph
    if view == "services":
        ids = {n["id"] for n in g.nodes.values() if n["label"] in _VIEW_LABELS}
        sub = g.subgraph(ids)
        # Aggregate file/function-level data access to module → database edges
        agg: Counter = Counter()
        for src, rel, dst in g.edges():
            if rel == "ACCESSES" and dst.startswith("db:") and not src.startswith("module:"):
                mod = g.nodes[src].get("module")
                if mod:
                    agg[(f"module:{mod}", dst)] += 1
        sub["edges"] += [{"source": s, "target": d, "type": "ACCESSES", "weight": w} for (s, d), w in agg.items()]
        return sub
    if view == "hierarchy":
        ids: set[str] = {"repo"} | {f"module:{m.name}" for m in ctx.modules}
        if module:
            mid = f"module:{module}"
            if mid not in g.nodes:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found")
            files = [f for f in g.successors(mid, ("CONTAINS",)) if not g.nodes[f].get("is_test")]
            apis = list(g.successors(mid, ("EXPOSES",)))
            ids |= set(apis)
            classes = [c for f in files for c in g.successors(f, ("DEFINES",)) if g.nodes[c]["label"] == "Class"]
            funcs = [fn for c in classes for fn in g.successors(c, ("DEFINES",))]
            funcs += [fn for f in files for fn in g.successors(f, ("DEFINES",)) if g.nodes[fn]["label"] == "Function"]
            handlers = {h for a in apis for h in g.successors(a, ("HANDLED_BY",))}
            budget = max(0, limit - len(ids))
            ids |= set(classes[: budget // 2])
            ids |= handlers
            ids |= set(sorted(funcs, key=lambda f: (f not in handlers, -g.nodes[f].get("complexity", 1)))[: max(0, limit - len(ids))])
            for n in list(ids):
                for d in g.successors(n, ("ACCESSES",)):
                    ids.add(d)
                    ids |= g.successors(d, ("STORED_IN",))
        sub = g.subgraph(ids, rels=("CONTAINS", "EXPOSES", "HANDLED_BY", "DEFINES", "CALLS", "ACCESSES", "STORED_IN", "DEPENDS_ON"))
        # Bridge module → class (file layer hidden in this view)
        if module:
            for f in g.successors(f"module:{module}", ("CONTAINS",)):
                for c in g.successors(f, ("DEFINES",)):
                    if c in ids:
                        sub["edges"].append({"source": f"module:{module}", "target": c, "type": "CONTAINS"})
            for src, rel, dst in g.edges():
                if rel == "ACCESSES" and src.startswith("file:") and dst in ids:
                    for c in g.successors(src, ("DEFINES",)):
                        if c in ids:
                            sub["edges"].append({"source": c, "target": dst, "type": "ACCESSES"})
                            break
        return sub
    if not node or node not in g.nodes:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
    ids = {node}
    frontier = {node}
    for _ in range(depth):
        nxt: set[str] = set()
        for n in frontier:
            nxt |= g.successors(n) | g.predecessors(n)
        nxt -= ids
        ids |= set(sorted(nxt)[: max(0, limit - len(ids))])
        frontier = nxt
    return g.subgraph(ids)


@router.get("/graph/search")
def graph_search(q: str = Query(..., min_length=2, max_length=200), repo: Repository = Depends(get_ready_repo)) -> list[dict]:
    return _ctx(repo).graph.search(q, limit=25)


@router.get("/graph/stats")
def graph_stats(repo: Repository = Depends(get_ready_repo)) -> dict:
    return _ctx(repo).graph.stats()


# ---------------------------------------------------------------- chat
@router.get("/chat")
def chat_history(repo: Repository = Depends(get_ready_repo), db: Session = Depends(get_db)) -> list[dict]:
    msgs = db.scalars(select(ChatMessage).where(ChatMessage.repository_id == repo.id).order_by(ChatMessage.created_at)).all()
    return [{"id": m.id, "role": m.role, "content": m.content, "sources": m.sources, "created_at": m.created_at.isoformat()} for m in msgs[-100:]]


@router.delete("/chat", status_code=status.HTTP_204_NO_CONTENT)
def clear_chat(repo: Repository = Depends(get_ready_repo), db: Session = Depends(get_db)) -> None:
    db.execute(delete(ChatMessage).where(ChatMessage.repository_id == repo.id))
    db.commit()


@router.post("/chat")
async def chat(body: ChatRequest, repo: Repository = Depends(get_ready_repo), db: Session = Depends(get_db)) -> dict:
    ctx = await run_in_threadpool(_ctx, repo)
    try:
        retriever = await run_in_threadpool(get_retriever, repo.id, rag_dir(repo.id))
    except FileNotFoundError:
        retriever = None
    prior = db.scalars(select(ChatMessage).where(ChatMessage.repository_id == repo.id).order_by(ChatMessage.created_at.desc()).limit(6)).all()
    history = [(m.role, m.content) for m in reversed(prior)]
    result = await run_in_threadpool(run_agent, ctx, retriever, _record(repo), body.message, history)
    db.add(ChatMessage(repository_id=repo.id, role="user", content=body.message))
    db.add(ChatMessage(repository_id=repo.id, role="assistant", content=result["answer"], sources=result["sources"]))
    db.commit()
    return result


# ---------------------------------------------------------------- impact
@router.post("/impact")
async def impact(body: ImpactRequest, repo: Repository = Depends(get_ready_repo)) -> dict:
    ctx = await run_in_threadpool(_ctx, repo)
    if body.path not in ctx.file_info:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found in repository")
    return await run_in_threadpool(analyze_impact, ctx, [body.path], body.symbol, body.max_depth)


# ---------------------------------------------------------------- pull requests
@router.post("/pr")
async def pull_request(body: PRRequest, repo: Repository = Depends(get_ready_repo)) -> dict:
    settings = get_settings()
    ctx = await run_in_threadpool(_ctx, repo)
    meta: dict = {}
    if body.pr_url:
        try:
            meta, diffs = await run_in_threadpool(fetch_github_pr, body.pr_url, body.token or settings.github_token, settings.allowed_git_hosts)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to fetch pull request: {type(exc).__name__}") from exc
        diff_text = None
    elif body.diff:
        diffs = parse_unified_diff(body.diff)
        diff_text = body.diff
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide either a unified diff or a pull request URL")
    if not diffs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No file changes found in diff")
    result = await run_in_threadpool(analyze_pr, ctx, diffs, meta)
    llm = get_llm() if body.ai_review else None
    if llm is not None:
        excerpt = diff_text[:18000] if diff_text else "\n".join(
            f"--- {d.path} ({d.status})\n" + "\n".join(f"+{t}" for _, t in d.added[:120]) for d in diffs[:25])[:18000]
        prompt = (
            "Review this pull request as a principal engineer. Give: 1) a two-sentence summary, 2) the top risks, "
            "3) concrete review comments as `file:line — comment`, 4) tests that should be added. Be concise.\n\n"
            f"Risk factors: {result['risk']}\nArchitecture notes: {result['architecture'][:15]}\n"
            f"Security findings: {[(f['title'], f['file'], f['line']) for f in result['security']['findings'][:15]]}\n"
            f"Impact summary: {(result['impact'] or {}).get('summary')}\n\nDIFF:\n{excerpt}"
        )
        try:
            result["ai_review"] = await run_in_threadpool(llm.complete, "You are a meticulous code reviewer.", [("user", prompt)], 1500)
        except Exception as exc:  # noqa: BLE001
            log.warning("AI review failed: %s", exc)
    return result


# ---------------------------------------------------------------- tests
@router.get("/tests/insights")
def tests_insights(repo: Repository = Depends(get_ready_repo)) -> dict:
    return repo.test_insights or {}


@router.post("/tests/generate")
async def generate_tests(body: TestGenRequest, repo: Repository = Depends(get_ready_repo)) -> dict:
    ctx = await run_in_threadpool(_ctx, repo)
    if body.path not in ctx.parsed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source file not found or not parseable")
    template = await run_in_threadpool(generate_template, ctx, body.path, body.symbol, body.kind)
    llm = get_llm() if body.use_llm else None
    if llm is None:
        return template
    prompt = await run_in_threadpool(build_test_prompt, ctx, body.path, body.symbol, body.kind)
    try:
        answer = await run_in_threadpool(llm.complete, "You are a senior test engineer who writes idiomatic, runnable tests.", [("user", prompt)], 3000)
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM test generation failed: %s", exc)
        return {**template, "notes": "LLM unavailable; returned template"}
    m = re.search(r"```[\w+#-]*\n(.*?)```", answer, re.S)
    return {**template, "code": (m.group(1) if m else answer).strip() + "\n", "mode": llm.name}


@router.get("/modules")
def modules(repo: Repository = Depends(get_ready_repo)) -> list[dict]:
    ctx = _ctx(repo)
    return [{"name": m.name, "path": m.path, "kind": m.kind, "loc": m.loc} for m in ctx.modules]
