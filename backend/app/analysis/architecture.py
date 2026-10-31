"""Repository understanding: overview, architecture style, service map, data flows, ownership, diagrams."""
from __future__ import annotations

import re
import zlib
from collections import Counter, defaultdict, deque
from pathlib import PurePosixPath

from app.graph.store import KnowledgeGraph
from app.services.context import RepoContext

LAYER_KEYWORDS = {
    "presentation": ("controller", "controllers", "api", "routes", "router", "routers", "views", "handlers", "resource", "resources", "endpoints", "web", "pages", "components"),
    "application": ("service", "services", "usecase", "usecases", "application", "app_services", "manager", "managers"),
    "domain": ("domain", "model", "models", "entity", "entities", "core", "dto", "dtos", "schemas"),
    "data": ("repository", "repositories", "dao", "daos", "persistence", "db", "database", "data", "store", "stores", "migrations", "mapper", "mappers"),
    "infrastructure": ("infrastructure", "infra", "adapter", "adapters", "config", "configuration", "integration", "clients", "client", "gateway", "gateways"),
}
LAYER_ORDER = ["presentation", "application", "domain", "data", "infrastructure"]
_API_NOISE = {"api", "v1", "v2", "v3", "v4", "rest", "public", "internal", "admin", ""}


def layer_of(path: str) -> str | None:
    parts = [p.lower() for p in PurePosixPath(path).parts[:-1]]
    stem = PurePosixPath(path).stem.lower()
    for layer in LAYER_ORDER:
        if any(p in LAYER_KEYWORDS[layer] for p in parts):
            return layer
    for suffix, layer in (("controller", "presentation"), ("resource", "presentation"), ("handler", "presentation"),
                          ("service", "application"), ("repository", "data"), ("dao", "data"), ("entity", "domain"),
                          ("model", "domain"), ("client", "infrastructure"), ("config", "infrastructure")):
        if stem.endswith(suffix):
            return layer
    return None


