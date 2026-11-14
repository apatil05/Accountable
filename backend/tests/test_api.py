"""HTTP layer. The app's database dependency is overridden with the throwaway test database."""

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from app.main import app, get_database
from app.models import UserProfile
from app.services import ingest, nessie


@pytest.fixture
def client(db):
    ingest.sync_customer(db, "cust_demo")
    app.dependency_overrides[get_database] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_health_reports_database_and_last_sync(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["db"]["connected"] is True
    assert body["last_sync"] is not None


def test_spending_summary(client):
    body = client.get("/api/spending/summary").json()
    assert body["total_spent"] == 2067.62
    assert body["merchants"][0]["merchant_id"] == "amazon"


def test_transactions_expose_brand_ids_in_dollars(client):
    rows = client.get("/api/spending/transactions").json()
    assert len(rows) == 60
    starbucks = next(r for r in rows if r["id"] == "p_015")
    assert (starbucks["merchant_id"], starbucks["amount"], starbucks["purchase_date"]) == ("starbucks", 6.78, "2026-11-08")


def test_profile_round_trip(client):
    assert client.get("/api/profile").json() == UserProfile().model_dump()
    updated = {**UserProfile().model_dump(), "name": "Sam", "prefer_local": False, "stances": "workers first"}
    assert client.put("/api/profile", json=updated).json() == updated
    assert client.get("/api/profile").json() == updated


def test_merchant_ethics_supply_chain_and_alternatives(client):
    assert client.get("/api/merchants/amazon/ethics").json()["merchant_id"] == "amazon"
    assert client.get("/api/merchants/amazon/supply-chain").json()["merchant_id"] == "amazon"
    assert client.get("/api/merchants/starbucks/alternatives").json()


def test_unknown_merchant_is_404(client):
    assert client.get("/api/merchants/nope/ethics").status_code == 404


def test_junk_brand_is_not_researched(client, db):
    db.brands.insert_one({"_id": "junk-demo", "name": "Demo", "categories": ["Other"], "is_junk": True})
    assert client.get("/api/merchants/junk-demo/ethics").status_code == 404


def test_supply_chains_cover_every_demo_brand(client):
    assert len(client.get("/api/supply-chains").json()) == 10


def test_sync_endpoint_is_idempotent(client, db):
    first = client.post("/api/sync").json()
    before = db.purchases.count_documents({})
    second = client.post("/api/sync").json()
    assert (first["purchases"], second["purchases"]) == (60, 60)
    assert db.purchases.count_documents({}) == before


def test_invalid_customer_id_is_rejected(client):
    assert client.get("/api/spending/summary", params={"customer_id": "../etc/passwd"}).status_code == 400


def test_nessie_failure_becomes_502_without_leaking_details(client, monkeypatch):
    def fail(db, customer_id):
        raise nessie.NessieError("Nessie returned HTTP 503")

    monkeypatch.setattr(ingest, "sync_customer", fail)
    response = client.post("/api/sync")
    assert response.status_code == 502
    assert response.json() == {"detail": "Nessie returned HTTP 503"}


def test_database_outage_becomes_503_without_leaking_details(client, monkeypatch):
    def down(db, customer_id):
        raise ServerSelectionTimeoutError("cluster0-shard-00-00.abc12.mongodb.net:27017: timed out")

    monkeypatch.setattr(ingest, "sync_customer", down)
    response = client.post("/api/sync")
    assert response.status_code == 503
    assert response.json() == {"detail": "The database is temporarily unavailable"}
    assert "mongodb.net" not in response.text


@pytest.mark.parametrize(
    "change",
    [
        {"weights": {"labor": 9}},
        {"weights": {"not_a_category": 3}},
        {"stances": "x" * 2001},
        {"trusted_sources": ["a.com"] * 21},
    ],
)
def test_invalid_profiles_are_rejected(client, change):
    body = {**UserProfile().model_dump(), **change}
    assert client.put("/api/profile", json=body).status_code == 422
