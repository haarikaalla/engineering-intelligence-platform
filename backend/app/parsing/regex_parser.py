"""Regex-based structural parser used for languages without a Tree-sitter grammar (and as a fallback)."""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from app.parsing.models import ParsedFile, Symbol

KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "new", "else", "do", "try", "synchronized", "using", "lock",
    "foreach", "sizeof", "typeof", "await", "throw", "yield", "function", "super", "this", "base", "when", "in", "is",
    "as", "delete", "case", "default", "assert", "print", "elif", "except", "with", "not", "and", "or", "match", "fn",
    "func", "def", "defer", "go", "select", "make", "len", "cap", "append", "constructor", "nameof", "checked",
    "fixed", "unchecked", "void", "var", "let", "const", "static", "public", "private", "protected", "class",
    "require", "import", "export", "package", "throws", "finally", "instanceof", "unless", "until",
    "record", "struct", "interface", "enum",
}
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_TYPE = r"[\w.]+(?:<[^()]*?>)?(?:\[\])*\??"


@dataclass
class RegexSpec:
    package: re.Pattern | None = None
    imports: list[re.Pattern] = field(default_factory=list)
    types: list[re.Pattern] = field(default_factory=list)
    functions: list[re.Pattern] = field(default_factory=list)
    braces: bool = True
    import_block: re.Pattern | None = None


M = re.M
JAVA_MODS = r"(?:(?:public|private|protected|static|final|abstract|synchronized|native|default|strictfp|transient|@\w+(?:\([^)]*\))?)\s+)*"
CS_MODS = r"(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed|partial|extern|unsafe|new|readonly)\s+)*"

