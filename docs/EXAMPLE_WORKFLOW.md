# Example Workflow — Understanding and Adopting an Enterprise Repository

This walkthrough uses real output from analyzing
[`spring-projects/spring-petclinic`](https://github.com/spring-projects/spring-petclinic) and shows how the same flow
applies to large codebases such as [`dotnet/eShop`](https://github.com/dotnet/eShop) (microservices, .NET) or an
internal Java monolith.

## 1. Ingest

Dashboard → paste `https://github.com/spring-projects/spring-petclinic` → **Analyze repository**
(private repo: add a token — used once, never stored).

```bash
curl -X POST http://localhost:8000/api/repositories \
  -H "Content-Type: application/json" \
  -d '{"url": "https://github.com/spring-projects/spring-petclinic"}'
```

Pipeline stages are shown live: cloning → scanning → parsing → knowledge graph → embeddings → analysis → security.

## 2. What is it and what does it solve? (Purpose & Adoption tab)

| Insight | Result |
|---|---|
| Project type | Reference / sample application |
| Domain | Healthcare / clinic (owners, pets, vets, visits) |
| What it solves | Manages owners, pets, specialties and vets through 17 HTTP endpoints; capabilities: owners, vets |
| How to run | `./mvnw spring-boot:run`, `./gradlew bootRun`, `docker compose up -d`, `./mvnw spring-boot:build-image` |
| Enterprise readiness | 86/100 — tests 80 %, GitHub Actions CI, Kubernetes manifests, Actuator; **gaps: no auth framework, no OpenAPI contract, no DB migration tool** |
| License | Apache-2.0 — safe for commercial use; keep NOTICE |

Suggestions generated for the team:

* **Reuse** — extract `model` as a shared library (used by owner/vet modules, no outbound deps); build on core
  abstractions `Pet`, `Owner`, `PetType`; consume the *owners* capability via its API.
* **Develop next** — enterprise SSO (OIDC) + RBAC, publish an OpenAPI contract, versioned DB migrations (Flyway),
  caching for read-heavy entities, API versioning.

### Fit for *our* purpose

Goal entered: *"We want a clinic appointment booking system for pet owners with vet schedules, billing and SMS reminders"*

* Fit **29 %** — *weak fit: use as a reference rather than a base*
* Covered: clinic, owner, pet, vet · Gaps: appointment, booking, schedules, billing, reminders
* Plan: reuse modules `owner`, `vet`; integrate `GET /owners/{ownerId}`…; extend the `owners` data model;
  develop booking/scheduling/billing/reminders; add OAuth2/OIDC before production.

With an LLM key configured an executive brief and tailored adoption advice are added automatically.

## 3. Onboard to a large codebase

* **Reading order**: README → entry point `PetClinicApplication.java` → core abstractions (most-referenced classes)
  → main request flows (`GET /owners/{ownerId}` → `OwnerController.showOwner` → `Owner` → `owners` → mysql)
  → data model → configuration files.
* **Most important files** ranked by fan-in, APIs, entry points and churn — each with an **explain** action.
* **Large file explainer**: role (controller/service/repository/entity), structure, dependencies & dependents,
  endpoints, data access, security findings, complexity hotspots and a suggested decomposition for big classes.
* **Glossary** of domain terms with the class that defines each.

## 4. Architecture

Detected *Modular monolith* with modules `owner`, `vet`, `system`, `model`; Mermaid architecture and data-flow
diagrams; service dependency graph; data flows such as
`GET /owners/{ownerId} → OwnerController.showOwner → Owner → owners → mysql`.

For microservice repositories (e.g. eShop) the same view shows services, HTTP/compose/event dependencies,
Kafka/RabbitMQ topics with publishers/subscribers, and dependency cycles.

## 5. Ask questions (Intelligence Chat)

* *"What problem does it solve and how can we use it?"* → purpose, features, run commands, readiness gaps, suggestions
* *"Which modules interact with the database?"* → databases, tables/entities and the files touching them
* *"What happens if I change OwnerRepository?"* → impacted controllers, APIs, tables and tests to run
* *"Explain OwnerController.java"* → file explanation with complexity hotspots

## 6. Change safety

* **Impact Analysis**: pick `OwnerRepository.java` → impacted files, services, APIs (`/owners/**`), tables, tests, chains.
* **Pull Requests**: paste a GitHub PR URL or a diff → risk score with factors, new secrets/injection in added lines,
  API/schema/infra changes, cross-module dependencies, missing tests, review comments (+ AI review with an LLM).

## 7. Quality, security, tests

* **Security**: masked secrets, injection, auth/crypto/TLS issues, misconfigurations, dependency CVEs (OSV.dev).
* **Test Intelligence**: coverage estimate, regression-risk ranking, one-click unit/integration test generation
  (JUnit 5 + Mockito / MockMvc, pytest / TestClient, xUnit / WebApplicationFactory, Jest / supertest, Go testing).

## 8. Explore the knowledge graph

Knowledge Graph tab: Repository → Services → APIs → Classes → Functions → Databases per module; search any
symbol and expand its neighbourhood. With Neo4j enabled, query across repositories:

```cypher
// Which APIs end up writing to the 'owners' table?
MATCH p=(a:API)-[:HANDLED_BY]->(:Function)-[:CALLS*0..4]->()-[:ACCESSES]->(t:Table {name:'owners'})
RETURN a.name, length(p) LIMIT 25;

// Modules depending on a given module, across all analyzed repositories
MATCH (m:Node)-[:DEPENDS_ON]->(t:Node {name:'payment-service'}) RETURN m.repo_id, m.name;
```
