import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

from app.api import intelligence, repositories
from app.config import get_settings
from app.db.database import init_db, session_scope
from app.db.models import Repository
from app.graph.neo4j_store import get_mirror
from app.llm.provider import get_llm
from app.parsing.treesitter_parser import TREE_SITTER_AVAILABLE


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with session_scope() as db:
        db.execute(
            update(Repository)
            .where(Repository.status.in_(["queued", "running"]))
            .values(status="failed", stage="failed", error="Interrupted by server restart — click Re-analyze")
        )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )
    app.include_router(repositories.router)
    app.include_router(intelligence.router)

    @app.get("/api/health", tags=["health"])
    def health() -> dict:
        llm = get_llm()
        try:
            import chromadb  # noqa: F401

            chroma = bool(settings.use_chroma)
        except ImportError:
            chroma = False
        return {
            "status": "ok",
            "capabilities": {
                "tree_sitter": TREE_SITTER_AVAILABLE,
                "llm": llm.name if llm else None,
                "neo4j": get_mirror().enabled,
                "chroma": chroma,
                "embeddings": "openai" if settings.openai_api_key and settings.embedding_provider != "local" else "local",
            },
        }

    return app


app = create_app()
