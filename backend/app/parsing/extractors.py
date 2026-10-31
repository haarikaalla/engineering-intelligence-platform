"""Cross-language semantic extractors: endpoints, data access, service calls, messaging, entry points, complexity."""
from __future__ import annotations

import bisect
import re
from pathlib import PurePosixPath

from app.parsing.models import DbAccess, Endpoint, Messaging, ParsedFile, Symbol

I, M = re.I, re.M

# ---------------------------------------------------------------- endpoints
# Each pattern: (languages, regex, framework, style, method_group|fixed method, path_group)
# style: "decorator" (annotation precedes handler) | "call" (inline registration) | "mapping" (route prefix / path)
_JVM = frozenset({"Java", "Kotlin", "Groovy", "Scala"})
_JS = frozenset({"JavaScript", "TypeScript"})
_CS = frozenset({"C#"})
_PY = frozenset({"Python"})
_GO = frozenset({"Go"})
ENDPOINT_PATTERNS: list[tuple[frozenset, re.Pattern, str, str, str | int, int | None]] = [
    (_JVM, re.compile(r"@(Get|Post|Put|Delete|Patch)Mapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?\s*\"([^\"]*)\""), "Spring", "decorator", 1, 2),
    (_JVM, re.compile(r"@(Get|Post|Put|Delete|Patch)Mapping\b(?!\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?\s*\")"), "Spring", "decorator", 1, None),
    (_JVM, re.compile(r"@RequestMapping\s*\(([^)]*)\)"), "Spring", "mapping", "ANY", 1),
    (_JVM, re.compile(r"^\s*@(GET|POST|PUT|DELETE|PATCH)\s*$", M), "JAX-RS", "decorator", 1, None),
    (_JVM, re.compile(r"^\s*@Path\s*\(\s*\"([^\"]*)\"\s*\)", M), "JAX-RS", "mapping", "PATH", 1),
    (_CS, re.compile(r"\[Http(Get|Post|Put|Delete|Patch)\s*(?:\(\s*(?:template:\s*)?\"([^\"]*)\"[^)]*\))?\s*\]"), "ASP.NET", "decorator", 1, 2),
    (_CS, re.compile(r"\[Route\s*\(\s*\"([^\"]*)\"\s*\)\]"), "ASP.NET", "mapping", "ANY", 1),
    (_CS, re.compile(r"\.Map(Get|Post|Put|Delete|Patch)\s*\(\s*\"([^\"]+)\""), "ASP.NET Minimal API", "call", 1, 2),
    (_PY, re.compile(r"^\s*@\w+(?:\.\w+)*\.(get|post|put|delete|patch|head|options)\(\s*[rf]?['\"]([^'\"]*)['\"]", M), "FastAPI/Flask", "decorator", 1, 2),
    (_PY, re.compile(r"^\s*@\w+(?:\.\w+)*\.(?:route|api_route)\(\s*['\"]([^'\"]*)['\"](?:[^)]*methods\s*=\s*\[([^\]]+)\])?", M), "Flask", "decorator", 2, 1),
    (_PY, re.compile(r"\b(?:re_)?path\(\s*r?['\"]([^'\"]*)['\"]\s*,\s*([\w.]+)"), "Django", "call", "ANY", 1),
    (_JS, re.compile(r"\b(?:app|router|server|route|\w+Router|\w+Routes)\.(get|post|put|delete|patch|all)\s*\(\s*['\"`]([^'\"`]+)['\"`]\s*,"), "Express", "call", 1, 2),
    (_JS, re.compile(r"^\s*@(Get|Post|Put|Delete|Patch|All)\s*\(\s*(?:['\"`]([^'\"`]*)['\"`])?\s*\)", M), "NestJS", "decorator", 1, 2),
    (_JS, re.compile(r"^\s*@Controller\s*\(\s*(?:['\"`]([^'\"`]*)['\"`])?", M), "NestJS", "mapping", "PREFIX", 1),
    (_GO, re.compile(r"\.(GET|POST|PUT|DELETE|PATCH)\s*\(\s*\"([^\"]+)\""), "Gin/Echo", "call", 1, 2),
    (_GO, re.compile(r"\.(Get|Post|Put|Delete|Patch)\s*\(\s*\"(/[^\"]*)\""), "Fiber/Chi", "call", 1, 2),
    (_GO, re.compile(r"\bHandleFunc\s*\(\s*\"([^\"]+)\""), "net/http", "call", "ANY", 1),
    (frozenset({"Protocol Buffers"}), re.compile(r"^\s*rpc\s+(\w+)\s*\(", M), "gRPC", "call", "RPC", 1),
]
_NEXT_ROUTE_RE = re.compile(r"(^|/)(pages/api/.+|app/(.+/)?route)\.(t|j)sx?$")
_NEXT_HANDLER_RE = re.compile(r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|DELETE|PATCH)\b")


