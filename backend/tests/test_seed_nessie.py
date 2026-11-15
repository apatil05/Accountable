"""Seed script: address fix-up and resumability, against a stubbed Nessie."""

import json

import httpx
import pytest

from app import config
from scripts import seed_nessie

DATA = {
    "customer": {"first_name": "Demo", "last_name": "User"},
    "merchants": [
        {"_id": "m_a", "name": "A", "category": ["Food", "Coffee"], "address": {"state": "", "zip": ""}, "geocode": {"lat": 1, "lng": 2}},
        {"_id": "m_b", "name": "B", "category": "Gas", "address": None, "geocode": {"lat": 1, "lng": 2}},
    ],
    "purchases": [
        {"merchant_id": "m_a", "purchase_date": "2026-10-01", "amount": 5, "description": ""},
        {"merchant_id": "m_b", "purchase_date": "2026-10-02", "amount": 7, "description": "gas"},
    ],
}


class FakeNessie:
    """Records POSTs, hands out sequential ids, and can be told to fail on a path."""

    def __init__(self, fail_on: str | None = None):
        self.fail_on, self.posts = fail_on, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.posts.append((request.url.path, body))
        if self.fail_on and request.url.path == self.fail_on:
            return httpx.Response(400, text="nope")
        return httpx.Response(201, json={"objectCreated": {"_id": f"id{len(self.posts)}"}})


@pytest.fixture(autouse=True)
def nessie_config(monkeypatch):
    monkeypatch.setattr(config, "NESSIE_API_KEY", "k")
    monkeypatch.setattr(config, "NESSIE_BASE_URL", "https://nessie.test")


def _client(fake):
    return httpx.Client(transport=httpx.MockTransport(fake))


def test_blank_address_parts_are_filled_in():
    assert seed_nessie.complete_address({"state": "", "zip": "", "city": "Boston"}) == {
        "street_number": "1", "street_name": "Main St", "city": "Boston", "state": "MA", "zip": "01002",
    }
    assert seed_nessie.complete_address(None)["state"] == "MA"


def test_merchants_are_posted_with_valid_addresses_and_string_categories():
    fake = FakeNessie()
    seed_nessie.seed(_client(fake), DATA, seed_nessie.new_record(), lambda r: None)
    merchants = [body for path, body in fake.posts if path == "/merchants"]
    assert [m["category"] for m in merchants] == ["Food", "Gas"]
    assert all(len(m["address"]["state"]) == 2 and m["address"]["zip"] for m in merchants)


def test_every_purchase_is_created_with_a_status():
    # Nessie's own GET /accounts/{id}/purchases returns HTTP 400 for purchases created without one.
    fake = FakeNessie()
    seed_nessie.seed(_client(fake), DATA, seed_nessie.new_record(), lambda r: None)
    purchases = [body for path, body in fake.posts if path.endswith("/purchases")]
    assert len(purchases) == 2
    assert all(p["status"] == "completed" for p in purchases)


def test_purchase_status_keeps_nessie_statuses_and_defaults_the_rest():
    statuses = [seed_nessie.purchase_status({"status": s}) for s in ("pending", "cancelled", "executed", None)]
    assert statuses == ["pending", "cancelled", "completed", "completed"]


def test_a_complete_run_creates_everything_once():
    fake = FakeNessie()
    record = seed_nessie.seed(_client(fake), DATA, seed_nessie.new_record(), lambda r: None)
    assert record["complete"] and len(record["merchant_ids"]) == 2 and len(record["purchase_ids"]) == 2
    assert len(fake.posts) == 1 + 1 + 2 + 2


def test_a_failed_run_resumes_without_duplicating_anything():
    saved = []
    record = seed_nessie.new_record()
    first = FakeNessie(fail_on="/merchants")
    with pytest.raises(RuntimeError, match="HTTP 400"):
        seed_nessie.seed(_client(first), DATA, record, lambda r: saved.append(json.loads(json.dumps(r))))
    assert "customer_id" in saved[-1] and "account_id" in saved[-1]  # progress survived the failure

    second = FakeNessie()
    seed_nessie.seed(_client(second), DATA, record, lambda r: None)
    paths = [p for p, _ in second.posts]
    assert "/customers" not in paths and not any(p.endswith("/accounts") for p in paths)
    assert paths.count("/merchants") == 2 and record["complete"]
