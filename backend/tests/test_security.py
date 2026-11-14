"""Pre-hosting hardening: admin token + cooldown on sync, no customer switching, CORS by env.
These tests need no database: the DB dependency is stubbed and sync is faked."""

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.services import ingest

TOKEN = "t" * 32
AUTH = {"X-Admin-Token": TOKEN}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    monkeypatch.setattr(config, "ALLOW_CUSTOMER_OVERRIDE", False)
    monkeypatch.setattr(main, "sync_guard", main.SyncGuard(cooldown_seconds=30))
    monkeypatch.setattr(ingest, "sync_customer", lambda db, cid: {"customer_id": cid, "purchases": 0})
    main.app.dependency_overrides[main.get_database] = lambda: None
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


# ---------- POST /api/sync ----------
def test_sync_without_a_token_is_rejected(api):
    assert api.post("/api/sync").status_code == 401


def test_sync_with_a_wrong_token_is_rejected(api):
    assert api.post("/api/sync", headers={"X-Admin-Token": "nope"}).status_code == 401


def test_sync_with_the_admin_token_runs(api):
    response = api.post("/api/sync", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["customer_id"] == "cust_demo"


def test_sync_over_http_is_disabled_when_no_admin_token_is_configured(api, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    response = api.post("/api/sync", headers={"X-Admin-Token": ""})
    assert response.status_code == 403
    assert "scripts.sync" in response.json()["detail"]


def test_a_second_sync_within_the_cooldown_is_refused(api):
    assert api.post("/api/sync", headers=AUTH).status_code == 200
    assert api.post("/api/sync", headers=AUTH).status_code == 429


def test_cooldown_applies_even_after_a_failed_sync(api, monkeypatch):
    def boom(db, cid):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(ingest, "sync_customer", boom)
    with pytest.raises(RuntimeError):
        api.post("/api/sync", headers=AUTH)
    monkeypatch.setattr(ingest, "sync_customer", lambda db, cid: {"customer_id": cid})
    assert api.post("/api/sync", headers=AUTH).status_code == 429


def test_the_admin_may_sync_a_specific_customer(api):
    response = api.post("/api/sync", params={"customer_id": "abc123"}, headers=AUTH)
    assert response.json()["customer_id"] == "abc123"


# ---------- reading other customers' data ----------
@pytest.mark.parametrize("path", ["/api/spending/summary", "/api/spending/transactions", "/api/supply-chains"])
def test_choosing_a_customer_is_refused_by_default(api, path):
    assert api.get(path, params={"customer_id": "someone_else"}).status_code == 403


def test_listing_all_customers_is_refused_by_default(api):
    assert api.get("/api/customers").status_code == 403


def test_listing_customers_works_when_override_is_enabled(api, monkeypatch):
    monkeypatch.setattr(config, "ALLOW_CUSTOMER_OVERRIDE", True)
    assert api.get("/api/customers").json()[0]["_id"] == "cust_demo"


# ---------- CORS ----------
def _preflight(app, origin: str):
    return TestClient(app).options(
        "/api/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"}
    )


def test_production_only_allows_the_configured_frontend(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "FRONTEND_ORIGINS", ("https://accountable.example",))
    app = main.create_app()
    assert _preflight(app, "https://accountable.example").headers.get("access-control-allow-origin") == "https://accountable.example"
    assert "access-control-allow-origin" not in _preflight(app, "http://localhost:5173").headers


def test_production_hides_the_api_docs(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    client = TestClient(main.create_app())
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_development_keeps_the_api_docs(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "development")
    assert TestClient(main.create_app()).get("/openapi.json").status_code == 200


def test_development_also_allows_the_local_vite_server(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "development")
    monkeypatch.setattr(config, "FRONTEND_ORIGINS", ("https://accountable.example",))
    app = main.create_app()
    assert _preflight(app, "http://localhost:5173").headers.get("access-control-allow-origin") == "http://localhost:5173"