SPECS: dict[str, RegexSpec] = {
    "Java": RegexSpec(
        package=re.compile(r"^\s*package\s+([\w.]+)\s*;", M),
        imports=[re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", M)],
        types=[re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*" + JAVA_MODS + r"(?P<kind>class|interface|enum|record|@interface)\s+(?P<name>\w+)", M)],
        functions=[re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*" + JAVA_MODS + r"(?:<[^>]+>\s+)?(?P<rtype>" + _TYPE + r")\s+(?P<name>\w+)\s*\(", M)],
    ),
    "C#": RegexSpec(
        package=re.compile(r"^\s*namespace\s+([\w.]+)", M),
        imports=[re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?([\w.]+)\s*;", M)],
        types=[re.compile(r"^\s*(?:\[[^\]]*\]\s*)*" + CS_MODS + r"(?P<kind>class|interface|enum|record|struct)\s+(?P<name>\w+)", M)],
        functions=[re.compile(r"^\s*(?:\[[^\]]*\]\s*)*" + CS_MODS + r"(?P<rtype>" + _TYPE + r")\s+(?P<name>\w+)\s*(?:<[^>]*>)?\s*\(", M)],
    ),
    "Kotlin": RegexSpec(
        package=re.compile(r"^\s*package\s+([\w.]+)", M),
        imports=[re.compile(r"^\s*import\s+([\w.*]+)", M)],
        types=[re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:data|enum|sealed|abstract|open|internal|private|public|inner|annotation)\s+)*(?P<kind>class|interface|object)\s+(?P<name>\w+)", M)],
        functions=[re.compile(r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w.]+\.)?(?P<name>\w+)\s*\(", M)],
    ),
    "Scala": RegexSpec(
        package=re.compile(r"^\s*package\s+([\w.]+)", M),
        imports=[re.compile(r"^\s*import\s+([\w.{}_,\s]+)", M)],
        types=[re.compile(r"^\s*(?:case\s+|abstract\s+|sealed\s+|final\s+)*(?P<kind>class|trait|object)\s+(?P<name>\w+)", M)],
        functions=[re.compile(r"\bdef\s+(?P<name>\w+)", M)],
    ),
    "JavaScript": RegexSpec(
        imports=[
            re.compile(r"^\s*import\s+(?:[\w*{}\s,$]+from\s+)?['\"]([^'\"]+)['\"]", M),
            re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)"),
            re.compile(r"import\(\s*['\"]([^'\"]+)['\"]\s*\)"),
            re.compile(r"^\s*export\s+[^;]*?from\s+['\"]([^'\"]+)['\"]", M),
        ],
        types=[re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:declare\s+)?(?P<kind>class|interface|enum)\s+(?P<name>\w+)", M)],
        functions=[
            re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>\w+)\s*[<(]", M),
            re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::\s*[^=]+)?=>|[\w$]+\s*=>)", M),
            re.compile(r"^\s*(?:(?:public|private|protected|static|async|readonly|override|get|set)\s+)*(?P<rtype>)(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\([^)]*\)\s*(?::\s*[^{;]+)?\{\s*$", M),
        ],
    ),
    "Go": RegexSpec(
        package=re.compile(r"^package\s+(\w+)", M),
        imports=[re.compile(r"^\s*import\s+(?:[\w.]+\s+)?\"([^\"]+)\"", M)],
        import_block=re.compile(r"^import\s*\((.*?)\)", re.S | M),
        types=[re.compile(r"^type\s+(?P<name>\w+)(?:\[[^\]]*\])?\s+(?P<kind>struct|interface)", M)],
        functions=[re.compile(r"^func\s+(?:\(\s*\w*\s*\*?(?P<recv>\w+)(?:\[[^\]]*\])?\s*\)\s*)?(?P<name>\w+)\s*[\[(]", M)],
    ),
    "Ruby": RegexSpec(
        imports=[re.compile(r"^\s*require(?:_relative)?\s+['\"]([^'\"]+)['\"]", M)],
        types=[re.compile(r"^\s*(?P<kind>class|module)\s+(?P<name>[\w:]+)", M)],
        functions=[re.compile(r"^\s*def\s+(?:self\.)?(?P<name>\w+[?!=]?)", M)],
        braces=False,
    ),
    "PHP": RegexSpec(
        package=re.compile(r"^\s*namespace\s+([\w\\]+)", M),
        imports=[re.compile(r"^\s*use\s+([\w\\]+)", M), re.compile(r"(?:require|include)(?:_once)?\s*\(?\s*['\"]([^'\"]+)['\"]")],
        types=[re.compile(r"^\s*(?:abstract\s+|final\s+)*(?P<kind>class|interface|trait)\s+(?P<name>\w+)", M)],
        functions=[re.compile(r"\bfunction\s+(?P<name>\w+)\s*\(", M)],
    ),
    "Rust": RegexSpec(
        imports=[re.compile(r"^\s*use\s+([\w:]+)", M)],
        types=[re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?P<kind>struct|enum|trait)\s+(?P<name>\w+)", M)],
        functions=[re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(?P<name>\w+)", M)],
    ),
    "C++": RegexSpec(
        imports=[re.compile(r"^\s*#include\s*[<\"]([^>\"]+)[>\"]", M)],
        types=[re.compile(r"^\s*(?:template\s*<[^>]*>\s*)?(?P<kind>class|struct)\s+(?P<name>\w+)\s*(?::[^{;]*)?\{?\s*$", M)],
        functions=[re.compile(r"^(?P<rtype>[\w:*&<>,\s]+?)\s+[*&]?(?:\w+::)?(?P<name>\w+)\s*\([^;]*\)\s*(?:const)?\s*(?:override)?\s*\{?\s*$", M)],
    ),
    "Swift": RegexSpec(
        imports=[re.compile(r"^\s*import\s+(\w+)", M)],
        types=[re.compile(r"^\s*(?:public\s+|private\s+|final\s+|open\s+)*(?P<kind>class|struct|protocol|enum|extension)\s+(?P<name>\w+)", M)],
        functions=[re.compile(r"\bfunc\s+(?P<name>\w+)", M)],
    ),
    "Dart": RegexSpec(
        imports=[re.compile(r"^\s*import\s+['\"]([^'\"]+)['\"]", M)],
        types=[re.compile(r"^\s*(?:abstract\s+)?(?P<kind>class|mixin|enum)\s+(?P<name>\w+)", M)],
        functions=[re.compile(r"^\s*(?:static\s+|Future<[^>]*>\s+|async\s+)*(?P<rtype>" + _TYPE + r")\s+(?P<name>\w+)\s*\([^;]*$", M)],
    ),
}
SPECS["TypeScript"] = SPECS["JavaScript"]
SPECS["Vue"] = SPECS["JavaScript"]
SPECS["Svelte"] = SPECS["JavaScript"]
SPECS["Groovy"] = SPECS["Java"]
SPECS["C"] = SPECS["C++"]


def strip_code(line: str) -> str:
    line = _STRING_RE.sub('""', line)
    idx = line.find("//")
    return line[:idx] if idx >= 0 else line


def block_end(lines: list[str], start_idx: int, max_scan: int = 4000) -> int:
    """Return the 1-based line on which the brace block opened at/after start_idx closes."""
    depth, started = 0, False
    stop = min(len(lines), start_idx + max_scan)
    for i in range(start_idx, stop):
        code = strip_code(lines[i])
        for ch in code:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth <= 0:
                    return i + 1
        if not started and ";" in code:
            return i + 1
        if not started and i - start_idx > 6:
            return start_idx + 1
    return stop if started else start_idx + 1


def _line_of(text: str, offset: int, line_starts: list[int]) -> int:
    return bisect.bisect_right(line_starts, offset)


def extract_calls(lines: list[str], start: int, end: int, own_name: str) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    for i in range(start, min(end, len(lines))):
        for m in _CALL_RE.finditer(strip_code(lines[i])):
            name = m.group(1)
            if name not in KEYWORDS and name != own_name:
                calls.append((name, i + 1))
                if len(calls) >= 300:
                    return calls
    return calls


def parse_regex(path: str, language: str, text: str) -> ParsedFile:
    pf = ParsedFile(path=path, language=language, parser="regex")
    spec = SPECS.get(language)
    if not spec:
        return pf
    lines = text.splitlines()
    line_starts = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line) + 1)

    if spec.package and (m := spec.package.search(text)):
        pf.package = m.group(1).replace("\\", ".")
    for pattern in spec.imports:
        pf.imports.extend(m.group(1).strip() for m in pattern.finditer(text))
    if spec.import_block:
        for block in spec.import_block.finditer(text):
            pf.imports.extend(re.findall(r"\"([^\"]+)\"", block.group(1)))

    types: list[Symbol] = []
    for pattern in spec.types:
        for m in pattern.finditer(text):
            line = _line_of(text, m.start("name"), line_starts)
            end = block_end(lines, line - 1) if spec.braces else line
            types.append(Symbol(name=m.group("name").split("::")[-1], kind=m.group("kind").replace("@", "annotation_"),
                                start_line=line, end_line=max(end, line), signature=lines[line - 1].strip()[:200]))

    functions: list[Symbol] = []
    seen_lines: set[int] = set()
    for pattern in spec.functions:
        for m in pattern.finditer(text):
            name = m.group("name")
            rtype = (m.groupdict().get("rtype") or "").strip()
            if name in KEYWORDS or rtype.split(".")[-1] in KEYWORDS:
                continue
            line = _line_of(text, m.start("name"), line_starts)
            if line in seen_lines:
                continue
            code_line = strip_code(lines[line - 1])
            if spec.braces and re.search(r"\)\s*;\s*$", code_line) and language not in ("Java", "C#"):
                continue  # prototype / call statement
            seen_lines.add(line)
            end = block_end(lines, line - 1) if spec.braces else line
            recv = m.groupdict().get("recv")
            functions.append(Symbol(name=name, kind="method" if recv else "function", start_line=line,
                                    end_line=max(end, line), parent=recv, signature=lines[line - 1].strip()[:200]))

    # Constructors (Java/C#): `public ClassName(`
    if language in ("Java", "C#"):
        type_names = {t.name for t in types}
        for m in re.finditer(r"^\s*(?:public|protected|private|internal)\s+(\w+)\s*\(", text, M):
            if m.group(1) in type_names:
                line = _line_of(text, m.start(1), line_starts)
                if line not in seen_lines:
                    seen_lines.add(line)
                    functions.append(Symbol(name=m.group(1), kind="constructor", start_line=line,
                                            end_line=block_end(lines, line - 1), signature=lines[line - 1].strip()[:200]))

    if not spec.braces:
        _infer_ends_by_successor(types + functions, len(lines))

    for fn in functions:
        if fn.parent:
            continue
        owner = None
        for t in types:
            if t.start_line < fn.start_line <= t.end_line and (owner is None or t.start_line > owner.start_line):
                owner = t
        if owner:
            fn.parent = owner.name
            fn.kind = "constructor" if fn.kind == "constructor" else "method"
        elif language in ("JavaScript", "TypeScript") and m_is_method_only(fn):
            fn.kind = "skip"
    functions = [f for f in functions if f.kind != "skip"]

    for fn in functions:
        for callee, line in extract_calls(lines, fn.start_line, fn.end_line, fn.name):
            pf.calls.append((fn.qualified, callee, line))

    # Leading annotations / attributes for symbols
    for sym in types + functions:
        anns = []
        i = sym.start_line - 2
        while i >= 0 and len(anns) < 8:
            prev = lines[i].strip()
            if prev.startswith("@") or (prev.startswith("[") and prev.endswith("]")):
                anns.append(prev[:200])
                i -= 1
            else:
                break
        first = lines[sym.start_line - 1].strip()
        anns.extend(re.findall(r"@\w+(?:\([^)]*\))?", first.split("(")[0] if not first.startswith("@") else first))
        sym.annotations = list(dict.fromkeys(anns))

    pf.symbols = sorted(types + functions, key=lambda s: s.start_line)
    return pf


def m_is_method_only(fn: Symbol) -> bool:
    """A JS 'method-shaped' line outside any class (e.g. `foo(x) {` in an object literal) – keep only declarations."""
    sig = fn.signature
    return not re.match(r"^\s*(export\s+)?(default\s+)?(async\s+)?(function|const|let|var)\b", sig)


def _infer_ends_by_successor(symbols: list[Symbol], total_lines: int) -> None:
    ordered = sorted(symbols, key=lambda s: s.start_line)
    for i, sym in enumerate(ordered):
        nxt = next((o.start_line - 1 for o in ordered[i + 1:] if o.kind == sym.kind or sym.kind in ("function", "method")), total_lines)
        sym.end_line = max(sym.start_line, nxt)
