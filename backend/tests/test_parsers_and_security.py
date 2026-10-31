from app.analysis.security import scan_text
from app.ingestion.git_ops import parse_repo_url
from app.ingestion.manifests import parse_manifest
from app.parsing.registry import parse_source
from tests.sample_repo import FAKE_AWS_KEY, SAMPLE_REPO

ALLOWED = ["github.com", "gitlab.com"]


def test_java_controller_endpoints_combine_class_prefix():
    path = "order-service/src/main/java/com/acme/order/controller/OrderController.java"
    pf = parse_source(path, "Java", SAMPLE_REPO[path])
    assert pf.package == "com.acme.order.controller"
    assert "com.acme.order.service.OrderService" in pf.imports
    names = {s.qualified for s in pf.symbols}
    assert {"OrderController", "OrderController.getOrder", "OrderController.createOrder"} <= names
    routes = {(e.method, e.path, e.handler) for e in pf.endpoints}
    assert ("GET", "/api/orders/{id}", "OrderController.getOrder") in routes
    assert ("POST", "/api/orders", "OrderController.createOrder") in routes
    create = next(s for s in pf.symbols if s.name == "createOrder")
    assert create.complexity >= 2


def test_python_fastapi_endpoints_and_imports():
    path = "notification-service/app/main.py"
    pf = parse_source(path, "Python", SAMPLE_REPO[path])
    assert pf.parser == "ast"
    assert "app.services.notifier" in pf.imports
    assert {(e.method, e.path) for e in pf.endpoints} == {("POST", "/notifications"), ("GET", "/notifications/{nid}")}
    assert pf.entry_point == "FastAPI application"


def test_express_and_db_detection():
    code = """const express = require('express');
const router = express.Router();
router.get('/users/:id', async (req, res) => {
  const rows = await pool.query(`SELECT * FROM users WHERE id = ${req.params.id}`);
  res.json(rows);
});
module.exports = router;
"""
    pf = parse_source("src/routes/users.js", "JavaScript", code)
    assert ("GET", "/users/:id") in {(e.method, e.path) for e in pf.endpoints}
    assert "express" in pf.imports
    assert any(d.kind == "sql" and d.target == "users" for d in pf.db_access)
    findings = scan_text("src/routes/users.js", "JavaScript", code, "source")
    assert any(f.rule_id == "INJ005" for f in findings)


def test_entities_and_messaging_extraction():
    path = "payment-service/src/main/java/com/acme/payment/service/PaymentService.java"
    pf = parse_source(path, "Java", SAMPLE_REPO[path])
    assert [(m.direction, m.topic) for m in pf.messaging] == [("consume", "order-created")]
    model = parse_source("x/Order.java", "Java", SAMPLE_REPO["order-service/src/main/java/com/acme/order/model/Order.java"])
    assert any(d.kind == "entity" and d.target == "orders" for d in model.db_access)


def test_security_rules_detect_and_mask_secrets():
    text = SAMPLE_REPO["notification-service/app/services/notifier.py"].replace("__FAKE_AWS_KEY__", FAKE_AWS_KEY)
    findings = scan_text("notification-service/app/services/notifier.py", "Python", text, "source")
    ids = {f.rule_id for f in findings}
    assert "SEC001" in ids and "INJ001" in ids
    aws = next(f for f in findings if f.rule_id == "SEC001")
    assert FAKE_AWS_KEY not in aws.snippet
    placeholder = scan_text("cfg.py", "Python", 'password = "changeme123"\napi_key = "your_api_key_here"\n', "source")
    assert not [f for f in placeholder if f.category == "secret"]


def test_manifest_parsing_resolves_maven_properties():
    deps = parse_manifest("order-service/pom.xml", SAMPLE_REPO["order-service/pom.xml"])
    log4j = next(d for d in deps if d.name.endswith("log4j-core"))
    assert log4j.version == "2.14.1"
    parent = next(d for d in deps if d.scope == "parent")
    assert parent.version == "2.5.0"


def test_repo_url_validation_rejects_unsafe_input():
    ref = parse_repo_url("https://github.com/acme/shop/tree/main", ALLOWED)
    assert ref.clone_url == "https://github.com/acme/shop.git"
    for bad in ("file:///etc/passwd", "https://evil.example.com/a/b", "https://github.com/--upload-pack=x/y", "git@github.com:a/b.git"):
        try:
            parse_repo_url(bad, ALLOWED)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
