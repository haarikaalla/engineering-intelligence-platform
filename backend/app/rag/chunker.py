"""Symbol-aware chunking of source code, documentation and configuration for RAG."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ingestion.scanner import SourceFile
from app.parsing.models import ParsedFile

MAX_LINES = 80
OVERLAP = 10
_HEADING_RE = re.compile(r"^#{1,4}\s+")


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict = field(default_factory=dict)


def _windows(lines: list[str], start: int, end: int, size: int = MAX_LINES) -> list[tuple[int, int]]:
    out = []
    s = start
    while s <= end:
        e = min(end, s + size - 1)
        out.append((s, e))
        if e == end:
            break
        s = e - OVERLAP + 1
    return out


def _make(sf: SourceFile, lines: list[str], start: int, end: int, kind: str, symbol: str = "") -> Chunk | None:
    body = "\n".join(lines[start - 1:end]).strip()
    if len(body) < 20:
        return None
    header = f"File: {sf.path}\nLanguage: {sf.language}\nModule: {sf.module}\n" + (f"Symbol: {symbol}\n" if symbol else "")
    return Chunk(
        id=f"{sf.path}:{start}-{end}",
        text=header + "\n" + body[:6000],
        metadata={"path": sf.path, "start_line": start, "end_line": end, "language": sf.language, "kind": kind,
                  "symbol": symbol, "module": sf.module, "category": sf.category},
    )


def chunk_file(sf: SourceFile, text: str, pf: ParsedFile | None) -> list[Chunk]:
    lines = text.splitlines()
    n = len(lines)
    if n == 0:
        return []
    chunks: list[Chunk | None] = []

    if sf.category == "doc":
        sections: list[int] = [i + 1 for i, line in enumerate(lines) if _HEADING_RE.match(line)] or [1]
        if sections[0] != 1:
            sections.insert(0, 1)
        bounds = list(zip(sections, [*(s - 1 for s in sections[1:]), n]))
        for s, e in bounds:
            title = lines[s - 1].lstrip("# ").strip() if _HEADING_RE.match(lines[s - 1]) else ""
            for ws, we in _windows(lines, s, e):
                chunks.append(_make(sf, lines, ws, we, "doc", title))
        return [c for c in chunks if c]

    if pf is None or not pf.symbols or sf.category not in ("source", "test"):
        kind = "code" if sf.category in ("source", "test") else "config"
        return [c for c in (_make(sf, lines, s, e, kind) for s, e in _windows(lines, 1, n, 60)) if c]

    covered = [False] * (n + 2)
    units: list[tuple[int, int, str]] = []
    classes = [s for s in pf.symbols if s.kind not in ("function", "method", "constructor")]
    funcs = [s for s in pf.symbols if s.kind in ("function", "method", "constructor")]
    small_classes = [c for c in classes if c.end_line - c.start_line <= 150 and not c.parent]
    for c in small_classes:
        units.append((c.start_line, c.end_line, c.name))
    in_small = lambda f: any(c.start_line <= f.start_line <= c.end_line for c in small_classes)  # noqa: E731
    for f in funcs:
        if not in_small(f):
            units.append((f.start_line, f.end_line, f.qualified))
    for c in classes:
        if c not in small_classes:
            first_member = min((f.start_line for f in funcs if c.start_line < f.start_line <= c.end_line), default=c.end_line)
            units.append((c.start_line, min(first_member - 1, c.start_line + 30), f"{c.name} (declaration)"))

    for s, e, name in sorted(units):
        s, e = max(1, s), min(n, max(s, e))
        for ws, we in _windows(lines, s, e):
            chunks.append(_make(sf, lines, ws, we, "code", name))
        for i in range(s, e + 1):
            covered[i] = True

    # Uncovered regions (imports, module-level code)
    i = 1
    while i <= n:
        if covered[i]:
            i += 1
            continue
        j = i
        while j <= n and not covered[j]:
            j += 1
        for ws, we in _windows(lines, i, j - 1, 60):
            chunks.append(_make(sf, lines, ws, we, "code", "module-level"))
        i = j
    return [c for c in chunks if c]
