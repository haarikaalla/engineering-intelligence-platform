"""Test intelligence: coverage estimation, regression risk and unit / integration test generation."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from app.services.context import RepoContext

TEST_FRAMEWORKS = {
    "Python": ("pytest", "pytest"),
    "Java": ("JUnit 5 + Mockito", "junit"),
    "Kotlin": ("JUnit 5 + MockK", "junit"),
    "C#": ("xUnit + Moq", "xunit"),
    "JavaScript": ("Jest", "jest"),
    "TypeScript": ("Jest", "jest"),
    "Go": ("testing", "gotest"),
}


def _fan_in(ctx: RepoContext, path: str) -> int:
    g = ctx.graph
    fid = f"file:{path}"
    sources = set(g.predecessors(fid, ("IMPORTS",)))
    for sym in g.successors(fid, ("DEFINES",)):
        sources |= {s for s in g.predecessors(sym, ("REFERENCES", "CALLS"))}
    return len({g.file_of(s) for s in sources if g.file_of(s) and g.file_of(s) != path})


def test_insights(ctx: RepoContext) -> dict:
    g = ctx.graph
    churn = (ctx.history or {}).get("files", {})
    rows = []
    for path, pf in ctx.parsed.items():
        info = ctx.file_info[path]
        if info.category != "source" or pf.language not in TEST_FRAMEWORKS and pf.language not in ("Scala", "Ruby", "PHP"):
            continue
        funcs = [s for s in pf.symbols if s.kind in ("function", "method", "constructor")]
        if not funcs:
            continue
        tests = sorted(g.nodes[t]["path"] for t in g.predecessors(f"file:{path}", ("TESTS",)))
        complexity = sum(s.complexity for s in funcs)
        fan_in = _fan_in(ctx, path)
        commits = churn.get(path, {}).get("commits", 0)
        has_api = any(True for _ in pf.endpoints)
        has_db = bool(pf.db_access)
        risk = (min(40, complexity / 2) + min(30, fan_in * 3) + min(15, commits * 1.5) + (10 if has_api else 0) + (5 if has_db else 0))
        if tests:
            risk *= 0.35
        rows.append({
            "file": path, "module": info.module, "language": pf.language, "functions": len(funcs), "complexity": complexity,
            "max_complexity": max(s.complexity for s in funcs), "fan_in": fan_in, "commits": commits, "tests": tests,
            "has_api": has_api, "has_db": has_db, "risk": round(min(100, risk), 1),
        })
    tested = [r for r in rows if r["tests"]]
    loc_total = sum(ctx.parsed[r["file"]].loc for r in rows) or 1
    loc_tested = sum(ctx.parsed[r["file"]].loc for r in tested)
    untested = sorted((r for r in rows if not r["tests"]), key=lambda r: -r["risk"])
    recommendations = []
    for r in untested[:25]:
        kind = "integration" if r["has_api"] or r["has_db"] else "unit"
        reason = []
        if r["fan_in"] >= 3:
            reason.append(f"used by {r['fan_in']} files")
        if r["max_complexity"] >= 10:
            reason.append(f"max cyclomatic complexity {r['max_complexity']}")
        if r["commits"] >= 5:
            reason.append(f"changed {r['commits']} times recently")
        if r["has_api"]:
            reason.append("exposes API endpoints")
        if r["has_db"]:
            reason.append("touches the database")
        recommendations.append({"file": r["file"], "test_type": kind, "priority": "high" if r["risk"] >= 50 else "medium" if r["risk"] >= 25 else "low",
                                "reason": ", ".join(reason) or "no tests found", "risk": r["risk"]})
    frameworks = sorted({f["name"] for f in ctx.frameworks if f["category"] == "testing"})
    return {
        "coverage_estimate": {
            "files_percent": round(100 * len(tested) / max(1, len(rows)), 1),
            "loc_percent": round(100 * loc_tested / loc_total, 1),
            "tested_files": len(tested),
            "testable_files": len(rows),
            "test_files": sum(1 for f in ctx.files if f.category == "test"),
        },
        "frameworks": frameworks,
        "recommendations": recommendations,
        "regression_risk": sorted(rows, key=lambda r: -r["risk"])[:30],
    }


# ---------------------------------------------------------------- generation
def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _java_deps(text: str, class_name: str) -> list[tuple[str, str]]:
    deps = re.findall(r"^\s*(?:@Autowired\s+)?private\s+(?:final\s+)?([A-Z]\w*(?:<[^>]+>)?)\s+(\w+)\s*;", text, re.M)
    if not deps:
        m = re.search(rf"\b{class_name}\s*\(([^)]*)\)", text)
        if m:
            deps = re.findall(r"([A-Z]\w*(?:<[^>]+>)?)\s+(\w+)", m.group(1))
    return [(t, n) for t, n in deps if t not in ("String", "Integer", "Long", "Boolean", "Logger")]


def generate_template(ctx: RepoContext, path: str, symbol: str | None, kind: str) -> dict:
    pf = ctx.parsed.get(path)
    if pf is None:
        raise ValueError("File not found in analysis")
    text = ctx.read(path)
    lang = pf.language
    funcs = [s for s in pf.symbols if s.kind in ("function", "method") and not s.name.startswith("_")]
    if symbol:
        funcs = [s for s in funcs if s.name == symbol or s.qualified == symbol] or funcs
    classes = [s for s in pf.symbols if s.kind == "class"]
    cls = next((c for c in classes if any(f.parent == c.name for f in funcs)), classes[0] if classes else None)
    stem = PurePosixPath(path).stem
    framework = TEST_FRAMEWORKS.get(lang, ("generic", "generic"))[0]

    if lang == "Python":
        module = ".".join(PurePosixPath(path).with_suffix("").parts).removeprefix("src.")
        if kind == "integration" and pf.endpoints:
            body = ["import pytest", "from fastapi.testclient import TestClient  # or app.test_client() for Flask", "",
                    f"from {module} import app  # adjust to your application factory", "", "",
                    "@pytest.fixture", "def client():", "    return TestClient(app)", ""]
            for ep in pf.endpoints:
                route = re.sub(r"\{[^}]+\}|<[^>]+>", "1", ep.path)
                body += ["", f"def test_{_snake(ep.handler or 'endpoint').replace('.', '_')}_{ep.method.lower()}(client):",
                         f"    response = client.{ep.method.lower() if ep.method != 'ANY' else 'get'}(\"{route}\")",
                         "    assert response.status_code < 500", "    # TODO: assert response payload and side effects"]
        else:
            targets = [f.parent or f.name for f in funcs]
            imports = sorted(set(t for t in targets if t))
            body = ["from unittest.mock import MagicMock, patch", "", "import pytest", "",
                    f"from {module} import {', '.join(imports[:10]) or '*'}", ""]
            for f in funcs[:25]:
                params = [p.strip() for p in re.sub(r".*\(|\).*", "", f.signature).split(",") if p.strip() and p.strip() not in ("self", "cls")]
                args = ", ".join("MagicMock()" for _ in params)
                body += ["", f"def test_{_snake(f.name)}_returns_expected_result():"]
                if f.parent:
                    body += [f"    instance = {f.parent}.__new__({f.parent})", f"    result = instance.{f.name}({args})"]
                else:
                    body += [f"    result = {f.name}({args})"]
                body += ["    # Arrange inputs above and assert the expected behaviour", "    assert result is not None or result is None", "",
                         f"def test_{_snake(f.name)}_handles_invalid_input():",
                         f"    with pytest.raises(Exception):", f"        {(f.parent + '.__new__(' + f.parent + ').') if f.parent else ''}{f.name}({', '.join('None' for _ in params)})" if params else "        raise Exception('replace with edge-case scenario')"]
        file_name = f"tests/test_{stem}.py"
    elif lang in ("Java", "Kotlin"):
        name = cls.name if cls else stem
        pkg = pf.package or ""
        if kind == "integration" and pf.endpoints:
            body = [f"package {pkg};" if pkg else "", "", "import org.junit.jupiter.api.Test;",
                    "import org.springframework.beans.factory.annotation.Autowired;",
                    "import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;",
                    "import org.springframework.boot.test.context.SpringBootTest;", "import org.springframework.test.web.servlet.MockMvc;", "",
                    "import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;",
                    "import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;", "",
                    "@SpringBootTest", "@AutoConfigureMockMvc", f"class {name}IT {{", "", "    @Autowired", "    private MockMvc mockMvc;"]
            for ep in pf.endpoints:
                route = re.sub(r"\{[^}]+\}", "1", ep.path)
                verb = ep.method.lower() if ep.method in ("GET", "POST", "PUT", "DELETE", "PATCH") else "get"
                handler = (ep.handler or "endpoint").split(".")[-1]
                body += ["", "    @Test", f"    void {handler}_{verb}_returnsSuccess() throws Exception {{",
                         f"        mockMvc.perform({verb}(\"{route}\"))", "               .andExpect(status().is2xxSuccessful());", "    }"]
            body.append("}")
            file_name = f"src/test/java/{pkg.replace('.', '/')}/{name}IT.java"
        else:
            deps = _java_deps(text, name)
            body = [f"package {pkg};" if pkg else "", "", "import org.junit.jupiter.api.Test;", "import org.junit.jupiter.api.extension.ExtendWith;",
                    "import org.mockito.InjectMocks;", "import org.mockito.Mock;", "import org.mockito.junit.jupiter.MockitoExtension;", "",
                    "import static org.assertj.core.api.Assertions.*;", "import static org.mockito.Mockito.*;", "",
                    "@ExtendWith(MockitoExtension.class)", f"class {name}Test {{", ""]
            for t, n in deps:
                body += ["    @Mock", f"    private {t} {n};", ""]
            body += ["    @InjectMocks", f"    private {name} underTest;"]
            for f in funcs[:25]:
                body += ["", "    @Test", f"    void {f.name}_shouldBehaveAsExpected() {{", "        // given",
                         *(f"        // when({n}.someCall()).thenReturn(...);" for _, n in deps[:2]),
                         "        // when", f"        // var result = underTest.{f.name}(...);", "        // then",
                         "        // assertThat(result).isEqualTo(expected);", "    }"]
            body.append("}")
            file_name = f"src/test/java/{pkg.replace('.', '/')}/{name}Test.java"
    elif lang == "C#":
        name = cls.name if cls else stem
        if kind == "integration" and pf.endpoints:
            body = ["using System.Net;", "using Microsoft.AspNetCore.Mvc.Testing;", "using Xunit;", "",
                    f"public class {name}IntegrationTests : IClassFixture<WebApplicationFactory<Program>>", "{",
                    "    private readonly HttpClient _client;", "",
                    f"    public {name}IntegrationTests(WebApplicationFactory<Program> factory) => _client = factory.CreateClient();"]
            for ep in pf.endpoints:
                route = re.sub(r"\{[^}]+\}", "1", ep.path)
                handler = (ep.handler or "Endpoint").split(".")[-1]
                body += ["", "    [Fact]", f"    public async Task {handler}_{ep.method.title()}_ReturnsSuccess()", "    {",
                         f"        var response = await _client.GetAsync(\"{route}\");  // use {ep.method} as appropriate",
                         "        Assert.True(response.IsSuccessStatusCode);", "    }"]
            body.append("}")
        else:
            deps = re.findall(r"private\s+readonly\s+(I[A-Z]\w*)\s+(_?\w+)\s*;", text)
            body = ["using Moq;", "using Xunit;", "", f"public class {name}Tests", "{"]
            for t, n in deps:
                body.append(f"    private readonly Mock<{t}> {n.lstrip('_')}Mock = new();")
            ctor_args = ", ".join(f"{n.lstrip('_')}Mock.Object" for _, n in deps)
            for f in funcs[:25]:
                body += ["", "    [Fact]", f"    public void {f.name}_ShouldBehaveAsExpected()", "    {",
                         f"        var sut = new {name}({ctor_args});", f"        // var result = sut.{f.name}(...);",
                         "        // Assert.Equal(expected, result);", "    }"]
            body.append("}")
        file_name = f"tests/{name}Tests.cs"
    elif lang in ("JavaScript", "TypeScript"):
        ext = "ts" if lang == "TypeScript" else "js"
        if kind == "integration" and pf.endpoints:
            body = ["import request from 'supertest';", "import app from '../src/app'; // adjust to your app export", "",
                    f"describe('{stem} API', () => {{"]
            for ep in pf.endpoints:
                route = re.sub(r":\w+|\{[^}]+\}", "1", ep.path)
                verb = ep.method.lower() if ep.method in ("GET", "POST", "PUT", "DELETE", "PATCH") else "get"
                body += [f"  it('{ep.method} {ep.path} responds', async () => {{", f"    const res = await request(app).{verb}('{route}');",
                         "    expect(res.status).toBeLessThan(500);", "  });", ""]
            body.append("});")
        else:
            names = sorted({f.parent or f.name for f in funcs})[:10]
            body = [f"import {{ {', '.join(names)} }} from './{stem}';", "", f"describe('{stem}', () => {{"]
            for f in funcs[:25]:
                body += [f"  it('{f.qualified} behaves as expected', () => {{", "    // arrange", "    // act",
                         "    // assert", "    expect(true).toBe(true);", "  });", ""]
            body.append("});")
        file_name = f"{PurePosixPath(path).parent}/{stem}.test.{ext}"
    elif lang == "Go":
        pkg = pf.package or "main"
        body = [f"package {pkg}", "", "import \"testing\"", ""]
        for f in funcs[:25]:
            if not f.name[:1].isupper():
                continue
            body += [f"func Test{f.parent or ''}{f.name}(t *testing.T) {{", "\ttests := []struct {", "\t\tname string", "\t}{", "\t\t{name: \"happy path\"},", "\t}",
                     "\tfor _, tt := range tests {", "\t\tt.Run(tt.name, func(t *testing.T) {", f"\t\t\t// call {f.name} and assert", "\t\t})", "\t}", "}", ""]
        file_name = f"{PurePosixPath(path).with_suffix('')}_test.go"
    else:
        body = [f"// Test generation template not available for {lang}; configure an LLM provider for AI-generated tests."]
        file_name = f"{stem}_test"
    return {"language": lang, "framework": framework, "file_name": file_name, "code": "\n".join(body).strip() + "\n",
            "mode": "template", "targets": [f.qualified for f in funcs[:25]]}


def build_test_prompt(ctx: RepoContext, path: str, symbol: str | None, kind: str) -> str:
    pf = ctx.parsed[path]
    text = ctx.read(path, 14000)
    if symbol:
        sym = next((s for s in pf.symbols if s.name == symbol or s.qualified == symbol), None)
        if sym:
            lines = text.splitlines()
            text = "\n".join(lines[max(0, sym.start_line - 1):sym.end_line])
    deps = []
    for target in ctx.graph.successors(f"file:{path}", ("IMPORTS",)):
        tp = ctx.graph.nodes[target]["path"]
        sigs = [s.signature for s in ctx.parsed.get(tp, pf).symbols][:15] if tp in ctx.parsed else []
        deps.append(f"// {tp}\n" + "\n".join(sigs))
    example = next((t for t in ctx.files if t.category == "test" and t.language == pf.language), None)
    example_text = ctx.read(example.path, 2500) if example else ""
    framework = TEST_FRAMEWORKS.get(pf.language, ("the project's test framework",))[0]
    endpoints = "\n".join(f"{e.method} {e.path} -> {e.handler}" for e in pf.endpoints)
    return (
        f"Write {kind} tests in {pf.language} using {framework} for the code below.\n"
        "Cover the happy path, edge cases, error handling and boundary values. Mock external collaborators. "
        "Return ONLY the complete test file in a single fenced code block.\n\n"
        f"File: {path}\n```\n{text}\n```\n\n"
        + (f"Endpoints:\n{endpoints}\n\n" if endpoints else "")
        + (f"Collaborator signatures:\n{chr(10).join(deps[:6])}\n\n" if deps else "")
        + (f"Existing test style example ({example.path}):\n```\n{example_text}\n```\n" if example_text else "")
    )
