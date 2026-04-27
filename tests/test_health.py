from fastapi.testclient import TestClient

from adv_lens.app.main import app


def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "components" in body
    assert "hitl_enabled" in body["components"]
