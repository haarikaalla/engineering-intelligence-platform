"""Large-file explainer and 'fit for our purpose' analysis."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from app.analysis.architecture import layer_of
from app.rag.embeddings import STOPWORDS, tokenize
from app.services.context import RepoContext

_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_VERBS = {"get", "set", "is", "has", "find", "create", "update", "delete", "remove", "add", "save", "load", "build", "make", "to", "from",
          "handle", "process", "validate", "check", "init", "on", "do", "run", "parse", "compute", "calculate", "fetch", "list", "put",
          "post", "apply", "map", "convert", "send", "read", "write", "with", "for", "of", "new", "show", "render", "test", "should", "can"}
_ROLE_ANNOTATIONS = [
    (r"@RestController|@Controller|\[ApiController\]|@Controller\(", "HTTP controller (presentation layer)"),
    (r"@Service\b", "Business service (application layer)"),
    (r"@Repository\b", "Data repository (data layer)"),
    (r"@Entity\b|__tablename__|\[Table\(", "Persistent entity (domain model)"),
    (r"@Configuration\b|@ConfigurationProperties", "Configuration"),
    (r"@Component\b|@Injectable\(", "Managed component"),
    (r"@KafkaListener|@RabbitListener|@EventListener", "Event consumer"),
]
_GOAL_STOP = STOPWORDS | {"want", "need", "build", "system", "application", "using", "use", "like", "our", "we", "should", "would", "platform",
                          "create", "develop", "make", "into", "have", "also", "able", "allow", "support", "based", "some", "more", "them",
                          "their", "will", "about", "data", "service", "services", "feature", "features", "project", "company", "enterprise"}


def _header_comment(text: str, language: str) -> str:
    lines = text.splitlines()[:60]
    block: list[str] = []
    in_block = False
    for line in lines:
        s = line.strip()
        if language == "Python" and (s.startswith('"""') or s.startswith("'''")):
            m = re.search(r'^("""|\'\'\')(.*?)(\1)', text.lstrip(), re.S)
            return re.sub(r"\s+", " ", m.group(2)).strip()[:600] if m else ""
        if s.startswith("/**") or s.startswith("/*"):
            in_block = True
        if in_block or s.startswith(("//", "#", "*", "///")) and not s.startswith(("#!", "#include", "#region", "#pragma")):
            cleaned = re.sub(r"^(/\*\*?|\*/|\*|///|//|#)\s?", "", s).strip()
            if cleaned and not re.match(r"(copyright|licensed|license|spdx|@author|all rights)", cleaned, re.I):
                block.append(cleaned)
            if s.endswith("*/"):
                in_block = False
                if block:
                    break
        elif block:
            break
    return " ".join(block)[:600]


def _role(path: str, text: str, category: str) -> str:
    if category == "test":
        return "Test suite"
    for pattern, role in _ROLE_ANNOTATIONS:
        if re.search(pattern, text):
            return role
    layer = layer_of(path)
    return f"{layer.title()} layer code" if layer else "General source file"


def _split_suggestions(funcs: list) -> list[dict]:
    groups: dict[str, list[str]] = defaultdict(list)
    for f in funcs:
        parts = [p.lower() for p in _CAMEL.findall(f.name.replace("_", " ").title().replace(" ", ""))]
        nouns = [p for p in parts if p not in _VERBS and len(p) > 2]
        if nouns:
            groups[nouns[-1]].append(f.name)
    out = [{"concern": k, "methods": v[:12], "suggested_class": f"{k.title()}Handler"} for k, v in groups.items() if len(v) >= 3]
    return sorted(out, key=lambda g: -len(g["methods"]))[:6]


