"""Real suppliers (ImportYeti) + nearby places (Wikipedia). No network: the HTTP calls are faked."""

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_database
from app.models import NearbyPlace
from app.services import ingest, suppliers


@pytest.fixture
def client(db):
    ingest.sync_customer(db, "cust_demo")
    app.dependency_overrides[get_database] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _fake_network(monkeypatch, calls):
    def fake_iy(_client, path, **params):
        assert path in suppliers.FREE_PATHS
        if path == "/company/search":
            calls["company"] += 1
            return [{"title": "Starbucks Coffee Trade", "address": "2401 Utah Ave S, Seattle, WA", "countryCode": "US",
                     "topSuppliers": ["Carcafe", "Missing in source document"]}]
        return [{"title": params["name"], "address": "Calle 72, Bogota", "countryCode": "CO", "totalShipments": 12}]

    monkeypatch.setenv("IMPORTYETI_API_KEY", "test-key")
    monkeypatch.setattr(suppliers, "_iy", fake_iy)
    monkeypatch.setattr(suppliers, "_geocode", lambda _c, address, country=None: (47.58, -122.34))
    monkeypatch.setattr(suppliers, "_locate_address", lambda _c, address: (4.66, -74.06, "Colombia", "COL"))
    monkeypatch.setattr(suppliers, "_nearby_place", lambda _c, lat, lng: NearbyPlace(title="Chapinero", url="https://en.wikipedia.org/wiki/Chapinero"))


def test_only_free_importyeti_endpoints_are_allowed():
    with pytest.raises(suppliers.PaidEndpointBlocked):
        suppliers._iy(None, "/company/some-key/shipments")


def test_suppliers_503_when_key_missing(client, monkeypatch):
    monkeypatch.delenv("IMPORTYETI_API_KEY", raising=False)
    assert client.get("/api/merchants/starbucks/suppliers").status_code == 503


def test_suppliers_unknown_merchant_404(client):
    assert client.get("/api/merchants/not-a-brand/suppliers").status_code == 404


def test_report_is_real_filtered_and_stored(client, monkeypatch):
    calls = {"company": 0}
    _fake_network(monkeypatch, calls)
    body = client.get("/api/merchants/starbucks/suppliers").json()
    assert body["company_name"] == "Starbucks Coffee Trade"
    assert body["importer"]["lat"] == 47.58
    assert body["importer"]["place"]["title"] == "Chapinero"  # the company's own address gets a place article too
    assert [s["name"] for s in body["suppliers"]] == ["Carcafe"]  # junk supplier filtered out
    assert body["suppliers"][0]["place"]["title"] == "Chapinero"
    assert body["suppliers"][0]["country"] == "Colombia"  # from the address, not the record's label
    client.get("/api/merchants/starbucks/suppliers")
    assert calls["company"] == 1  # stored after the first lookup


def test_brand_without_trusted_record_gets_no_suppliers(client, monkeypatch):
    calls = {"company": 0}
    _fake_network(monkeypatch, calls)
    body = client.get("/api/merchants/chipotle/suppliers").json()
    assert body["suppliers"] == [] and body["company_name"] is None
    assert calls["company"] == 0  # never guesses a match


def test_places_nearby_validates_and_degrades_gracefully(client, monkeypatch):
    assert client.get("/api/places/nearby?lat=95&lng=0").status_code == 422

    def boom(_db, _lat, _lng):
        raise RuntimeError("wikipedia down")

    monkeypatch.setattr(suppliers, "nearby_place", boom)
    r = client.get("/api/places/nearby?lat=2.5&lng=-75.5")
    assert r.status_code == 200 and r.json() is None


def test_address_must_name_the_country_it_lands_in(monkeypatch):
    class R:
        def __init__(self, cand): self.cand = cand
        def json(self): return {"candidates": [self.cand]}

    class C:
        def __init__(self, cand): self.cand = cand
        def get(self, *_a, **_k): return R(self.cand)

    shanghai = {"score": 73.5, "location": {"x": 121.47, "y": 31.23}, "attributes": {"CntryName": "China", "Region": "Shanghai", "Country": "CHN"}}
    kansas = {"score": 75.2, "location": {"x": -96.83, "y": 39.03}, "attributes": {"CntryName": "United States", "Region": "Kansas", "Country": "USA"}}
    assert suppliers._locate_address(C(shanghai), "338 Nanjing Road Shanghai China O/B Of Yixing Garment")[2] == "China"
    assert suppliers._locate_address(C(kansas), "Junction City Towersuite No18 0809") is None
