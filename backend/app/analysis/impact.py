"""Change impact analysis over the knowledge graph."""
from __future__ import annotations

import math
from collections import deque

from app.graph.store import KnowledgeGraph
from app.services.context import RepoContext

CODE_RELS = ("IMPORTS", "REFERENCES", "CALLS")


def _container(g: KnowledgeGraph, nid: str) -> list[str]:
    """Function → owning class & file; Class → file. Used to lift symbol-level impact to file level."""
    out = []
    for parent in g.predecessors(nid, ("DEFINES",)):
        out.append(parent)
    path = g.file_of(nid)
    if path and f"file:{path}" not in out:
        out.append(f"file:{path}")
    return out


def _reverse_closure(g: KnowledgeGraph, starts: list[str], max_depth: int, limit: int = 4000) -> dict[str, tuple[int, str | None]]:
    seen: dict[str, tuple[int, str | None]] = {s: (0, None) for s in starts}
    queue = deque(starts)
    while queue and len(seen) < limit:
        cur = queue.popleft()
        depth = seen[cur][0]
        if depth >= max_depth:
            continue
        label = g.nodes[cur]["label"]
        nbrs = set(g.predecessors(cur, CODE_RELS))
        if label in ("Function", "Class") and depth >= 1:
            # lifted container nodes share the same depth
            for c in _container(g, cur):
                if c not in seen:
                    seen[c] = (depth, cur)
                    queue.appendleft(c)
        for n in nbrs:
            if n not in seen:
                seen[n] = (depth + 1, cur)
                queue.append(n)
    return seen


def resolve_targets(ctx: RepoContext, path: str, symbol: str | None = None) -> list[str]:
    g = ctx.graph
    fid = f"file:{path}"
    if fid not in g.nodes:
        return []
    defined = [n for n in g.successors(fid, ("DEFINES",))]
    nested = [m for c in defined if g.nodes[c]["label"] == "Class" for m in g.successors(c, ("DEFINES",))]
    symbols = defined + nested
    if symbol:
        wanted = symbol.lower()
        chosen = [n for n in symbols if g.nodes[n]["name"].lower() in (wanted, wanted.split(".")[-1])
                  or g.nodes[n]["name"].lower().endswith("." + wanted)]
        if chosen:
            return chosen
    return [fid, *symbols]


