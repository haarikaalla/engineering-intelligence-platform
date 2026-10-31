# Engineering Intelligence Platform

Paste any GitHub / GitLab / Bitbucket repository URL (public or private) — or upload a source archive — and get
architecture discovery, repository understanding, purpose & adoption guidance, impact analysis, security, PR and
test intelligence, a knowledge graph and an AI assistant over the code.

Works on Java/Spring monoliths, .NET, Python, Node.js/TypeScript, Go, multi-module and microservice repositories.

## Dashboard

| Tab | What you get |
|---|---|
| **Overview** | summary, tech stack, languages, frameworks, module breakdown, entry points, technical debt |
| **Deep Report** | evidence-backed report — what the project does and how it works (stack, startup, modules, API catalog, request flows, events, data model with fields, key components, security, configuration, complexity) where every fact links to `file:line`; limitations listed explicitly; Markdown export; optional AI narrative restricted to cited facts |
| **Purpose & Adoption** | what the repo is and what problem it solves, domain, key features, how to run it, env configuration, integration points, enterprise-readiness checklist + license guidance, *reuse / develop-next / improve* suggestions, **"fit for our purpose"** goal analysis, onboarding guide (reading order, key files, glossary), **large-file explainer** |
| **Architecture** | style detection, layers, Mermaid architecture + data-flow diagrams, service dependency graph, business capabilities, messaging, cycles, god classes, ownership, hotspots |
| **Intelligence Chat** | LangGraph agent grounded in the knowledge graph + hybrid RAG with source citations |
| **Security** | secrets, injection, auth weaknesses, crypto, misconfiguration, dependency CVEs (OSV.dev) |
| **Pull Requests** | GitHub PR URL or diff → risk score, impact, security/architecture impact, missing tests, review comments, AI review |
| **Impact Analysis** | changed file/symbol → impacted services, APIs, databases, tests, dependency chains, impact graph |
| **Test Intelligence** | coverage estimate, regression risk, recommendations, unit/integration test generation |
| **Knowledge Graph** | Repository → Services → APIs → Classes → Functions → Databases viewer, search, neighbourhood exploration |

## Quick start

```bash
cp .env.example .env          # set passwords; optionally OPENAI_API_KEY / ANTHROPIC_API_KEY
docker compose up -d --build
open http://localhost:3000
```

No Docker? See [docs/SETUP.md](docs/SETUP.md) — the backend runs with zero infrastructure (SQLite, embedded graph & vector store, offline reasoning).

## Documentation

* [Architecture & implementation plan](docs/ARCHITECTURE.md)
* [Setup guide](docs/SETUP.md)
* [Deployment guide (Docker / Kubernetes)](docs/DEPLOYMENT.md)
* [Example workflow on an enterprise repository](docs/EXAMPLE_WORKFLOW.md)

## Project layout

```
backend/    FastAPI, ingestion pipeline, parsers, knowledge graph, RAG, analysis engines, LangGraph agent, tests
frontend/   Next.js 14 + TypeScript + Tailwind dashboard (server-side /api proxy)
deploy/k8s/ Kustomize manifests
docs/       architecture, setup, deployment, example workflow
```
