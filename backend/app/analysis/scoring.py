"""Engineering intelligence scores and technical-debt insights."""
from __future__ import annotations

from app.services.context import RepoContext


def _clamp(v: float) -> int:
    return int(max(0, min(100, round(v))))


def compute_scores(ctx: RepoContext, architecture: dict, security: dict, tests: dict) -> dict:
    funcs = [(p, s) for p, pf in ctx.parsed.items() if ctx.file_info[p].category == "source"
             for s in pf.symbols if s.kind in ("function", "method", "constructor")]
    source_files = [f for f in ctx.files if f.category == "source"]
    kloc = max(1.0, sum(f.loc for f in source_files) / 1000)

    # ---------------------------------------------------------------- architecture
    arch = 100.0
    arch -= min(30, 10 * len(architecture.get("module_cycles", [])))
    arch -= min(15, architecture.get("file_cycle_count", 0) * 1.0)
    arch -= min(20, 2 * len(architecture.get("god_classes", [])))
    arch -= min(15, len(architecture.get("layer_violations", [])) * 1.0)
    deps = architecture.get("dependency_map", [])
    if ctx.modules:
        avg_fan_out = len(deps) / max(1, len(ctx.modules))
        arch -= max(0, (avg_fan_out - 4) * 3)

    # ---------------------------------------------------------------- maintainability
    lengths = [s.end_line - s.start_line + 1 for _, s in funcs]
    avg_len = sum(lengths) / len(lengths) if lengths else 0
    complex_fns = [(p, s) for p, s in funcs if s.complexity > 15]
    big_files = [f for f in source_files if f.loc > 1000]
    todos = sum(pf.todo_count for pf in ctx.parsed.values())
    maint = 100.0
    maint -= max(0, (avg_len - 30) * 0.8)
    maint -= min(30, 150 * len(complex_fns) / max(1, len(funcs)))
    maint -= min(20, 100 * len(big_files) / max(1, len(source_files)))
    maint -= min(10, max(0, todos / kloc - 5))
    if not any(f.path.lower().startswith("readme") for f in ctx.files):
        maint -= 5

    test_score = tests["coverage_estimate"]["files_percent"]
    sec = security.get("score", 100)
    health = 0.3 * sec + 0.25 * _clamp(arch) + 0.25 * _clamp(maint) + 0.2 * test_score

    # ---------------------------------------------------------------- debt
    debt = []
    if complex_fns:
        top = sorted(complex_fns, key=lambda x: -x[1].complexity)[:10]
        debt.append({"title": "High cyclomatic complexity", "severity": "high" if len(complex_fns) > 20 else "medium",
                     "count": len(complex_fns), "effort_hours": len(complex_fns) * 2,
                     "items": [f"{p}::{s.qualified} (complexity {s.complexity})" for p, s in top]})
    gods = architecture.get("god_classes", [])
    if gods:
        debt.append({"title": "God classes", "severity": "high", "count": len(gods), "effort_hours": len(gods) * 8,
                     "items": [f"{g['file']}::{g['name']} ({g['lines']} lines, {g['methods']} members)" for g in gods[:10]]})
    if big_files:
        debt.append({"title": "Very large files (>1000 LOC)", "severity": "medium", "count": len(big_files),
                     "effort_hours": len(big_files) * 4, "items": [f"{f.path} ({f.loc} LOC)" for f in sorted(big_files, key=lambda f: -f.loc)[:10]]})
    if architecture.get("module_cycles"):
        debt.append({"title": "Cyclic module dependencies", "severity": "high", "count": len(architecture["module_cycles"]),
                     "effort_hours": 6 * len(architecture["module_cycles"]), "items": [" ↔ ".join(c) for c in architecture["module_cycles"][:10]]})
    if architecture.get("file_cycle_count"):
        debt.append({"title": "Circular file imports", "severity": "medium", "count": architecture["file_cycle_count"],
                     "effort_hours": 2 * architecture["file_cycle_count"], "items": [" → ".join(c[:5]) for c in architecture.get("file_cycles", [])[:8]]})
    if architecture.get("layer_violations"):
        v = architecture["layer_violations"]
        debt.append({"title": "Layering violations", "severity": "medium", "count": len(v), "effort_hours": len(v),
                     "items": [f"{x['from']} → {x['to']}" for x in v[:10]]})
    untested = [r for r in tests.get("recommendations", []) if r["priority"] == "high"]
    if untested:
        debt.append({"title": "High-risk code without tests", "severity": "high", "count": len(untested),
                     "effort_hours": int(len(untested) * 1.5), "items": [r["file"] for r in untested[:10]]})
    if todos:
        debt.append({"title": "TODO / FIXME markers", "severity": "low", "count": todos, "effort_hours": int(todos * 0.5),
                     "items": [p for p, pf in sorted(ctx.parsed.items(), key=lambda kv: -kv[1].todo_count)[:10] if pf.todo_count]})
    unpinned = security.get("dependencies", {}).get("unpinned", 0)
    if unpinned:
        debt.append({"title": "Unpinned dependencies", "severity": "low", "count": unpinned, "effort_hours": max(1, unpinned // 5), "items": []})

    return {
        "health": _clamp(health),
        "architecture": _clamp(arch),
        "security": _clamp(sec),
        "maintainability": _clamp(maint),
        "test": _clamp(test_score),
        "metrics": {
            "kloc": round(kloc, 1),
            "functions": len(funcs),
            "avg_function_length": round(avg_len, 1),
            "complex_functions": len(complex_fns),
            "large_files": len(big_files),
            "todo_count": todos,
        },
        "technical_debt": {"total_hours": sum(d["effort_hours"] for d in debt), "items": debt},
    }
