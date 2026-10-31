"""Evidence-backed repository report.

Every statement is derived from parsed code, manifests, configuration or the knowledge graph and carries
`path:line` evidence. Anything that could not be determined is listed under Limitations instead of being guessed.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from app.analysis.architecture import LAYER_ORDER, layer_of, trace_data_flow
from app.analysis.purpose import _clean
from app.ingestion.manifests import _DEP_FRAMEWORKS, _dep_matches
from app.parsing import extractors
from app.parsing.models import ParsedFile, Symbol
from app.services.context import RepoContext

FIELD_PATTERNS: dict[str, re.Pattern] = {
    "Java": re.compile(r"^\s*(?:private|protected|public)\s+(?:final\s+)?(?:transient\s+)?([\w<>\[\], ?.]+?)\s+(\w+)\s*(?:=[^;]*)?;\s*(?://.*)?$"),
    "Kotlin": re.compile(r"^\s*(?:override\s+)?(?:val|var)\s+(\w+)\s*:\s*([\w<>?, .]+)"),
    "C#": re.compile(r"^\s*public\s+(?:virtual\s+|required\s+)?([\w<>?\[\], .]+?)\s+(\w+)\s*\{\s*get;"),
    "Python": re.compile(r"^\s+(\w+)\s*(?::\s*[\w\[\], .|\"']+)?\s*=\s*(?:db\.|sa\.|orm\.)?(Column|mapped_column|relationship|models\.\w+|fields\.\w+|Field)\("),
    "TypeScript": re.compile(r"^\s*(?:public\s+|private\s+|readonly\s+)*(\w+)[?!]?\s*:\s*([\w<>\[\]| .]+);"),
}
RELATION_RE = re.compile(r"@(OneToMany|ManyToOne|OneToOne|ManyToMany|ElementCollection|Embedded)|\brelationship\(|ForeignKey\(|models\.ForeignKey|ICollection<|@(ManyToOne|OneToMany)\(")
SECURITY_MARKERS = re.compile(
    r"@PreAuthorize|@Secured|@RolesAllowed|@EnableWebSecurity|SecurityFilterChain|\[Authorize|AddAuthentication|AddAuthorization|"
    r"@login_required|@permission_required|Depends\(\s*get_current_user|OAuth2PasswordBearer|passport\.authenticate|jwt\.(?:sign|verify|decode|encode)|"
    r"JwtDecoder|BCryptPasswordEncoder|bcrypt|argon2|@UseGuards|AuthGuard|HttpSecurity",
)
_PROP_KEY = re.compile(r"^\s*([A-Za-z][\w.\-\[\]]*)\s*[=:]")


class FactBook:
    def __init__(self) -> None:
        self.facts: dict[str, dict] = {}

    def add(self, text: str, evidence: list[dict | None] | None = None, kind: str = "fact") -> dict:
        fid = f"F{len(self.facts) + 1}"
        ev = []
        for e in evidence or []:
            if e and e.get("path") and e not in ev:
                ev.append(e)
        if kind in ("fact", "quote") and not ev:
            kind = "inference"
        fact = {"id": fid, "text": text, "evidence": ev[:6], "kind": kind}
        self.facts[fid] = fact
        return fact


def ev(path: str | None, line: int | None = None, end: int | None = None) -> dict | None:
    if not path:
        return None
    out: dict = {"path": path, "line": line or 1}
    if end and end > (line or 1):
        out["end_line"] = end
    return out


class Lines:
    """Cached file line access for evidence lookup."""

    def __init__(self, ctx: RepoContext) -> None:
        self.ctx = ctx
        self._cache: dict[str, list[str]] = {}

    def get(self, path: str) -> list[str]:
        if path not in self._cache:
            if len(self._cache) > 400:
                self._cache.pop(next(iter(self._cache)))
            self._cache[path] = self.ctx.read(path, 400_000).splitlines()
        return self._cache[path]

    def find(self, path: str, needle: str, regex: bool = False) -> int | None:
        pattern = re.compile(needle if regex else re.escape(needle), re.I)
        for i, line in enumerate(self.get(path), 1):
            if pattern.search(line):
                return i
        return None


def _sym_ev(path: str, s: Symbol) -> dict | None:
    return ev(path, s.start_line, s.end_line)


def _fn_node_ev(g, nid: str) -> dict | None:
    n = g.nodes.get(nid, {})
    return ev(n.get("file"), n.get("line"), n.get("end_line"))


def _params(signature: str) -> str:
    m = re.search(r"\((.*)\)", signature)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()[:160]


# ---------------------------------------------------------------- data model
def entity_fields(lines: list[str], language: str, cls: Symbol) -> list[dict]:
    pattern = FIELD_PATTERNS.get(language) or (FIELD_PATTERNS["TypeScript"] if language == "JavaScript" else None)
    if pattern is None:
        return []
    fields: list[dict] = []
    for i in range(cls.start_line, min(cls.end_line, len(lines))):
        line = lines[i]
        m = pattern.match(line)
        if not m or "(" in line.split("=")[0] and language in ("Java", "C#"):
            continue
        if language in ("Java", "C#"):
            ftype, name = m.group(1).strip(), m.group(2)
            if ftype in ("return", "static", "class") or name in ("serialVersionUID",):
                continue
        elif language == "Kotlin" or language in ("TypeScript", "JavaScript"):
            name, ftype = m.group(1), m.group(2).strip()
        else:
            name, ftype = m.group(1), m.group(2)
        context = " ".join(lines[max(0, i - 3):i + 1])
        rel = RELATION_RE.search(context)
        fields.append({"name": name, "type": ftype[:60], "line": i + 1, "relation": rel.group(0).lstrip("@").rstrip("(") if rel else None})
        if len(fields) >= 40:
            break
    return fields


def _entities(ctx: RepoContext) -> list[dict]:
    out: dict[str, dict] = {}
    for path, pf in ctx.parsed.items():
        if ctx.file_info[path].category != "source":
            continue
        for acc in pf.db_access:
            if acc.kind not in ("entity", "schema"):
                continue
            if acc.kind == "schema":
                key = f"schema:{acc.target.lower()}"
                out.setdefault(key, {"name": acc.target, "table": acc.target, "class": None, "path": path, "line": acc.line,
                                     "language": pf.language, "fields": [], "source": "SQL schema"})
                continue
            cls = next((s for s in pf.symbols if s.kind == "class" and acc.line - 1 <= s.start_line <= acc.line + 6), None) \
                or pf.enclosing_symbol(acc.line)
            if cls is None or cls.kind not in ("class", "record", "struct"):
                continue
            key = f"{path}#{cls.name}"
            entry = out.setdefault(key, {"name": cls.name, "table": acc.target, "class": cls.name, "path": path, "line": cls.start_line,
                                         "end_line": cls.end_line, "language": pf.language, "symbol": cls, "source": acc.detail[:40]})
            if acc.target.lower() != cls.name.lower():
                entry["table"] = acc.target
    return list(out.values())


# ---------------------------------------------------------------- builder
def build_report(ctx: RepoContext, overview: dict, architecture: dict, purpose: dict, security: dict, tests: dict) -> dict:
    g = ctx.graph
    fb = FactBook()
    lines = Lines(ctx)
    sections: list[dict] = []
    limitations: list[str] = []

    def section(sid: str, title: str, facts: list[dict], intro: str = "") -> None:
        sections.append({"id": sid, "title": title, "intro": intro, "facts": [f["id"] for f in facts]})

    # ------------------------------------------------ 1. what the project is
    s1: list[dict] = []
    readme_path = next((f.path for f in sorted(ctx.files, key=lambda f: f.path.count("/")) if PurePosixPath(f.path).name.lower().startswith("readme")), None)
    rp = purpose.get("readme_purpose") or ""
    if readme_path and rp:
        words = " ".join(rp.split()[:5])
        line = lines.find(readme_path, words) or lines.find(readme_path, " ".join(rp.split()[:3])) or 1
        s1.append(fb.add(f"README states: \u201c{rp[:600]}\u201d", [ev(readme_path, line)], "quote"))
    elif readme_path:
        limitations.append(f"README ({readme_path}) has no descriptive paragraph; purpose is inferred only from code.")
    else:
        limitations.append("No README found; project purpose is inferred only from code structure.")
    ptype = purpose.get("project_type") or {}
    entries = [p for p, pf in ctx.parsed.items() if pf.entry_point and ctx.file_info[p].category == "source"]
    if ptype:
        s1.append(fb.add(f"Classified as **{ptype.get('type')}** based on: {', '.join(ptype.get('signals', [])) or 'code structure'}.",
                         [ev(p) for p in entries[:3]], "inference"))
    for d in (purpose.get("domains") or [])[:3]:
        terms = d.get("evidence", [])
        examples = [c for c in g.by_label("Class") if any(t in c["name"].lower() for t in terms)][:3]
        examples += [t for t in g.by_label("Table") if any(x in t["name"].lower() for x in terms) and t.get("file")][:2]
        if not examples:
            continue  # README-only vocabulary is not evidence of what the code does
        s1.append(fb.add(f"Domain vocabulary in code points to **{d['domain']}** (terms: {', '.join(terms)}).",
                         [ev(c.get("file"), c.get("line")) for c in examples], "inference"))
    if readme_path:
        heads = [(_clean(re.split(r"\s*\[?!\[", m.group(2))[0]), i) for i, line in enumerate(lines.get(readme_path), 1) if (m := re.match(r"^(#{1,3})\s+(.*)", line))]
        heads = [(h, i) for h, i in heads if h]
        if heads:
            s1.append(fb.add("README sections: " + "; ".join(f"“{h}”" for h, _ in heads[:14]) + ".", [ev(readme_path, heads[0][1])], "quote"))
    stats = overview.get("stats", {})
    langs = overview.get("languages", {})
    s1.append(fb.add(
        f"Codebase size: {stats.get('source_files', 0):,} source files, {stats.get('loc', 0):,} lines of code; "
        + ", ".join(f"{k} {v['percent']}%" for k, v in list(langs.items())[:5])
        + f"; {stats.get('classes', 0):,} classes, {stats.get('functions', 0):,} functions, {stats.get('apis', 0)} API endpoints.", [], "metric"))
    feats = purpose.get("key_features", [])
    if readme_path:
        for feat in feats[:10]:
            line = lines.find(readme_path, feat[:30])
            if line:
                s1.append(fb.add(f"Documented feature: {feat}", [ev(readme_path, line)], "quote"))
    section("what", "What the project is", s1)

    # ------------------------------------------------ 2. tech stack
    s2: list[dict] = []
    for fw in ctx.frameworks:
        evidence_path = fw.get("evidence")
        line = None
        if evidence_path == "manifests":
            evidence_path, line = None, None
            for f in ctx.files:
                if f.category == "infra" and f.language == "YAML" and not PurePosixPath(f.path).name.lower().startswith("docker-compose"):
                    found = lines.find(f.path, r"^\s*kind:\s*(Deployment|StatefulSet|Service|Ingress)", regex=True)
                    if found:
                        evidence_path, line = f.path, found
                        break
        elif evidence_path and evidence_path in ctx.file_info:
            needles = [n for n, name, _ in _DEP_FRAMEWORKS if name == fw["name"]]
            dep = next((d for d in ctx.dependencies if d["file"] == evidence_path and any(_dep_matches(d["name"], d["ecosystem"], n) for n in needles)), None)
            needle = dep["name"].split(":")[-1] if dep else fw["name"].split()[0]
            line = lines.find(evidence_path, needle)
        s2.append(fb.add(f"Uses **{fw['name']}**{' ' + fw['version'] if fw.get('version') else ''} ({fw['category']}).",
                         [ev(evidence_path, line)] if evidence_path else [], "fact"))
    tech_ev: dict[str, list[dict]] = defaultdict(list)
    for f in ctx.files:
        if f.category in ("config", "infra") and len(tech_ev) < 20:
            pf = ParsedFile(path=f.path, language=f.language)
            extractors.extract_db_access(pf, ctx.read(f.path, 200_000))
            for d in pf.db_access:
                if d.target in extractors.DATABASE_TECHS and len(tech_ev[d.target]) < 3:
                    tech_ev[d.target].append(ev(f.path, d.line))
    for path, pf in ctx.parsed.items():
        for d in pf.db_access:
            if d.target in extractors.DATABASE_TECHS and len(tech_ev[d.target]) < 3:
                tech_ev[d.target].append(ev(path, d.line))
    for db in g.by_label("Database"):
        if db["name"] == "database":
            continue
        s2.append(fb.add(f"Persists data in **{db['name']}**.", tech_ev.get(db["name"], []), "fact"))
    section("stack", "Technology stack (with evidence)", s2)

    # ------------------------------------------------ 3. entry points & startup
    s3: list[dict] = []
    for path in entries[:12]:
        pf = ctx.parsed[path]
        main = next((s for s in pf.symbols if s.name in ("main", "Main") and s.kind in ("function", "method")), None)
        calls = [c for caller, c, _ in pf.calls if main and caller and caller.endswith(main.name)]
        line = main.start_line if main else (lines.find(path, r"SpringBootApplication|FastAPI\(|Flask\(|CreateBuilder|NestFactory|\.listen\(", regex=True) or 1)
        text = f"Application entry point `{path}` ({pf.entry_point})."
        if calls:
            text += f" Startup calls: {', '.join(dict.fromkeys(calls[:8]))}."
        s3.append(fb.add(text, [ev(path, line, main.end_line if main else None)]))
    if not entries:
        limitations.append("No runtime entry point was detected (library, or an unsupported bootstrapping style).")
    section("startup", "Entry points & startup", s3)

    # ------------------------------------------------ 4. architecture & modules
    s4: list[dict] = []
    style = architecture.get("style", {})
    layer_examples = {}
    for f in ctx.files:
        if f.category == "source":
            layer = layer_of(f.path)
            if layer and layer not in layer_examples:
                layer_examples[layer] = f.path
    s4.append(fb.add(f"Architecture style: **{style.get('primary')}**" + (f" with {', '.join(style.get('traits', []))}" if style.get("traits") else "") + ".",
                     [ev(layer_examples[layer]) for layer in LAYER_ORDER if layer in layer_examples][:4], "inference"))
    cross_example: dict[tuple[str, str], tuple[str, str]] = {}
    for s, r, t in g.edges():
        if r in ("IMPORTS", "REFERENCES"):
            a, b = g.nodes[s].get("module"), g.nodes[t].get("module")
            if a and b and a != b:
                cross_example.setdefault((a, b), (s, t))
    for m in sorted(ctx.modules, key=lambda m: -m.loc)[:15]:
        if m.loc == 0:
            continue
        mid = f"module:{m.name}"
        deps = []
        evidence = [ev(f"{m.path}/{m.manifest}" if m.manifest and m.path != "." else m.manifest)] if m.manifest else []
        if not evidence:
            biggest = max((f for f in ctx.files if f.module == m.name and f.category == "source"), key=lambda f: f.loc, default=None)
            evidence = [ev(biggest.path)] if biggest else []
        for d in g.successors(mid, ("DEPENDS_ON",)):
            props = g.edge_props.get((mid, "DEPENDS_ON", d), {})
            deps.append(f"{g.nodes[d]['name']} (via {props.get('via', 'code')})")
            example = cross_example.get((m.name, g.nodes[d]["name"]))
            if example:
                src_path = g.file_of(example[0])
                tgt_name = g.nodes[example[1]]["name"]
                evidence.append(ev(src_path, lines.find(src_path, PurePosixPath(tgt_name).stem) if src_path else None))
        s4.append(fb.add(f"Module **{m.name}** (`{m.path}`, {m.kind}, {m.loc:,} LOC)" + (f" depends on {', '.join(deps)}." if deps else "."), evidence))
    section("architecture", "Architecture & modules", s4)

    # ------------------------------------------------ 5. capabilities & API catalog
    s5: list[dict] = []
    apis = sorted(g.by_label("API"), key=lambda a: (a.get("module") or "", a.get("route") or "", a.get("method") or ""))
    for a in apis[:250]:
        handler_id = next(iter(g.successors(a["id"], ("HANDLED_BY",))), None)
        params = ""
        if handler_id:
            params = _params(g.nodes[handler_id].get("signature", ""))
        text = f"`{a['name']}` → handler `{a.get('handler') or PurePosixPath(a['file']).name}`" + (f"({params})" if params else "") + f" [{a.get('framework')}]"
        s5.append(fb.add(text, [ev(a["file"], a.get("line")), _fn_node_ev(g, handler_id) if handler_id else None]))
    if len(apis) > 250:
        limitations.append(f"API catalog truncated to 250 of {len(apis)} endpoints.")
    if not apis:
        limitations.append("No HTTP/RPC endpoints detected (not a server, or routing style not recognised).")
    section("apis", f"API catalog ({len(apis)} endpoints)", s5)

    # ------------------------------------------------ 6. how requests are processed
    s6: list[dict] = []
    relational = sorted(d["name"] for d in g.by_label("Database") if d["name"] not in ("redis", "mongodb", "elasticsearch", "database"))
    for a in apis[:150]:
        path = trace_data_flow(g, a["id"])
        if not path:
            continue
        names, evidence = [], []
        for nid in path:
            n = g.nodes[nid]
            label = n["label"]
            if label == "API":
                names.append(f"`{n['name']}`")
                evidence.append(ev(n.get("file"), n.get("line")))
            elif label == "Function":
                names.append(f"`{n['name']}()`")
                evidence.append(_fn_node_ev(g, nid))
            elif label == "File":
                names.append(f"`{PurePosixPath(n['path']).name}`")
                evidence.append(ev(n["path"]))
            elif label == "Table":
                names.append(f"table **{n['name']}**")
            elif label == "Database":
                if len(relational) > 1 and n["name"] in relational:
                    names.append("configured database (" + " | ".join(f"**{r}**" for r in relational) + ", per active profile)")
                else:
                    names.append(f"**{n['name']}**")
        s6.append(fb.add("Request flow: " + " → ".join(dict.fromkeys(names)), evidence))
        if len(s6) >= 20:
            break
    for a in apis[:60]:
        handler_id = next(iter(g.successors(a["id"], ("HANDLED_BY",))), None)
        if not handler_id:
            continue
        callees = [c for c in g.successors(handler_id, ("CALLS",)) if g.nodes[c].get("file") != g.nodes[handler_id].get("file")]
        if callees:
            s6.append(fb.add(f"`{g.nodes[handler_id]['name']}()` delegates to " + ", ".join(f"`{g.nodes[c]['name']}()`" for c in sorted(callees)[:6]) + ".",
                             [_fn_node_ev(g, handler_id), *[_fn_node_ev(g, c) for c in sorted(callees)[:4]]]))
    if apis and not s6:
        limitations.append("Could not trace API handlers to data stores (calls may go through framework indirection or dynamic dispatch).")
    section("flows", "How it works: request processing", s6,
            "Traced through resolved calls, imports and references in the knowledge graph.")

    # ------------------------------------------------ 7. events & integrations
    s7: list[dict] = []
    topic_sites: dict[str, dict[str, list]] = defaultdict(lambda: {"publish": [], "consume": []})
    for path, pf in ctx.parsed.items():
        for m in pf.messaging:
            topic_sites[m.topic][m.direction].append(ev(path, m.line))
    for topic, sites in topic_sites.items():
        pubs = sorted({ctx.module_of(e["path"]) for e in sites["publish"]})
        subs = sorted({ctx.module_of(e["path"]) for e in sites["consume"]})
        s7.append(fb.add(f"Event `{topic}`: published by {', '.join(p for p in pubs if p) or 'unknown (external)'}; consumed by {', '.join(s for s in subs if s) or 'unknown (external)'}.",
                         sites["publish"][:3] + sites["consume"][:3]))
    for path, pf in ctx.parsed.items():
        if len(s7) >= 40:
            break
        for host in pf.http_targets:
            s7.append(fb.add(f"`{PurePosixPath(path).name}` calls remote host `{host}` over HTTP/RPC.", [ev(path, lines.find(path, host))]))
    for c in g.by_label("Class"):
        if re.search(r"(Client|Gateway|Adapter|Proxy)$", c["name"]) and ctx.file_info.get(c["file"]) and ctx.file_info[c["file"]].category == "source":
            s7.append(fb.add(f"Outbound integration class `{c['name']}`.", [ev(c["file"], c.get("line"), c.get("end_line"))]))
    section("integrations", "Events & external integrations", s7)

    # ------------------------------------------------ 8. data model
    s8: list[dict] = []
    entities = _entities(ctx)
    for e in entities[:60]:
        if e.get("symbol") is not None:
            fields = entity_fields(lines.get(e["path"]), e["language"], e["symbol"])
            rels = [f"{f['name']} ({f['relation']})" for f in fields if f["relation"]]
            text = f"Entity `{e['class']}` maps to table **{e['table']}**"
            if fields:
                text += f"; fields: {', '.join(f['name'] + ': ' + f['type'] for f in fields[:14])}"
            if rels:
                text += f"; relationships: {', '.join(rels[:6])}"
            evidence = [ev(e["path"], e["line"], e.get("end_line"))] + [ev(e["path"], f["line"]) for f in fields if f["relation"]][:3]
            s8.append(fb.add(text + ".", evidence))
        else:
            s8.append(fb.add(f"Schema defines table **{e['table']}**.", [ev(e["path"], e["line"])]))
    repos_seen = set()
    for path, pf in ctx.parsed.items():
        for acc in pf.db_access:
            if acc.kind == "repository" and (path, acc.target) not in repos_seen:
                repos_seen.add((path, acc.target))
                s8.append(fb.add(f"`{PurePosixPath(path).stem}` is the data repository for `{acc.target}`.", [ev(path, acc.line)]))
    sql_sites = Counter()
    for path, pf in ctx.parsed.items():
        for acc in pf.db_access:
            if acc.kind == "sql" and sql_sites[acc.target] < 1:
                sql_sites[acc.target] += 1
                s8.append(fb.add(f"Query (SQL/JPQL) against **{acc.target}** in `{PurePosixPath(path).name}`.", [ev(path, acc.line)]))
    if not entities and not repos_seen:
        limitations.append("No ORM entities or schema definitions were found; the data model could not be reconstructed.")
    section("data", "Data model", s8)

    # ------------------------------------------------ 9. components
    s9: list[dict] = []
    fan_in: Counter = Counter()
    for c in g.by_label("Class"):
        fan_in[c["id"]] = len({g.file_of(p) for p in g.predecessors(c["id"], ("REFERENCES", "CALLS"))} - {c["file"], None})
    for m in sorted(ctx.modules, key=lambda m: -m.loc)[:12]:
        classes = [c for c in g.by_label("Class") if c.get("module") == m.name and ctx.file_info.get(c["file"]) and ctx.file_info[c["file"]].category == "source"]
        for c in sorted(classes, key=lambda c: -fan_in[c["id"]])[:8]:
            pf = ctx.parsed.get(c["file"])
            sym = next((s for s in (pf.symbols if pf else []) if s.name == c["name"] and s.kind not in ("function", "method", "constructor")), None)
            methods = [s.name for s in (pf.symbols if pf else []) if s.parent == c["name"] and s.kind in ("method", "function")]
            doc = (sym.doc.split(". ")[0].strip() if sym and sym.doc else "")
            role = layer_of(c["file"]) or "general"
            text = f"[{m.name}] `{c['name']}` ({c.get('kind')}, {role} layer, used by {fan_in[c['id']]} files)"
            if doc:
                text += f" — doc: \u201c{doc[:200]}\u201d"
            if methods:
                text += f"; methods: {', '.join(dict.fromkeys(methods[:12]))}"
            s9.append(fb.add(text + ".", [ev(c["file"], c.get("line"), c.get("end_line"))]))
    section("components", "Key components", s9, "Classes ranked by how many other files depend on them.")

    # ------------------------------------------------ 10. security mechanisms
    s10: list[dict] = []
    for path, pf in ctx.parsed.items():
        if ctx.file_info[path].category != "source" or len(s10) >= 30:
            continue
        for i, line in enumerate(lines.get(path), 1):
            m = SECURITY_MARKERS.search(line)
            if m:
                s10.append(fb.add(f"Security mechanism `{m.group(0)}` in `{PurePosixPath(path).name}`.", [ev(path, i)]))
                break
    sec_summary = (security or {}).get("summary", {})
    if sec_summary:
        top = [f for f in security.get("findings", []) if f.get("confidence") != "low"][:5]
        s10.append(fb.add(f"Static analysis found {sec_summary.get('total', 0)} findings ({sec_summary.get('by_severity')}) and "
                          f"{sec_summary.get('dependency_vulnerabilities', 0)} vulnerable dependencies.",
                          [ev(f["file"], f["line"]) for f in top], "metric"))
    if not any(f["kind"] != "metric" for f in s10):
        limitations.append("No authentication/authorization mechanism was found in the code.")
    section("security", "Security mechanisms", s10)

    # ------------------------------------------------ 11. configuration
    s11: list[dict] = []
    for f in [f for f in ctx.files if f.category == "config" and re.search(r"\.(properties|ya?ml|env|ini|toml)$|appsettings.*\.json$", f.path)][:12]:
        keys = []
        for i, line in enumerate(lines.get(f.path), 1):
            m = _PROP_KEY.match(line)
            if m and not line.startswith((" ", "\t")) or (m and f.path.endswith(".properties")):
                keys.append((m.group(1), i))
            if len(keys) >= 20:
                break
        if keys:
            s11.append(fb.add(f"`{f.path}` configures: {', '.join(dict.fromkeys(k for k, _ in keys))} (values not shown).", [ev(f.path, keys[0][1])]))
    for v in (purpose.get("configuration") or [])[:25]:
        path = v["files"][0] if v["files"] else None
        s11.append(fb.add(f"Reads environment variable `{v['name']}`" + (" (secret)" if v["secret"] else "") + ".",
                          [ev(path, lines.find(path, v["name"]))] if path else []))
    section("config", "Configuration", s11)

    # ------------------------------------------------ 12. quality
    s12: list[dict] = []
    cov = (tests or {}).get("coverage_estimate", {})
    if cov:
        s12.append(fb.add(f"{cov.get('tested_files', 0)} of {cov.get('testable_files', 0)} testable source files have associated tests "
                          f"({cov.get('files_percent', 0)}%), across {cov.get('test_files', 0)} test files.", [], "metric"))
    funcs = sorted(((p, s) for p, pf in ctx.parsed.items() if ctx.file_info[p].category == "source" for s in pf.symbols
                    if s.kind in ("function", "method")), key=lambda x: -x[1].complexity)[:10]
    for p, s in funcs:
        if s.complexity >= 5:
            s12.append(fb.add(f"Complex logic: `{s.qualified}()` has cyclomatic complexity {s.complexity} ({s.end_line - s.start_line + 1} lines).", [_sym_ev(p, s)]))
    section("quality", "Complexity & testing", s12)

    # ------------------------------------------------ limitations
    regex_files = sum(1 for pf in ctx.parsed.values() if pf.parser == "regex")
    if regex_files:
        limitations.append(f"{regex_files} files were parsed with the heuristic regex parser (Tree-sitter unavailable for them); symbol boundaries may be approximate.")
    total_imports = sum(len(pf.imports) for pf in ctx.parsed.values())
    resolved = sum(1 for _ in (e for e in g.edges() if e[1] == "IMPORTS"))
    if total_imports:
        limitations.append(f"{resolved} of {total_imports} import statements resolved to files in this repository; the rest are external libraries or unresolved.")
    skipped = (overview.get("pipeline") or {}).get("skipped_files")
    if skipped:
        limitations.append(f"{skipped} files were skipped (binary, generated, vendored, or larger than the size limit).")
    if not (ctx.history or {}).get("available"):
        limitations.append("Git history unavailable — ownership and churn insights are omitted.")
    limitations.append("Runtime behaviour (reflection, dependency injection wiring, dynamic routing, configuration-driven logic) is not executed; findings reflect static analysis only.")

    cited = {e["path"] for f in fb.facts.values() for e in f["evidence"]}
    return {
        "repository": ctx.name,
        "sections": sections,
        "facts": fb.facts,
        "limitations": limitations,
        "stats": {"facts": len(fb.facts), "files_cited": len(cited),
                  "evidence_backed": sum(1 for f in fb.facts.values() if f["evidence"]),
                  "metric_facts": sum(1 for f in fb.facts.values() if f["kind"] == "metric")},
    }


def report_markdown(report: dict) -> str:
    out = [f"# Repository report: {report['repository']}", "",
           f"_{report['stats']['facts']} facts, {report['stats']['files_cited']} files cited. Every statement links to its source._", ""]
    for sec in report["sections"]:
        if not sec["facts"]:
            continue
        out += [f"## {sec['title']}", ""]
        if sec.get("intro"):
            out += [f"_{sec['intro']}_", ""]
        for fid in sec["facts"]:
            f = report["facts"][fid]
            refs = ", ".join(f"`{e['path']}:{e['line']}`" for e in f["evidence"][:3])
            out.append(f"- {f['text']}" + (f" — {refs}" if refs else ""))
        out.append("")
    out += ["## Limitations (what could not be determined)", ""] + [f"- {x}" for x in report["limitations"]]
    if report.get("narrative"):
        out += ["", "## AI narrative (grounded in the facts above)", "", report["narrative"]["text"]]
    return "\n".join(out) + "\n"
