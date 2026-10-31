"""Parser selection: Tree-sitter → language-native AST → regex fallback, followed by semantic extraction."""
from __future__ import annotations

import logging
import re

from app.parsing import extractors
from app.parsing.models import ParsedFile
from app.parsing.python_parser import parse_python
from app.parsing.regex_parser import SPECS as REGEX_SPECS
from app.parsing.regex_parser import parse_regex
from app.parsing.treesitter_parser import TREE_SITTER_AVAILABLE, parse_tree_sitter, supports

log = logging.getLogger(__name__)

PARSEABLE = set(REGEX_SPECS) | {"Python", "Protocol Buffers", "SQL"}
_TYPE_REF_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]{2,}\b")


def parse_source(path: str, language: str, text: str) -> ParsedFile:
    pf: ParsedFile | None = None
    if language == "Python":
        try:
            pf = parse_python(path, text)
        except (SyntaxError, ValueError, RecursionError):
            pf = None
    elif supports(language, path):
        try:
            pf = parse_tree_sitter(path, language, text)
        except Exception as exc:  # noqa: BLE001 - grammar issues must never break ingestion
            log.debug("tree-sitter failed for %s: %s", path, exc)
            pf = None
    if pf is None:
        pf = parse_regex(path, language, text) if language in REGEX_SPECS else ParsedFile(path=path, language=language)
        if language == "Python":
            pf.parser = "regex"

    lines = text.splitlines()
    pf.loc = sum(1 for line in lines if line.strip())
    extractors.compute_complexity(pf, lines)
    extractors.extract_endpoints(pf, text)
    extractors.extract_db_access(pf, text)
    extractors.extract_integrations(pf, text)
    if pf.entry_point is None:
        extractors.extract_entry_point(pf, text)
    pf.imports = list(dict.fromkeys(i for i in pf.imports if i))
    own = {s.name for s in pf.symbols}
    pf.type_refs = sorted({t for t in _TYPE_REF_RE.findall(text) if t not in own})[:800]
    return pf


def parse_config(path: str, language: str, text: str) -> ParsedFile:
    """Config files only contribute data-store connections and service URLs."""
    pf = ParsedFile(path=path, language=language, parser="config")
    extractors.extract_db_access(pf, text)
    extractors.extract_integrations(pf, text)
    pf.db_access = [d for d in pf.db_access if d.kind in ("connection", "driver")]
    return pf


def parser_backend() -> str:
    return "tree-sitter" if TREE_SITTER_AVAILABLE else "regex"
