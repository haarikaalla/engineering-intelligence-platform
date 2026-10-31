"""Tree-sitter based AST parser (Java, JavaScript, TypeScript, C#, Go, Rust, Ruby, PHP, Kotlin).

Requires `tree-sitter-language-pack`. If the package or a grammar is missing the caller falls back to the regex parser.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from app.parsing.models import ParsedFile, Symbol

log = logging.getLogger(__name__)

try:  # pragma: no cover - availability depends on platform wheels
    from tree_sitter_language_pack import get_parser as _get_parser

    TREE_SITTER_AVAILABLE = True
except Exception:  # noqa: BLE001
    _get_parser = None
    TREE_SITTER_AVAILABLE = False


@dataclass(frozen=True)
class NodeSpec:
    classes: frozenset[str]
    functions: frozenset[str]
    imports: frozenset[str]
    calls: frozenset[str]
    package: frozenset[str] = frozenset()


def _fs(*items: str) -> frozenset[str]:
    return frozenset(items)


_JS = NodeSpec(
    classes=_fs("class_declaration", "interface_declaration", "enum_declaration", "abstract_class_declaration"),
    functions=_fs("function_declaration", "method_definition", "generator_function_declaration"),
    imports=_fs("import_statement", "export_statement"),
    calls=_fs("call_expression", "new_expression"),
)
SPECS: dict[str, NodeSpec] = {
    "java": NodeSpec(
        classes=_fs("class_declaration", "interface_declaration", "enum_declaration", "record_declaration", "annotation_type_declaration"),
        functions=_fs("method_declaration", "constructor_declaration"),
        imports=_fs("import_declaration"),
        calls=_fs("method_invocation", "object_creation_expression"),
        package=_fs("package_declaration"),
    ),
    "javascript": _JS,
    "typescript": _JS,
    "tsx": _JS,
    "csharp": NodeSpec(
        classes=_fs("class_declaration", "interface_declaration", "struct_declaration", "record_declaration", "enum_declaration"),
        functions=_fs("method_declaration", "constructor_declaration", "local_function_statement"),
        imports=_fs("using_directive"),
        calls=_fs("invocation_expression", "object_creation_expression"),
        package=_fs("namespace_declaration", "file_scoped_namespace_declaration"),
    ),
    "go": NodeSpec(
        classes=_fs("type_spec"),
        functions=_fs("function_declaration", "method_declaration"),
        imports=_fs("import_spec"),
        calls=_fs("call_expression"),
        package=_fs("package_clause"),
    ),
    "rust": NodeSpec(
        classes=_fs("struct_item", "enum_item", "trait_item"),
        functions=_fs("function_item"),
        imports=_fs("use_declaration"),
        calls=_fs("call_expression", "macro_invocation"),
    ),
    "kotlin": NodeSpec(
        classes=_fs("class_declaration", "object_declaration"),
        functions=_fs("function_declaration"),
        imports=_fs("import_header"),
        calls=_fs("call_expression"),
        package=_fs("package_header"),
    ),
    "php": NodeSpec(
        classes=_fs("class_declaration", "interface_declaration", "trait_declaration"),
        functions=_fs("function_definition", "method_declaration"),
        imports=_fs("namespace_use_clause"),
        calls=_fs("function_call_expression", "member_call_expression", "scoped_call_expression"),
        package=_fs("namespace_definition"),
    ),
    "ruby": NodeSpec(
        classes=_fs("class", "module"),
        functions=_fs("method", "singleton_method"),
        imports=_fs(),
        calls=_fs("call"),
    ),
}
LANGUAGE_MAP = {
    "Java": "java", "JavaScript": "javascript", "TypeScript": "typescript", "C#": "csharp", "Go": "go",
    "Rust": "rust", "Kotlin": "kotlin", "PHP": "php", "Ruby": "ruby",
}
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_KIND = {
    "interface_declaration": "interface", "enum_declaration": "enum", "struct_declaration": "struct",
    "record_declaration": "record", "trait_item": "trait", "struct_item": "struct", "enum_item": "enum",
    "module": "module", "trait_declaration": "trait",
}


@lru_cache(maxsize=16)
def _parser(lang: str):
    return _get_parser(lang)  # type: ignore[misc]


def supports(language: str, path: str) -> bool:
    return TREE_SITTER_AVAILABLE and language in LANGUAGE_MAP


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def _row(point) -> int:
    return (point.row if hasattr(point, "row") else point[0]) + 1


def _name(node, src: bytes) -> str | None:
    named = node.child_by_field_name("name")
    if named is not None:
        return _text(named, src)
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "constant", "simple_identifier", "name"):
            return _text(child, src)
    return None


def _callee(node, src: bytes) -> str | None:
    for field in ("name", "function", "method", "constructor", "type", "macro"):
        target = node.child_by_field_name(field)
        if target is not None:
            idents = _IDENT.findall(_text(target, src))
            return idents[-1] if idents else None
    first = node.children[0] if node.children else None
    if first is not None:
        idents = _IDENT.findall(_text(first, src))
        return idents[-1] if idents else None
    return None


def _import_value(node, lang: str, src: bytes) -> list[str]:
    text = _text(node, src)
    if lang in ("javascript", "typescript", "tsx"):
        source = node.child_by_field_name("source")
        if source is None:
            return []
        return [_text(source, src).strip("'\"`")]
    if lang == "go":
        path = node.child_by_field_name("path")
        return [_text(path, src).strip('"')] if path is not None else []
    text = re.sub(r"^\s*(import|using|use|global using)\s+(static\s+)?", "", text).rstrip(";").strip()
    if "=" in text and lang == "csharp":
        text = text.split("=", 1)[1].strip()
    return [text] if text else []


def parse_tree_sitter(path: str, language: str, text: str) -> ParsedFile:
    lang = LANGUAGE_MAP[language]
    if lang == "typescript" and path.endswith(".tsx"):
        lang = "tsx"
    spec = SPECS[lang]
    src = text.encode("utf-8", errors="ignore")
    tree = _parser(lang).parse(src)
    pf = ParsedFile(path=path, language=language, parser="tree-sitter")

    stack = [(tree.root_node, None, None)]  # node, enclosing class, enclosing function qualified name
    while stack:
        node, cls, fn = stack.pop()
        ntype = node.type
        next_cls, next_fn = cls, fn

        if ntype in spec.package and pf.package is None:
            name = node.child_by_field_name("name")
            raw = _text(name, src) if name is not None else _text(node, src)
            pf.package = re.sub(r"^\s*(package|namespace)\s+", "", raw).split("{")[0].strip().rstrip(";").strip()
        elif ntype in spec.imports:
            pf.imports.extend(_import_value(node, lang, src))
        elif ntype in spec.classes:
            name = _name(node, src)
            if name:
                kind = _KIND.get(ntype, "class")
                if lang == "go":
                    type_node = node.child_by_field_name("type")
                    kind = type_node.type.replace("_type", "") if type_node is not None else "type"
                pf.symbols.append(Symbol(name=name, kind=kind, start_line=_row(node.start_point), end_line=_row(node.end_point),
                                         parent=cls, signature=_text(node, src).split("{")[0].strip()[:200]))
                next_cls = name
        elif ntype in spec.functions:
            name = _name(node, src)
            parent = cls
            if lang == "go" and ntype == "method_declaration":
                receiver = node.child_by_field_name("receiver")
                if receiver is not None:
                    idents = re.findall(r"\*?\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\)?\s*$", _text(receiver, src).strip("() "))
                    parent = idents[-1] if idents else parent
            if name:
                kind = "constructor" if "constructor" in ntype else ("method" if parent else "function")
                sym = Symbol(name=name, kind=kind, start_line=_row(node.start_point), end_line=_row(node.end_point),
                             parent=parent, signature=_text(node, src).split("{")[0].strip().replace("\n", " ")[:200])
                pf.symbols.append(sym)
                next_fn = sym.qualified
        elif ntype == "variable_declarator" and lang in ("javascript", "typescript", "tsx"):
            value = node.child_by_field_name("value")
            if value is not None and value.type in ("arrow_function", "function_expression", "function"):
                name = _name(node, src)
                if name:
                    sym = Symbol(name=name, kind="function", start_line=_row(node.start_point), end_line=_row(node.end_point),
                                 parent=cls, signature=_text(node, src).split("=>")[0].strip()[:200])
                    pf.symbols.append(sym)
                    next_fn = sym.qualified
        elif ntype in spec.calls:
            callee = _callee(node, src)
            if callee:
                if lang in ("javascript", "typescript", "tsx") and callee == "require":
                    args = node.child_by_field_name("arguments")
                    if args is not None:
                        m = re.search(r"['\"`]([^'\"`]+)['\"`]", _text(args, src))
                        if m:
                            pf.imports.append(m.group(1))
                elif fn is not None or ntype.startswith("object_creation") or ntype == "new_expression":
                    pf.calls.append((fn, callee, _row(node.start_point)))

        for child in reversed(node.children):
            stack.append((child, next_cls, next_fn))

    _attach_annotations(pf, text)
    return pf


def _attach_annotations(pf: ParsedFile, text: str) -> None:
    lines = text.splitlines()
    for sym in pf.symbols:
        anns: list[str] = []
        first = lines[sym.start_line - 1].strip() if sym.start_line - 1 < len(lines) else ""
        anns.extend(re.findall(r"@\w+(?:\([^)]*\))?", first))
        i = sym.start_line - 2
        while i >= 0 and len(anns) < 8:
            prev = lines[i].strip()
            if prev.startswith("@") or (prev.startswith("[") and prev.endswith("]")):
                anns.append(prev[:200])
                i -= 1
            else:
                break
        sym.annotations = list(dict.fromkeys(anns))
