"""Purpose & adoption intelligence: what a repository is for, how to run / integrate it, enterprise readiness,
reuse and development suggestions, and an onboarding guide for large codebases."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from app.services.context import RepoContext

_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_FEATURE_HEAD = re.compile(r"feature|overview|about|what|introduction|highlight|capabilit|use case|why|purpose|functionalit|description|summary", re.I)
_RUN_HEAD = re.compile(r"run|start|install|getting started|usage|build|quick ?start|setup|develop|deploy|local", re.I)
_CMD_START = re.compile(
    r"^(\$\s*)?(\./mvnw|mvn|\./gradlew|gradle|npm|npx|yarn|pnpm|pip|pip3|python3?|poetry|uv|uvicorn|flask|django-admin|docker|docker-compose|"
    r"make|go|dotnet|java|cargo|kubectl|helm|terraform|bundle|rails|php|composer|ng|next|node|deno|bun|sbt)\b"
)
ENV_PATTERNS = [
    re.compile(r"os\.(?:getenv|environ\.get)\(\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']"),
    re.compile(r"os\.environ\[\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']\s*\]"),
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]{2,})"),
    re.compile(r"process\.env\[\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']\s*\]"),
    re.compile(r"System\.getenv\(\s*\"([A-Z][A-Z0-9_]{2,})\""),
    re.compile(r"Environment\.GetEnvironmentVariable\(\s*\"([A-Z][A-Z0-9_]{2,})\""),
    re.compile(r"os\.Getenv\(\s*\"([A-Z][A-Z0-9_]{2,})\""),
    re.compile(r"\$\{([A-Z][A-Z0-9_]{2,})(?::[^}]*)?\}"),
]
DOMAINS: dict[str, list[str]] = {
    "E-commerce / retail": ["order", "cart", "product", "checkout", "catalog", "catalogue", "sku", "basket", "shop", "store", "coupon", "shipping"],
    "Payments / fintech": ["payment", "invoice", "billing", "transaction", "ledger", "wallet", "card", "refund", "charge", "settlement"],
    "Banking / finance": ["account", "loan", "deposit", "interest", "portfolio", "trade", "balance", "credit", "debit", "bank"],
    "Insurance": ["policy", "claim", "premium", "underwriting", "insured", "coverage", "quote"],
    "Healthcare / clinic": ["patient", "doctor", "clinic", "appointment", "visit", "vet", "diagnosis", "prescription", "medical", "hospital", "pet", "owner"],
    "HR / workforce": ["employee", "payroll", "leave", "timesheet", "recruit", "candidate", "department", "salary"],
    "Logistics / supply chain": ["shipment", "warehouse", "inventory", "delivery", "tracking", "carrier", "route", "fleet", "stock"],
    "Education / LMS": ["course", "student", "lesson", "enrollment", "grade", "teacher", "quiz", "classroom"],
    "Content / CMS": ["post", "article", "comment", "blog", "page", "media", "tag", "author", "publish"],
    "Identity / access management": ["user", "role", "permission", "login", "token", "oauth", "session", "tenant", "group", "auth"],
    "Messaging / collaboration": ["message", "chat", "notification", "channel", "inbox", "email", "sms", "conversation"],
    "IoT / telemetry": ["device", "sensor", "telemetry", "gateway", "firmware", "reading", "metric"],
    "Analytics / data platform": ["report", "dashboard", "metric", "pipeline", "etl", "dataset", "aggregate", "warehouse", "query"],
    "AI / machine learning": ["model", "train", "inference", "embedding", "dataset", "predict", "feature", "llm", "prompt", "agent"],
    "DevOps / developer tooling": ["deploy", "build", "pipeline", "cluster", "container", "plugin", "cli", "runner", "workflow", "lint"],
    "Travel / booking": ["booking", "reservation", "flight", "hotel", "ticket", "seat", "itinerary"],
}
LICENSES = [
    ("AGPL-3.0", re.compile(r"GNU AFFERO GENERAL PUBLIC LICENSE", re.I), "Strong copyleft incl. network use — offering it as a service requires releasing your modifications."),
    ("LGPL", re.compile(r"GNU LESSER GENERAL PUBLIC LICENSE", re.I), "Weak copyleft — linking is fine; modifications to the library itself must be shared."),
    ("GPL", re.compile(r"GNU GENERAL PUBLIC LICENSE", re.I), "Copyleft — distributing derived software requires releasing source under GPL."),
    ("Apache-2.0", re.compile(r"Apache License,?\s+Version 2\.0", re.I), "Permissive with patent grant — safe for commercial/enterprise use; keep NOTICE file."),
    ("MIT", re.compile(r"Permission is hereby granted, free of charge", re.I), "Permissive — commercial use allowed; keep the copyright notice."),
    ("BSD", re.compile(r"Redistribution and use in source and binary forms", re.I), "Permissive — commercial use allowed; keep the copyright notice."),
    ("MPL-2.0", re.compile(r"Mozilla Public License", re.I), "File-level copyleft — modified MPL files must stay open."),
    ("Unlicense", re.compile(r"This is free and unencumbered software", re.I), "Public domain dedication — unrestricted use."),
]
OBSERVABILITY = re.compile(r"micrometer|prometheus|opentelemetry|actuator|datadog|newrelic|sentry|jaeger|zipkin|elastic-apm|applicationinsights|structlog|serilog|winston|pino|logback", re.I)
AUTH_FW = re.compile(r"spring-security|spring-boot-starter-security|keycloak|passport|next-auth|authlib|python-jose|pyjwt|jsonwebtoken|Microsoft\.AspNetCore\.Authentication|Microsoft\.Identity|oauth|okta|auth0|django\.contrib\.auth|flask-login", re.I)
API_DOCS = re.compile(r"springdoc|swagger|openapi|Swashbuckle|NSwag|fastapi|drf-spectacular|apispec", re.I)
MIGRATIONS = re.compile(r"(^|/)(migrations?|db/migrate|flyway|liquibase|alembic)(/|$)|flyway|liquibase|alembic", re.I)


# ---------------------------------------------------------------- helpers
def _readme(ctx: RepoContext) -> tuple[str, str]:
    candidates = sorted((f for f in ctx.files if PurePosixPath(f.path).name.lower().startswith("readme")), key=lambda f: f.path.count("/"))
    if not candidates:
        return "", ""
    return candidates[0].path, ctx.read(candidates[0].path, 60000)


def _sections(markdown: str) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = [("", [])]
    in_code = False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = _HEADING.match(line) if not in_code else None
        if m:
            out.append((m.group(2).strip(), []))
        else:
            out[-1][1].append(line)
    return out


def _clean(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text)
    text = re.sub(r"<[^>]+>|`|\*\*|__", "", text)
    return re.sub(r"\s+", " ", text).strip()


def readme_features(markdown: str) -> list[str]:
    feats: list[str] = []
    for title, lines in _sections(markdown):
        if not _FEATURE_HEAD.search(title):
            continue
        for line in lines:
            m = re.match(r"^\s*(?:[-*+]|\d+\.)\s+(.*)", line)
            if m:
                item = _clean(m.group(1))
                if 8 <= len(item) <= 220:
                    feats.append(item)
        if len(feats) >= 12:
            break
    return feats[:12]


def readme_purpose(markdown: str) -> str:
    sections = _sections(markdown)
    preferred = [s for i, s in enumerate(sections) if i <= 1 or _FEATURE_HEAD.search(s[0])]
    for _title, lines in preferred:
        raw = [p for p in re.split(r"\n\s*\n", "\n".join(lines)) if "```" not in p and not p.lstrip().startswith(("|", "<", "[!", "![", ">"))
               and not re.match(r"\s*(see|note|please|click|check out|read more)\b", p, re.I)]
        paras = [_clean(p) for p in raw]
        paras = [p for p in paras if len(p) > 40 and not p.startswith(("|", "-", "!", "$", "```")) and "badge" not in p.lower()]
        if paras:
            return " ".join(paras[:2])[:900]
    return ""


def readme_commands(markdown: str) -> list[dict]:
    cmds: list[dict] = []
    title = ""
    in_code = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            m = _HEADING.match(line)
            if m:
                title = m.group(2).strip()
            continue
        if _RUN_HEAD.search(title) and _CMD_START.match(stripped):
            cmd = stripped.lstrip("$ ").strip()
            if cmd not in {c["command"] for c in cmds}:
                cmds.append({"command": cmd, "section": _clean(title)})
    return cmds[:15]


def detect_license(ctx: RepoContext) -> dict:
    lic_file = next((f for f in ctx.files if re.match(r"^(LICEN[CS]E|COPYING)(\.\w+)?$", PurePosixPath(f.path).name, re.I) and "/" not in f.path), None)
    if lic_file is None:
        return {"name": "Not found", "file": None, "guidance": "No LICENSE file — all rights reserved by default. Obtain permission before reuse."}
    text = ctx.read(lic_file.path, 20000)
    for name, pattern, guidance in LICENSES:
        if pattern.search(text):
            return {"name": name, "file": lic_file.path, "guidance": guidance}
    return {"name": "Custom / unknown", "file": lic_file.path, "guidance": "Review the license terms with legal before reuse."}


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _tokens_from_identifiers(ctx: RepoContext, labels: tuple[str, ...] = ("Class", "Table", "API", "Module", "Service", "Topic")) -> Counter:
    words: Counter = Counter()
    g = ctx.graph
    for n in g.nodes.values():
        if n["label"] in labels:
            if n["label"] == "Class" and ctx.file_info.get(n.get("file", ""), None) and ctx.file_info[n["file"]].category == "test":
                continue
            for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", n["name"]):
                if len(w) > 2:
                    words[_singular(w.lower())] += 3 if n["label"] in ("Table", "API") else 1
    return words


def detect_domain(ctx: RepoContext, readme: str) -> list[dict]:
    words = _tokens_from_identifiers(ctx)
    readme_words = Counter(_singular(w) for w in re.findall(r"[a-z]{3,}", readme.lower()))
    results = []
    for domain, terms in DOMAINS.items():
        score, evidence = 0.0, []
        for t in terms:
            hit = words.get(t, 0) + 2 * readme_words.get(t, 0)
            if hit:
                score += min(hit, 15)
                evidence.append(t)
        if score:
            results.append({"domain": domain, "score": round(score, 1), "evidence": evidence[:6]})
    results.sort(key=lambda r: -r["score"])
    if results:
        top = results[0]["score"]
        results = [r for r in results if r["score"] >= top * 0.35][:4]
        for r in results:
            r["confidence"] = round(min(1.0, r["score"] / max(top, 20)), 2)
    return results


def env_vars(ctx: RepoContext, limit: int = 40) -> list[dict]:
    found: dict[str, dict] = {}
    for f in ctx.files:
        if f.category not in ("source", "config", "infra", "build"):
            continue
        text = ctx.read(f.path, 200000)
        for pattern in ENV_PATTERNS:
            for m in pattern.finditer(text):
                name = m.group(1)
                entry = found.setdefault(name, {"name": name, "files": [], "secret": bool(re.search(r"PASS|SECRET|TOKEN|KEY|CRED", name))})
                if f.path not in entry["files"] and len(entry["files"]) < 3:
                    entry["files"].append(f.path)
    return sorted(found.values(), key=lambda e: (not e["secret"], e["name"]))[:limit]


# ---------------------------------------------------------------- classification
def project_type(ctx: RepoContext, overview: dict) -> dict:
    g = ctx.graph
    fw = {f["name"] for f in ctx.frameworks}
    cats = Counter(f["category"] for f in ctx.frameworks)
    apis = len(g.by_label("API"))
    entries = [e["type"] for e in overview.get("entry_points", [])]
    langs = list(overview.get("languages", {}))
    loc_by_lang = {k: v["loc"] for k, v in overview.get("languages", {}).items()}
    total = sum(loc_by_lang.values()) or 1
    text = _readme(ctx)[1].lower()[:4000]
    signals: list[str] = []
    services = [m for m in ctx.modules if m.kind == "service"]

    if loc_by_lang.get("Terraform", 0) / total > 0.4 or loc_by_lang.get("HCL", 0) / total > 0.4:
        kind = "Infrastructure-as-Code"
    elif re.search(r"\b(sample|example|demo|tutorial|workshop|reference application|starter)\b", text) and apis:
        kind = "Reference / sample application"
        signals.append("README describes it as a sample/reference application")
    elif len(services) >= 3 and apis:
        kind = "Microservices platform"
    elif apis and any(m.kind == "frontend" for m in ctx.modules):
        kind = "Full-stack web application"
    elif apis:
        kind = "Backend service / REST API"
    elif cats.get("frontend") and not apis:
        kind = "Frontend web application"
    elif cats.get("ai") or {"PyTorch", "TensorFlow"} & fw:
        kind = "Data / ML project"
    elif any("main" in e.lower() or "entry" in e.lower() for e in entries) and not apis:
        kind = "CLI tool / batch application"
    elif not entries:
        kind = "Library / SDK"
        signals.append("no runtime entry point detected — consumed as a dependency")
    else:
        kind = "Application"
    if apis:
        signals.append(f"{apis} HTTP/RPC endpoints")
    if entries:
        signals.append(f"entry points: {', '.join(sorted(set(entries))[:3])}")
    if langs:
        signals.append(f"primary language {langs[0]}")
    return {"type": kind, "signals": signals}


def _class_fan_in(ctx: RepoContext) -> list[tuple[dict, int]]:
    g = ctx.graph
    rows = []
    for c in g.by_label("Class"):
        info = ctx.file_info.get(c["file"])
        if not info or info.category != "source":
            continue
        users = {g.file_of(p) for p in g.predecessors(c["id"], ("REFERENCES", "CALLS"))} - {c["file"], None}
        rows.append((c, len(users)))
    rows.sort(key=lambda r: -r[1])
    return rows


def module_summaries(ctx: RepoContext) -> list[dict]:
    g = ctx.graph
    out = []
    for m in sorted(ctx.modules, key=lambda m: -m.loc)[:40]:
        mid = f"module:{m.name}"
        files = [f for f in g.successors(mid, ("CONTAINS",)) if g.nodes[f]["label"] == "File" and not g.nodes[f].get("is_test")]
        classes = [g.nodes[c]["name"] for f in files for c in g.successors(f, ("DEFINES",)) if g.nodes[c]["label"] == "Class"]
        apis = [g.nodes[a]["name"] for a in g.successors(mid, ("EXPOSES",))]
        tables = sorted({g.nodes[t]["name"] for f in files for n in [f, *g.successors(f, ("DEFINES",))]
                         for t in g.successors(n, ("ACCESSES",)) if g.nodes[t]["label"] == "Table"})
        deps = sorted(g.nodes[d]["name"] for d in g.successors(mid, ("DEPENDS_ON",)))
        roles = Counter()
        for c in classes:
            for suffix, role in (("Controller", "HTTP controllers"), ("Resource", "HTTP resources"), ("Service", "business services"),
                                 ("Repository", "data repositories"), ("Dao", "data access objects"), ("Client", "outbound clients"),
                                 ("Config", "configuration"), ("Handler", "handlers"), ("Listener", "event listeners"),
                                 ("Consumer", "event consumers"), ("Producer", "event producers"), ("Mapper", "mappers"),
                                 ("Dto", "DTOs"), ("Exception", "error types"), ("Util", "utilities"), ("Validator", "validators")):
                if c.endswith(suffix):
                    roles[role] += 1
        parts = [f"{m.loc:,} LOC across {len(files)} files"]
        if roles:
            parts.append("contains " + ", ".join(f"{n} {r}" for r, n in roles.most_common(4)))
        if apis:
            routes = sorted({re.sub(r"\{[^}]+\}|:\w+", "*", a.split(" ", 1)[-1]) for a in apis})
            parts.append(f"exposes {len(apis)} endpoints ({', '.join(routes[:4])}{'…' if len(routes) > 4 else ''})")
        if tables:
            parts.append(f"persists {', '.join(tables[:5])}")
        if deps:
            parts.append(f"depends on {', '.join(deps[:4])}")
        out.append({"module": m.name, "kind": m.kind, "path": m.path, "summary": "; ".join(parts) + ".",
                    "key_classes": classes[:8], "apis": apis[:10], "tables": tables[:10]})
    return out


def run_instructions(ctx: RepoContext, readme_md: str) -> list[dict]:
    paths = {f.path for f in ctx.files}
    names = {PurePosixPath(p).name for p in paths}
    steps: list[dict] = []

    def add(cmd: str, why: str) -> None:
        if cmd not in {s["command"] for s in steps}:
            steps.append({"command": cmd, "purpose": why})

    if "docker-compose.yml" in names or "docker-compose.yaml" in names or "compose.yaml" in names:
        add("docker compose up -d", "start the full stack (services + dependencies) with Docker Compose")
    if "mvnw" in names:
        add("./mvnw spring-boot:run" if any(f["name"] == "Spring Boot" for f in ctx.frameworks) else "./mvnw package", "build/run with the Maven wrapper")
    elif "pom.xml" in names:
        add("mvn clean package", "build with Maven")
    if "gradlew" in names:
        add("./gradlew bootRun" if any(f["name"] == "Spring Boot" for f in ctx.frameworks) else "./gradlew build", "build/run with the Gradle wrapper")
    for f in ctx.files:
        if PurePosixPath(f.path).name == "package.json" and f.path.count("/") <= 1:
            try:
                scripts = json.loads(ctx.read(f.path)).get("scripts", {})
            except ValueError:
                scripts = {}
            prefix = f"cd {PurePosixPath(f.path).parent} && " if "/" in f.path else ""
            add(f"{prefix}npm install", "install Node.js dependencies")
            for s in ("dev", "start", "build", "test"):
                if s in scripts:
                    add(f"{prefix}npm run {s}", f"`{scripts[s]}`")
    reqs = sorted(p for p in paths if PurePosixPath(p).name == "requirements.txt")
    if reqs:
        add(f"pip install -r {reqs[0]}", "install Python dependencies")
    if "pyproject.toml" in names and not reqs:
        add("pip install -e .", "install the Python package in editable mode")
    if "manage.py" in names:
        add("python manage.py migrate && python manage.py runserver", "run the Django development server")
    for p, pf in ctx.parsed.items():
        if pf.entry_point == "FastAPI application":
            module = ".".join(PurePosixPath(p).with_suffix("").parts)
            add(f"uvicorn {module}:app --reload", "run the FastAPI application")
            break
    csprojs = [p for p in paths if p.endswith(".csproj") and not re.search(r"test", p, re.I)]
    if csprojs:
        add(f"dotnet run --project {sorted(csprojs, key=len)[0]}", "run the .NET application")
    if "go.mod" in names:
        mains = sorted(p for p, pf in ctx.parsed.items() if pf.entry_point == "Go main()")
        add(f"go run ./{PurePosixPath(mains[0]).parent}" if mains else "go build ./...", "build/run the Go program")
    if "Makefile" in names:
        add("make", "default Makefile target")
    tests = [f for f in ctx.frameworks if f["category"] == "testing"]
    if tests:
        add({"JUnit": "./mvnw test" if "mvnw" in names else "mvn test", "pytest": "pytest", "Jest": "npm test", "xUnit": "dotnet test",
             "NUnit": "dotnet test", "Vitest": "npx vitest"}.get(tests[0]["name"], "run the test suite"), f"run {tests[0]['name']} tests")
    for c in readme_commands(readme_md):
        add(c["command"], f"from README › {c['section']}")
    return steps[:20]


def integration_points(ctx: RepoContext) -> dict:
    g = ctx.graph
    apis = g.by_label("API")
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for a in apis:
        segs = [s for s in a.get("route", "").strip("/").split("/") if s and not s.startswith(("{", ":", "<"))]
        by_prefix["/" + "/".join(segs[:2]) if segs else "/"].append(a["name"])
    topics = [{"topic": t["name"], "publishers": sorted({g.nodes[p].get("module") or g.nodes[p]["name"] for p in g.predecessors(t["id"], ("PUBLISHES",))}),
               "subscribers": sorted({g.nodes[p].get("module") or g.nodes[p]["name"] for p in g.predecessors(t["id"], ("SUBSCRIBES",))})}
              for t in g.by_label("Topic")]
    core = [{"name": c["name"], "file": c["file"], "used_by_files": n, "kind": c.get("kind")} for c, n in _class_fan_in(ctx)[:12] if n > 0]
    extension = [x for x in core if x["kind"] in ("interface", "trait", "protocol")]
    has_openapi = any(re.search(r"openapi|swagger", f.path, re.I) for f in ctx.files) or any(API_DOCS.search(d["name"]) for d in ctx.dependencies)
    return {
        "api_groups": [{"prefix": k, "endpoints": v[:15], "count": len(v)} for k, v in sorted(by_prefix.items(), key=lambda kv: -len(kv[1]))][:15],
        "events": topics,
        "core_abstractions": core,
        "extension_points": extension,
        "openapi": has_openapi,
        "databases": [d["name"] for d in g.by_label("Database")],
    }


def readiness(ctx: RepoContext, security: dict, tests: dict, scores: dict, architecture: dict) -> dict:
    paths = [f.path for f in ctx.files]
    dep_names = " ".join(d["name"] for d in ctx.dependencies)
    fw = {f["name"] for f in ctx.frameworks}
    lic = detect_license(ctx)
    cov = tests.get("coverage_estimate", {}).get("files_percent", 0)
    vulns = security.get("dependencies", {}).get("vulnerabilities", [])
    crit = sum(1 for f in security.get("findings", []) if f["severity"] in ("critical", "high") and f.get("confidence") != "low")
    apis = ctx.graph.by_label("API")
    bus = [m for m in (architecture.get("ownership") or {}).get("modules", []) if m["bus_factor"] <= 1 and m["commits"] >= 10]

    has_api_docs = any(API_DOCS.search(d["name"]) for d in ctx.dependencies) or any(re.search(r"openapi|swagger", p, re.I) for p in paths)
    readme_text = _readme(ctx)[1]
    image_build = bool(re.search(r"build-image|jib|buildpack|docker build", readme_text + dep_names, re.I))
    containerized = "Docker" in fw or image_build

    def item(area: str, status: str, detail: str, action: str = "") -> dict:
        return {"area": area, "status": status, "detail": detail, "action": action}

    checks = [
        item("Automated tests", "pass" if cov >= 60 else "warn" if cov >= 25 else "fail", f"{cov}% of testable files have tests",
             "Add tests for high-risk files (see Test Intelligence)" if cov < 60 else ""),
        item("CI/CD pipeline", "pass" if {"GitHub Actions", "Jenkins", "GitLab CI", "Azure Pipelines"} & fw else "fail",
             ", ".join(sorted({"GitHub Actions", "Jenkins", "GitLab CI", "Azure Pipelines"} & fw)) or "no pipeline definition found",
             "Add a CI pipeline running build, tests and security scans"),
        item("Containerization", "pass" if containerized else "fail",
             "Dockerfile present" if "Docker" in fw else "image built via buildpacks/Jib" if image_build else "no Dockerfile",
             "Add a multi-stage Dockerfile running as non-root"),
        item("Orchestration", "pass" if {"Kubernetes", "Helm"} & fw else "warn", ", ".join(sorted({"Kubernetes", "Helm", "Docker Compose"} & fw)) or "none",
             "Provide Kubernetes/Helm manifests with probes and resource limits"),
        item("Observability", "pass" if OBSERVABILITY.search(dep_names) else "warn",
             ", ".join(sorted(set(m.group(0).lower() for m in OBSERVABILITY.finditer(dep_names)))) or "no metrics/tracing libraries",
             "Add structured logging, metrics (Prometheus/OpenTelemetry) and tracing"),
        item("Authentication / authorization", "pass" if AUTH_FW.search(dep_names) or any(re.search(r"auth|security", p, re.I) for p in paths if ctx.file_info[p].category == "source")
             else ("fail" if apis else "warn"), "auth framework or security module detected" if AUTH_FW.search(dep_names) else "no auth framework detected",
             "Protect APIs with OAuth2/OIDC (e.g. Keycloak, Entra ID) and role-based access"),
        item("Security posture", "pass" if crit == 0 and not vulns else "warn" if crit < 5 else "fail",
             f"{crit} high/critical code findings, {len(vulns)} vulnerable dependencies", "Fix findings in the Security tab and upgrade vulnerable packages"),
        item("API documentation", "pass" if not apis or has_api_docs else "warn",
             "no HTTP APIs" if not apis else "OpenAPI/Swagger available" if has_api_docs else "no OpenAPI/Swagger contract found",
             "Generate an OpenAPI spec (springdoc, Swashbuckle, FastAPI docs)"),
        item("Database migrations", "pass" if any(MIGRATIONS.search(p) for p in paths) or MIGRATIONS.search(dep_names) else ("warn" if ctx.graph.by_label("Table") else "pass"),
             "versioned schema migrations detected" if any(MIGRATIONS.search(p) for p in paths) or MIGRATIONS.search(dep_names) else "no migration tool found",
             "Adopt Flyway/Liquibase/Alembic/EF migrations"),
        item("Externalized configuration", "pass" if env_vars(ctx, 5) else "warn", "environment-driven settings found" if env_vars(ctx, 5) else "configuration appears hardcoded",
             "Move environment-specific settings to env vars / config server"),
        item("Documentation", "pass" if _readme(ctx)[0] and any(f.category == "doc" for f in ctx.files if f.path.count("/") >= 1) else "warn" if _readme(ctx)[0] else "fail",
             "README" + (" + docs" if any(f.category == "doc" and f.path.count("/") >= 1 for f in ctx.files) else ""), "Add architecture/runbook docs"),
        item("License clarity", "pass" if lic["name"] in ("MIT", "Apache-2.0", "BSD", "Unlicense") else "warn" if lic["name"] != "Not found" else "fail",
             lic["name"], lic["guidance"]),
        item("Knowledge concentration", "warn" if bus else "pass", f"{len(bus)} module(s) with bus factor 1" if bus else "ownership is distributed (or history unavailable)",
             "Pair/rotate ownership of single-owner modules"),
        item("Maintainability", "pass" if scores.get("maintainability", 0) >= 70 else "warn" if scores.get("maintainability", 0) >= 50 else "fail",
             f"maintainability score {scores.get('maintainability')}", "Refactor complex functions and god classes"),
    ]
    weight = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
    score = int(round(100 * sum(weight[c["status"]] for c in checks) / len(checks)))
    level = "Production-ready" if score >= 80 else "Needs hardening" if score >= 55 else "Prototype / significant gaps"
    return {"score": score, "level": level, "checks": checks, "license": lic}


def suggestions(ctx: RepoContext, readiness_report: dict, architecture: dict, tests: dict, integration: dict, ptype: str) -> dict:
    g = ctx.graph
    reuse: list[dict] = []
    extend: list[dict] = []
    improve: list[dict] = []

    # Reuse: loosely-coupled modules and API capabilities
    dep_map = architecture.get("dependency_map", [])
    fan_out = Counter(d["source"] for d in dep_map)
    fan_in = Counter(d["target"] for d in dep_map)
    for m in ctx.modules:
        if m.name == "root" or m.loc < 150:
            continue
        if fan_in[m.name] >= 2 and fan_out[m.name] <= 1:
            reuse.append({"title": f"Extract '{m.name}' as a shared library", "priority": "high",
                          "reason": f"used by {fan_in[m.name]} modules and has few outbound dependencies ({fan_out[m.name]}) — a stable, reusable core."})
    for cap in architecture.get("business_capabilities", [])[:5]:
        reuse.append({"title": f"Consume the '{cap['name']}' capability via its API", "priority": "medium",
                      "reason": f"{cap['endpoints']} endpoints ({', '.join(f'{k}×{v}' for k, v in cap['methods'].items())}) owned by {', '.join(cap['modules'])} — integrate instead of rebuilding."})
    for core in integration.get("core_abstractions", [])[:3]:
        reuse.append({"title": f"Build on core abstraction {core['name']}", "priority": "medium",
                      "reason": f"referenced from {core['used_by_files']} files ({core['file']}); new features should extend it rather than duplicate logic."})
    for t in integration.get("events", [])[:3]:
        reuse.append({"title": f"Subscribe to event '{t['topic']}'", "priority": "medium",
                      "reason": "react to business events asynchronously without coupling to the publisher (" + ", ".join(t["publishers"]) + ")."})
    if ptype == "Library / SDK":
        reuse.insert(0, {"title": "Add as a dependency", "priority": "high", "reason": "this repository is a library — depend on its published artifact and use its public classes."})

    # Extend: gaps worth developing
    checks = {c["area"]: c for c in readiness_report["checks"]}
    gap_map = [
        ("Authentication / authorization", "Add enterprise SSO (OIDC/SAML) and RBAC", "high"),
        ("Observability", "Add OpenTelemetry tracing, Prometheus metrics and structured logs", "high"),
        ("API documentation", "Publish an OpenAPI contract and API portal", "medium"),
        ("CI/CD pipeline", "Add CI with build, tests, SAST and dependency scanning", "high"),
        ("Containerization", "Containerize the application", "medium"),
        ("Orchestration", "Add Kubernetes/Helm deployment with probes, autoscaling and secrets", "medium"),
        ("Database migrations", "Introduce versioned DB migrations", "medium"),
    ]
    for area, title, prio in gap_map:
        if checks.get(area, {}).get("status") in ("warn", "fail"):
            extend.append({"title": title, "priority": prio, "reason": checks[area]["detail"]})
    sync_links = [d for d in dep_map if "http" in d.get("via", "")]
    if len(sync_links) >= 2:
        extend.append({"title": "Replace synchronous service chains with events", "priority": "medium",
                       "reason": f"{len(sync_links)} synchronous HTTP dependencies between services increase coupling and cascading failures."})
    db_heavy = len(g.by_label("Table")) >= 5 and not any(d["name"] == "redis" for d in g.nodes.values() if d["label"] == "Database")
    if db_heavy:
        extend.append({"title": "Add a caching layer (Redis) for read-heavy entities", "priority": "low",
                       "reason": f"{len(g.by_label('Table'))} persisted entities and no cache detected."})
    if g.by_label("API") and not any(re.search(r"/v\d+/", a.get("route", "")) for a in g.by_label("API")):
        extend.append({"title": "Version the public API (/v1)", "priority": "low", "reason": "no versioned routes found; versioning enables safe evolution for consumers."})
    extend.append({"title": "Add an AI assistant over this codebase", "priority": "low",
                   "reason": "the knowledge graph and RAG index built here can power developer onboarding, PR review bots and impact checks in CI."})

    # Improve: code health
    for debt in architecture.get("god_classes", [])[:3]:
        improve.append({"title": f"Split god class {debt['name']}", "priority": "high", "reason": f"{debt['lines']} lines / {debt['methods']} members in {debt['file']}."})
    for cyc in architecture.get("module_cycles", [])[:3]:
        improve.append({"title": f"Break dependency cycle {' ↔ '.join(cyc)}", "priority": "high", "reason": "cycles block independent deployment and testing."})
    for rec in tests.get("recommendations", [])[:3]:
        if rec["priority"] == "high":
            improve.append({"title": f"Add {rec['test_type']} tests for {PurePosixPath(rec['file']).name}", "priority": "high", "reason": rec["reason"]})
    if checks.get("Security posture", {}).get("status") != "pass":
        improve.append({"title": "Remediate security findings", "priority": "high", "reason": checks["Security posture"]["detail"]})
    return {"reuse": reuse[:10], "extend": extend[:10], "improve": improve[:10]}


def onboarding(ctx: RepoContext, readme_path: str, architecture: dict, overview: dict) -> dict:
    g = ctx.graph
    steps: list[dict] = []
    if readme_path:
        steps.append({"step": "Read the project README", "items": [readme_path]})
    docs = [f.path for f in ctx.files if f.category == "doc" and f.path != readme_path and re.search(r"architect|design|overview|adr|contributing|guide", f.path, re.I)]
    if docs:
        steps.append({"step": "Skim architecture / design docs", "items": docs[:6]})
    entries = [e["file"] for e in overview.get("entry_points", [])][:6]
    if entries:
        steps.append({"step": "Start at the entry points (bootstrapping & wiring)", "items": entries})
    core = [f"{c['file']} ({c['name']}, used by {n} files)" for c, n in _class_fan_in(ctx)[:6] if n > 0]
    if core:
        steps.append({"step": "Understand the core abstractions everything depends on", "items": core})
    flows = [f"{f['api']}: " + " → ".join(s["name"] for s in f["steps"]) for f in architecture.get("data_flows", [])[:5]]
    if flows:
        steps.append({"step": "Trace the main request flows end-to-end", "items": flows})
    entities = sorted({t["name"] for t in g.by_label("Table")})[:15]
    if entities:
        steps.append({"step": "Learn the data model (persisted entities)", "items": entities})
    configs = [f.path for f in ctx.files if f.category == "config" and re.search(r"application|appsettings|settings|config|\.env", f.path, re.I)][:6]
    if configs:
        steps.append({"step": "Review runtime configuration", "items": configs})

    importance = []
    churn = (ctx.history or {}).get("files", {})
    for path, pf in ctx.parsed.items():
        info = ctx.file_info[path]
        if info.category != "source":
            continue
        fid = f"file:{path}"
        fan = len({g.file_of(p) for s in [fid, *g.successors(fid, ("DEFINES",))] for p in g.predecessors(s, ("IMPORTS", "REFERENCES", "CALLS"))} - {path, None})
        score = fan * 3 + len(pf.endpoints) * 2 + (8 if pf.entry_point else 0) + min(10, churn.get(path, {}).get("commits", 0)) + min(5, pf.loc / 200)
        importance.append({"file": path, "score": round(score, 1), "fan_in": fan, "loc": pf.loc, "apis": len(pf.endpoints), "entry": pf.entry_point})
    importance.sort(key=lambda r: -r["score"])

    large = []
    for path, pf in sorted(ctx.parsed.items(), key=lambda kv: -kv[1].loc):
        if ctx.file_info[path].category != "source" or pf.loc < 300:
            continue
        large.append({"file": path, "loc": pf.loc, "symbols": len(pf.symbols),
                      "max_complexity": max((s.complexity for s in pf.symbols), default=0)})
        if len(large) >= 15:
            break

    glossary_counts = _tokens_from_identifiers(ctx, ("Class", "Table", "Topic"))
    stop = {"get", "set", "impl", "service", "controller", "repository", "test", "base", "abstract", "default", "config", "util", "utils",
            "helper", "manager", "handler", "factory", "exception", "application", "app", "api", "dto", "model", "entity", "request", "response",
            "module", "root", "src", "main", "java", "post", "put", "delete", "patch", "the", "for", "and", "data", "type", "list", "new",
            "configuration", "runtime", "hint", "named", "formatter", "validator", "html", "web", "client", "provider", "interface", "error",
            "edit", "find", "create", "update", "show", "process", "init", "init", "page", "view", "form", "result", "info", "system", "string"}
    glossary = []
    for term, n in glossary_counts.most_common(120):
        if term in stop or len(term) < 3:
            continue
        defining = next((c for c in g.by_label("Class") if _singular(c["name"].lower()) == term), None)
        glossary.append({"term": term, "mentions": n, "defined_in": defining["file"] if defining else None})
        if len(glossary) >= 20:
            break
    return {"reading_order": steps, "key_files": importance[:15], "large_files": large, "glossary": glossary}


# ---------------------------------------------------------------- entry point
def compute_purpose(ctx: RepoContext, overview: dict, architecture: dict, security: dict, tests: dict, scores: dict) -> dict:
    readme_path, readme_md = _readme(ctx)
    ptype = project_type(ctx, overview)
    domains = detect_domain(ctx, readme_md)
    features = readme_features(readme_md)
    purpose_text = readme_purpose(readme_md)
    caps = architecture.get("business_capabilities", [])
    if not features:
        features = [f"{c['name'].replace('-', ' ').title()} management ({c['endpoints']} endpoints)" for c in caps[:8]]
    integration = integration_points(ctx)
    ready = readiness(ctx, security, tests, scores, architecture)
    domain_txt = domains[0]["domain"] if domains else "general-purpose software"
    tables = sorted({t["name"] for t in ctx.graph.by_label("Table")})
    solves = (
        f"{ctx.name} is a {ptype['type'].lower()} in the {domain_txt} domain. "
        + (f"{purpose_text} " if purpose_text else "")
        + (f"It lets users/systems work with {', '.join(tables[:6])} through {len(ctx.graph.by_label('API'))} API endpoints. " if tables and ctx.graph.by_label("API") else "")
        + (f"Main capabilities: {', '.join(c['name'] for c in caps[:6])}." if caps else "")
    ).strip()
    return {
        "project_type": ptype,
        "domains": domains,
        "what_it_solves": solves,
        "readme_purpose": purpose_text,
        "key_features": features,
        "main_functionality": module_summaries(ctx),
        "how_to_run": run_instructions(ctx, readme_md),
        "configuration": env_vars(ctx),
        "integration": integration,
        "enterprise_readiness": ready,
        "suggestions": suggestions(ctx, ready, architecture, tests, integration, ptype["type"]),
        "onboarding": onboarding(ctx, readme_path, architecture, overview),
    }
