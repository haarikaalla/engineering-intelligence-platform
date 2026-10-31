"""Language-neutral intermediate representation produced by all parsers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Symbol:
    name: str
    kind: str  # class | interface | enum | function | method | struct
    start_line: int
    end_line: int
    parent: str | None = None
    signature: str = ""
    annotations: list[str] = field(default_factory=list)
    doc: str = ""
    complexity: int = 1
    bases: list[str] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


@dataclass
class Endpoint:
    method: str
    path: str
    line: int
    framework: str
    handler: str | None = None


@dataclass
class DbAccess:
    kind: str  # sql | orm | entity | repository | driver | connection | nosql | cache
    target: str
    line: int
    detail: str = ""


@dataclass
class Messaging:
    direction: str  # publish | consume
    topic: str
    line: int
    technology: str = "kafka"


@dataclass
class ParsedFile:
    path: str
    language: str
    parser: str = "regex"
    package: str | None = None
    imports: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    calls: list[tuple[str | None, str, int]] = field(default_factory=list)  # (caller qualified, callee name, line)
    endpoints: list[Endpoint] = field(default_factory=list)
    db_access: list[DbAccess] = field(default_factory=list)
    http_targets: list[str] = field(default_factory=list)
    messaging: list[Messaging] = field(default_factory=list)
    entry_point: str | None = None
    todo_count: int = 0
    loc: int = 0
    type_refs: list[str] = field(default_factory=list)
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ParsedFile":
        return cls(
            path=d["path"],
            language=d["language"],
            parser=d.get("parser", "regex"),
            package=d.get("package"),
            imports=d.get("imports", []),
            symbols=[Symbol(**s) for s in d.get("symbols", [])],
            calls=[tuple(c) for c in d.get("calls", [])],
            endpoints=[Endpoint(**e) for e in d.get("endpoints", [])],
            db_access=[DbAccess(**x) for x in d.get("db_access", [])],
            http_targets=d.get("http_targets", []),
            messaging=[Messaging(**m) for m in d.get("messaging", [])],
            entry_point=d.get("entry_point"),
            todo_count=d.get("todo_count", 0),
            loc=d.get("loc", 0),
            type_refs=d.get("type_refs", []),
        )

    def enclosing_symbol(self, line: int, functions_only: bool = False) -> Symbol | None:
        best: Symbol | None = None
        for s in self.symbols:
            if functions_only and s.kind not in ("function", "method"):
                continue
            if s.start_line <= line <= s.end_line and (best is None or s.start_line >= best.start_line):
                best = s
        return best
