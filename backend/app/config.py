from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Engineering Intelligence Platform"
    environment: str = "development"
    log_level: str = "INFO"

    # Security
    api_key: str | None = Field(default=None, description="If set, X-API-Key header is required")
    cors_origins: list[str] = ["http://localhost:3000"]
    allowed_git_hosts: list[str] = ["github.com", "gitlab.com", "bitbucket.org"]
    github_token: str | None = None

    # Storage
    database_url: str = "sqlite:///./data/eip.db"
    workspace_dir: Path = Path("./data/workspace")

    # Neo4j (optional – embedded graph store is used when unset)
    neo4j_uri: str | None = None
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None

    # ChromaDB (optional – embedded NumPy store is used when chromadb is not installed)
    chroma_host: str | None = None
    chroma_port: int = 8000
    use_chroma: bool = True

    # LLM
    llm_provider: str = "auto"  # auto | openai | anthropic | none
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-3-5-sonnet-latest"
    embedding_provider: str = "auto"  # auto | openai | local
    llm_max_context_chars: int = 24000

    # Ingestion limits
    clone_depth: int = 500
    clone_timeout_s: int = 900
    max_file_size_kb: int = 512
    max_files: int = 60000
    max_upload_mb: int = 200
    pipeline_workers: int = 2

    # Dependency vulnerability lookups against OSV.dev
    enable_osv: bool = True
    osv_timeout_s: int = 20


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    return settings
