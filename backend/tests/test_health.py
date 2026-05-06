from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app, raise_server_exceptions=False)


def test_health_returns_unified_response() -> None:
    response = client.get("/api/health", headers={"X-Request-ID": "req_test"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req_test"
    assert response.json() == {
        "success": True,
        "data": {
            "status": "ok",
            "service": "webmail-mvp",
            "environment": "development",
        },
        "error": None,
        "request_id": "req_test",
    }


def test_ready_returns_unified_response() -> None:
    response = client.get("/api/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ready"
    assert body["data"]["dependencies"] == {
        "postgres": "configured",
        "redis": "configured",
    }
    assert body["error"] is None
    assert body["request_id"].startswith("req_")


def test_validation_error_uses_unified_error_shape() -> None:
    response = client.get("/api/health", params={"verbose": "bad"}, headers={"X-Request-ID": "req_bad"})

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["request_id"] == "req_bad"
