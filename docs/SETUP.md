# Setup Guide

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11 – 3.13 (3.14 works with `requirements-core.txt`) | backend |
| Node.js | 20+ | dashboard |
| Git | 2.30+ | repository cloning |
| Docker (optional) | 24+ | full stack with PostgreSQL, Neo4j, ChromaDB |

## Option A — Docker Compose (full stack)

```bash
cp .env.example .env     # set POSTGRES_PASSWORD, NEO4J_PASSWORD, optional LLM keys
docker compose up -d --build
```

* Dashboard: http://localhost:3000
* API docs (OpenAPI): http://localhost:8000/docs
* Neo4j Browser: http://localhost:7474 (user `neo4j`)

## Option B — Local development (no infrastructure)

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt          # full stack (Tree-sitter, ChromaDB, Neo4j driver, LangGraph, LangChain)
# or: pip install -r requirements-dev.txt  # minimal: embedded stores + regex/AST parsers + pytest
uvicorn app.main:app --reload --port 8000
```

Defaults: SQLite at `backend/data/eip.db`, workspace at `backend/data/workspace`, embedded graph and NumPy vector
store, offline (non-LLM) reasoning. Every component upgrades automatically when configured:

| Capability | Enable with |
|---|---|
| PostgreSQL | `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/eip` |
| Neo4j mirror | `NEO4J_URI=bolt://host:7687`, `NEO4J_PASSWORD=...` |
| ChromaDB server | `CHROMA_HOST=host` (`pip install chromadb` for embedded persistent mode) |
| Tree-sitter AST | `pip install tree-sitter tree-sitter-language-pack` |
| OpenAI | `OPENAI_API_KEY` (chat, PR review, test generation, executive brief, embeddings) |
| Anthropic Claude | `ANTHROPIC_API_KEY` (preferred when both keys are set; `LLM_PROVIDER` to force) |
| Private repos | per-request token in the UI, or `GITHUB_TOKEN` |
| GitHub Enterprise / self-hosted GitLab | add the host to `ALLOWED_GIT_HOSTS` |
| API protection | `API_KEY` on the backend + `EIP_API_KEY` on the frontend |

`GET /api/health` shows which capabilities are active.

### Frontend

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000, proxies /api to BACKEND_URL (default http://localhost:8000)
```

### Tests

```bash
cd backend
pytest -q
```

The end-to-end suite ingests a sample multi-service repository (Spring Boot order/payment services + a FastAPI
notification service, Kafka, PostgreSQL) and exercises every API.

## Configuration reference

All settings are environment variables (or `.env`) — see [`.env.example`](../.env.example) and
[`backend/app/config.py`](../backend/app/config.py). List values are JSON, e.g. `CORS_ORIGINS='["https://eip.example.com"]'`.

## Security notes

* Access tokens are used once for `git clone` via an HTTP header and are never written to disk or the database.
* Repository URLs are restricted to `https://` on allow-listed hosts (prevents SSRF and git option injection).
* Uploaded archives are checked for path traversal, symlinks and decompression bombs.
* Detected secrets are masked in all API responses.
* The browser never sees the backend API key — the Next.js server route injects it.
