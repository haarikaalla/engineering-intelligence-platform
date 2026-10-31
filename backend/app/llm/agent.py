"""Repository chat agent orchestrated with LangGraph: classify → retrieve → graph tools → generate."""
from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath
from typing import Any, TypedDict

from app.analysis.impact import analyze_impact
from app.config import get_settings
from app.llm.grounding import verify_mentions
from app.llm.provider import get_llm
from app.rag.retriever import Retriever
from app.services.context import RepoContext

log = logging.getLogger(__name__)

INTENTS: list[tuple[str, re.Pattern]] = [
    ("impact", re.compile(r"what happens if|impact|if i (change|modify|remove|delete|rename)|would break|blast radius|affected by", re.I)),
    ("dependents", re.compile(r"depend(s|ent|ents)? on|who uses|which \w+ (use|uses|call|calls|import)|callers? of|used by|consumers? of|references? to", re.I)),
    ("database", re.compile(r"\bdatabases?\b|\bdb\b|\btables?\b|\bsql\b|persist|\bentit(y|ies)\b|data ?store|queries", re.I)),
    ("security", re.compile(r"secur|vulnerab|secret|injection|cve|owasp|credential", re.I)),
    ("purpose", re.compile(r"purpose|what (problem|does it solve|is it for|is this for)|use ?cases?|how (can|could|do) (we|i) (use|adopt|reuse|leverage|integrate)|"
                           r"suggest|recommend|reuse|adopt|what (can|could|should) (we|i) (build|develop|add)|enterprise.?read|production.?read|how to run|getting started|onboard", re.I)),
    ("tests", re.compile(r"\btests?\b|coverage|untested|regression", re.I)),
    ("overview", re.compile(r"explain (this|the) (repo|repository|project|codebase|system)|overview|what does (this|the) (repo|project|system|codebase) do|summari[sz]e|tech(nology)? stack", re.I)),
    ("architecture", re.compile(r"architecture|services?\b|modules?\b|data ?flow|dependency (graph|map)|layers?|how .* (structured|organi[sz]ed)", re.I)),
    ("locate", re.compile(r"where (is|are|do|does)|which files?|find|implemented|located|defined|entry ?points?|how (is|does) .* (work|implemented|handled)", re.I)),
]
CONCEPTS = {
    "authentication": ["auth", "login", "jwt", "token", "security", "session", "oauth", "password", "credential", "signin"],
    "authorization": ["authoriz", "permission", "role", "acl", "policy", "access", "preauthorize", "security"],
    "payment": ["payment", "billing", "invoice", "charge", "stripe", "checkout"],
    "logging": ["log", "logger", "logging", "audit"],
    "configuration": ["config", "settings", "properties", "environment"],
    "caching": ["cache", "redis", "memo"],
    "messaging": ["kafka", "rabbit", "queue", "topic", "event", "publish", "consumer", "producer"],
    "validation": ["valid", "validator", "constraint", "sanitiz"],
    "error handling": ["exception", "error", "handler", "advice"],
}
SYSTEM_PROMPT = """You are an expert software architect embedded in an Engineering Intelligence Platform.
Answer questions about the repository strictly from the provided CONTEXT (knowledge-graph facts and retrieved code).
Rules:
- Be precise and concrete: cite files as `path:line` and name classes/functions/APIs exactly.
- If the context is insufficient, say what is missing instead of guessing.
- Never invent files, classes, functions, endpoints, tables, libraries or behaviour that do not appear in the CONTEXT.
- Put every code reference (file, class, function, endpoint) in `backticks` exactly as it appears in the CONTEXT.
- Use concise Markdown with short sections and bullet lists. Include small code excerpts only when they help.
- For change-impact questions, enumerate impacted services, APIs, databases and tests, then give a risk assessment."""


