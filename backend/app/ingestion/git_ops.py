"""Git cloning, upload extraction and commit-history mining."""
from __future__ import annotations

import base64
import os
import re
import shutil
import stat
import subprocess
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

_URL_RE = re.compile(r"^https://(?P<host>[A-Za-z0-9.\-]+(?::\d{1,5})?)/(?P<path>[A-Za-z0-9._~\-/]+?)(?:\.git)?/?$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/\-]{1,200}$")
_TOKEN_USER = {"gitlab.com": "oauth2", "bitbucket.org": "x-token-auth"}


class CloneError(Exception):
    pass


@dataclass
class RepoRef:
    clone_url: str
    host: str
    name: str
    full_name: str


def parse_repo_url(url: str, allowed_hosts: list[str]) -> RepoRef:
    """Validate a repository URL. Only https URLs on allow-listed hosts are accepted (prevents SSRF / option injection)."""
    match = _URL_RE.match(url.strip())
    if not match:
        raise ValueError("Repository URL must be an https:// URL such as https://github.com/org/repo")
    host = match["host"].lower()
    hostname = host.split(":")[0]
    if hostname not in {h.lower() for h in allowed_hosts}:
        raise ValueError(f"Host '{hostname}' is not in ALLOWED_GIT_HOSTS")
    segments = match["path"].split("/")
    if len(segments) < 2 or any(s in ("", ".", "..") or s.startswith("-") for s in segments):
        raise ValueError("Repository URL must include owner and repository name")
    if hostname == "github.com":
        segments = segments[:2]  # strip /tree/main, /pull/1, ... from browser URLs
    elif "-" in segments:  # GitLab browser URLs: /group/project/-/tree/main
        segments = segments[: segments.index("-")]
    full_name = "/".join(segments)
    return RepoRef(clone_url=f"https://{host}/{full_name}.git", host=hostname, name=segments[-1], full_name=full_name)


def _sanitize(text: str, token: str | None) -> str:
    if token:
        text = text.replace(token, "***")
        text = text.replace(base64.b64encode(f"x-access-token:{token}".encode()).decode(), "***")
    return re.sub(r"Authorization: \S+ \S+", "Authorization: ***", text)[-2000:]


def clone_repository(
    ref: RepoRef, dest: Path, branch: str | None, token: str | None, depth: int, timeout: int
) -> str:
    if dest.exists():
        remove_tree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    args = ["git"]
    if token:
        user = _TOKEN_USER.get(ref.host, "x-access-token")
        basic = base64.b64encode(f"{user}:{token}".encode()).decode()
        # Header is passed per-invocation so the token is never written to .git/config.
        args += ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
    args += ["clone", "--no-tags", "--single-branch", "--filter=blob:limit=5m"]
    if depth > 0:
        args += ["--depth", str(depth)]
    if branch:
        if not _BRANCH_RE.match(branch) or branch.startswith("-") or ".." in branch:
            raise ValueError("Invalid branch name")
        args += ["--branch", branch]
    args += ["--", ref.clone_url, str(dest)]

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1", "GIT_ASKPASS": "echo"}
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CloneError(f"git clone timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise CloneError(f"git clone failed: {_sanitize(proc.stderr, token)}")
    return head_commit(dest)


def head_commit(repo_dir: Path) -> str | None:
    if not (repo_dir / ".git").exists():
        return None
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"], capture_output=True, text=True, check=False, timeout=30
    )
    return proc.stdout.strip() or None


def _on_rm_error(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onexc=_on_rm_error)


def extract_zip(archive: Path, dest: Path, max_bytes: int) -> None:
    """Extract an uploaded archive with zip-slip, symlink and zip-bomb protection."""
    if dest.exists():
        remove_tree(dest)
    dest.mkdir(parents=True)
    root = dest.resolve()
    total = 0
    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        # Some Windows tools write backslash separators into archives.
        names = {id(m): m.filename.replace("\\", "/") for m in members}
        # Strip a single top-level folder (GitHub "Download ZIP" layout).
        top = {names[id(m)].split("/", 1)[0] for m in members if names[id(m)].strip("/")}
        strip = len(top) == 1 and all("/" in names[id(m)] or m.is_dir() for m in members)
        for member in members:
            filename = names[id(member)]
            name = filename.split("/", 1)[1] if strip and "/" in filename else filename
            if not name or member.is_dir() or name.endswith("/"):
                continue
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                continue  # symlink
            target = (dest / name).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"Unsafe path in archive: {member.filename}")
            total += member.file_size
            if total > max_bytes:
                raise ValueError("Archive exceeds maximum extracted size")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def mine_history(repo_dir: Path, max_commits: int = 3000) -> dict:
    """Return per-file churn/authors and per-author totals from git history (best effort)."""
    if not (repo_dir / ".git").exists():
        return {"available": False, "files": {}, "authors": []}
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "log", "--no-merges", f"-n{max_commits}", "--format=@@%an|%at", "--name-only"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    files: dict[str, dict] = defaultdict(lambda: {"commits": 0, "authors": Counter(), "last_modified": 0})
    authors: Counter = Counter()
    author, ts = None, 0
    for line in proc.stdout.splitlines():
        if line.startswith("@@"):
            author, _, raw_ts = line[2:].rpartition("|")
            ts = int(raw_ts) if raw_ts.isdigit() else 0
            authors[author] += 1
        elif line.strip() and author:
            entry = files[line.strip()]
            entry["commits"] += 1
            entry["authors"][author] += 1
            entry["last_modified"] = max(entry["last_modified"], ts)
    return {
        "available": True,
        "commit_count": sum(authors.values()),
        "authors": [{"name": n, "commits": c} for n, c in authors.most_common(50)],
        "files": {
            p: {"commits": v["commits"], "authors": dict(v["authors"].most_common(5)), "last_modified": v["last_modified"]}
            for p, v in files.items()
        },
    }
