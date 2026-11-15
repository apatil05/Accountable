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
            calls["searched"] = params["name"]
            return [
                {"title": "Starbucks Tea Distribution Center", "address": "Kent, WA", "countryCode": "US",
                 "totalShipments": 17, "topSuppliers": ["Wrong Supplier"]},
                {"title": "Starbucks Coffee Trade", "address": "2401 Utah Ave S, Seattle, WA", "countryCode": "US",
                 "totalShipments": 15958, "topSuppliers": ["Carcafe", "Missing in source document"]},
                {"title": "Starbucks Roasting", "address": "York, PA", "countryCode": "US",
                 "totalShipments": 5, "topSuppliers": ["Another Wrong Supplier"]},
            ]
        return [{"title": params["name"], "address": "Calle 72, Bogota", "countryCode": "CO", "totalShipments": 12}]

    monkeypatch.setenv("IMPORTYETI_API_KEY", "test-key")
    monkeypatch.setattr(suppliers, "_iy", fake_iy)
    monkeypatch.setattr(suppliers, "_geocode", lambda _c, address, country=None: (47.58, -122.34))
    monkeypatch.setattr(suppliers, "_locate_address", lambda _c, address: (4.66, -74.06, "Colombia", "COL"))
    monkeypatch.setattr(suppliers, "_nearby_place", lambda _c, lat, lng: NearbyPlace(title="Chapinero", url="https://en.wikipedia.org/wiki/Chapinero"))
    monkeypatch.setattr(suppliers, "_nearby_articles", lambda _c, lat, lng, skip=None, limit=6: [
        NearbyPlace(title="Parque de la 93", url="https://en.wikipedia.org/wiki/Parque_de_la_93", distance_km=0.8)])


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
    assert calls["searched"] == "Starbucks"  # the hand-picked search phrase for Starbucks
    assert body["company_name"] == "Starbucks Coffee Trade"  # the hand-picked record
    assert body["suppliers"][0]["nearby"][0]["title"] == "Parque de la 93"  # articles around the exact point
    assert body["importer"]["nearby"]
    assert body["importer"]["lat"] == 47.58
    assert body["importer"]["place"]["title"] == "Chapinero"  # the company's own address gets a place article too
    assert [s["name"] for s in body["suppliers"]] == ["Carcafe"]  # junk supplier filtered out
    assert body["suppliers"][0]["place"]["title"] == "Chapinero"
    assert body["suppliers"][0]["country"] == "Colombia"  # from the address, not the record's label
    client.get("/api/merchants/starbucks/suppliers")
    assert calls["company"] == 1  # stored after the first lookup


def test_no_search_results_means_no_suppliers(client, monkeypatch):
    _fake_network(monkeypatch, {"company": 0})
    monkeypatch.setattr(suppliers, "_iy", lambda _c, path, **params: [])
    body = client.get("/api/merchants/chipotle/suppliers").json()
    assert body["suppliers"] == [] and body["company_name"] is None


def test_pick_company_takes_most_shipments():
    hits = [{"title": "A", "totalShipments": 3}, {"title": "B", "totalShipments": 900}, {"title": "C"}]
    assert suppliers._pick_company(hits)["title"] == "B"
    assert suppliers._pick_company([]) is None


def test_places_nearby_is_not_a_public_endpoint(client):
    """Unused by the UI, and every new coordinate cost a Wikipedia + ArcGIS call: an open abuse vector."""
    assert client.get("/api/places/nearby?lat=2.5&lng=-75.5").status_code == 404


def test_a_non_brand_needs_no_key(client, db, monkeypatch):
    """A cash withdrawal or test record is never searched, so it answers even without an ImportYeti key."""
    monkeypatch.delenv("IMPORTYETI_API_KEY", raising=False)
    db.brands.insert_one({"_id": "atm", "name": "ATM Withdrawal", "is_junk": True, "categories": []})
    body = client.get("/api/merchants/atm/suppliers")
    assert body.status_code == 200 and body.json()["suppliers"] == []


def test_one_failing_brand_does_not_blank_the_globe(client, db, monkeypatch):
    db.supplier_reports.insert_one({"_id": "nike", "merchant_id": "nike", "suppliers": [], "fetched_at": "2026-11-14"})

    def down(_db, merchant_id, refresh=False):
        if merchant_id == "nike":
            return suppliers.SupplierReport(merchant_id="nike", suppliers=[], fetched_at="2026-11-14")
        raise suppliers.SuppliersUnavailable("ImportYeti down")

    monkeypatch.setattr(suppliers, "get_report", down)
    assert [r.merchant_id for r in suppliers.get_reports(db, ["nike", "starbucks"])] == ["nike"]


def test_importyeti_error_page_is_unavailable_not_a_crash():
    class R:
        status_code = 502
        def json(self): raise ValueError("not json")

    class C:
        def get(self, *_a, **_k): return R()

    with pytest.raises(suppliers.SuppliersUnavailable):
        suppliers._iy(C(), "/company/search", name="x")


def test_importer_place_lookup_failure_still_returns_the_report(client, monkeypatch):
    import httpx
    calls = {"company": 0}
    _fake_network(monkeypatch, calls)

    def timeout(*_a, **_k):
        raise httpx.ReadTimeout("arcgis slow")

    monkeypatch.setattr(suppliers, "_geocode", timeout)
    body = client.get("/api/merchants/starbucks/suppliers").json()
    assert body["company_name"] == "Starbucks Coffee Trade" and body["importer"]["lat"] is None


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


def test_curated_record_missing_falls_back_to_most_shipments(monkeypatch):
    searched = []

    def fake_iy(_client, path, **params):
        searched.append(params["name"])
        if params["name"] == "Apple Inc":  # the hand-picked "Apple" record no longer turns up
            return [{"title": "Apple Tree International", "totalShipments": 9}]
        return [{"title": "Apple Geodis", "totalShipments": 400}, {"title": "Pink Apple", "totalShipments": 31}]

    monkeypatch.setattr(suppliers, "_iy", fake_iy)
    assert suppliers._find_company(None, "Apple")["title"] == "Apple Geodis"
    assert searched == ["Apple Inc", "Apple"]


def test_unknown_company_uses_most_shipments(monkeypatch):
    monkeypatch.setattr(suppliers, "_iy", lambda _c, path, **params: [{"title": "A", "totalShipments": 2}, {"title": "B", "totalShipments": 70}])
    assert suppliers._find_company(None, "Some Brand Nobody Curated")["title"] == "B"


def test_curated_names_ignore_case_and_punctuation():
    assert suppliers._curated()[suppliers._norm("TRADER JOE'S")]["title"] == "Trader Joe S"
    assert suppliers._norm("The Home Depot") == suppliers._norm("Home Depot")
    assert len(suppliers._curated()) >= 200


def test_lookup_endpoint_searches_any_company_and_stores_it(client, monkeypatch):
    calls = {"company": 0}
    _fake_network(monkeypatch, calls)
    body = client.get("/api/suppliers/lookup?name=Starbucks").json()
    assert body["merchant_id"] == "lookup:starbucks" and body["company_name"] == "Starbucks Coffee Trade"
    client.get("/api/suppliers/lookup?name=starbucks")
    assert calls["company"] == 1  # stored: the same company typed again costs no search
    assert client.get("/api/suppliers/lookup?name=a").status_code == 422
