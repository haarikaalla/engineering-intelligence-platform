# Architecture & Implementation Plan

## 1. Architecture analysis

The platform has to ingest *arbitrary* repositories — from a 50-file Python
service to a multi-million-line Java monolith — and answer questions that need
both **semantic** context (what does this code mean?) and **structural**
context (what depends on what?). That drives three design choices:

1. **Language-agnostic intermediate model.** Every parser (Tree-sitter, Python
   `ast`, regex fallback) emits the same `ParsedFile` structure (symbols,
   imports, calls, endpoints, DB access). All downstream engines operate on this
   model, so adding a language means adding one parser, not touching engines.
2. **Dual index: vectors + graph.** RAG (ChromaDB) answers "where/what"
   questions; the knowledge graph (Neo4j or the embedded graph store) answers
   "who depends on / what breaks" questions. The chat agent (LangGraph) routes
   between them and fuses the results.
3. **Graceful degradation.** Every external system is optional at runtime:
   Neo4j → embedded graph, ChromaDB → embedded NumPy vector store, OpenAI/Claude
   → deterministic offline reasoning, Tree-sitter → regex parser,
   PostgreSQL → SQLite. Production uses the full stack; dev laptops and CI run
   with zero infrastructure.

```mermaid
flowchart LR
    subgraph Client
        UI[Next.js Dashboard<br/>8 tabs]
    end
    subgraph API[FastAPI Backend]
        R[REST API] --> SVC[Repository Service]
        SVC --> PIPE[Ingestion Pipeline]
        SVC --> AGENT[LangGraph Chat Agent]
        SVC --> ENG[Analysis Engines<br/>Architecture / Security / Impact / PR / Tests / Scores]
    end
    subgraph Ingestion
        PIPE --> CL[Git Cloner]
        PIPE --> SC[Scanner + Language/Framework Detection]
        PIPE --> PA[Parsers<br/>Tree-sitter / AST / Regex]
        PIPE --> KG[Graph Builder]
        PIPE --> RAG[Chunker + Embedder]
    end
    subgraph Storage
        PG[(PostgreSQL<br/>metadata, results, chat)]
        NEO[(Neo4j<br/>knowledge graph)]
        CH[(ChromaDB<br/>embeddings)]
        FS[(Workspace volume<br/>clones + artifacts)]
    end
    subgraph LLM
        OAI[OpenAI]
        ANT[Anthropic Claude]
    end
    UI -->|/api| R
    SVC --> PG
    KG --> NEO
    RAG --> CH
    CL --> FS
    AGENT --> OAI
    AGENT --> ANT
    AGENT --> CH
    AGENT --> NEO
```

## 2. Ingestion pipeline

```mermaid
sequenceDiagram
    participant U as User
    participant API as FastAPI
    participant P as Pipeline (worker thread)
    participant G as Git
    participant DB as PostgreSQL
    U->>API: POST /api/repositories {url}
    API->>DB: insert repo (status=queued)
    API-->>U: 202 {id}
    API->>P: submit(repo_id)
    P->>G: git clone --depth N (token via http.extraHeader)
    P->>P: scan files, detect languages / frameworks / modules
    P->>P: parse (Tree-sitter → AST → regex fallback)
    P->>P: build knowledge graph (+ sync Neo4j)
    P->>P: chunk + embed (ChromaDB)
    P->>P: architecture, security, scores, test insights
    P->>DB: persist results (status=ready)
    U->>API: GET /api/repositories/{id} (polling)
```

Stages and their progress are persisted, so the UI shows live progress and
failures are reported with sanitized error messages (tokens are never logged or
stored).

## 3. Knowledge graph model

```mermaid
graph TD
    Repository -->|CONTAINS| Service
    Service -->|CONTAINS| File
    File -->|DEFINES| Class
    Class -->|DEFINES| Function
    File -->|DEFINES| Function
    Service -->|EXPOSES| API
    API -->|HANDLED_BY| Function
    File -->|IMPORTS| File
    File -->|REFERENCES| Class
    Function -->|CALLS| Function
    File -->|ACCESSES| Database
    Service -->|DEPENDS_ON| Service
    Infrastructure -->|DEPLOYS| Service
    TestFile[File:test] -->|TESTS| File
```

Node IDs are deterministic (`file:src/a.py`, `class:src/a.py#User`,
`api:GET /users`), so incremental re-analysis and Neo4j `MERGE` are idempotent.

## 4. Chat agent (LangGraph)

```mermaid
stateDiagram-v2
    [*] --> classify
    classify --> retrieve
    retrieve --> graph_tools
    graph_tools --> generate
    generate --> [*]
```

* **classify** – rule-based intent detection (overview, locate, dependents,
  impact, database, security, tests, general) + entity extraction.
* **retrieve** – hybrid search: vector similarity + keyword re-rank + symbol
  name boosting.
* **graph_tools** – deterministic graph queries chosen by intent (reverse
  dependencies, impact analysis, DB accessors…).
* **generate** – LLM (OpenAI / Claude through LangChain) grounded on the
  retrieved context, with source citations. Without an API key the agent
  returns a structured, citation-backed answer built from the same evidence.

## 5. Scoring model

| Score | Inputs |
|---|---|
| Security | weighted findings (critical 15, high 8, medium 3, low 1) normalised per KLOC |
| Architecture | module dependency cycles, average coupling, god classes, layering |
| Maintainability | function length, cyclomatic complexity, file size, TODO/FIXME density |
| Test | share of source files with associated tests |
| Health | weighted mean (security 30%, architecture 25%, maintainability 25%, tests 20%) |

## 6. Project structure

```
engineering-intelligence-platform/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app factory
│   │   ├── config.py               # pydantic-settings
│   │   ├── api/                    # routers + auth dependency
│   │   ├── db/                     # SQLAlchemy engine + models
│   │   ├── ingestion/              # cloner, scanner, frameworks, pipeline
│   │   ├── parsing/                # tree-sitter, python ast, regex, extractors
│   │   ├── graph/                  # graph store (memory/Neo4j) + builder
│   │   ├── rag/                    # chunker, embeddings, vector store, retriever
│   │   ├── analysis/               # architecture, security, impact, pr, testgen, scoring
│   │   ├── llm/                    # provider + LangGraph agent
│   │   └── services/               # repository service (artifact access)
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                       # Next.js 14 + TS + Tailwind
│   └── src/{app,components,lib}
├── deploy/k8s/                     # Kustomize manifests
├── docker-compose.yml
└── docs/                           # architecture, setup, deployment, example workflow
```

## 7. Scalability notes for enterprise repositories

* Files larger than `MAX_FILE_SIZE_KB` and vendored/generated directories are
  skipped; per-repo file cap `MAX_FILES` guards memory.
* Parsing runs in a process-independent, side-effect-free way and can be moved
  to a worker queue (Celery/Arq) by replacing `JobRunner` — the pipeline is a
  pure function of `repo_id`.
* Embeddings and Neo4j writes are batched (`UNWIND`, 256–500 items).
* Graph artifacts are cached per repo on the workspace volume; API reads are
  served from memory after first load (LRU).
