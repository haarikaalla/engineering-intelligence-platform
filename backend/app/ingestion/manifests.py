"""Dependency manifest parsing and framework / infrastructure detection."""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from app.ingestion.scanner import SourceFile


@dataclass
class Dependency:
    name: str
    version: str | None
    ecosystem: str  # PyPI | npm | Maven | NuGet | Go | RubyGems | Packagist
    file: str
    scope: str = "runtime"


_REQ_RE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*(?:(==|>=|<=|~=|>|<|!=)\s*([A-Za-z0-9_.\-*+]+))?")
_POM_DEP_RE = re.compile(r"<dependency>(.*?)</dependency>", re.S)
_POM_PROP_RE = re.compile(r"<properties>(.*?)</properties>", re.S)
_POM_PARENT_RE = re.compile(r"<parent>(.*?)</parent>", re.S)
_GRADLE_RE = re.compile(r"""(implementation|api|compile|compileOnly|runtimeOnly|testImplementation|annotationProcessor)\s*\(?\s*['"]([\w.\-]+):([\w.\-]+)(?::([\w.\-]+))?['"]""")
_CSPROJ_RE = re.compile(r'<PackageReference\s+Include="([^"]+)"(?:\s+Version="([^"]+)")?', re.I)
_PKGCFG_RE = re.compile(r'<package\s+id="([^"]+)"\s+version="([^"]+)"', re.I)
_GOMOD_RE = re.compile(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+(v[\w.\-+]+)", re.M)
_GEM_RE = re.compile(r"""^\s*gem\s+['"]([\w\-]+)['"](?:\s*,\s*['"][~><=\s]*([\d.]+)['"])?""", re.M)


def _tag(block: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>\s*([^<]+?)\s*</{tag}>", block)
    return m.group(1) if m else None


def _clean_version(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip().lstrip("^~=v><! ")
    return v if re.match(r"^\d", v) else None


def parse_manifest(path: str, text: str) -> list[Dependency]:
    name = PurePosixPath(path).name
    deps: list[Dependency] = []
    try:
        if name.startswith("requirements") and name.endswith(".txt"):
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or line.startswith("-"):
                    continue
                m = _REQ_RE.match(line)
                if m:
                    pkg = re.sub(r"\[.*\]", "", m.group(1)).lower()
                    deps.append(Dependency(pkg, m.group(3) if m.group(2) == "==" else None, "PyPI", path))
        elif name == "pyproject.toml":
            data = tomllib.loads(text)
            for spec in data.get("project", {}).get("dependencies", []) or []:
                m = _REQ_RE.match(spec)
                if m:
                    deps.append(Dependency(re.sub(r"\[.*\]", "", m.group(1)).lower(), m.group(3) if m.group(2) == "==" else None, "PyPI", path))
            poetry = data.get("tool", {}).get("poetry", {})
            for section in ("dependencies", "dev-dependencies"):
                for pkg, ver in (poetry.get(section) or {}).items():
                    if pkg.lower() == "python":
                        continue
                    v = ver if isinstance(ver, str) else (ver or {}).get("version") if isinstance(ver, dict) else None
                    deps.append(Dependency(pkg.lower(), _clean_version(v), "PyPI", path, "dev" if "dev" in section else "runtime"))
        elif name == "package.json":
            data = json.loads(text)
            for section, scope in (("dependencies", "runtime"), ("devDependencies", "dev"), ("peerDependencies", "runtime")):
                for pkg, ver in (data.get(section) or {}).items():
                    deps.append(Dependency(pkg, _clean_version(str(ver)), "npm", path, scope))
        elif name == "pom.xml":
            props: dict[str, str] = {}
            pm = _POM_PROP_RE.search(text)
            if pm:
                props = dict(re.findall(r"<([\w.\-]+)>\s*([^<]+?)\s*</\1>", pm.group(1)))
            parent = _POM_PARENT_RE.search(text)
            if parent:
                g, a, v = _tag(parent.group(1), "groupId"), _tag(parent.group(1), "artifactId"), _tag(parent.group(1), "version")
                if g and a:
                    deps.append(Dependency(f"{g}:{a}", _clean_version(v), "Maven", path, "parent"))
            for block in _POM_DEP_RE.findall(text):
                g, a, v = _tag(block, "groupId"), _tag(block, "artifactId"), _tag(block, "version")
                if v and v.startswith("${"):
                    v = props.get(v[2:-1])
                if g and a:
                    scope = _tag(block, "scope") or "runtime"
                    deps.append(Dependency(f"{g}:{a}", _clean_version(v), "Maven", path, scope))
        elif name in ("build.gradle", "build.gradle.kts"):
            for conf, g, a, v in _GRADLE_RE.findall(text):
                deps.append(Dependency(f"{g}:{a}", _clean_version(v), "Maven", path, "test" if conf.startswith("test") else "runtime"))
        elif name.endswith(".csproj") or name == "Directory.Packages.props":
            for pkg, ver in _CSPROJ_RE.findall(text):
                deps.append(Dependency(pkg, _clean_version(ver), "NuGet", path))
        elif name == "packages.config":
            for pkg, ver in _PKGCFG_RE.findall(text):
                deps.append(Dependency(pkg, _clean_version(ver), "NuGet", path))
        elif name == "go.mod":
            for mod, ver in _GOMOD_RE.findall(text):
                deps.append(Dependency(mod, ver.lstrip("v"), "Go", path))
        elif name == "Gemfile":
            for gem, ver in _GEM_RE.findall(text):
                deps.append(Dependency(gem, _clean_version(ver), "RubyGems", path))
        elif name == "composer.json":
            data = json.loads(text)
            for pkg, ver in (data.get("require") or {}).items():
                if "/" in pkg:
                    deps.append(Dependency(pkg, _clean_version(str(ver)), "Packagist", path))
    except (ValueError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return deps


def is_manifest(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        (name.startswith("requirements") and name.endswith(".txt"))
        or name in {"pyproject.toml", "package.json", "pom.xml", "build.gradle", "build.gradle.kts", "packages.config",
                    "go.mod", "Gemfile", "composer.json", "Directory.Packages.props"}
        or name.endswith(".csproj")
    )


# (needle in dependency name, framework, category)
_DEP_FRAMEWORKS: list[tuple[str, str, str]] = [
    ("spring-boot", "Spring Boot", "backend"), ("spring-cloud", "Spring Cloud", "backend"),
    ("spring-webmvc", "Spring MVC", "backend"), ("spring-webflux", "Spring WebFlux", "backend"),
    ("spring-security", "Spring Security", "security"), ("spring-data-jpa", "Spring Data JPA", "data"),
    ("spring-boot-starter-data-jpa", "Spring Data JPA", "data"), ("hibernate", "Hibernate", "data"),
    ("mybatis", "MyBatis", "data"), ("quarkus", "Quarkus", "backend"), ("micronaut", "Micronaut", "backend"),
    ("jakarta.ws.rs", "JAX-RS", "backend"), ("jersey", "Jersey", "backend"), ("struts", "Apache Struts", "backend"),
    ("javax.servlet", "Java Servlet", "backend"), ("kafka", "Apache Kafka", "messaging"),
    ("amqp", "RabbitMQ", "messaging"), ("junit", "JUnit", "testing"), ("mockito", "Mockito", "testing"),
    ("testcontainers", "Testcontainers", "testing"), ("lombok", "Lombok", "tooling"),
    ("log4j-core", "Log4j", "tooling"), ("grpc", "gRPC", "backend"), ("graphql", "GraphQL", "backend"),
    # Python
    ("django", "Django", "backend"), ("flask", "Flask", "backend"), ("fastapi", "FastAPI", "backend"),
    ("sqlalchemy", "SQLAlchemy", "data"), ("alembic", "Alembic", "data"), ("celery", "Celery", "messaging"),
    ("pytest", "pytest", "testing"), ("pydantic", "Pydantic", "tooling"), ("tornado", "Tornado", "backend"),
    ("aiohttp", "aiohttp", "backend"), ("langchain", "LangChain", "ai"), ("torch", "PyTorch", "ai"),
    ("tensorflow", "TensorFlow", "ai"), ("pandas", "pandas", "data"), ("boto3", "AWS SDK", "cloud"),
    # Node
    ("next", "Next.js", "frontend"), ("react", "React", "frontend"), ("vue", "Vue", "frontend"),
    ("@angular/core", "Angular", "frontend"), ("svelte", "Svelte", "frontend"), ("express", "Express", "backend"),
    ("@nestjs/core", "NestJS", "backend"), ("fastify", "Fastify", "backend"), ("koa", "Koa", "backend"),
    ("mongoose", "Mongoose", "data"), ("sequelize", "Sequelize", "data"), ("typeorm", "TypeORM", "data"),
    ("prisma", "Prisma", "data"), ("jest", "Jest", "testing"), ("vitest", "Vitest", "testing"),
    ("mocha", "Mocha", "testing"), ("cypress", "Cypress", "testing"), ("playwright", "Playwright", "testing"),
    ("redux", "Redux", "frontend"), ("tailwindcss", "TailwindCSS", "frontend"), ("socket.io", "Socket.IO", "backend"),
    # .NET
    ("Microsoft.AspNetCore", "ASP.NET Core", "backend"), ("Microsoft.EntityFrameworkCore", "Entity Framework Core", "data"),
    ("EntityFramework", "Entity Framework", "data"), ("Dapper", "Dapper", "data"), ("xunit", "xUnit", "testing"),
    ("NUnit", "NUnit", "testing"), ("MSTest", "MSTest", "testing"), ("MediatR", "MediatR", "backend"),
    ("Serilog", "Serilog", "tooling"), ("AutoMapper", "AutoMapper", "tooling"), ("Moq", "Moq", "testing"),
    ("Microsoft.AspNet.Mvc", "ASP.NET MVC", "backend"),
    # Go / Ruby / PHP
    ("gin-gonic/gin", "Gin", "backend"), ("labstack/echo", "Echo", "backend"), ("gorilla/mux", "Gorilla Mux", "backend"),
    ("gofiber/fiber", "Fiber", "backend"), ("gorm.io", "GORM", "data"), ("google.golang.org/grpc", "gRPC", "backend"),
    ("rails", "Ruby on Rails", "backend"), ("sinatra", "Sinatra", "backend"),
    ("laravel/framework", "Laravel", "backend"), ("symfony/", "Symfony", "backend"),
]

_FILE_SIGNALS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"(^|/)Dockerfile[^/]*$", re.I), "Docker", "infra"),
    (re.compile(r"(^|/)docker-compose[^/]*\.ya?ml$", re.I), "Docker Compose", "infra"),
    (re.compile(r"\.tf$"), "Terraform", "infra"),
    (re.compile(r"(^|/)Chart\.yaml$"), "Helm", "infra"),
    (re.compile(r"^\.github/workflows/"), "GitHub Actions", "ci"),
    (re.compile(r"(^|/)Jenkinsfile$"), "Jenkins", "ci"),
    (re.compile(r"(^|/)\.gitlab-ci\.yml$"), "GitLab CI", "ci"),
    (re.compile(r"(^|/)azure-pipelines\.ya?ml$"), "Azure Pipelines", "ci"),
    (re.compile(r"(^|/)web\.config$", re.I), "ASP.NET (IIS)", "backend"),
    (re.compile(r"(^|/)manage\.py$"), "Django", "backend"),
    (re.compile(r"(^|/)serverless\.ya?ml$"), "Serverless Framework", "infra"),
    (re.compile(r"\.proto$"), "Protocol Buffers", "backend"),
]


def _dep_matches(dep_name: str, ecosystem: str, needle: str) -> bool:
    d, n = dep_name.lower(), needle.lower()
    if ecosystem == "Maven":
        group, _, artifact = d.partition(":")
        return n in artifact or n in group
    if ecosystem == "NuGet":
        return d == n or d.startswith(n + ".")
    if ecosystem == "Go":
        return n in d
    return d == n or d.startswith((n + "/", n + "-"))


def detect_frameworks(files: list[SourceFile], deps: list[dict], infra_kinds: set[str]) -> list[dict]:
    found: dict[str, dict] = {}
    for dep in deps:
        for needle, fw, cat in _DEP_FRAMEWORKS:
            if fw not in found and _dep_matches(dep["name"], dep["ecosystem"], needle):
                found[fw] = {"name": fw, "category": cat, "evidence": dep["file"], "version": dep["version"]}
    for f in files:
        for pattern, fw, cat in _FILE_SIGNALS:
            if fw not in found and pattern.search(f.path):
                found[fw] = {"name": fw, "category": cat, "evidence": f.path, "version": None}
    if "kubernetes" in infra_kinds and "Kubernetes" not in found:
        found["Kubernetes"] = {"name": "Kubernetes", "category": "infra", "evidence": "manifests", "version": None}
    return sorted(found.values(), key=lambda x: (x["category"], x["name"]))


def collect_dependencies(root: Path, files: list[SourceFile]) -> list[dict]:
    deps: list[Dependency] = []
    for f in files:
        if is_manifest(f.path):
            text = (root / f.path).read_text(encoding="utf-8", errors="ignore")
            deps.extend(parse_manifest(f.path, text))
    return [asdict(d) for d in deps]