def explain_file(ctx: RepoContext, path: str, security: dict | None = None) -> dict:
    g = ctx.graph
    pf = ctx.parsed.get(path)
    info = ctx.file_info[path]
    text = ctx.read(path, 500_000)
    fid = f"file:{path}"
    header = _header_comment(text, info.language)
    role = _role(path, text, info.category)

    classes = []
    funcs = [s for s in (pf.symbols if pf else []) if s.kind in ("function", "method", "constructor")]
    for c in [s for s in (pf.symbols if pf else []) if s.kind not in ("function", "method", "constructor")]:
        members = [f for f in funcs if f.parent == c.name]
        classes.append({"name": c.name, "kind": c.kind, "lines": [c.start_line, c.end_line], "doc": c.doc[:300],
                        "annotations": c.annotations[:4],
                        "methods": [{"name": m.name, "lines": [m.start_line, m.end_line], "complexity": m.complexity, "signature": m.signature[:160]} for m in members]})
    top_level = [{"name": f.name, "lines": [f.start_line, f.end_line], "complexity": f.complexity, "signature": f.signature[:160]} for f in funcs if not f.parent]

    depends_on = sorted({g.nodes[t]["path"] for t in g.successors(fid, ("IMPORTS",))}
                        | {g.file_of(c) for c in g.successors(fid, ("REFERENCES",)) if g.file_of(c)})
    symbols = [fid, *g.successors(fid, ("DEFINES",))]
    symbols += [m for s in list(symbols) for m in g.successors(s, ("DEFINES",))]
    used_by = sorted({g.file_of(p) for s in symbols for p in g.predecessors(s, ("IMPORTS", "REFERENCES", "CALLS"))} - {path, None})
    hotspots = sorted(funcs, key=lambda f: -f.complexity)[:5]
    findings = [f for f in (security or {}).get("findings", []) if f["file"] == path][:20]
    tests = sorted(g.nodes[t]["path"] for t in g.predecessors(fid, ("TESTS",)))

    names = [c["name"] for c in classes] or [f["name"] for f in top_level[:5]]
    summary = (
        f"{PurePosixPath(path).name} is a {role.lower()} in module '{info.module}' ({info.language}, {info.loc:,} LOC). "
        + (f"{header} " if header else "")
        + (f"It defines {', '.join(names[:6])}" + (f" with {len(funcs)} functions/methods. " if funcs else ". ") if names else "")
        + (f"It exposes {len(pf.endpoints)} endpoint(s): {', '.join(f'{e.method} {e.path}' for e in pf.endpoints[:5])}. " if pf and pf.endpoints else "")
        + (f"It touches data stores/entities: {', '.join(sorted({d.target for d in pf.db_access})[:6])}. " if pf and pf.db_access else "")
        + (f"It depends on {len(depends_on)} file(s) and is used by {len(used_by)} file(s). ")
        + ("It has no direct tests." if not tests and info.category == "source" else f"Covered by {len(tests)} test file(s)." if tests else "")
    )
    large = info.loc > 400 or len(funcs) > 25
    return {
        "path": path,
        "language": info.language,
        "module": info.module,
        "loc": info.loc,
        "role": role,
        "header_doc": header,
        "summary": summary,
        "classes": classes,
        "functions": top_level,
        "endpoints": [e.__dict__ for e in (pf.endpoints if pf else [])],
        "data_access": [d.__dict__ for d in (pf.db_access if pf else [])][:30],
        "messaging": [m.__dict__ for m in (pf.messaging if pf else [])],
        "depends_on": depends_on[:40],
        "used_by": used_by[:40],
        "tests": tests,
        "complexity_hotspots": [{"name": f.qualified, "complexity": f.complexity, "lines": [f.start_line, f.end_line]} for f in hotspots],
        "security_findings": findings,
        "refactoring": {"is_large": large, "split_suggestions": _split_suggestions(funcs) if large else []},
    }


def goal_fit(ctx: RepoContext, retriever, goal: str, record: dict) -> dict:
    g = ctx.graph
    goal_terms = [t for t in dict.fromkeys(tokenize(goal)) if len(t) >= 4 and t not in _GOAL_STOP and not t.isdigit()]
    chunks = retriever.search(goal, k=25, exclude_tests=True) if retriever else []
    file_scores: Counter = Counter()
    for rank, c in enumerate(chunks):
        file_scores[c["path"]] += 1.0 / (1 + rank)
    module_scores: Counter = Counter()
    for p, s in file_scores.items():
        module_scores[ctx.module_of(p) or "root"] += s

    corpus_names = " ".join(n["name"].lower() for n in g.nodes.values() if n["label"] in ("Class", "API", "Table", "Module", "Service", "Topic", "Function"))
    chunk_text = " ".join(c["text"].lower() for c in chunks)
    covered, gaps = [], []
    for t in goal_terms:
        stem = t.rstrip("s")
        (covered if stem in corpus_names or stem in chunk_text else gaps).append(t)

    def matches(name: str) -> bool:
        low = name.lower()
        return any(t.rstrip("s") in low for t in goal_terms)

    apis = [a["name"] for a in g.by_label("API") if matches(a["name"])][:20]
    entities = sorted({t["name"] for t in g.by_label("Table") if matches(t["name"])})[:15]
    classes = [{"name": c["name"], "file": c["file"]} for c in g.by_label("Class")
               if matches(c["name"]) and ctx.file_info.get(c["file"]) and ctx.file_info[c["file"]].category == "source"][:15]
    reusable = [{"file": p, "module": ctx.module_of(p), "relevance": round(s, 3),
                 "symbols": [s2.name for s2 in (ctx.parsed[p].symbols if p in ctx.parsed else []) if s2.kind not in ("function", "method", "constructor")][:4]}
                for p, s in file_scores.most_common(10)]
    fit = round(100 * len(covered) / max(1, len(goal_terms)))
    readiness = (record.get("purpose") or {}).get("enterprise_readiness", {})
    failing = [c for c in readiness.get("checks", []) if c["status"] == "fail"]
    plan = []
    if module_scores:
        plan.append(f"Reuse module(s) {', '.join(m for m, _ in module_scores.most_common(3))} — they contain the code most relevant to your goal.")
    if apis:
        plan.append(f"Integrate through existing APIs ({', '.join(apis[:5])}) instead of re-implementing them.")
    if entities:
        plan.append(f"Extend the existing data model ({', '.join(entities[:6])}).")
    if gaps:
        plan.append(f"Develop new capabilities for: {', '.join(gaps[:8])} — not present in this codebase.")
    for c in failing[:3]:
        plan.append(f"Before production use: {c['action'] or c['area']}.")
    verdict = "Strong fit — most of your goal is already implemented" if fit >= 70 else \
        "Partial fit — good foundation, but notable development needed" if fit >= 35 else \
        "Weak fit — consider it as a reference rather than a base"
    return {
        "goal": goal,
        "fit_score": fit,
        "verdict": verdict,
        "covered_terms": covered,
        "gaps": gaps,
        "relevant_modules": [{"module": m, "relevance": round(s, 3)} for m, s in module_scores.most_common(6)],
        "reusable_components": reusable,
        "matching_apis": apis,
        "matching_entities": entities,
        "matching_classes": classes,
        "plan": plan,
        "evidence": [{"path": c["path"], "start_line": c["start_line"], "end_line": c["end_line"], "symbol": c.get("symbol")} for c in chunks[:8]],
    }
