"""Python parser built on the standard-library AST."""
from __future__ import annotations

import ast
from pathlib import PurePosixPath

from app.parsing.models import ParsedFile, Symbol


def _module_name(path: str) -> str:
    p = PurePosixPath(path)
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if parts and parts[0] in ("src", "lib"):
        parts = parts[1:]
    return ".".join(parts)


def _resolve_relative(path: str, module: str | None, level: int) -> str:
    package_parts = _module_name(path).split(".")
    if not PurePosixPath(path).name == "__init__.py":
        package_parts = package_parts[:-1]
    base = package_parts[: len(package_parts) - (level - 1)] if level > 1 else package_parts
    return ".".join([*base, module] if module else base)


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def parse_python(path: str, text: str) -> ParsedFile:
    tree = ast.parse(text, filename=path)
    pf = ParsedFile(path=path, language="Python", parser="ast", package=_module_name(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            pf.imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_relative(path, node.module, node.level) if node.level else (node.module or "")
            if module:
                pf.imports.append(module)
                pf.imports.extend(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")

    def visit_function(fn: ast.FunctionDef | ast.AsyncFunctionDef, parent: str | None) -> None:
        args = [a.arg for a in fn.args.args]
        sym = Symbol(
            name=fn.name,
            kind="method" if parent else "function",
            start_line=fn.lineno,
            end_line=getattr(fn, "end_lineno", fn.lineno) or fn.lineno,
            parent=parent,
            signature=f"{'async ' if isinstance(fn, ast.AsyncFunctionDef) else ''}def {fn.name}({', '.join(args)})",
            annotations=[_safe_unparse(d) for d in fn.decorator_list],
            doc=(ast.get_docstring(fn) or "")[:500],
        )
        pf.symbols.append(sym)
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call):
                callee = _call_name(sub)
                if callee:
                    pf.calls.append((sym.qualified, callee, sub.lineno))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit_function(node, None)
        elif isinstance(node, ast.ClassDef):
            pf.symbols.append(
                Symbol(
                    name=node.name,
                    kind="class",
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    signature=f"class {node.name}",
                    annotations=[_safe_unparse(d) for d in node.decorator_list],
                    doc=(ast.get_docstring(node) or "")[:500],
                    bases=[_safe_unparse(b) for b in node.bases],
                )
            )
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit_function(item, node.name)
        elif isinstance(node, ast.If) and _is_main_guard(node):
            pf.entry_point = "__main__ guard"
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and (callee := _call_name(sub)):
                    pf.calls.append((None, callee, sub.lineno))
    return pf


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
    )


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)[:200]
    except Exception:  # pragma: no cover - defensive for exotic nodes
        return ""
