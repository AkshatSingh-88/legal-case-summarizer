from fastapi.testclient import TestClient

from backend.app.main import app, create_app


def test_app_starts():
    new_app = create_app()
    assert new_app.title == "Legal Case Summarizer"


def test_root():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert "name" in data


def test_health():
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_alias():
    client = TestClient(app)
    resp = client.get("/api/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
