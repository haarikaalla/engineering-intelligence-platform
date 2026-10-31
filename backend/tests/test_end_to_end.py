"""End-to-end: ingest the sample multi-service repository and exercise every API surface."""
import pytest
from fastapi.testclient import TestClient

from app.db.database import session_scope
from app.db.models import Repository
from app.ingestion.pipeline import run_pipeline
from app.main import app
from app.services.context import source_dir
from tests.sample_repo import FAKE_STRIPE_KEY, write_sample_repo


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def repo_id(client):
    with session_scope() as db:
        repo = Repository(name="acme/shop", source="upload", status="queued")
        db.add(repo)
        db.flush()
        rid = repo.id
    write_sample_repo(source_dir(rid))
    run_pipeline(rid)
    return rid


def test_pipeline_ready(client, repo_id):
    r = client.get(f"/api/repositories/{repo_id}").json()
    assert r["status"] == "ready", r["error"]
    assert r["scores"]["health"] > 0


def test_overview(client, repo_id):
    ov = client.get(f"/api/repositories/{repo_id}/overview").json()
    assert "Java" in ov["languages"] and "Python" in ov["languages"]
    fw = {f["name"] for f in ov["frameworks"]}
    assert {"Spring Boot", "FastAPI", "Docker Compose"} <= fw
    mods = {m["name"]: m for m in ov["modules"]}
    assert mods["order-service"]["kind"] == "service"
    assert "payment-service" in mods["order-service"]["depends_on"]
    assert mods["notification-service"]["kind"] == "service"
    assert ov["stats"]["apis"] >= 5


def test_architecture(client, repo_id):
    arch = client.get(f"/api/repositories/{repo_id}/architecture").json()
    assert arch["style"]["primary"] == "Microservices"
    assert "Event-driven messaging" in arch["style"]["traits"]
    msg = {m["topic"]: m for m in arch["messaging"]}
    assert msg["order-created"]["publishers"] == ["order-service"]
    assert msg["order-created"]["subscribers"] == ["payment-service"]
    flows = {f["api"]: f for f in arch["data_flows"]}
    assert any(s["name"] == "orders" for s in flows["GET /api/orders/{id}"]["steps"])
    assert arch["diagrams"]["architecture"].startswith("flowchart")


def test_security(client, repo_id):
    sec = client.get(f"/api/repositories/{repo_id}/security").json()
    ids = {f["rule_id"] for f in sec["findings"]}
    assert {"SEC001", "SEC009", "INJ001", "INJ002", "AUTH003", "CRY001", "CFG004", "CFG012"} <= ids
    vulns = {v["id"] for v in sec["dependencies"]["vulnerabilities"]}
    assert "CVE-2021-44228" in vulns and "CVE-2020-14343" in vulns
    assert sec["score"] < 100


def test_impact_analysis(client, repo_id):
    res = client.post(f"/api/repositories/{repo_id}/impact",
                      json={"path": "order-service/src/main/java/com/acme/order/repository/OrderRepository.java"}).json()
    files = {f["file"] for f in res["files"]}
    assert "order-service/src/main/java/com/acme/order/service/OrderService.java" in files
    assert "order-service/src/main/java/com/acme/order/controller/OrderController.java" in files
    assert {a["name"] for a in res["apis"]} >= {"GET /api/orders/{id}", "POST /api/orders"}
    assert any(t["file"].endswith("OrderServiceTest.java") for t in res["tests"])
    assert any(d["name"] == "orders" for d in res["databases"])
    svc = client.post(f"/api/repositories/{repo_id}/impact",
                      json={"path": "order-service/src/main/java/com/acme/order/service/OrderService.java"}).json()
    assert {s["name"] for s in svc["services"]} >= {"order-service", "payment-service"}
    assert any(d["name"] == "orders" and d["impact"] == "via dependency" for d in svc["databases"])


def test_chat(client, repo_id):
    ans = client.post(f"/api/repositories/{repo_id}/chat", json={"message": "Which services depend on PaymentService?"}).json()
    assert ans["intent"] == "dependents"
    assert "PaymentController" in ans["answer"] and "order-service (via compose,http)" in ans["answer"]
    ans = client.post(f"/api/repositories/{repo_id}/chat", json={"message": "Which modules interact with the database?"}).json()
    assert ans["intent"] == "database" and "postgresql" in ans["answer"]
    ans = client.post(f"/api/repositories/{repo_id}/chat", json={"message": "Where is authentication implemented?"}).json()
    assert "auth.py" in ans["answer"]
    hist = client.get(f"/api/repositories/{repo_id}/chat").json()
    assert len(hist) == 6


