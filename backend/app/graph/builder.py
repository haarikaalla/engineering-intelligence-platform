"""Builds the repository knowledge graph from parsed files, modules and infrastructure."""
from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from app.graph.store import KnowledgeGraph
from app.ingestion.infra import classify_image
from app.ingestion.scanner import ModuleInfo, SourceFile
from app.parsing.extractors import DATABASE_TECHS
from app.parsing.models import ParsedFile

CODE_EDGES = ("IMPORTS", "REFERENCES", "CALLS")
_GENERIC_CALLS = {
    "get", "set", "add", "put", "run", "main", "init", "toString", "equals", "hashCode", "execute", "apply", "map",
    "filter", "len", "print", "str", "list", "dict", "int", "float", "open", "close", "read", "write", "format",
    "append", "update", "remove", "delete", "save", "find", "create", "build", "of", "valueOf", "push", "log",
    "info", "debug", "error", "warn", "then", "catch", "next", "send", "call", "handle", "process", "parse",
    "load", "start", "stop", "isEmpty", "size", "length", "keys", "values", "items", "join", "split", "test",
    "assertEquals", "assertTrue", "expect", "describe", "it", "setUp", "tearDown", "super", "Ok", "require",
}
_GENERIC_TYPES = {"String", "Object", "List", "Map", "Set", "Integer", "Long", "Boolean", "Override", "Exception",
                  "Test", "Config", "Base", "Error", "Optional", "Response", "Request", "Result", "Task", "Void",
                  "None", "True", "False", "TODO", "FIXME", "HTTP", "JSON", "API", "URL", "UUID", "Date"}
_JS_EXT = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "/index.ts", "/index.tsx", "/index.js", "/index.jsx")


def _norm_name(name: str) -> str:
    return re.sub(r"[-_.\s]|service$|svc$|api$", "", name.lower())


def test_subject_stem(path: str) -> str:
    stem = PurePosixPath(path).name
    stem = re.sub(r"\.(test|spec)\.(js|jsx|ts|tsx|mjs)$", "", stem)
    stem = re.sub(r"\.\w+$", "", stem)
    stem = re.sub(r"^test_|_test$|Tests?$|IT$|Spec$", "", stem)
    return stem.lower()


class ImportResolver:
    def __init__(self, paths: list[str], parsed: dict[str, ParsedFile]) -> None:
        self.paths = set(paths)
        self.suffix: dict[str, list[str]] = defaultdict(list)
        self.dirs: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            pp = PurePosixPath(p)
            self.dirs[str(pp.parent)].append(p)
            parts = list(pp.with_suffix("").parts)
            if parts[-1] in ("__init__", "index", "mod"):
                parts = parts[:-1]
            for k in range(1, min(len(parts), 9) + 1):
                self.suffix[".".join(parts[-k:])].append(p)

    def resolve(self, pf: ParsedFile, imp: str) -> list[str]:
        lang = pf.language
        if lang in ("JavaScript", "TypeScript", "Vue", "Svelte") or imp.startswith("."):
            return self._resolve_relative(pf.path, imp)
        if lang == "Go":
            parts = imp.split("/")
            for k in range(min(len(parts), 5), 0, -1):
                suffix = "/".join(parts[-k:])
                for d, files in self.dirs.items():
                    if d == suffix or d.endswith("/" + suffix):
                        return [f for f in files if f.endswith(".go") and not f.endswith("_test.go")][:30]
            return []
        if lang == "C#" or imp.endswith(".*"):
            return []
        key = imp.replace("\\", ".").replace("/", ".").replace("::", ".")
        key = re.sub(r"\.(h|hpp|rb|py|php)$", "", key)
        for candidate in (key, key.rsplit(".", 1)[0] if "." in key else None):
            if candidate and "." in candidate:
                hits = self.suffix.get(candidate)
                if hits and len(hits) <= 3:
                    return hits
        return []

    def _resolve_relative(self, path: str, imp: str) -> list[str]:
        if imp.startswith("."):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(path), imp))
            bases = [base]
        elif imp.startswith(("@/", "~/")):
            bases = [f"src/{imp[2:]}", imp[2:]]
        else:
            return []
        for b in bases:
            for ext in _JS_EXT:
                if b + ext in self.paths:
                    return [b + ext]
        return []


