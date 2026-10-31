"""Pull request intelligence: diff parsing, risk scoring, impact, security & architecture review, missing tests."""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import httpx

from app.analysis.impact import analyze_impact
from app.analysis.security import SEVERITY_WEIGHT, scan_text
from app.graph.builder import ImportResolver, test_subject_stem
from app.ingestion.manifests import is_manifest
from app.ingestion.scanner import classify, detect_language, is_test_path
from app.parsing.extractors import ENDPOINT_PATTERNS
from app.parsing.models import ParsedFile
from app.parsing.regex_parser import SPECS as REGEX_SPECS
from app.services.context import RepoContext

log = logging.getLogger(__name__)
_PR_URL = re.compile(r"^https://(?P<host>[\w.\-]+)/(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+)/pull/(?P<num>\d+)/?$")
SENSITIVE = re.compile(r"auth|security|login|password|token|jwt|oauth|crypto|payment|billing|checkout|permission|role|acl|session", re.I)
SCHEMA = re.compile(r"(^|/)(migrations?|db/migrate|flyway|liquibase|alembic/versions)/|\.sql$|schema\.prisma$|changelog.*\.(xml|ya?ml)$", re.I)


@dataclass
class FileDiff:
    path: str
    status: str = "modified"
    old_path: str | None = None
    added: list[tuple[int, str]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def parse_unified_diff(diff: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    new_line = 0
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", raw)
            cur = FileDiff(path=m.group(2) if m else raw.split()[-1].removeprefix("b/"), old_path=m.group(1) if m else None)
            files.append(cur)
        elif raw.startswith("new file mode") and cur:
            cur.status = "added"
        elif raw.startswith("deleted file mode") and cur:
            cur.status = "deleted"
        elif raw.startswith("rename from") and cur:
            cur.status = "renamed"
        elif raw.startswith("+++ "):
            target = raw[4:].strip()
            if cur is None:
                cur = FileDiff(path=target.removeprefix("b/"))
                files.append(cur)
            elif target != "/dev/null":
                cur.path = target.removeprefix("b/")
        elif raw.startswith("--- "):
            if cur is None:
                cur = FileDiff(path=raw[4:].strip().removeprefix("a/"))
                files.append(cur)
            if raw[4:].strip() == "/dev/null" and cur:
                cur.status = "added"
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", raw)
            new_line = int(m.group(1)) if m else 0
        elif cur is not None and raw.startswith("+"):
            cur.added.append((new_line, raw[1:]))
            new_line += 1
        elif cur is not None and raw.startswith("-"):
            cur.removed.append(raw[1:])
        elif cur is not None and not raw.startswith("\\"):
            new_line += 1
    return files


def fetch_github_pr(pr_url: str, token: str | None, allowed_hosts: list[str]) -> tuple[dict, list[FileDiff]]:
    m = _PR_URL.match(pr_url.strip())
    if not m:
        raise ValueError("PR URL must look like https://github.com/{owner}/{repo}/pull/{number}")
    host = m["host"].lower()
    if host not in [h.lower() for h in allowed_hosts]:
        raise ValueError(f"Host '{host}' is not allowed")
    api = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base = f"{api}/repos/{m['owner']}/{m['repo']}/pulls/{m['num']}"
    with httpx.Client(timeout=30, headers=headers) as client:
        pr = client.get(base)
        if pr.status_code == 404:
            raise ValueError("Pull request not found (private repositories require a token)")
        pr.raise_for_status()
        files: list[FileDiff] = []
        for page in range(1, 31):
            resp = client.get(f"{base}/files", params={"per_page": 100, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            for f in batch:
                patch = f.get("patch") or ""
                fd = parse_unified_diff(f"diff --git a/{f.get('previous_filename') or f['filename']} b/{f['filename']}\n+++ b/{f['filename']}\n{patch}")
                entry = fd[0] if fd else FileDiff(path=f["filename"])
                entry.status = {"removed": "deleted"}.get(f["status"], f["status"])
                files.append(entry)
            if len(batch) < 100:
                break
    meta = pr.json()
    return {"title": meta.get("title"), "body": (meta.get("body") or "")[:2000], "author": (meta.get("user") or {}).get("login"),
            "base": (meta.get("base") or {}).get("ref"), "head": (meta.get("head") or {}).get("ref"), "url": pr_url}, files


def _endpoint_lines(language: str, lines: list[str]) -> list[str]:
    text = "\n".join(lines)
    hits = []
    for languages, pattern, *_ in ENDPOINT_PATTERNS:
        if language in languages:
            hits.extend(m.group(0).strip()[:120] for m in pattern.finditer(text))
    return hits


def analyze_pr(ctx: RepoContext, diffs: list[FileDiff], meta: dict | None = None) -> dict:
    meta = meta or {}
    g = ctx.graph
    changed_paths = [d.path for d in diffs]
    added_total = sum(len(d.added) for d in diffs)
    removed_total = sum(len(d.removed) for d in diffs)

    # ---------------------------------------------------------------- impact
    existing = [p for p in changed_paths if f"file:{p}" in g.nodes]
    impact = analyze_impact(ctx, existing, max_depth=3) if existing else None

    # ---------------------------------------------------------------- security on added lines
    sec_findings = []
    for d in diffs:
        lang = detect_language(d.path) or "Text"
        if not d.added:
            continue
        local = "\n".join(t for _, t in d.added)
        offsets = {i + 1: ln for i, (ln, _) in enumerate(d.added)}
        category = classify(d.path, lang)
        for f in scan_text(d.path, lang, local, category, offsets):
            if f.rule_id == "CFG012":
                continue
            sec_findings.append(f.__dict__)

    # ---------------------------------------------------------------- architecture impact
    arch_notes: list[dict] = []
    resolver = ImportResolver(list(ctx.parsed), ctx.parsed)
    for d in diffs:
        lang = detect_language(d.path) or ""
        own_module = ctx.module_of(d.path)
        added_text = "\n".join(t for _, t in d.added)
        probe = ParsedFile(path=d.path, language=lang)
        spec = REGEX_SPECS.get(lang)
        imports = []
        if spec:
            for pat in spec.imports:
                imports.extend(m.group(1) for m in pat.finditer(added_text))
        if lang == "Python":
            for a, b in re.findall(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", added_text, re.M):
                imports.append(a or b)
        for imp in imports:
            for target in resolver.resolve(probe, imp):
                tm = ctx.module_of(target)
                if own_module and tm and tm != own_module:
                    reverse = f"module:{own_module}" in g.successors(f"module:{tm}", ("DEPENDS_ON",))
                    arch_notes.append({"type": "new_cross_module_dependency", "severity": "high" if reverse else "medium",
                                       "file": d.path, "detail": f"{own_module} → {tm} via {imp}" + (" (creates a dependency cycle)" if reverse else "")})
        added_eps = _endpoint_lines(lang, [t for _, t in d.added])
        removed_eps = _endpoint_lines(lang, d.removed)
        if added_eps or removed_eps:
            arch_notes.append({"type": "api_contract_change", "severity": "high" if removed_eps else "medium", "file": d.path,
                               "detail": f"{len(added_eps)} endpoint mapping(s) added, {len(removed_eps)} removed/changed"})
        if SCHEMA.search(d.path) or re.search(r"@Entity|@Table|__tablename__|DbSet<|CREATE TABLE|ALTER TABLE|DROP TABLE", added_text + "\n".join(d.removed), re.I):
            arch_notes.append({"type": "schema_change", "severity": "high", "file": d.path, "detail": "Database schema / entity mapping changed"})
        cat = classify(d.path, lang)
        if cat == "infra":
            arch_notes.append({"type": "infrastructure_change", "severity": "medium", "file": d.path, "detail": "Deployment / infrastructure definition changed"})
        if is_manifest(d.path):
            arch_notes.append({"type": "dependency_change", "severity": "medium", "file": d.path, "detail": f"{len(d.added)} dependency manifest line(s) added"})
        if cat == "config" and re.search(r"application|appsettings|settings|config", d.path, re.I):
            arch_notes.append({"type": "configuration_change", "severity": "low", "file": d.path, "detail": "Runtime configuration changed"})
        if d.status == "deleted" and f"file:{d.path}" in g.nodes:
            dependents = len(g.predecessors(f"file:{d.path}", ("IMPORTS",)))
            if dependents:
                arch_notes.append({"type": "deleted_dependency", "severity": "high", "file": d.path, "detail": f"Deleted file is imported by {dependents} file(s)"})

    # ---------------------------------------------------------------- missing tests
    changed_tests = [p for p in changed_paths if is_test_path(p)]
    test_stems = {test_subject_stem(p) for p in changed_tests}
    missing_tests = []
    for d in diffs:
        lang = detect_language(d.path) or ""
        if d.status == "deleted" or is_test_path(d.path) or classify(d.path, lang) != "source" or len(d.added) < 3:
            continue
        stem = PurePosixPath(d.path).stem.lower()
        existing_tests = sorted(g.nodes[t]["path"] for t in g.predecessors(f"file:{d.path}", ("TESTS",)))
        touched = stem in test_stems or any(t in changed_tests for t in existing_tests)
        if not touched:
            missing_tests.append({"file": d.path, "existing_tests": existing_tests,
                                  "suggestion": "Update existing tests to cover the change" if existing_tests else "Add unit tests for the new/changed logic"})

    # ---------------------------------------------------------------- risk
    factors: list[dict] = []

    def add(points: int, reason: str) -> None:
        if points > 0:
            factors.append({"points": points, "reason": reason})

    churn = added_total + removed_total
    add(20 if churn > 800 else 12 if churn > 300 else 5 if churn > 80 else 0, f"{churn} lines changed")
    add(min(10, len(diffs) // 5 * 2), f"{len(diffs)} files changed")
    if impact:
        add(min(24, 8 * max(0, impact["summary"]["impacted_services"] - 1)), f"{impact['summary']['impacted_services']} services impacted")
        add(min(12, impact["summary"]["impacted_apis"] * 2), f"{impact['summary']['impacted_apis']} APIs impacted")
        add(min(10, impact["summary"]["impacted_files"] // 5), f"{impact['summary']['impacted_files']} downstream files")
    sev_points = {"critical": 25, "high": 15, "medium": 5, "low": 1}
    add(min(35, sum(sev_points.get(f["severity"], 0) for f in sec_findings)), f"{len(sec_findings)} security finding(s) in added code")
    for note_type, pts, label in (("schema_change", 15, "database schema change"), ("api_contract_change", 10, "API contract change"),
                                  ("infrastructure_change", 8, "infrastructure change"), ("new_cross_module_dependency", 6, "new cross-module dependency"),
                                  ("deleted_dependency", 10, "deleted file still referenced")):
        if any(n["type"] == note_type for n in arch_notes):
            add(pts, label)
    add(min(20, 7 * len(missing_tests)), f"{len(missing_tests)} changed source file(s) without test changes")
    sensitive = [p for p in changed_paths if SENSITIVE.search(p)]
    add(10 if sensitive else 0, f"touches sensitive areas: {', '.join(sensitive[:3])}")
    hot = [p for p in changed_paths if (ctx.history or {}).get("files", {}).get(p, {}).get("commits", 0) >= 10]
    add(5 if hot else 0, f"modifies hotspot files: {', '.join(hot[:3])}")
    score = min(100, sum(f["points"] for f in factors))
    level = "critical" if score >= 75 else "high" if score >= 50 else "medium" if score >= 25 else "low"

    # ---------------------------------------------------------------- suggestions
    suggestions: list[dict] = []
    for f in sorted(sec_findings, key=lambda x: -SEVERITY_WEIGHT.get(x["severity"], 0))[:15]:
        suggestions.append({"file": f["file"], "line": f["line"], "severity": f["severity"], "category": "security",
                            "comment": f"{f['title']} ({f['cwe']}): {f['recommendation']}"})
    for n in arch_notes:
        advice = {
            "new_cross_module_dependency": "Introduce an interface/port in the target module or route through an API to keep modules decoupled.",
            "api_contract_change": "Version the API or keep backward compatibility; update API docs and consumer contract tests.",
            "schema_change": "Ship a backward-compatible migration (expand/contract) and verify rollback.",
            "infrastructure_change": "Validate in a staging environment and review resource limits / security context.",
            "dependency_change": "Check licences and known vulnerabilities for added/updated packages.",
            "configuration_change": "Ensure all environments define the new/changed settings.",
            "deleted_dependency": "Remove or migrate remaining imports before deleting the file.",
        }.get(n["type"], "")
        suggestions.append({"file": n["file"], "line": None, "severity": n["severity"], "category": "architecture", "comment": f"{n['detail']}. {advice}"})
    for m in missing_tests:
        suggestions.append({"file": m["file"], "line": None, "severity": "medium", "category": "testing", "comment": m["suggestion"]})
    for d in diffs:
        long_lines = [ln for ln, t in d.added if len(t) > 160]
        if long_lines:
            suggestions.append({"file": d.path, "line": long_lines[0], "severity": "low", "category": "style",
                                "comment": f"{len(long_lines)} very long line(s); consider reformatting."})
        todos = [ln for ln, t in d.added if re.search(r"\b(TODO|FIXME|HACK)\b", t)]
        if todos:
            suggestions.append({"file": d.path, "line": todos[0], "severity": "low", "category": "maintainability",
                                "comment": "New TODO/FIXME added; track it in the issue tracker."})
        prints = [ln for ln, t in d.added if re.search(r"\b(console\.log|System\.out\.print|printStackTrace|print\()", t)]
        if prints and not is_test_path(d.path):
            suggestions.append({"file": d.path, "line": prints[0], "severity": "low", "category": "maintainability",
                                "comment": "Debug output added; use the project logger instead."})

    return {
        "meta": meta,
        "stats": {"files": len(diffs), "additions": added_total, "deletions": removed_total,
                  "by_status": dict(_count(d.status for d in diffs))},
        "files": [{"path": d.path, "status": d.status, "additions": len(d.added), "deletions": len(d.removed),
                   "module": ctx.module_of(d.path)} for d in diffs],
        "risk": {"score": score, "level": level, "factors": sorted(factors, key=lambda f: -f["points"])},
        "impact": impact,
        "security": {"findings": sec_findings, "count": len(sec_findings)},
        "architecture": arch_notes,
        "missing_tests": missing_tests,
        "suggestions": suggestions,
    }


def _count(items) -> dict:
    out: dict = defaultdict(int)
    for i in items:
        out[i] += 1
    return out
