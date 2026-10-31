"""Anti-hallucination guards: fact-cited narratives and verification of code references in LLM output."""
from __future__ import annotations

import re

from app.config import get_settings
from app.services.context import RepoContext

_CITE = re.compile(r"\[(F\d+)\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\[(])")
_CODE_SPAN = re.compile(r"`([^`\n]{2,200})`")
_PATHLIKE = re.compile(r"[\w.\-]+/[\w./\-]+|\b[\w\-]+\.(?:java|py|ts|tsx|js|jsx|cs|go|kt|rb|php|rs|scala|xml|ya?ml|json|properties|sql|gradle|md)\b")
_API = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|ANY|RPC)\s+(\S+)$")

NARRATIVE_PROMPT = """You explain a software repository using ONLY the numbered facts below.
Rules (strict):
- Every sentence and every bullet MUST end with one or more citations like [F12] referring to facts that support it.
- Do not mention any file, class, function, endpoint, table, library or behaviour that is not in the facts.
- If something is unknown, say "Not determined from the code" — never guess.
- Use these sections as Markdown headings: What it does; How it works (high level); Main components; Data & integrations; Limitations.
- Be specific and concise (max ~450 words)."""


def _known_names(ctx: RepoContext) -> tuple[set[str], set[str], set[str]]:
    names: set[str] = set()
    short: set[str] = set()
    apis: set[str] = set()
    for n in ctx.graph.nodes.values():
        if n["label"] == "API":
            apis.add(n["name"])
            apis.add(re.sub(r"\{[^}]+\}|:\w+", "*", n["name"]))
            continue
        if n["label"] in ("Class", "Function", "Module", "Service", "Table", "Topic", "Database", "Infrastructure"):
            names.add(n["name"].lower())
            short.add(n["name"].split(".")[-1].lower())
    for f in ctx.frameworks:
        names.add(f["name"].lower())
    for d in ctx.dependencies:
        names.add(d["name"].lower())
        short.add(d["name"].split(":")[-1].split("/")[-1].lower())
    return names, short, apis


def verify_mentions(ctx: RepoContext, text: str) -> dict:
    """Check every code reference in `backticks` against the repository; return verified / unverified lists."""
    names, short, apis = _known_names(ctx)
    paths = list(ctx.file_info)
    verified, unverified = [], []
    for raw in dict.fromkeys(_CODE_SPAN.findall(text)):
        token = raw.strip().rstrip(".,;:")
        api = _API.match(token)
        if api:
            norm = re.sub(r"\{[^}]+\}|:\w+", "*", token)
            (verified if token in apis or norm in apis else unverified).append(token)
            continue
        if _PATHLIKE.search(token) and " " not in token:
            path = re.sub(r":\d+(-\d+)?$", "", token).lstrip("./")
            ok = path in ctx.file_info or any(p.endswith("/" + path) or p == path for p in paths)
            (verified if ok else unverified).append(token)
            continue
        ident = re.sub(r"\(.*\)$", "", token)
        if " " in ident or not re.match(r"^[A-Za-z_$][\w.$:\-]*$", ident):
            continue  # commands, prose or values — not a code reference
        if not (re.search(r"[A-Z_.]", ident) or ident.lower() in names):
            continue  # plain lowercase words are not treated as code identifiers
        low = ident.lower()
        last = low.split(".")[-1]
        ok = low in names or last in short or low in short or _in_config(ctx, ident)
        (verified if ok else unverified).append(token)
    total = len(verified) + len(unverified)
    return {"verified": verified, "unverified": unverified, "score": round(100 * len(verified) / total) if total else 100}


def _in_config(ctx: RepoContext, ident: str) -> bool:
    if not ident.isupper() or len(ident) < 3:
        return False
    tokens = getattr(ctx, "_upper_tokens", None)
    if tokens is None:
        tokens = set()
        for f in ctx.files[:20000]:
            if f.category in ("config", "infra", "source", "build"):
                tokens.update(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", ctx.read(f.path, 200_000)))
        ctx._upper_tokens = tokens  # type: ignore[attr-defined]
    return ident in tokens


def grounded_narrative(ctx: RepoContext, llm, report: dict) -> dict:
    facts = report["facts"]
    budget = get_settings().llm_max_context_chars
    lines, used = [], 0
    for fid, f in facts.items():
        ref = f["evidence"][0] if f["evidence"] else None
        line = f"{fid}: {f['text']}" + (f" (source: {ref['path']}:{ref['line']})" if ref else "")
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line)
    lines += [f"L{i + 1}: LIMITATION — {x}" for i, x in enumerate(report.get("limitations", []))]
    raw = llm.complete(NARRATIVE_PROMPT, [("user", "FACTS:\n" + "\n".join(lines) + f"\n\nRepository: {report['repository']}")], 1600)
    return enforce_citations(ctx, raw, set(facts))


def enforce_citations(ctx: RepoContext, text: str, valid_ids: set[str]) -> dict:
    kept_lines, dropped = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept_lines.append(line)
            continue
        prefix = re.match(r"^\s*(?:[-*+]|\d+\.)\s+", line)
        body = line[prefix.end():] if prefix else line
        kept_sentences = []
        for sentence in _SENTENCE_SPLIT.split(body):
            cites = set(_CITE.findall(sentence))
            if (cites and cites <= valid_ids) or "Not determined from the code" in sentence or re.search(r"\[L\d+\]", sentence):
                kept_sentences.append(sentence)
            elif sentence.strip():
                dropped.append(sentence.strip())
        if kept_sentences:
            kept_lines.append((prefix.group(0) if prefix else "") + " ".join(kept_sentences))
    clean = "\n".join(kept_lines).strip()
    cited = sorted(set(_CITE.findall(clean)) & valid_ids, key=lambda x: int(x[1:]))
    check = verify_mentions(ctx, clean)
    return {"text": clean, "cited_facts": cited, "dropped_unsupported": dropped, "mentions": check}