def _line_index(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _line(starts: list[int], offset: int) -> int:
    return bisect.bisect_right(starts, offset)


def _target_symbol(pf: ParsedFile, line: int) -> Symbol | None:
    funcs = [s for s in pf.symbols if s.kind in ("function", "method", "constructor")]
    containing = [f for f in funcs if f.start_line <= line <= f.end_line and line - f.start_line <= 8]
    if containing:
        return max(containing, key=lambda s: s.start_line)
    following = [s for s in pf.symbols if line < s.start_line <= line + 8]
    if following:
        return min(following, key=lambda s: s.start_line)
    classes = [s for s in pf.symbols if s.kind not in ("function", "method", "constructor")
               and s.start_line <= line <= s.end_line and line - s.start_line <= 8]
    return max(classes, key=lambda s: s.start_line) if classes else None


def _join(*parts: str) -> str:
    joined = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return "/" + joined if joined else "/"


def _spring_mapping(args: str) -> tuple[str, str]:
    path_m = re.search(r"(?:value|path)\s*=\s*\{?\s*\"([^\"]*)\"", args) or re.search(r"^\s*\{?\s*\"([^\"]*)\"", args)
    method_m = re.search(r"RequestMethod\.(\w+)", args)
    return (method_m.group(1).upper() if method_m else "ANY", path_m.group(1) if path_m else "")


def extract_endpoints(pf: ParsedFile, text: str) -> None:
    starts = _line_index(text)
    prefixes: dict[str, str] = {}
    method_paths: dict[str, str] = {}
    raw: list[tuple[str, str, int, str, Symbol | None, str]] = []

    for languages, pattern, framework, style, method_spec, path_group in ENDPOINT_PATTERNS:
        if pf.language not in languages:
            continue
        for m in pattern.finditer(text):
            line = _line(starts, m.start())
            path = (m.group(path_group) or "") if path_group else ""
            method = m.group(method_spec) if isinstance(method_spec, int) else method_spec
            if framework == "Spring" and style == "mapping":
                method, path = _spring_mapping(m.group(1))
            if framework == "Flask" and style == "decorator":
                methods = re.findall(r"['\"](\w+)['\"]", method or "")
                method = methods[0] if methods else "GET"
            target = _target_symbol(pf, line) if style != "call" else pf.enclosing_symbol(line, functions_only=True)
            is_class = target is not None and target.kind not in ("function", "method", "constructor")
            if style == "mapping":
                if is_class:
                    prefixes[target.name] = path
                    continue
                if method == "PATH" and target is not None:
                    method_paths[target.qualified] = path
                    continue
                if method == "PREFIX":
                    continue
            if method == "PATH":
                continue
            raw.append(((method or "ANY").upper(), path, line, framework, target, style))

    if _NEXT_ROUTE_RE.search(pf.path):
        route = re.sub(r"^.*?(pages/api|app)/", "/", pf.path)
        route = re.sub(r"(/route|/index)?\.(t|j)sx?$", "", route)
        route = ("/api" + route) if "pages/api" in pf.path else route
        for m in _NEXT_HANDLER_RE.finditer(text):
            line = _line(starts, m.start())
            raw.append((m.group(1), route, line, "Next.js", pf.enclosing_symbol(line, True), "decorator"))
        if "pages/api" in pf.path and not _NEXT_HANDLER_RE.search(text):
            raw.append(("ANY", route, 1, "Next.js", None, "call"))

    by_handler: dict[str, list[int]] = {}
    endpoints: list[Endpoint] = []
    for method, path, line, framework, target, style in raw:
        handler = target.qualified if target is not None and target.kind in ("function", "method", "constructor") else None
        if handler and handler in method_paths and not path:
            path = method_paths[handler]
        if target is not None and target.parent and target.parent in prefixes:
            prefix = prefixes[target.parent].replace("[controller]", target.parent.removesuffix("Controller"))
            path = _join(prefix, path)
        elif not path.startswith("/") and framework != "gRPC":
            path = _join(path)
        path = path.replace("[action]", target.name if target is not None else "")
        endpoints.append(Endpoint(method=method, path=path, line=line, framework=framework, handler=handler))
        if handler:
            by_handler.setdefault(handler, []).append(len(endpoints) - 1)

    # Merge "ANY" route attributes into specific-verb endpoints of the same handler (ASP.NET / Spring)
    drop: set[int] = set()
    for idxs in by_handler.values():
        specific = [i for i in idxs if endpoints[i].method != "ANY"]
        generic = [i for i in idxs if endpoints[i].method == "ANY"]
        if specific and generic:
            for i in specific:
                if endpoints[i].path in ("/", ""):
                    endpoints[i].path = endpoints[generic[0]].path
            drop.update(generic)
    pf.endpoints = [e for i, e in enumerate(endpoints) if i not in drop]


# ---------------------------------------------------------------- data access
_TECH_ALIASES = {
    "postgres": "postgresql", "postgresql": "postgresql", "pg": "postgresql", "npgsql": "postgresql",
    "mysql": "mysql", "mariadb": "mariadb", "mongodb": "mongodb", "mongodb+srv": "mongodb", "mongo": "mongodb",
    "redis": "redis", "sqlserver": "sqlserver", "mssql": "sqlserver", "sql": "sqlserver", "oracle": "oracle",
    "h2": "h2", "sqlite": "sqlite", "sqlite3": "sqlite", "cassandra": "cassandra", "dynamodb": "dynamodb",
    "elasticsearch": "elasticsearch", "neo4j": "neo4j", "hsqldb": "hsqldb", "db2": "db2",
}
DATABASE_TECHS = set(_TECH_ALIASES.values())

DB_PATTERNS: list[tuple[re.Pattern, str, str | int]] = [
    (re.compile(r"@Table\s*\(\s*name\s*=\s*\"(\w+)\""), "entity", 1),
    (re.compile(r"\bextends\s+(?:Jpa|Crud|PagingAndSorting|ListCrud|ListPagingAndSorting|Mongo|Reactive\w*|R2dbc)?Repository\s*<\s*(\w+)"), "repository", 1),
    (re.compile(r"\b(?:JdbcTemplate|NamedParameterJdbcTemplate|DataSource)\b"), "driver", "jdbc"),
    (re.compile(r"\bEntityManager\b"), "orm", "jpa"),
    (re.compile(r"__tablename__\s*=\s*['\"](\w+)['\"]"), "entity", 1),
    (re.compile(r"^class\s+(\w+)\s*\(\s*(?:models\.Model|db\.Model)", M), "entity", 1),
    (re.compile(r"^class\s+(\w+)\s*\([^)]*table\s*=\s*True", M), "entity", 1),
    (re.compile(r"\b(?:session|db\.session|Session\(\))\.(?:query|add|execute|commit|delete|scalars|get)\("), "orm", "sqlalchemy"),
    (re.compile(r"\b\w+\.objects\.(?:filter|get|create|all|exclude|update|delete)\("), "orm", "django-orm"),
    (re.compile(r"\b(?:psycopg2?|asyncpg)\.(?:connect|create_pool)\b"), "driver", "postgresql"),
    (re.compile(r"\bsqlite3\.connect\b"), "driver", "sqlite"),
    (re.compile(r"\b(?:pymysql\.connect|mysql\.connector|mysql2?\.create(?:Connection|Pool))\b"), "driver", "mysql"),
    (re.compile(r"\bMongoClient\s*\(|mongoose\.connect\b|\bMongoTemplate\b"), "nosql", "mongodb"),
    (re.compile(r"\bredis\.(?:Redis|StrictRedis|createClient)\b|\bnew\s+Redis\s*\(|StackExchange\.Redis|\bRedisTemplate\b|\bJedis\b"), "cache", "redis"),
    (re.compile(r"mongoose\.model\s*\(\s*['\"](\w+)"), "entity", 1),
    (re.compile(r"\bsequelize\.define\s*\(\s*['\"](\w+)"), "entity", 1),
    (re.compile(r"\bprisma\.(\w+)\.(?:find\w*|create\w*|update\w*|delete\w*|upsert|count|aggregate)\b"), "orm", 1),
    (re.compile(r"""(?:require\(\s*['"]pg['"]\s*\)|from\s+['"]pg['"])"""), "driver", "postgresql"),
    (re.compile(r"\bDbSet\s*<\s*(\w+)\s*>"), "entity", 1),
    (re.compile(r"\bclass\s+\w+\s*(?:\([^)]*\))?\s*:\s*(?:\w+\.)?(?:Identity)?DbContext\b"), "orm", "efcore"),
    (re.compile(r"\[Table\(\s*\"(\w+)\""), "entity", 1),
    (re.compile(r"\bnew\s+(Sql|Npgsql|MySql|Oracle|Sqlite)Connection\s*\("), "driver", 1),
    (re.compile(r"\bsql\.Open\s*\(\s*\"(\w+)\""), "driver", 1),
    (re.compile(r"\bgorm\.Open\b"), "orm", "gorm"),
    (re.compile(r"jdbc:(\w+):"), "connection", 1),
    (re.compile(r"\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql|sqlserver|mariadb)://"), "connection", 1),
    (re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(?:\w+\.)?(\w+)", I | M), "schema", 1),
]
SQL_PATTERNS = [
    re.compile(r"[\"'`]\s*select\b[^\"'`]*?\bfrom\s+[`\"\[]?([A-Za-z_][\w]*)", I),
    re.compile(r"[\"'`]\s*insert\s+into\s+[`\"\[]?([A-Za-z_][\w]*)", I),
    re.compile(r"[\"'`]\s*update\s+[`\"\[]?([A-Za-z_][\w]*)\s+set\b", I),
    re.compile(r"[\"'`]\s*delete\s+from\s+[`\"\[]?([A-Za-z_][\w]*)", I),
]


def normalize_tech(value: str) -> str:
    return _TECH_ALIASES.get(value.lower(), value.lower())


def extract_db_access(pf: ParsedFile, text: str) -> None:
    starts = _line_index(text)
    out: list[DbAccess] = []
    for pattern, kind, target_spec in DB_PATTERNS:
        for m in pattern.finditer(text):
            target = m.group(target_spec) if isinstance(target_spec, int) else target_spec
            if kind in ("driver", "connection", "nosql", "cache"):
                target = normalize_tech(target)
            out.append(DbAccess(kind=kind, target=target, line=_line(starts, m.start()), detail=m.group(0)[:120]))
    if re.search(r"^\s*@Entity\b", text, M):
        for sym in pf.symbols:
            if sym.kind == "class" and any(a.startswith("@Entity") for a in sym.annotations):
                if not any(d.kind == "entity" and d.line >= sym.start_line - 5 and d.line <= sym.start_line for d in out):
                    out.append(DbAccess(kind="entity", target=sym.name, line=sym.start_line, detail="@Entity"))
    for pattern in SQL_PATTERNS:
        for m in pattern.finditer(text):
            table = m.group(1)
            if table.lower() not in ("dual", "select", "where", "set", "values"):
                out.append(DbAccess(kind="sql", target=table, line=_line(starts, m.start()), detail=m.group(0)[:120]))
    seen: set[tuple[str, str, int]] = set()
    pf.db_access = [d for d in out if not ((d.kind, d.target, d.line) in seen or seen.add((d.kind, d.target, d.line)))]


# ---------------------------------------------------------------- service calls & messaging
_URL_HOST_RE = re.compile(r"(?:https?|lb|grpc)://([A-Za-z][\w\-]*)(?::\d+)?(?:/|[\"'`])")
_FEIGN_RE = re.compile(r"@FeignClient\s*\(\s*(?:name\s*=\s*|value\s*=\s*)?\"([\w\-]+)\"")
_MSG_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"@KafkaListener\s*\([^)]*?topics\s*=\s*\{?\s*\"([^\"]+)\""), "consume", "kafka"),
    (re.compile(r"[kK]afkaTemplate\s*\.\s*send\s*\(\s*\"([^\"]+)\""), "publish", "kafka"),
    (re.compile(r"@RabbitListener\s*\([^)]*?queues\s*=\s*\{?\s*\"([^\"]+)\""), "consume", "rabbitmq"),
    (re.compile(r"[rR]abbitTemplate\s*\.\s*convertAndSend\s*\(\s*\"([^\"]+)\""), "publish", "rabbitmq"),
    (re.compile(r"subscribe\s*\(\s*\{\s*topics?\s*:\s*\[?\s*['\"]([\w.\-]+)['\"]"), "consume", "kafka"),
    (re.compile(r"send\s*\(\s*\{\s*topic\s*:\s*['\"]([\w.\-]+)['\"]"), "publish", "kafka"),
    (re.compile(r"KafkaConsumer\s*\(\s*['\"]([\w.\-]+)['\"]"), "consume", "kafka"),
    (re.compile(r"producer\s*\.\s*(?:send|produce)\s*\(\s*['\"]([\w.\-]+)['\"]"), "publish", "kafka"),
    (re.compile(r"@(?:Sqs|Jms)Listener\s*\(\s*(?:destination\s*=\s*|value\s*=\s*)?\"([^\"]+)\""), "consume", "queue"),
]


