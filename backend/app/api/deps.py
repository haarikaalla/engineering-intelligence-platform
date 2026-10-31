from __future__ import annotations

import hmac
from typing import Literal

from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import get_db
from app.db.models import Repository


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().api_key
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key")


def get_repo(repo_id: str, db: Session = Depends(get_db)) -> Repository:
    if not repo_id.isalnum() or len(repo_id) > 32:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
    return repo


def get_ready_repo(repo: Repository = Depends(get_repo)) -> Repository:
    if repo.status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Repository analysis is {repo.status}")
    return repo


class RepoCreate(BaseModel):
    url: str = Field(..., max_length=1024)
    branch: str | None = Field(default=None, max_length=200)
    token: str | None = Field(default=None, max_length=500, description="Access token for private repositories. Used once, never stored.")


class ReanalyzeRequest(BaseModel):
    token: str | None = Field(default=None, max_length=500)


class GoalFitRequest(BaseModel):
    goal: str = Field(..., min_length=5, max_length=3000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class ImpactRequest(BaseModel):
    path: str = Field(..., max_length=1024)
    symbol: str | None = Field(default=None, max_length=300)
    max_depth: int = Field(default=4, ge=1, le=8)


class PRRequest(BaseModel):
    diff: str | None = Field(default=None, max_length=5_000_000)
    pr_url: str | None = Field(default=None, max_length=500)
    token: str | None = Field(default=None, max_length=500)
    ai_review: bool = True


class TestGenRequest(BaseModel):
    path: str = Field(..., max_length=1024)
    symbol: str | None = Field(default=None, max_length=300)
    kind: Literal["unit", "integration"] = "unit"
    use_llm: bool = True


def repo_summary(repo: Repository) -> dict:
    return {
        "id": repo.id,
        "name": repo.name,
        "url": repo.url,
        "branch": repo.branch,
        "source": repo.source,
        "status": repo.status,
        "stage": repo.stage,
        "progress": repo.progress,
        "error": repo.error,
        "commit_sha": repo.commit_sha,
        "created_at": repo.created_at.isoformat() if repo.created_at else None,
        "updated_at": repo.updated_at.isoformat() if repo.updated_at else None,
        "scores": repo.scores and {k: repo.scores.get(k) for k in ("health", "architecture", "security", "maintainability", "test")},
        "languages": list((repo.overview or {}).get("languages", {}).keys())[:5],
    }