def test_pr_analysis(client, repo_id):
    diff = """diff --git a/order-service/src/main/java/com/acme/order/service/OrderService.java b/order-service/src/main/java/com/acme/order/service/OrderService.java
--- a/order-service/src/main/java/com/acme/order/service/OrderService.java
+++ b/order-service/src/main/java/com/acme/order/service/OrderService.java
@@ -20,3 +20,6 @@ public class OrderService {
     public Order findOrder(Long id) {
+        String apiKey = "__FAKE_STRIPE_KEY__";
+        // TODO remove
+        System.out.println("lookup " + id);
         return orderRepository.findById(id).orElseThrow();
"""
    res = client.post(f"/api/repositories/{repo_id}/pr", json={"diff": diff.replace("__FAKE_STRIPE_KEY__", FAKE_STRIPE_KEY)}).json()
    assert res["stats"]["additions"] == 3
    assert any(f["rule_id"] == "SEC004" and f["line"] == 21 for f in res["security"]["findings"])
    assert res["missing_tests"] and res["risk"]["score"] > 20
    assert res["impact"]["summary"]["impacted_apis"] >= 1


def test_tests_and_graph(client, repo_id):
    gen = client.post(f"/api/repositories/{repo_id}/tests/generate",
                      json={"path": "order-service/src/main/java/com/acme/order/service/OrderService.java", "kind": "unit"}).json()
    assert "@InjectMocks" in gen["code"] and "OrderRepository orderRepository" in gen["code"]
    integ = client.post(f"/api/repositories/{repo_id}/tests/generate",
                        json={"path": "notification-service/app/main.py", "kind": "integration"}).json()
    assert "client.post(\"/notifications\")" in integ["code"]
    insights = client.get(f"/api/repositories/{repo_id}/tests/insights").json()
    assert insights["coverage_estimate"]["tested_files"] >= 1
    g = client.get(f"/api/repositories/{repo_id}/graph", params={"view": "services"}).json()
    labels = {n["label"] for n in g["nodes"]}
    assert {"Repository", "Service", "Database", "Topic"} <= labels
    h = client.get(f"/api/repositories/{repo_id}/graph", params={"view": "hierarchy", "module": "order-service"}).json()
    assert {"API", "Class", "Function"} <= {n["label"] for n in h["nodes"]}


def test_purpose_and_adoption(client, repo_id):
    p = client.get(f"/api/repositories/{repo_id}/purpose").json()
    assert p["project_type"]["type"] == "Microservices platform"
    domains = [d["domain"] for d in p["domains"]]
    assert "E-commerce / retail" in domains or "Payments / fintech" in domains
    assert "order management platform" in p["what_it_solves"]
    assert any(s["command"] == "docker compose up -d" for s in p["how_to_run"])
    areas = {c["area"]: c["status"] for c in p["enterprise_readiness"]["checks"]}
    assert areas["CI/CD pipeline"] == "fail" and areas["Containerization"] == "pass"
    assert p["suggestions"]["extend"] and p["onboarding"]["reading_order"]
    assert any(m["module"] == "order-service" and "orders" in m["summary"] for m in p["main_functionality"])

    fit = client.post(f"/api/repositories/{repo_id}/purpose/fit", json={"goal": "We need order checkout with payments and loyalty rewards"}).json()
    assert "order" in fit["covered_terms"] and "loyalty" in fit["gaps"]
    assert fit["matching_apis"] and fit["plan"]

    ex = client.get(f"/api/repositories/{repo_id}/explain",
                    params={"path": "order-service/src/main/java/com/acme/order/service/OrderService.java"}).json()
    assert ex["role"].startswith("Business service")
    assert "order-service/src/main/java/com/acme/order/controller/OrderController.java" in ex["used_by"]
    assert ex["classes"][0]["name"] == "OrderService"

    ans = client.post(f"/api/repositories/{repo_id}/chat", json={"message": "How can we use this for our purpose? Any suggestions?"}).json()
    assert ans["intent"] == "purpose" and "Develop next" in ans["answer"]