def extract_integrations(pf: ParsedFile, text: str) -> None:
    starts = _line_index(text)
    hosts = {h for h in _URL_HOST_RE.findall(text) if h.lower() not in ("localhost", "www", "schemas", "example")}
    hosts.update(_FEIGN_RE.findall(text))
    pf.http_targets = sorted(hosts)
    for pattern, direction, tech in _MSG_PATTERNS:
        for m in pattern.finditer(text):
            pf.messaging.append(Messaging(direction=direction, topic=m.group(1), line=_line(starts, m.start()), technology=tech))


# ---------------------------------------------------------------- entry points
ENTRY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"@SpringBootApplication"), "Spring Boot application"),
    (re.compile(r"public\s+static\s+void\s+main\s*\(\s*(?:final\s+)?String"), "Java main()"),
    (re.compile(r"static\s+(?:async\s+)?(?:Task|void|int|Task<int>)\s+Main\s*\("), ".NET Main()"),
    (re.compile(r"WebApplication\.CreateBuilder|Host\.CreateDefaultBuilder|CreateHostBuilder"), "ASP.NET Core host"),
    (re.compile(r"\bFastAPI\s*\("), "FastAPI application"),
    (re.compile(r"\bFlask\s*\(\s*__name__"), "Flask application"),
    (re.compile(r"get_wsgi_application|get_asgi_application|execute_from_command_line"), "Django application"),
    (re.compile(r"NestFactory\.create"), "NestJS bootstrap"),
    (re.compile(r"\b(?:app|server)\.listen\s*\("), "HTTP server"),
    (re.compile(r"(?:ReactDOM\.render|createRoot)\s*\("), "React root"),
    (re.compile(r"^func\s+main\s*\(\s*\)", M), "Go main()"),
    (re.compile(r"^fn\s+main\s*\(\s*\)", M), "Rust main()"),
]


def extract_entry_point(pf: ParsedFile, text: str) -> None:
    for pattern, label in ENTRY_PATTERNS:
        if pattern.search(text):
            pf.entry_point = label
            return
    name = PurePosixPath(pf.path).name
    if name in ("manage.py", "Program.cs", "main.go", "main.py", "app.py", "server.js", "index.js", "main.ts") and pf.entry_point is None:
        if any(s.name in ("main", "Main") for s in pf.symbols) or name in ("manage.py", "Program.cs"):
            pf.entry_point = f"{name} entry"


# ---------------------------------------------------------------- complexity / debt
_BRANCH_RE = re.compile(r"\b(if|elif|for|foreach|while|case|catch|except|when)\b|&&|\|\||\?\?|\band\b|\bor\b")
_TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")


def compute_complexity(pf: ParsedFile, lines: list[str]) -> None:
    for sym in pf.symbols:
        if sym.kind in ("function", "method", "constructor"):
            body = lines[sym.start_line - 1:sym.end_line]
            sym.complexity = 1 + sum(len(_BRANCH_RE.findall(line)) for line in body)
    pf.todo_count = len(_TODO_RE.findall("\n".join(lines)))
