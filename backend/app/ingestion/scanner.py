"""Walks a repository, classifies files and detects languages and modules."""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

LANGUAGE_BY_EXT = {
    ".py": "Python", ".pyi": "Python",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".cs": "C#", ".vb": "VB.NET", ".fs": "F#",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".cxx": "C++",
    ".m": "Objective-C", ".dart": "Dart", ".lua": "Lua", ".r": "R", ".pl": "Perl",
    ".sql": "SQL", ".proto": "Protocol Buffers", ".graphql": "GraphQL", ".gql": "GraphQL",
    ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell",
    ".html": "HTML", ".htm": "HTML", ".jsp": "JSP", ".cshtml": "Razor", ".vue": "Vue", ".svelte": "Svelte",
    ".css": "CSS", ".scss": "SCSS", ".less": "LESS",
    ".tf": "Terraform", ".hcl": "HCL",
    ".yml": "YAML", ".yaml": "YAML", ".json": "JSON", ".xml": "XML", ".toml": "TOML",
    ".properties": "Properties", ".ini": "INI", ".cfg": "INI", ".conf": "Config", ".gradle": "Gradle",
    ".md": "Markdown", ".rst": "reStructuredText", ".adoc": "AsciiDoc", ".txt": "Text",
}
SPECIAL_FILES = {
    "dockerfile": "Dockerfile", "makefile": "Makefile", "jenkinsfile": "Groovy",
    "gemfile": "Ruby", "rakefile": "Ruby", "procfile": "Config",
}
PROGRAMMING_LANGUAGES = {
    "Python", "Java", "Kotlin", "Scala", "Groovy", "C#", "VB.NET", "F#", "JavaScript", "TypeScript", "Go", "Rust",
    "Ruby", "PHP", "Swift", "C", "C++", "Objective-C", "Dart", "Lua", "R", "Perl", "Vue", "Svelte", "JSP", "Razor",
    "SQL", "Shell", "PowerShell", "Protocol Buffers", "GraphQL", "Terraform",
}
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor", "third_party", "target", "build", "dist",
    "out", "bin", "obj", ".gradle", ".mvn", ".idea", ".vscode", ".vs", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".tox", ".venv", "venv", ".next", ".nuxt", "coverage", ".terraform", "Pods", ".dart_tool",
    "site-packages", ".cache",
}
SKIP_FILE_RE = re.compile(r"(\.min\.(js|css)$|\.map$|\.lock$|package-lock\.json$|yarn\.lock$|pnpm-lock\.yaml$|\.designer\.cs$|\.g\.cs$|_pb2\.py$|\.pb\.go$)", re.I)
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|testing|it|integration-tests?|e2e)(/|$)|"
    r"(^|/)test_[^/]+\.py$|_test\.(py|go)$|(Test|Tests|IT|Spec)\.(java|kt|cs|scala|groovy)$|"
    r"\.(test|spec)\.(js|jsx|ts|tsx|mjs)$|(^|/)src/test/",
    re.I,
)
MANIFESTS = {
    "pom.xml", "build.gradle", "build.gradle.kts", "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "go.mod", "Cargo.toml", "composer.json", "Gemfile", "requirements.txt",
}
DOC_EXT = {".md", ".rst", ".adoc", ".txt"}
CONFIG_EXT = {".yml", ".yaml", ".json", ".xml", ".toml", ".properties", ".ini", ".cfg", ".conf", ".env", ".gradle"}


@dataclass
class SourceFile:
    path: str
    language: str
    category: str  # source | test | config | doc | build | infra | other
    size: int
    loc: int
    module: str = "."


@dataclass
class ModuleInfo:
    name: str
    path: str
    kind: str = "component"  # service | library | component | frontend
    manifest: str | None = None
    languages: dict = field(default_factory=dict)
    files: int = 0
    loc: int = 0
    entry_points: list = field(default_factory=list)


@dataclass
class ScanResult:
    files: list[SourceFile]
    languages: dict
    total_loc: int
    total_files: int
    skipped: int
    modules: list[ModuleInfo]

    def to_dict(self) -> dict:
        return {
            "files": [asdict(f) for f in self.files],
            "languages": self.languages,
            "total_loc": self.total_loc,
            "total_files": self.total_files,
            "skipped": self.skipped,
            "modules": [asdict(m) for m in self.modules],
        }


def detect_language(path: str) -> str | None:
    p = PurePosixPath(path)
    name = p.name.lower()
    if name in SPECIAL_FILES or name.startswith("dockerfile"):
        return SPECIAL_FILES.get(name, "Dockerfile")
    if name.endswith(".csproj") or name.endswith(".sln"):
        return "MSBuild"
    return LANGUAGE_BY_EXT.get(p.suffix.lower())


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH_RE.search(path))


def classify(path: str, language: str) -> str:
    p = PurePosixPath(path)
    name = p.name
    lower = name.lower()
    if language == "Dockerfile" or lower.startswith("docker-compose") or lower == "chart.yaml" or p.suffix in (".tf", ".hcl"):
        return "infra"
    if "/k8s/" in f"/{path}" or "/helm/" in f"/{path}" or "/kubernetes/" in f"/{path}" or path.startswith(".github/workflows"):
        return "infra"
    if name in MANIFESTS or lower.endswith((".csproj", ".sln")) or lower.startswith("requirements") and lower.endswith(".txt"):
        return "build"
    if p.suffix.lower() in DOC_EXT:
        return "doc"
    if p.suffix.lower() in CONFIG_EXT or lower.startswith(".env") or lower.startswith("application"):
        return "config"
    if language in PROGRAMMING_LANGUAGES:
        return "test" if is_test_path(path) else "source"
    return "other"