def build_graph(
    repo_name: str,
    files: list[SourceFile],
    modules: list[ModuleInfo],
    parsed: dict[str, ParsedFile],
    config_parsed: dict[str, ParsedFile],
    infra: dict,
) -> KnowledgeGraph:
    g = KnowledgeGraph()
    g.add_node("repo", "Repository", repo_name)
    module_by_name = {m.name: m for m in modules}
    file_module = {f.path: f.module for f in files}
    file_info = {f.path: f for f in files}

    for m in modules:
        label = "Service" if m.kind == "service" else "Module"
        g.add_node(f"module:{m.name}", label, m.name, path=m.path, kind=m.kind, loc=m.loc, files=m.files,
                   entry_points=len(m.entry_points))
        g.add_edge("repo", f"module:{m.name}", "CONTAINS")

    # ---------------------------------------------------------------- files & symbols
    fn_index: dict[str, list[str]] = defaultdict(list)
    class_index: dict[str, list[str]] = defaultdict(list)
    for path, pf in parsed.items():
        info = file_info[path]
        fid = f"file:{path}"
        g.add_node(fid, "File", PurePosixPath(path).name, path=path, language=pf.language, module=info.module,
                   category=info.category, loc=pf.loc, is_test=info.category == "test", entry_point=pf.entry_point)
        g.add_edge(f"module:{info.module}", fid, "CONTAINS")
        class_ids: dict[str, str] = {}
        for sym in pf.symbols:
            if sym.kind in ("function", "method", "constructor"):
                continue
            cid = f"class:{path}#{sym.name}"
            g.add_node(cid, "Class", sym.name, file=path, kind=sym.kind, line=sym.start_line, end_line=sym.end_line,
                       module=info.module, annotations=", ".join(sym.annotations)[:300])
            g.add_edge(fid, cid, "DEFINES")
            class_ids[sym.name] = cid
            if info.category != "test":
                class_index[sym.name].append(cid)
        for sym in pf.symbols:
            if sym.kind not in ("function", "method", "constructor"):
                continue
            fnid = f"fn:{path}#{sym.qualified}"
            g.add_node(fnid, "Function", sym.qualified, file=path, line=sym.start_line, end_line=sym.end_line,
                       complexity=sym.complexity, module=info.module, signature=sym.signature[:200])
            owner = class_ids.get(sym.parent or "")
            g.add_edge(owner or fid, fnid, "DEFINES")
            fn_index[sym.name].append(fnid)

    # ---------------------------------------------------------------- imports / references
    resolver = ImportResolver(list(parsed), parsed)
    imported: dict[str, set[str]] = defaultdict(set)
    for path, pf in parsed.items():
        for imp in pf.imports:
            for target in resolver.resolve(pf, imp):
                if target != path:
                    g.add_edge(f"file:{path}", f"file:{target}", "IMPORTS")
                    imported[path].add(target)
    for path, pf in parsed.items():
        for ref in pf.type_refs:
            if ref in _GENERIC_TYPES:
                continue
            candidates = class_index.get(ref, [])
            if not candidates:
                continue
            if len(candidates) > 1:
                preferred = [c for c in candidates if g.nodes[c]["file"] in imported[path]]
                candidates = preferred or (candidates if len(candidates) <= 2 else [])
            for cid in candidates:
                if g.nodes[cid]["file"] != path:
                    g.add_edge(f"file:{path}", cid, "REFERENCES")
                    imported[path].add(g.nodes[cid]["file"])

    # ---------------------------------------------------------------- calls
    for path, pf in parsed.items():
        for caller, callee, _line in pf.calls:
            candidates = fn_index.get(callee)
            if not candidates:
                continue
            same_file = [c for c in candidates if g.nodes[c]["file"] == path]
            if same_file:
                targets = same_file[:1]
            elif callee in _GENERIC_CALLS:
                continue
            else:
                related = [c for c in candidates if g.nodes[c]["file"] in imported[path]]
                targets = related[:3] if related else (candidates if len(candidates) == 1 else [])
            src = f"fn:{path}#{caller}" if caller and f"fn:{path}#{caller}" in g.nodes else f"file:{path}"
            for t in targets:
                g.add_edge(src, t, "CALLS")

    # ---------------------------------------------------------------- APIs
    for path, pf in parsed.items():
        module = file_module[path]
        for ep in pf.endpoints:
            aid = f"api:{module}:{ep.method} {ep.path}"
            g.add_node(aid, "API", f"{ep.method} {ep.path}", method=ep.method, route=ep.path, file=path,
                       line=ep.line, framework=ep.framework, module=module, handler=ep.handler)
            g.add_edge(f"module:{module}", aid, "EXPOSES")
            handler_id = f"fn:{path}#{ep.handler}" if ep.handler else None
            if handler_id and handler_id in g.nodes:
                g.add_edge(aid, handler_id, "HANDLED_BY")
            else:
                g.add_edge(aid, f"file:{path}", "DEFINED_IN")

    # ---------------------------------------------------------------- data stores
    all_access = {**{p: pf.db_access for p, pf in parsed.items()}, **{p: pf.db_access for p, pf in config_parsed.items()}}
    techs = Counter(d.target for acc in all_access.values() for d in acc if d.target in DATABASE_TECHS)
    for svc in infra.get("compose_services", []):
        if svc.get("image") and (cls := classify_image(svc["image"])) and cls[0] == "database":
            techs[cls[1]] += 1
    relational = [t for t, _ in techs.most_common() if t not in ("redis", "mongodb", "elasticsearch", "neo4j", "cassandra", "dynamodb")]
    primary_db = relational[0] if relational else (techs.most_common(1)[0][0] if techs else None)

    def db_node(tech: str) -> str:
        return g.add_node(f"db:{tech}", "Database", tech, technology=tech)

    # Entity class name -> mapped table name (e.g. Order -> orders via @Table / __tablename__)
    entity_alias: dict[str, str] = {}
    for pf in parsed.values():
        for acc in pf.db_access:
            if acc.kind != "entity":
                continue
            cls = next((s for s in pf.symbols if s.kind == "class" and acc.line - 1 <= s.start_line <= acc.line + 6), None) \
                or pf.enclosing_symbol(acc.line)
            if cls and cls.kind == "class" and cls.name.lower() != acc.target.lower():
                entity_alias[cls.name.lower()] = acc.target

    for path, accesses in all_access.items():
        if path not in g.nodes and f"file:{path}" not in g.nodes:
            info = file_info.get(path)
            g.add_node(f"file:{path}", "File", PurePosixPath(path).name, path=path, language=info.language if info else "",
                       module=file_module.get(path, "root"), category=info.category if info else "config", loc=info.loc if info else 0)
            g.add_edge(f"module:{file_module.get(path, 'root')}", f"file:{path}", "CONTAINS")
        for acc in accesses:
            src = f"file:{path}"
            if parsed.get(path) and (sym := parsed[path].enclosing_symbol(acc.line, functions_only=True)):
                cand = f"fn:{path}#{sym.qualified}"
                src = cand if cand in g.nodes else src
            if acc.target in DATABASE_TECHS:
                g.add_edge(src, db_node(acc.target), "ACCESSES", kind=acc.kind)
            elif acc.kind in ("entity", "repository", "sql", "schema") or (acc.kind == "orm" and acc.target not in
                                                                           ("jpa", "sqlalchemy", "efcore", "gorm", "django-orm")):
                table = entity_alias.get(acc.target.lower(), acc.target)
                tid = g.add_node(f"table:{table.lower()}", "Table", table, technology=primary_db)
                g.add_edge(src, tid, "ACCESSES", kind=acc.kind)
                if primary_db:
                    g.add_edge(tid, db_node(primary_db), "STORED_IN")
            else:
                g.add_edge(src, db_node(primary_db or "database"), "ACCESSES", kind=acc.kind)

    # ---------------------------------------------------------------- messaging
    for path, pf in parsed.items():
        module = file_module[path]
        for msg in pf.messaging:
            tid = g.add_node(f"topic:{msg.topic}", "Topic", msg.topic, technology=msg.technology)
            rel = "PUBLISHES" if msg.direction == "publish" else "SUBSCRIBES"
            g.add_edge(f"file:{path}", tid, rel)
            g.add_edge(f"module:{module}", tid, rel)

    # ---------------------------------------------------------------- tests
    stems: dict[str, list[str]] = defaultdict(list)
    for path in parsed:
        if file_info[path].category == "source":
            stems[PurePosixPath(path).stem.lower()].append(path)
    for path in parsed:
        if file_info[path].category != "test":
            continue
        targets = {t for t in imported[path] if file_info.get(t) and file_info[t].category == "source"}
        targets.update(stems.get(test_subject_stem(path), []))
        for t in targets:
            g.add_edge(f"file:{path}", f"file:{t}", "TESTS")

    # ---------------------------------------------------------------- module dependencies
    module_of_node = lambda nid: g.nodes[nid].get("module")  # noqa: E731
    dep_weights: Counter = Counter()
    for src, rel, dst in g.edges():
        if rel in CODE_EDGES:
            a, b = module_of_node(src), module_of_node(dst)
            if a and b and a != b:
                dep_weights[(a, b)] += 1
    for (a, b), w in dep_weights.items():
        g.add_edge(f"module:{a}", f"module:{b}", "DEPENDS_ON", weight=w, via="code")

    norm_modules = {_norm_name(m.name): m.name for m in modules}
    for path, pf in {**parsed, **config_parsed}.items():
        for host in pf.http_targets:
            target = norm_modules.get(_norm_name(host))
            src_mod = file_module.get(path)
            if target and src_mod and target != src_mod:
                g.add_edge(f"module:{src_mod}", f"module:{target}", "DEPENDS_ON", weight=1, via="http")

    # ---------------------------------------------------------------- infrastructure
    compose_to_module: dict[str, str] = {}
    for svc in infra.get("compose_services", []):
        mod = None
        ctx = svc.get("build_context")
        if ctx is not None:
            ctx = ctx or "."
            mod = next((m.name for m in modules if m.path == ctx), None) or norm_modules.get(_norm_name(svc["name"]))
        elif svc.get("image"):
            cls = classify_image(svc["image"])
            if cls and cls[0] == "database":
                compose_to_module[svc["name"]] = db_node(cls[1])
                continue
            if cls:
                compose_to_module[svc["name"]] = g.add_node(f"infra:{cls[1]}", "Infrastructure", cls[1], image=svc["image"])
                continue
            mod = norm_modules.get(_norm_name(svc["name"]))
        if mod:
            compose_to_module[svc["name"]] = f"module:{mod}"
    for svc in infra.get("compose_services", []):
        src = compose_to_module.get(svc["name"])
        if not src or not src.startswith("module:"):
            continue
        for dep in svc.get("depends_on", []):
            dst = compose_to_module.get(dep)
            if not dst:
                continue
            rel = "ACCESSES" if dst.startswith("db:") else ("USES" if dst.startswith("infra:") else "DEPENDS_ON")
            g.add_edge(src, dst, rel, **({"weight": 1, "via": "compose"} if rel == "DEPENDS_ON" else {}))
    for wl in infra.get("k8s_workloads", []):
        if wl["kind"] not in ("Deployment", "StatefulSet", "DaemonSet") or not wl.get("name"):
            continue
        mod = norm_modules.get(_norm_name(wl["name"]))
        iid = g.add_node(f"infra:k8s:{wl['name']}", "Infrastructure", f"k8s/{wl['name']}", kind=wl["kind"], file=wl["file"])
        if mod:
            g.add_edge(iid, f"module:{mod}", "DEPLOYS")
    for df in infra.get("dockerfiles", []):
        mod = file_module.get(df)
        if mod:
            iid = g.add_node(f"infra:docker:{mod}", "Infrastructure", f"docker/{mod}", kind="Dockerfile", file=df)
            g.add_edge(iid, f"module:{mod}", "DEPLOYS")
    return g