def mermaid_id(raw: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", raw)[-40:] + f"_{zlib.crc32(raw.encode()):08x}"


def mermaid_label(raw: str) -> str:
    return raw.replace('"', "'").replace("[", "(").replace("]", ")")[:60]


# ---------------------------------------------------------------- graph algorithms
def strongly_connected(nodes: list[str], succ) -> list[list[str]]:
    """Iterative Tarjan SCC; returns components with more than one node."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(succ(root)))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(succ(w))))
                    advanced = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    result.append(comp)
    return result


def _file_node_of(g: KnowledgeGraph, nid: str) -> str | None:
    path = g.file_of(nid)
    return f"file:{path}" if path else None


def _flow_neighbors(g: KnowledgeGraph, nid: str) -> set[str]:
    label = g.nodes[nid]["label"]
    if label == "API":
        return g.successors(nid, ("HANDLED_BY", "DEFINED_IN"))
    if label == "Function":
        out = g.successors(nid, ("CALLS", "ACCESSES"))
        f = _file_node_of(g, nid)
        return out | ({f} if f else set())
    if label == "File":
        out = g.successors(nid, ("ACCESSES", "IMPORTS"))
        for cid in g.successors(nid, ("REFERENCES",)):
            f = _file_node_of(g, cid)
            if f:
                out.add(f)
        return out
    if label == "Table":
        return g.successors(nid, ("STORED_IN",))
    return set()


def _is_data_layer(g: KnowledgeGraph, nid: str) -> bool:
    path = g.file_of(nid) or ""
    return layer_of(path) == "data" or bool(re.search(r"(Repository|Dao|Mapper|Store)\.\w+$", path))


def trace_data_flow(g: KnowledgeGraph, api_id: str, max_depth: int = 7) -> list[str] | None:
    parent: dict[str, str | None] = {api_id: None}
    depth = {api_id: 0}
    queue = deque([api_id])
    while queue:
        cur = queue.popleft()
        if g.nodes[cur]["label"] in ("Table", "Database") and cur != api_id:
            path = [cur]
            while parent[path[-1]] is not None:
                path.append(parent[path[-1]])
            path.reverse()
            db = next(iter(g.successors(cur, ("STORED_IN",))), None)
            return path + ([db] if db else [])
        if depth[cur] >= max_depth:
            continue
        for n in sorted(_flow_neighbors(g, cur), key=lambda x: (not _is_data_layer(g, x), x)):
            if n not in parent and n in g.nodes:
                parent[n] = cur
                depth[n] = depth[cur] + 1
                queue.append(n)
    return None


def _flow_labels(g: KnowledgeGraph, path: list[str]) -> list[dict]:
    steps: list[dict] = []
    for nid in path:
        n = g.nodes[nid]
        name = n["name"]
        if n["label"] == "File":
            name = PurePosixPath(n["path"]).stem
        if steps and steps[-1]["name"] == name:
            continue
        if n["label"] == "File" and steps and steps[-1]["label"] == "Function" and steps[-1]["name"].split(".")[0] == name:
            continue
        steps.append({"id": nid, "label": n["label"], "name": name, "file": g.file_of(nid)})
    return steps


# ---------------------------------------------------------------- overview
def _readme_summary(ctx: RepoContext) -> str:
    for f in ctx.files:
        if PurePosixPath(f.path).name.lower() in ("readme.md", "readme.rst", "readme.txt", "readme") and "/" not in f.path:
            text = ctx.read(f.path, 6000)
            paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            body = [p for p in paras if not p.startswith(("#", "[!", "![", "<", "```", "|", "---"))]
            if body:
                return re.sub(r"\s+", " ", body[0])[:600]
    return ""


def compute_overview(ctx: RepoContext) -> dict:
    g = ctx.graph
    classes = g.by_label("Class")
    functions = g.by_label("Function")
    apis = g.by_label("API")
    tests = [f for f in ctx.files if f.category == "test"]
    entry_points = [
        {"file": p, "type": pf.entry_point, "module": ctx.module_of(p)}
        for p, pf in ctx.parsed.items() if pf.entry_point and ctx.file_info[p].category == "source"
    ]
    module_rows = []
    for m in ctx.modules:
        mid = f"module:{m.name}"
        module_rows.append({
            "name": m.name, "path": m.path, "kind": m.kind, "loc": m.loc, "files": m.files,
            "languages": dict(sorted(m.languages.items(), key=lambda kv: -kv[1])[:4]),
            "entry_points": m.entry_points[:5],
            "apis": len(g.successors(mid, ("EXPOSES",))),
            "depends_on": sorted(g.nodes[d]["name"] for d in g.successors(mid, ("DEPENDS_ON",))),
        })
    langs = list(ctx_languages(ctx).keys())
    primary = langs[0] if langs else "unknown"
    fw_names = [f["name"] for f in ctx.frameworks if f["category"] in ("backend", "frontend")]
    services = [m for m in ctx.modules if m.kind == "service"]
    summary = (
        f"{ctx.name} is a {primary} codebase"
        + (f" built with {', '.join(fw_names[:4])}" if fw_names else "")
        + f" containing {sum(1 for f in ctx.files if f.category == 'source')} source files "
        f"({sum(f.loc for f in ctx.files if f.category == 'source'):,} lines) organised into {len(ctx.modules)} modules"
        + (f", {len(services)} of which are deployable services" if services else "")
        + f". It exposes {len(apis)} API endpoints"
        + (f" and persists data in {', '.join(n['name'] for n in g.by_label('Database'))}" if g.by_label("Database") else "")
        + "."
    )
    readme = _readme_summary(ctx)
    return {
        "name": ctx.name,
        "summary": summary,
        "readme_summary": readme,
        "languages": ctx_languages(ctx),
        "frameworks": ctx.frameworks,
        "tech_stack": _tech_stack(ctx),
        "modules": module_rows,
        "entry_points": entry_points[:50],
        "stats": {
            "files": len(ctx.files),
            "source_files": sum(1 for f in ctx.files if f.category == "source"),
            "test_files": len(tests),
            "loc": sum(f.loc for f in ctx.files if f.category in ("source", "test")),
            "classes": len(classes),
            "functions": len(functions),
            "apis": len(apis),
            "databases": len(g.by_label("Database")),
            "tables": len(g.by_label("Table")),
            "topics": len(g.by_label("Topic")),
            "dependencies": len(ctx.dependencies),
            "graph": g.stats(),
        },
    }


def ctx_languages(ctx: RepoContext) -> dict:
    from app.ingestion.scanner import PROGRAMMING_LANGUAGES

    agg: dict[str, dict] = defaultdict(lambda: {"files": 0, "loc": 0})
    for f in ctx.files:
        if f.language in PROGRAMMING_LANGUAGES:
            agg[f.language]["files"] += 1
            agg[f.language]["loc"] += f.loc
    total = sum(v["loc"] for v in agg.values()) or 1
    return {k: {**v, "percent": round(100 * v["loc"] / total, 1)} for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["loc"])}


def _tech_stack(ctx: RepoContext) -> dict:
    stack: dict[str, list[str]] = defaultdict(list)
    for fw in ctx.frameworks:
        stack[fw["category"]].append(fw["name"] + (f" {fw['version']}" if fw.get("version") else ""))
    for db in ctx.graph.by_label("Database"):
        if db["name"] not in stack["databases"] and db["name"] != "database":
            stack["databases"].append(db["name"])
    for inf in ctx.graph.by_label("Infrastructure"):
        if not inf["id"].startswith(("infra:k8s:", "infra:docker:")):
            stack["infra"].append(inf["name"])
    stack["languages"] = list(ctx_languages(ctx))[:8]
    return {k: sorted(set(v), key=v.index) for k, v in stack.items() if v}


# ---------------------------------------------------------------- architecture
def compute_architecture(ctx: RepoContext) -> dict:
    g = ctx.graph
    modules = ctx.modules
    services = [m for m in modules if m.kind == "service"]
    layer_counts: Counter = Counter()
    file_layer: dict[str, str] = {}
    for f in ctx.files:
        if f.category == "source":
            layer = layer_of(f.path)
            if layer:
                layer_counts[layer] += 1
                file_layer[f.path] = layer

    # Layer violations: lower layers depending on presentation
    violations = []
    for src, rel, dst in g.edges():
        if rel not in ("IMPORTS", "REFERENCES"):
            continue
        a, b = g.file_of(src), g.file_of(dst)
        la, lb = file_layer.get(a or ""), file_layer.get(b or "")
        if la in ("data", "domain") and lb == "presentation":
            violations.append({"from": a, "to": b, "rule": f"{la} layer must not depend on presentation layer"})

    module_ids = [f"module:{m.name}" for m in modules]
    module_cycles = strongly_connected(module_ids, lambda n: g.successors(n, ("DEPENDS_ON",)))
    file_ids = [n for n in g.nodes if n.startswith("file:")]
    file_cycles = strongly_connected(file_ids, lambda n: [x for x in g.successors(n, ("IMPORTS",))])

    style = _architecture_style(ctx, layer_counts, services)
    dependency_map = [
        {"source": g.nodes[s]["name"], "target": g.nodes[d]["name"], "weight": g.edge_props.get((s, "DEPENDS_ON", d), {}).get("weight", 1),
         "via": g.edge_props.get((s, "DEPENDS_ON", d), {}).get("via", "code")}
        for s, rel, d in g.edges() if rel == "DEPENDS_ON"
    ]
    fan_out = Counter(e["source"] for e in dependency_map)
    fan_in = Counter(e["target"] for e in dependency_map)

    flows = []
    for api in sorted(g.by_label("API"), key=lambda a: a["name"])[:150]:
        path = trace_data_flow(g, api["id"])
        if path:
            flows.append({"api": api["name"], "module": api.get("module"), "steps": _flow_labels(g, path)})
        if len(flows) >= 25:
            break

    god_classes = []
    for c in g.by_label("Class"):
        methods = len(g.successors(c["id"], ("DEFINES",)))
        size = (c.get("end_line") or 0) - (c.get("line") or 0)
        if methods > 40 or size > 1000:
            god_classes.append({"name": c["name"], "file": c["file"], "methods": methods, "lines": size})
    god_classes.sort(key=lambda x: -x["lines"])

    return {
        "style": style,
        "layers": {layer: layer_counts.get(layer, 0) for layer in LAYER_ORDER},
        "services": [{"name": m.name, "path": m.path, "loc": m.loc, "entry_points": m.entry_points[:3],
                      "apis": len(g.successors(f"module:{m.name}", ("EXPOSES",))),
                      "fan_in": fan_in.get(m.name, 0), "fan_out": fan_out.get(m.name, 0)} for m in services],
        "dependency_map": dependency_map,
        "module_cycles": [[g.nodes[n]["name"] for n in c] for c in module_cycles],
        "file_cycles": [[g.nodes[n]["path"] for n in c][:12] for c in sorted(file_cycles, key=len, reverse=True)[:20]],
        "file_cycle_count": len(file_cycles),
        "layer_violations": violations[:100],
        "god_classes": god_classes[:30],
        "data_flows": flows,
        "business_capabilities": business_capabilities(ctx),
        "messaging": _messaging(g),
        "ownership": code_ownership(ctx),
        "hotspots": hotspots(ctx),
        "diagrams": {
            "architecture": architecture_mermaid(ctx),
            "data_flow": data_flow_mermaid(flows[:8]),
        },
        "business_flow_summary": business_flow_summary(ctx, flows),
    }


def _architecture_style(ctx: RepoContext, layers: Counter, services: list) -> dict:
    traits = []
    names = {p.lower() for f in ctx.files if f.category == "source" for p in PurePosixPath(f.path).parts[:-1]}
    compose = len([s for s in ctx.infra.get("compose_services", []) if s.get("build_context") is not None])
    if len(services) >= 3 and (compose >= 2 or len(ctx.infra.get("dockerfiles", [])) >= 2 or len(ctx.infra.get("k8s_workloads", [])) >= 2):
        primary = "Microservices"
    elif len(services) >= 2:
        primary = "Multi-service / distributed monolith"
    elif len(ctx.modules) >= 4:
        primary = "Modular monolith"
    else:
        primary = "Monolith"
    if {"domain"} <= names and names & {"adapter", "adapters", "ports", "infrastructure", "application"}:
        traits.append("Hexagonal / Clean Architecture")
    if sum(1 for k in ("presentation", "application", "data") if layers.get(k)) >= 3:
        traits.append("Layered (Controller → Service → Repository)")
    if names & {"controllers"} and names & {"views"} and names & {"models"}:
        traits.append("MVC")
    if ctx.graph.by_label("Topic"):
        traits.append("Event-driven messaging")
    if any(m.kind == "frontend" for m in ctx.modules) and ctx.graph.by_label("API"):
        traits.append("Client-Server (SPA + API)")
    if any(f["name"] == "gRPC" for f in ctx.frameworks):
        traits.append("gRPC service contracts")
    return {"primary": primary, "traits": traits}


def business_capabilities(ctx: RepoContext) -> list[dict]:
    caps: dict[str, dict] = {}
    for api in ctx.graph.by_label("API"):
        segs = [s for s in api.get("route", "").strip("/").split("/") if s.lower() not in _API_NOISE and not s.startswith(("{", ":", "<", "["))]
        key = re.sub(r"\.(html?|json|xml|jsp|php|aspx?)$", "", (segs[0] if segs else api.get("module") or "root").lower())
        cap = caps.setdefault(key, {"name": key, "endpoints": 0, "methods": Counter(), "modules": set()})
        cap["endpoints"] += 1
        cap["methods"][api.get("method", "ANY")] += 1
        cap["modules"].add(api.get("module"))
    return sorted(
        ({"name": c["name"], "endpoints": c["endpoints"], "methods": dict(c["methods"]), "modules": sorted(m for m in c["modules"] if m)}
         for c in caps.values()),
        key=lambda c: -c["endpoints"],
    )[:40]


def _messaging(g: KnowledgeGraph) -> list[dict]:
    out = []
    for t in g.by_label("Topic"):
        out.append({
            "topic": t["name"],
            "publishers": sorted(g.nodes[p]["name"] for p in g.predecessors(t["id"], ("PUBLISHES",)) if p.startswith("module:")),
            "subscribers": sorted(g.nodes[p]["name"] for p in g.predecessors(t["id"], ("SUBSCRIBES",)) if p.startswith("module:")),
        })
    return out


def business_flow_summary(ctx: RepoContext, flows: list[dict]) -> str:
    caps = business_capabilities(ctx)
    g = ctx.graph
    parts = []
    if caps:
        parts.append("Business capabilities exposed via APIs: " + ", ".join(f"{c['name']} ({c['endpoints']} endpoints)" for c in caps[:8]) + ".")
    tables = [t["name"] for t in g.by_label("Table")]
    if tables:
        parts.append(f"Core persisted entities: {', '.join(sorted(set(tables))[:12])}.")
    msgs = _messaging(g)
    for m in msgs[:5]:
        if m["publishers"] or m["subscribers"]:
            parts.append(f"Event '{m['topic']}' is published by {', '.join(m['publishers']) or 'unknown'} and consumed by {', '.join(m['subscribers']) or 'unknown'}.")
    for f in flows[:3]:
        parts.append(f"Request flow {f['api']}: " + " → ".join(s["name"] for s in f["steps"]) + ".")
    return " ".join(parts) or "No HTTP endpoints, entities or messaging flows were detected."


def code_ownership(ctx: RepoContext) -> dict:
    hist = ctx.history or {}
    if not hist.get("available"):
        return {"available": False}
    per_module: dict[str, Counter] = defaultdict(Counter)
    for path, info in hist.get("files", {}).items():
        mod = ctx.module_of(path)
        if mod:
            per_module[mod].update(info.get("authors", {}))
    modules = []
    for mod, authors in per_module.items():
        total = sum(authors.values())
        acc, bus = 0, 0
        for _, c in authors.most_common():
            acc += c
            bus += 1
            if acc >= total * 0.5:
                break
        modules.append({"module": mod, "top_authors": [{"name": a, "commits": c} for a, c in authors.most_common(3)],
                        "bus_factor": bus, "commits": total})
    return {"available": True, "commit_count": hist.get("commit_count", 0), "authors": hist.get("authors", [])[:15],
            "modules": sorted(modules, key=lambda m: -m["commits"])}


def hotspots(ctx: RepoContext, limit: int = 15) -> list[dict]:
    churn = (ctx.history or {}).get("files", {})
    rows = []
    for path, pf in ctx.parsed.items():
        if ctx.file_info[path].category != "source":
            continue
        complexity = sum(s.complexity for s in pf.symbols if s.kind in ("function", "method", "constructor"))
        commits = churn.get(path, {}).get("commits", 0)
        score = complexity * (1 + commits)
        if score:
            rows.append({"file": path, "complexity": complexity, "commits": commits, "loc": pf.loc, "score": score})
    return sorted(rows, key=lambda r: -r["score"])[:limit]


def architecture_mermaid(ctx: RepoContext, max_modules: int = 30) -> str:
    g = ctx.graph
    mods = sorted(ctx.modules, key=lambda m: -m.loc)[:max_modules]
    keep = {f"module:{m.name}" for m in mods}
    lines = ["flowchart LR"]
    for m in mods:
        shape = f'{mermaid_id(m.name)}(["{mermaid_label(m.name)}"])' if m.kind == "service" else f'{mermaid_id(m.name)}["{mermaid_label(m.name)}"]'
        lines.append(f"    {shape}")
    for db in g.by_label("Database"):
        lines.append(f'    {mermaid_id(db["id"])}[("{mermaid_label(db["name"])}")]')
    for t in g.by_label("Topic")[:15]:
        lines.append(f'    {mermaid_id(t["id"])}>"{mermaid_label(t["name"])}"]')
    for inf in g.by_label("Infrastructure"):
        if not inf["id"].startswith(("infra:k8s:", "infra:docker:")):
            lines.append(f'    {mermaid_id(inf["id"])}{{{{"{mermaid_label(inf["name"])}"}}}}')
    for s, rel, d in g.edges():
        if rel == "DEPENDS_ON" and s in keep and d in keep:
            w = g.edge_props.get((s, rel, d), {}).get("weight", 1)
            lines.append(f"    {mermaid_id(g.nodes[s]['name'])} -->|{w}| {mermaid_id(g.nodes[d]['name'])}")
    db_links: set[tuple[str, str]] = set()
    for src, rel, dst in g.edges():
        if rel == "ACCESSES" and dst.startswith("db:"):
            mod = g.nodes[src].get("module") if not src.startswith("module:") else g.nodes[src]["name"]
            if mod and f"module:{mod}" in keep:
                db_links.add((mod, dst))
        if rel in ("PUBLISHES", "SUBSCRIBES") and src.startswith("module:") and src in keep:
            arrow = "-.->" if rel == "PUBLISHES" else "<-.-"
            lines.append(f"    {mermaid_id(g.nodes[src]['name'])} {arrow} {mermaid_id(dst)}")
        if rel == "USES" and src in keep:
            lines.append(f"    {mermaid_id(g.nodes[src]['name'])} --> {mermaid_id(dst)}")
    for mod, db in sorted(db_links):
        lines.append(f"    {mermaid_id(mod)} --> {mermaid_id(db)}")
    return "\n".join(lines)


def data_flow_mermaid(flows: list[dict]) -> str:
    lines = ["flowchart LR"]
    seen_nodes: set[str] = set()
    seen_edges: set[tuple[str, str]] = set()
    for flow in flows:
        prev = None
        for step in flow["steps"]:
            nid = mermaid_id(step["id"])
            if nid not in seen_nodes:
                seen_nodes.add(nid)
                label = mermaid_label(step["name"])
                if step["label"] == "API":
                    lines.append(f'    {nid}(["{label}"])')
                elif step["label"] in ("Database", "Table"):
                    lines.append(f'    {nid}[("{label}")]')
                else:
                    lines.append(f'    {nid}["{label}"]')
            if prev and (prev, nid) not in seen_edges:
                seen_edges.add((prev, nid))
                lines.append(f"    {prev} --> {nid}")
            prev = nid
    if len(lines) == 1:
        lines.append('    none["No API-to-database flows detected"]')
    return "\n".join(lines)
