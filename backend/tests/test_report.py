"""The evidence report must only contain statements backed by real code locations."""
from app.llm.grounding import enforce_citations, verify_mentions
from app.services.context import load_context, source_dir
from tests.test_end_to_end import client, repo_id  # noqa: F401  (shared fixtures)


def test_report_facts_are_backed_by_real_code(client, repo_id):  # noqa: F811
    rep = client.get(f"/api/repositories/{repo_id}/report").json()
    titles = {s["id"] for s in rep["sections"]}
    assert {"what", "stack", "startup", "architecture", "apis", "flows", "integrations", "data", "components", "security", "config", "quality"} <= titles
    root = source_dir(repo_id)
    for fact in rep["facts"].values():
        if fact["kind"] in ("fact", "quote"):
            assert fact["evidence"], fact["text"]
        for e in fact["evidence"]:
            f = root / e["path"]
            assert f.is_file(), e
            assert 1 <= e["line"] <= len(f.read_text(encoding="utf-8").splitlines()) or e["line"] == 1, e
    texts = " ".join(f["text"] for f in rep["facts"].values())
    assert "order management platform" in texts                       # README quote
    assert "Entity `Order` maps to table **orders**" in texts and "total: double" in texts
    assert "Request flow: `GET /api/orders/{id}`" in texts
    assert "`OrderController.createOrder()` delegates to `OrderService.placeOrder()`" in texts
    assert "Event `order-created`: published by order-service; consumed by payment-service" in texts
    assert "SuperS3cretPassw0rd" not in texts                          # config values are never exposed
    assert rep["limitations"]
    md = client.get(f"/api/repositories/{repo_id}/report/markdown")
    assert md.status_code == 200 and md.text.startswith("# Repository report")
    assert client.post(f"/api/repositories/{repo_id}/report/narrative").status_code == 409  # no LLM in tests


def test_grounding_rejects_unsupported_and_invented_claims(client, repo_id):  # noqa: F811
    ctx = load_context(repo_id)
    llm_text = (
        "## What it does\n"
        "It manages orders via `OrderController` [F1]. It also uses blockchain ledgers.\n"
        "- `PaymentService` handles charges [F2]\n"
        "- `FraudDetectionEngine` scores risk [F3]\n"
        "- Unknown claim [F99999]"
    )
    out = enforce_citations(ctx, llm_text, {"F1", "F2", "F3"})
    assert "blockchain" not in out["text"] and "F99999" not in out["text"]
    assert "It also uses blockchain ledgers." in out["dropped_unsupported"]
    assert "FraudDetectionEngine" in out["mentions"]["unverified"]
    assert {"OrderController", "PaymentService"} <= set(out["mentions"]["verified"])
    check = verify_mentions(ctx, "See `order-service/src/main/java/com/acme/order/OrderApplication.java:9` and `GET /api/orders/{id}` and `src/Missing.java`")
    assert check["unverified"] == ["src/Missing.java"]