def analyze_impact(ctx: RepoContext, paths: list[str], symbol: str | None = None, max_depth: int = 4) -> dict:
    g = ctx.graph
    starts: list[str] = []
    unknown: list[str] = []
    for p in paths:
        t = resolve_targets(ctx, p, symbol if len(paths) == 1 else None)
        if t:
            starts.extend(t)
        else:
            unknown.append(p)
    changed_files = {g.file_of(s) for s in starts if g.file_of(s)}
    seen = _reverse_closure(g, starts, max_depth) if starts else {}

    impacted_files: dict[str, dict] = {}
    for nid, (depth, _) in seen.items():
        path = g.file_of(nid)
        if not path or path in changed_files:
            continue
        cur = impacted_files.get(path)
        if cur is None or depth < cur["depth"]:
            impacted_files[path] = {"file": path, "depth": depth, "module": ctx.module_of(path),
                                    "is_test": ctx.file_info.get(path).category == "test" if ctx.file_info.get(path) else False,
                                    "chain": _chain(g, seen, nid)}
    all_files = changed_files | set(impacted_files)
    function_nodes = {n for n in seen if g.nodes[n]["label"] == "Function"}

    # services
    changed_modules = {ctx.module_of(p) for p in changed_files if ctx.module_of(p)}
    services: dict[str, dict] = {m: {"name": m, "reason": "contains changed code", "depth": 0} for m in changed_modules}
    for info in impacted_files.values():
        m = info["module"]
        if m and m not in services:
            services[m] = {"name": m, "reason": f"code depends on changed code ({info['depth']} hop(s))", "depth": info["depth"]}

    # APIs
    apis = []
    for api in g.by_label("API"):
        handler = next(iter(g.successors(api["id"], ("HANDLED_BY",))), None)
        api_file = api.get("file")
        if (handler and handler in seen) or api_file in all_files:
            direct = api_file in changed_files
            apis.append({"name": api["name"], "module": api.get("module"), "file": api_file, "handler": api.get("handler"),
                         "impact": "direct" if direct else "indirect"})

    # contract-level consumers: services calling changed modules over HTTP or via topics
    if any(a["impact"] == "direct" for a in apis):
        for m in changed_modules:
            for consumer in g.predecessors(f"module:{m}", ("DEPENDS_ON",)):
                via = g.edge_props.get((consumer, "DEPENDS_ON", f"module:{m}"), {}).get("via", "code")
                name = g.nodes[consumer]["name"]
                if set(via.split(",")) & {"http", "compose"} and name not in services:
                    services[name] = {"name": name, "reason": f"calls {m} over {via}", "depth": 1}
    for p in changed_files:
        for topic in g.successors(f"file:{p}", ("PUBLISHES",)):
            for sub in g.predecessors(topic, ("SUBSCRIBES",)):
                if sub.startswith("module:") and g.nodes[sub]["name"] not in services:
                    services[g.nodes[sub]["name"]] = {"name": g.nodes[sub]["name"], "reason": f"consumes event {g.nodes[topic]['name']}", "depth": 1}

    # databases
    databases: dict[str, dict] = {}
    for nid in list(seen):
        path = g.file_of(nid)
        direct = path in changed_files
        sources = [nid] if g.nodes[nid]["label"] in ("File", "Function") else []
        for src in sources:
            for target in g.successors(src, ("ACCESSES",)):
                tnode = g.nodes[target]
                cur = databases.get(target)
                entry = {"name": tnode["name"], "type": tnode["label"], "impact": "direct" if direct else "indirect",
                         "via": path}
                if cur is None or (direct and cur["impact"] != "direct"):
                    databases[target] = entry
    for target in list(databases):
        for db in g.successors(target, ("STORED_IN",)):
            if db not in databases:
                databases[db] = {"name": g.nodes[db]["name"], "type": "Database", "impact": databases[target]["impact"], "via": databases[target]["via"]}

    # data stores the changed code writes/reads through its own data-access dependencies
    forward = g.traverse(starts, "out", ("CALLS", "REFERENCES", "IMPORTS"), max_depth=2, limit=500)
    for nid in forward:
        holders = {nid, f"file:{g.file_of(nid)}"} if g.file_of(nid) else {nid}
        for h in holders:
            if h not in g.nodes:
                continue
            for target in g.successors(h, ("ACCESSES",)):
                if target not in databases:
                    databases[target] = {"name": g.nodes[target]["name"], "type": g.nodes[target]["label"],
                                         "impact": "via dependency", "via": g.file_of(h)}
                for db in g.successors(target, ("STORED_IN",)):
                    databases.setdefault(db, {"name": g.nodes[db]["name"], "type": "Database", "impact": "via dependency", "via": g.file_of(h)})

    # tests
    tests: dict[str, dict] = {}
    for p in all_files:
        for t in g.predecessors(f"file:{p}", ("TESTS",)):
            tp = g.nodes[t]["path"]
            tests.setdefault(tp, {"file": tp, "covers": p, "relation": "tests changed code" if p in changed_files else "tests impacted code"})
    for info in impacted_files.values():
        if info["is_test"]:
            tests.setdefault(info["file"], {"file": info["file"], "covers": info["chain"][0] if info["chain"] else "", "relation": "depends on changed code"})

    untested_changed = [p for p in changed_files if not any(t["covers"] == p for t in tests.values())
                        and ctx.file_info.get(p) and ctx.file_info[p].category == "source"]
    complexity = sum(g.nodes[n].get("complexity", 1) for n in starts if g.nodes[n]["label"] == "Function")
    non_test_impacted = [f for f in impacted_files.values() if not f["is_test"]]
    score = min(100, int(
        12 * math.log2(1 + len(non_test_impacted))
        + 7 * max(0, len(services) - 1)
        + 4 * min(10, len(apis))
        + (12 if any(d["impact"] == "direct" for d in databases.values()) else 0)
        + min(10, complexity / 10)
        + (10 if untested_changed else 0)
    ))
    level = "critical" if score >= 75 else "high" if score >= 50 else "medium" if score >= 25 else "low"
    files_sorted = sorted(impacted_files.values(), key=lambda f: (f["depth"], f["file"]))

    return {
        "changed": sorted(changed_files),
        "unknown_files": unknown,
        "symbol": symbol,
        "risk": {"score": score, "level": level},
        "summary": {
            "impacted_files": len(non_test_impacted),
            "impacted_services": len(services),
            "impacted_apis": len(apis),
            "impacted_databases": len(databases),
            "impacted_tests": len(tests),
            "impacted_functions": len(function_nodes),
        },
        "services": sorted(services.values(), key=lambda s: s["depth"]),
        "apis": apis[:200],
        "databases": list(databases.values()),
        "tests": list(tests.values())[:200],
        "untested_changed_files": untested_changed,
        "files": files_sorted[:300],
        "dependency_chains": [{"file": f["file"], "chain": f["chain"]} for f in files_sorted if f["depth"] > 1][:30],
        "graph": _impact_graph(g, changed_files, files_sorted[:60], apis, databases),
    }