class AgentState(TypedDict, total=False):
    question: str
    history: list[tuple[str, str]]
    intent: str
    entities: list[dict]
    concepts: list[str]
    chunks: list[dict]
    facts: list[str]
    answer: str
    sources: list[dict]
    mode: str
    grounding: dict


# ---------------------------------------------------------------- nodes
def classify(state: AgentState, config: dict) -> AgentState:
    deps = config["configurable"]
    ctx: RepoContext = deps["ctx"]
    q = state["question"]
    intent = next((name for name, pat in INTENTS if pat.search(q)), "general")
    g = ctx.graph

    candidates = set(re.findall(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'", q))
    tokens = {t for group in candidates for t in group if t}
    tokens |= set(re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:[A-Z][a-zA-Z0-9]*)+\b", q))  # CamelCase
    tokens |= set(re.findall(r"\b[\w\-]+(?:/[\w.\-]+)+\b", q))  # paths
    tokens |= set(re.findall(r"\b\w+\.(?:java|py|ts|tsx|js|cs|go|kt|rb|php)\b", q))
    tokens |= set(re.findall(r"(?:GET|POST|PUT|DELETE|PATCH)?\s*(/[\w/{}:\-.]+)", q))
    words = {w for w in re.findall(r"\b[a-zA-Z_][\w\-]{3,}\b", q)}
    entities: list[dict] = []
    seen: set[str] = set()
    for tok in sorted(tokens, key=len, reverse=True):
        for node in g.search(tok.strip(), labels=("Class", "Function", "Service", "Module", "File", "API", "Database", "Table"), limit=5):
            if node["id"] not in seen:
                seen.add(node["id"])
                entities.append(node)
    module_names = {m.name.lower(): m.name for m in ctx.modules}
    for w in words:
        if w.lower() in module_names and f"module:{module_names[w.lower()]}" not in seen:
            seen.add(f"module:{module_names[w.lower()]}")
            entities.append(g.nodes[f"module:{module_names[w.lower()]}"])
        for node in g.search(w, labels=("Class", "Service", "Table"), limit=3):
            if node["name"].lower() == w.lower() and node["id"] not in seen:
                seen.add(node["id"])
                entities.append(node)
    concepts = [c for c, kws in CONCEPTS.items() if c.split()[0][:5] in q.lower() or any(k in q.lower() for k in kws[:2])]
    return {"intent": intent, "entities": entities[:12], "concepts": concepts}


def retrieve(state: AgentState, config: dict) -> AgentState:
    retriever: Retriever | None = config["configurable"].get("retriever")
    if retriever is None:
        return {"chunks": []}
    q = state["question"]
    extra = " ".join(e["name"] for e in state.get("entities", [])[:5])
    for c in state.get("concepts", []):
        extra += " " + " ".join(CONCEPTS[c][:5])
    boost = {e.get("path") or e.get("file") for e in state.get("entities", []) if e.get("path") or e.get("file")}
    chunks = retriever.search(f"{q} {extra}", k=10, boost_paths={b for b in boost if b}, exclude_tests=state.get("intent") != "tests")
    return {"chunks": chunks}


def graph_tools(state: AgentState, config: dict) -> AgentState:
    deps = config["configurable"]
    ctx: RepoContext = deps["ctx"]
    record: dict = deps.get("record") or {}
    g = ctx.graph
    intent = state.get("intent", "general")
    entities = state.get("entities", [])
    facts: list[str] = []

    def loc(n: dict) -> str:
        f = n.get("file") or n.get("path")
        return f"`{f}:{n.get('line', 1)}`" if f else ""

    if intent in ("overview", "general", "architecture"):
        ov = record.get("overview") or {}
        arch = record.get("architecture") or {}
        if ov.get("summary"):
            facts.append(f"Summary: {ov['summary']}")
        if (record.get("purpose") or {}).get("what_it_solves"):
            facts.append(f"Purpose: {record['purpose']['what_it_solves']}")
        if ov.get("readme_summary"):
            facts.append(f"README: {ov['readme_summary']}")
        if ov.get("tech_stack"):
            facts.append("Tech stack: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in ov["tech_stack"].items()))
        if arch.get("style"):
            facts.append(f"Architecture style: {arch['style']['primary']}" + (f" ({', '.join(arch['style']['traits'])})" if arch['style']['traits'] else ""))
        mods = ov.get("modules", [])[:12]
        if mods:
            facts.append("Modules: " + "; ".join(f"{m['name']} [{m['kind']}, {m['loc']} LOC, {m['apis']} APIs"
                                                  + (f", depends on {', '.join(m['depends_on'][:4])}" if m['depends_on'] else "") + "]" for m in mods))
        if ov.get("entry_points"):
            facts.append("Entry points: " + "; ".join(f"`{e['file']}` ({e['type']})" for e in ov["entry_points"][:8]))
        if arch.get("business_flow_summary"):
            facts.append(f"Business flows: {arch['business_flow_summary']}")
        if intent == "architecture":
            for flow in arch.get("data_flows", [])[:5]:
                facts.append(f"Data flow {flow['api']}: " + " → ".join(s["name"] for s in flow["steps"]))
            if arch.get("module_cycles"):
                facts.append("Module dependency cycles: " + "; ".join(" ↔ ".join(c) for c in arch["module_cycles"][:5]))

    if intent == "dependents":
        primary = [e for e in entities if e["label"] in ("Class", "Service", "Module", "File", "API")] or entities
        done_files: set[str] = set()
        for e in primary[:4]:
            nid = e["id"]
            if e["label"] in ("Service", "Module"):
                users = sorted(g.nodes[p]["name"] for p in g.predecessors(nid, ("DEPENDS_ON",)))
                facts.append(f"Modules/services depending on {e['name']}: {', '.join(users) or 'none detected'}")
                continue
            efile = e.get("file") or e.get("path")
            if e["label"] == "File" and efile in done_files:
                continue
            done_files.add(efile)
            targets = [nid]
            if e["label"] == "File":
                targets += list(g.successors(nid, ("DEFINES",)))
            users: dict[str, set[str]] = {}
            for t in targets:
                for p in g.predecessors(t, ("REFERENCES", "CALLS", "IMPORTS")):
                    f = g.file_of(p)
                    if f and f != efile:
                        users.setdefault(g.nodes[f"file:{f}"]["module"] if f"file:{f}" in g.nodes else "?", set()).add(f)
            if users:
                lines = [f"{mod}: {', '.join(sorted(files)[:8])}" for mod, files in sorted(users.items())]
                facts.append(f"Code depending on {e['label']} {e['name']} ({loc(e)}) grouped by module — " + " | ".join(lines))
            else:
                facts.append(f"No code dependents of {e['name']} were found in the knowledge graph.")
            owner = ctx.module_of(efile) if efile else None
            if owner:
                remote = []
                for p in g.predecessors(f"module:{owner}", ("DEPENDS_ON",)):
                    via = g.edge_props.get((p, "DEPENDS_ON", f"module:{owner}"), {}).get("via", "code")
                    remote.append(f"{g.nodes[p]['name']} (via {via})")
                for topic in g.successors(f"module:{owner}", ("PUBLISHES",)):
                    remote += [f"{g.nodes[s]['name']} (consumes {g.nodes[topic]['name']})" for s in g.predecessors(topic, ("SUBSCRIBES",)) if s.startswith("module:")]
                if remote:
                    facts.append(f"Services depending on {owner} (which owns {e['name']}): {', '.join(sorted(set(remote)))}")

    if intent == "impact":
        paths = []
        exact = [e for e in entities if e["label"] in ("Class", "File", "Function", "API")
                 and e["name"].lower().split(".")[0] in state["question"].lower()]
        for e in exact or entities:
            p = e.get("path") or e.get("file")
            if p and p not in paths and not (ctx.file_info.get(p) and ctx.file_info[p].category == "test"):
                paths.append(p)
        if not paths and state.get("chunks"):
            paths = [state["chunks"][0]["path"]]
        if paths:
            sym = next((e["name"].split(".")[-1] for e in entities if e["label"] in ("Function", "Class")), None)
            rep = analyze_impact(ctx, paths[:3], symbol=sym if len(paths) == 1 else None)
            s = rep["summary"]
            facts.append(f"Impact analysis for {', '.join(paths[:3])}" + (f" (symbol {sym})" if sym else "") +
                         f": risk {rep['risk']['level']} ({rep['risk']['score']}/100); {s['impacted_files']} files, {s['impacted_services']} services, "
                         f"{s['impacted_apis']} APIs, {s['impacted_databases']} data stores, {s['impacted_tests']} tests impacted.")
            if rep["services"]:
                facts.append("Impacted services: " + "; ".join(f"{x['name']} ({x['reason']})" for x in rep["services"][:10]))
            if rep["apis"]:
                facts.append("Impacted APIs: " + "; ".join(f"{a['name']} [{a['impact']}]" for a in rep["apis"][:12]))
            if rep["databases"]:
                facts.append("Impacted data stores: " + "; ".join(f"{d['type']} {d['name']} [{d['impact']}]" for d in rep["databases"][:10]))
            if rep["tests"]:
                facts.append("Tests to run: " + ", ".join(t["file"] for t in rep["tests"][:12]))
            if rep["dependency_chains"]:
                facts.append("Example dependency chains: " + " | ".join(" → ".join(c["chain"]) for c in rep["dependency_chains"][:4]))

    if intent == "database":
        for db in g.by_label("Database"):
            accessors = {g.nodes[p].get("module") for p in g.predecessors(db["id"], ("ACCESSES",)) if g.nodes[p].get("module")}
            accessors |= {g.nodes[p]["name"] for p in g.predecessors(db["id"], ("ACCESSES",)) if p.startswith("module:")}
            tables = [g.nodes[t]["name"] for t in g.predecessors(db["id"], ("STORED_IN",))]
            for t in g.predecessors(db["id"], ("STORED_IN",)):
                accessors |= {g.nodes[p].get("module") for p in g.predecessors(t, ("ACCESSES",))}
            facts.append(f"Database {db['name']}: accessed by modules {', '.join(sorted(a for a in accessors if a)) or 'n/a'}"
                         + (f"; tables/entities: {', '.join(sorted(tables)[:20])}" if tables else ""))
        by_module: dict[str, set[str]] = {}
        for t in g.by_label("Table"):
            for p in g.predecessors(t["id"], ("ACCESSES",)):
                f = g.file_of(p)
                if f:
                    by_module.setdefault(ctx.module_of(f) or "?", set()).add(f)
        if by_module:
            facts.append("Modules with data-access code: " + " | ".join(f"{m}: {', '.join(sorted(fs)[:6])}" for m, fs in sorted(by_module.items())))

    if intent == "security":
        sec = record.get("security") or {}
        if sec:
            facts.append(f"Security score {sec.get('score')}/100; findings by severity: {sec.get('summary', {}).get('by_severity')}; "
                         f"dependency vulnerabilities: {sec.get('summary', {}).get('dependency_vulnerabilities')}")
            for f in sec.get("findings", [])[:10]:
                facts.append(f"[{f['severity']}] {f['title']} ({f['cwe']}) at `{f['file']}:{f['line']}`")

    if intent == "purpose":
        pur = record.get("purpose") or {}
        if pur:
            facts.append(f"What it solves: {pur.get('what_it_solves')}")
            facts.append(f"Project type: {pur['project_type']['type']}; domain(s): {', '.join(d['domain'] for d in pur.get('domains', [])) or 'n/a'}")
            if pur.get("key_features"):
                facts.append("Key features: " + "; ".join(pur["key_features"][:8]))
            for m in pur.get("main_functionality", [])[:6]:
                facts.append(f"Module {m['module']}: {m['summary']}")
            if pur.get("how_to_run"):
                facts.append("How to run: " + "; ".join(f"`{s['command']}`" for s in pur["how_to_run"][:6]))
            ready = pur.get("enterprise_readiness", {})
            if ready:
                gaps = [c["area"] for c in ready["checks"] if c["status"] != "pass"]
                facts.append(f"Enterprise readiness {ready['score']}/100 ({ready['level']}); gaps: {', '.join(gaps) or 'none'}; license: {ready['license']['name']} — {ready['license']['guidance']}")
            sug = pur.get("suggestions", {})
            for bucket, label in (("reuse", "Reuse"), ("extend", "Develop next"), ("improve", "Improve")):
                for s in sug.get(bucket, [])[:4]:
                    facts.append(f"{label}: {s['title']} — {s['reason']}")
            if pur.get("ai_brief"):
                facts.append(f"Executive brief: {pur['ai_brief'][:1500]}")

    if re.search(r"\bexplain\b|what does .* (file|class) do|summari[sz]e .*\.\w+", state["question"], re.I):
        from app.analysis.explain import explain_file

        for e in [e for e in entities if e["label"] in ("File", "Class")][:2]:
            path = e.get("path") or e.get("file")
            if path in ctx.file_info:
                info = explain_file(ctx, path, record.get("security"))
                facts.append(f"File explanation ({path}): {info['summary']}")
                if info["complexity_hotspots"]:
                    facts.append("Most complex functions: " + ", ".join(f"{h['name']} ({h['complexity']})" for h in info["complexity_hotspots"][:4]))

    if intent == "tests":
        ti = record.get("test_insights") or {}
        if ti:
            facts.append(f"Estimated coverage: {ti['coverage_estimate']['files_percent']}% of testable files have tests; frameworks: {', '.join(ti.get('frameworks', [])) or 'n/a'}")
            for r in ti.get("recommendations", [])[:8]:
                facts.append(f"Untested ({r['priority']}): `{r['file']}` — {r['reason']} → add {r['test_type']} tests")

    if intent in ("locate", "general") or state.get("concepts"):
        for e in entities[:8]:
            if e["label"] in ("Class", "Function", "API", "File"):
                facts.append(f"{e['label']} {e['name']} is defined at {loc(e)}")
        for concept in state.get("concepts", []):
            kws = CONCEPTS[concept]
            hits = [n for n in g.nodes.values() if n["label"] in ("Class", "File")
                    and any(k in n["name"].lower() for k in kws) and not (n.get("is_test"))][:15]
            if hits:
                facts.append(f"Code related to {concept}: " + ", ".join(f"{h['name']} ({loc(h)})" for h in hits))
            apis = [a for a in g.by_label("API") if any(k in a["name"].lower() for k in kws)][:10]
            if apis:
                facts.append(f"APIs related to {concept}: " + ", ".join(a["name"] for a in apis))
    return {"facts": facts}


def _context_block(state: AgentState, budget: int) -> str:
    parts = ["## Knowledge graph facts", *[f"- {f}" for f in state.get("facts", [])], "", "## Retrieved code"]
    used = sum(len(p) for p in parts)
    for c in state.get("chunks", []):
        block = f"### {c['path']}:{c['start_line']}-{c['end_line']} {c.get('symbol') or ''}\n```\n{c['text'][:3500]}\n```"
        if used + len(block) > budget:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def generate(state: AgentState, config: dict) -> AgentState:
    llm = config["configurable"].get("llm")
    chunks = state.get("chunks", [])
    sources = [{"path": c["path"], "start_line": c["start_line"], "end_line": c["end_line"], "symbol": c.get("symbol"), "score": c.get("score")}
               for c in chunks[:8]]
    if llm is not None:
        try:
            context = _context_block(state, get_settings().llm_max_context_chars)
            history = [(r, m[:2000]) for r, m in state.get("history", [])[-6:]]
            answer = llm.complete(SYSTEM_PROMPT, [*history, ("user", f"CONTEXT:\n{context}\n\nQUESTION: {state['question']}")])
            check = verify_mentions(config["configurable"]["ctx"], answer)
            if check["unverified"]:
                answer += ("\n\n> ⚠️ Not found in this repository (treat as unverified): "
                           + ", ".join(f"`{u}`" for u in check["unverified"][:10]))
            return {"answer": answer, "sources": sources, "mode": llm.name, "grounding": check}
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM generation failed, using offline answer: %s", exc)
    return {"answer": _offline_answer(state), "sources": sources, "mode": "offline"}


def _offline_answer(state: AgentState) -> str:
    intent = state.get("intent", "general")
    titles = {"overview": "Repository overview", "dependents": "Dependency analysis", "impact": "Change impact analysis",
              "database": "Database interactions", "locate": "Where this is implemented", "security": "Security posture",
              "tests": "Test intelligence", "architecture": "Architecture", "general": "Findings", "purpose": "Purpose, usage & recommendations"}
    out = [f"### {titles.get(intent, 'Findings')}", ""]
    facts = state.get("facts", [])
    if facts:
        out += [f"- {f}" for f in facts]
    chunks = state.get("chunks", [])
    if chunks:
        out += ["", "**Most relevant code:**", ""]
        for c in chunks[:6]:
            sym = f" — `{c['symbol']}`" if c.get("symbol") and c["symbol"] != "module-level" else ""
            out.append(f"- `{c['path']}:{c['start_line']}-{c['end_line']}`{sym}")
        top = chunks[0]
        body = top["text"].split("\n\n", 1)[-1].splitlines()[:18]
        lang = PurePosixPath(top["path"]).suffix.lstrip(".")
        out += ["", f"Excerpt from `{top['path']}`:", f"```{lang}", *body, "```"]
    if not facts and not chunks:
        out.append("No matching code or graph facts were found. Try naming a class, file, module or API path.")
    out += ["", "_Answer generated from the knowledge graph and code search. Configure OPENAI_API_KEY or ANTHROPIC_API_KEY for LLM-written explanations._"]
    return "\n".join(out)


# ---------------------------------------------------------------- orchestration
_compiled = None


def _build_graph():
    global _compiled
    if _compiled is not None:
        return _compiled
    try:
        from langgraph.graph import END, StateGraph

        builder = StateGraph(AgentState)
        builder.add_node("classify", classify)
        builder.add_node("retrieve", retrieve)
        builder.add_node("graph_tools", graph_tools)
        builder.add_node("generate", generate)
        builder.set_entry_point("classify")
        builder.add_edge("classify", "retrieve")
        builder.add_edge("retrieve", "graph_tools")
        builder.add_edge("graph_tools", "generate")
        builder.add_edge("generate", END)
        _compiled = builder.compile()
    except ImportError:
        _compiled = False
    return _compiled


def run_agent(ctx: RepoContext, retriever: Retriever | None, record: dict, question: str, history: list[tuple[str, str]]) -> dict[str, Any]:
    config = {"configurable": {"ctx": ctx, "retriever": retriever, "record": record, "llm": get_llm()}}
    state: AgentState = {"question": question, "history": history}
    graph = _build_graph()
    if graph:
        result = graph.invoke(state, config=config)
    else:
        for node in (classify, retrieve, graph_tools, generate):
            state.update(node(state, config))
        result = state
    return {
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
        "intent": result.get("intent"),
        "entities": [{"id": e["id"], "label": e["label"], "name": e["name"]} for e in result.get("entities", [])],
        "mode": result.get("mode", "offline"),
        "grounding": result.get("grounding") or {"score": 100, "verified": [], "unverified": [], "note": "answer built only from extracted facts"},
    }
