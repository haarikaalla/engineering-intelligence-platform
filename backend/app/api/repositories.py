from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ReanalyzeRequest, RepoCreate, get_repo, repo_summary, require_api_key
from app.config import get_settings
from app.db.database import get_db
from app.db.models import Repository
from app.graph.neo4j_store import get_mirror
from app.ingestion.git_ops import extract_zip, parse_repo_url, remove_tree
from app.ingestion.pipeline import runner
from app.rag import retriever as rag_retriever
from app.rag.vector_store import open_store
from app.services.context import invalidate_context, rag_dir, repo_dir, source_dir

router = APIRouter(prefix="/api/repositories", tags=["repositories"], dependencies=[Depends(require_api_key)])


@router.get("")
def list_repositories(db: Session = Depends(get_db)) -> list[dict]:
    repos = db.scalars(select(Repository).order_by(Repository.created_at.desc())).all()
    return [repo_summary(r) for r in repos]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_repository(body: RepoCreate, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    try:
        ref = parse_repo_url(body.url, settings.allowed_git_hosts)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    existing = db.scalars(select(Repository).where(Repository.url == ref.clone_url, Repository.branch == body.branch)).first()
    if existing is not None:
        if existing.status in ("failed", "ready") and body.token:
            runner.submit(existing.id, body.token)
        return repo_summary(existing)
    repo = Repository(url=ref.clone_url, name=ref.full_name, branch=body.branch, source="git", status="queued", stage="queued")
    db.add(repo)
    db.commit()
    runner.submit(repo.id, body.token)
    return repo_summary(repo)


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_repository(name: str = Form(..., max_length=200), file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Upload a .zip archive of the repository")
    clean_name = re.sub(r"[^\w.\-/ ]", "", name).strip() or "uploaded-repository"
    repo = Repository(name=clean_name, source="upload", status="queued", stage="extracting")
    db.add(repo)
    db.commit()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        written = 0
        while chunk := file.file.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                db.delete(repo)
                db.commit()
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Archive too large")
            tmp.write(chunk)
    try:
        extract_zip(Path(tmp.name), source_dir(repo.id), max_bytes * 5)
    except Exception as exc:  # noqa: BLE001 - includes zipfile.BadZipFile
        db.delete(repo)
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid archive: {exc}") from exc
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    runner.submit(repo.id)
    return repo_summary(repo)


@router.get("/{repo_id}")
def get_repository(repo: Repository = Depends(get_repo)) -> dict:
    return {**repo_summary(repo), "running": runner.is_active(repo.id)}


@router.post("/{repo_id}/reanalyze", status_code=status.HTTP_202_ACCEPTED)
def reanalyze(body: ReanalyzeRequest | None = None, repo: Repository = Depends(get_repo), db: Session = Depends(get_db)) -> dict:
    if runner.is_active(repo.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Analysis already running")
    repo.status, repo.stage, repo.progress, repo.error = "queued", "queued", 0, None
    db.commit()
    runner.submit(repo.id, body.token if body else None)
    return repo_summary(repo)


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_repository(repo: Repository = Depends(get_repo), db: Session = Depends(get_db)) -> None:
    if runner.is_active(repo.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Analysis is running")
    try:
        manifest_dir = rag_dir(repo.id)
        if (manifest_dir / "manifest.json").exists():
            backend = json.loads((manifest_dir / "manifest.json").read_text()).get("backend")
            open_store(repo.id, manifest_dir, backend).delete()
    except Exception:  # noqa: BLE001 - best effort cleanup
        pass
    get_mirror().delete(repo.id)
    invalidate_context(repo.id)
    rag_retriever.invalidate(repo.id)
    remove_tree(repo_dir(repo.id))
    db.delete(repo)
    db.commit()