def _chain(g: KnowledgeGraph, seen: dict, nid: str) -> list[str]:
    out: list[str] = []
    cur: str | None = nid
    while cur is not None:
        path = g.file_of(cur)
        label = g.nodes[cur]["name"] if g.nodes[cur]["label"] in ("Function", "Class") else path
        if label and (not out or out[-1] != label):
            out.append(label)
        cur = seen.get(cur, (0, None))[1]
    return list(reversed(out))


def _impact_graph(g: KnowledgeGraph, changed: set[str], impacted: list[dict], apis: list[dict], dbs: dict) -> dict:
    nodes = [{"id": f"file:{p}", "name": p.split("/")[-1], "label": "Changed", "path": p} for p in changed]
    ids = {n["id"] for n in nodes}
    for f in impacted:
        nid = f"file:{f['file']}"
        if nid not in ids:
            ids.add(nid)
            nodes.append({"id": nid, "name": f["file"].split("/")[-1], "label": "Test" if f["is_test"] else "File", "path": f["file"], "depth": f["depth"]})
    edges = []
    for src in ids:
        for rel in ("IMPORTS", "REFERENCES"):
            for dst in g.successors(src, (rel,)):
                tgt = dst if dst.startswith("file:") else (f"file:{g.file_of(dst)}" if g.file_of(dst) else None)
                if tgt in ids and tgt != src:
                    edges.append({"source": src, "target": tgt, "type": rel})
        for dst in g.successors(src, ("CALLS",)):
            tgt = f"file:{g.file_of(dst)}"
            if tgt in ids and tgt != src:
                edges.append({"source": src, "target": tgt, "type": "CALLS"})
    for a in apis[:30]:
        aid = f"api:{a['name']}:{a['file']}"
        nodes.append({"id": aid, "name": a["name"], "label": "API"})
        if f"file:{a['file']}" in ids:
            edges.append({"source": aid, "target": f"file:{a['file']}", "type": "HANDLED_BY"})
    for did, d in dbs.items():
        nodes.append({"id": did, "name": d["name"], "label": d["type"]})
        if d.get("via") and f"file:{d['via']}" in ids:
            edges.append({"source": f"file:{d['via']}", "target": did, "type": "ACCESSES"})
    unique = {(e["source"], e["target"], e["type"]): e for e in edges}
    return {"nodes": nodes, "edges": list(unique.values())}
