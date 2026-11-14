"""Push our mock merchants + purchases into the real Nessie sandbox.

Owner: Person A. Usage (from backend/):
    python -m scripts.seed_nessie            # first run, or resume an interrupted run
    python -m scripts.seed_nessie --force    # start over with a brand-new customer

Creates a customer, a credit card account, the merchants, and the purchases,
then prints the customer id to put in NESSIE_CUSTOMER_ID.

Progress is journaled to app/data/nessie_seed.local.json (gitignored) after every
created object, so a failure midway resumes instead of creating duplicates. A finished
seed refuses to run again without --force.

Nessie wraps created objects as {"objectCreated": {...}}, takes `category` as a plain
string, and rejects addresses without a 2-letter state and a ZIP. Error output never
includes the request URL because it contains the API key.
"""

import argparse
import json
import sys
from typing import Any, Callable, Mapping

import httpx

from app import config

SEED_RECORD = config.DATA_DIR / "nessie_seed.local.json"
TIMEOUT_SECONDS = 15
PURCHASE_STATUS = "completed"
DEFAULT_ADDRESS = {"street_number": "1", "street_name": "Main St", "city": "Amherst", "state": "MA", "zip": "01002"}


def complete_address(address: Mapping[str, Any] | None) -> dict[str, str]:
    """Nessie rejects blank address parts; fill any missing ones from the demo address."""
    given = address or {}
    return {key: str(given.get(key) or default) for key, default in DEFAULT_ADDRESS.items()}


def post(client: httpx.Client, path: str, body: dict) -> dict:
    response = client.post(f"{config.NESSIE_BASE_URL}{path}", params={"key": config.NESSIE_API_KEY}, json=body)
    if response.status_code >= 400:
        raise RuntimeError(f"POST {path} failed with HTTP {response.status_code}: {response.text[:200]}")
    payload = response.json()
    return payload.get("objectCreated", payload)


def new_record() -> dict:
    return {"merchant_ids": {}, "purchase_ids": [], "complete": False}


def seed(client: httpx.Client, data: dict, record: dict, save: Callable[[dict], None]) -> dict:
    """Create whatever `record` does not already contain, saving after each object.

    `record` is a progress journal that is updated in place; that is what makes
    an interrupted run resumable.
    """
    if "customer_id" not in record:
        customer = post(client, "/customers", {
            "first_name": "Demo", "last_name": "User", "address": complete_address(data["customer"].get("address")),
        })
        record["customer_id"] = customer["_id"]
        save(record)
    if "account_id" not in record:
        account = post(client, f"/customers/{record['customer_id']}/accounts", {
            "type": "Credit Card", "nickname": "Demo card", "rewards": 0, "balance": 5000,
        })
        record["account_id"] = account["_id"]
        save(record)
    for m in data["merchants"]:
        if m["_id"] in record["merchant_ids"]:
            continue
        created = post(client, "/merchants", {
            "name": m["name"],
            "category": m["category"][0] if isinstance(m["category"], list) else m["category"],
            "address": complete_address(m.get("address")),
            "geocode": m["geocode"],
        })
        record["merchant_ids"][m["_id"]] = created["_id"]
        save(record)
    for p in data["purchases"][len(record["purchase_ids"]):]:
        created = post(client, f"/accounts/{record['account_id']}/purchases", {
            "merchant_id": record["merchant_ids"][p["merchant_id"]], "medium": "balance",
            "purchase_date": p["purchase_date"], "amount": p["amount"],
            "description": p["description"] or "purchase",
            # Without a status, Nessie's own GET /accounts/{id}/purchases fails with HTTP 400.
            "status": PURCHASE_STATUS,
        })
        record["purchase_ids"].append(created["_id"])
        save(record)
    record["complete"] = True
    save(record)
    return record


def _save(record: dict) -> None:
    SEED_RECORD.write_text(json.dumps(record, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="start over with a new customer")
    args = parser.parse_args()

    if not config.NESSIE_API_KEY:
        print("NESSIE_API_KEY is not set in backend/.env", file=sys.stderr)
        return 1

    record = json.loads(SEED_RECORD.read_text()) if SEED_RECORD.exists() else new_record()
    if record.get("complete") and not args.force:
        print(f"Already seeded (customer {record['customer_id']}). Use --force to seed again.", file=sys.stderr)
        return 1
    if args.force:
        record = new_record()

    data = json.loads((config.DATA_DIR / "mock_nessie.json").read_text())
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            seed(client, data, record, _save)
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        reason = exc if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"Seeding stopped: {reason}", file=sys.stderr)
        print("Progress was saved. Run the same command again to resume where it stopped.", file=sys.stderr)
        return 1

    print(f"NESSIE_CUSTOMER_ID={record['customer_id']}")
    print(f"Seeded {len(record['merchant_ids'])} merchants and {len(record['purchase_ids'])} purchases.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
