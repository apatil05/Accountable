"""Push our mock merchants + purchases into the real Nessie sandbox.

Owner: Person A. Untested until we have a key. Usage (from backend/):
    python -m scripts.seed_nessie

Creates a customer, a credit card account, the merchants, and the purchases,
then prints the customer id to put in NESSIE_CUSTOMER_ID.
Endpoints per the Nessie docs: POST /customers, /customers/{id}/accounts,
/merchants, /accounts/{id}/purchases. Check responses: Nessie wraps created
objects as {"objectCreated": {...}}.
"""

import json

import httpx

from app import config

BASE = config.NESSIE_BASE_URL
P = {"key": config.NESSIE_API_KEY}


def post(path: str, body: dict) -> dict:
    r = httpx.post(f"{BASE}{path}", params=P, json=body, timeout=15)
    r.raise_for_status()
    return r.json().get("objectCreated", r.json())


def main():
    data = json.loads((config.DATA_DIR / "mock_nessie.json").read_text())
    cust = post("/customers", {
        "first_name": "Demo", "last_name": "User",
        "address": {"street_number": "1", "street_name": "Main St", "city": "Amherst", "state": "MA", "zip": "01002"},
    })
    acct = post(f"/customers/{cust['_id']}/accounts", {
        "type": "Credit Card", "nickname": "Demo card", "rewards": 0, "balance": 5000,
    })
    id_map = {}
    for m in data["merchants"]:
        created = post("/merchants", {
            "name": m["name"], "category": m["category"], "address": m["address"], "geocode": m["geocode"],
        })
        id_map[m["_id"]] = created["_id"]
    for p in data["purchases"]:
        post(f"/accounts/{acct['_id']}/purchases", {
            "merchant_id": id_map[p["merchant_id"]], "medium": "balance",
            "purchase_date": p["purchase_date"], "amount": p["amount"], "description": p["description"] or "purchase",
        })
    print("NESSIE_CUSTOMER_ID=", cust["_id"])
    print("Merchant id map (mock -> nessie):", json.dumps(id_map, indent=2))


if __name__ == "__main__":
    main()