def _is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\0" in fh.read(4096)
    except OSError:
        return True


def scan_repository(root: Path, max_file_size_kb: int, max_files: int) -> ScanResult:
    files: list[SourceFile] = []
    skipped = 0
    max_bytes = max_file_size_kb * 1024
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") or d == ".github")
        for fname in sorted(filenames):
            abs_path = Path(dirpath) / fname
            rel = abs_path.relative_to(root).as_posix()
            language = detect_language(rel)
            if not language or SKIP_FILE_RE.search(fname):
                skipped += 1
                continue
            try:
                size = abs_path.stat().st_size
            except OSError:
                continue
            if size > max_bytes or abs_path.is_symlink() or _is_binary(abs_path):
                skipped += 1
                continue
            try:
                text = abs_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            loc = sum(1 for line in text.splitlines() if line.strip())
            category = classify(rel, language)
            if category == "config" and language == "YAML" and "apiVersion:" in text and "kind:" in text:
                category = "infra"
            files.append(SourceFile(path=rel, language=language, category=category, size=size, loc=loc))
            if len(files) >= max_files:
                break
        if len(files) >= max_files:
            break

    modules = detect_modules(files)
    languages: dict[str, dict] = defaultdict(lambda: {"files": 0, "loc": 0})
    for f in files:
        if f.language in PROGRAMMING_LANGUAGES:
            languages[f.language]["files"] += 1
            languages[f.language]["loc"] += f.loc
    total_code = sum(v["loc"] for v in languages.values()) or 1
    lang_out = {
        k: {**v, "percent": round(100 * v["loc"] / total_code, 1)}
        for k, v in sorted(languages.items(), key=lambda kv: -kv[1]["loc"])
    }
    return ScanResult(
        files=files,
        languages=lang_out,
        total_loc=sum(f.loc for f in files if f.category in ("source", "test")),
        total_files=len(files),
        skipped=skipped,
        modules=modules,
    )


def _module_roots_from_manifests(files: list[SourceFile]) -> dict[str, str]:
    roots: dict[str, str] = {}
    source_paths = [f.path for f in files if f.category == "source"]
    for f in files:
        p = PurePosixPath(f.path)
        is_root_marker = p.name in MANIFESTS or p.name.endswith(".csproj") or p.name.lower() == "dockerfile"
        if is_root_marker and not is_test_path(f.path):
            parent = str(p.parent)
            if p.name.lower() == "dockerfile" and parent != "." and not any(s.startswith(parent + "/") for s in source_paths):
                continue
            roots.setdefault(parent, p.name)
    return roots


def _logical_roots(files: list[SourceFile]) -> list[str]:
    """For single-module repos derive components from the first directory level where code fans out.

    Descends through wrapper directories (src/main/java/com/acme/...) as long as one child holds the vast majority of code.
    """
    aux = {"SQL", "Shell", "PowerShell", "GraphQL", "Protocol Buffers", "Terraform"}
    weighted = [(PurePosixPath(f.path).parent.parts, max(1, f.loc)) for f in files if f.category == "source" and f.language not in aux]
    if not weighted:
        return []
    prefix: tuple[str, ...] = ()
    for _ in range(14):
        under = [(parts, w) for parts, w in weighted if parts[: len(prefix)] == prefix]
        total = sum(w for _, w in under)
        counts: dict[str, int] = defaultdict(int)
        for parts, w in under:
            if len(parts) > len(prefix):
                counts[parts[len(prefix)]] += w
        direct = total - sum(counts.values())
        if not counts:
            break
        top, top_count = max(counts.items(), key=lambda kv: kv[1])
        if top_count >= 0.85 * total and direct <= 0.15 * total:
            prefix = (*prefix, top)
            continue
        significant = [c for c, n in counts.items() if n >= 0.02 * total]
        if len(significant) >= 2:
            return ["/".join((*prefix, c)) for c in sorted(significant)]
        break
    return []


def detect_modules(files: list[SourceFile]) -> list[ModuleInfo]:
    roots = _module_roots_from_manifests(files)
    code_roots = {r for r in roots if r != "."}
    if not code_roots:
        for logical in _logical_roots(files):
            roots.setdefault(logical, None)  # type: ignore[arg-type]
    roots.setdefault(".", None)  # type: ignore[arg-type]

    ordered = sorted(roots, key=lambda r: -len(r.split("/")) if r != "." else 0)
    modules: dict[str, ModuleInfo] = {}
    for root in roots:
        name = "root" if root == "." else root.split("/")[-1]
        if root != "." and sum(1 for r in roots if r != "." and r.split("/")[-1] == name) > 1:
            name = root.replace("/", "-")
        modules[root] = ModuleInfo(name=name, path=root, manifest=roots[root])

    for f in files:
        owner = "."
        for root in ordered:
            if root == "." or f.path.startswith(root + "/"):
                owner = root
                break
        f.module = modules[owner].name
        mod = modules[owner]
        mod.files += 1
        if f.category in ("source", "test"):
            mod.loc += f.loc
            mod.languages[f.language] = mod.languages.get(f.language, 0) + f.loc

    result = [m for m in modules.values() if m.files > 0]
    for m in result:
        langs = set(m.languages)
        if langs & {"TypeScript", "JavaScript", "Vue", "Svelte"} and not langs & {"Java", "C#", "Python", "Go"}:
            m.kind = "frontend" if any(
                f.module == m.name and f.path.endswith((".tsx", ".jsx", ".vue", ".svelte")) for f in files
            ) else "component"
    return sorted(result, key=lambda m: -m.loc)
