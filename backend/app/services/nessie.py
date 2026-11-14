"""Capital One Nessie client with a mock fallback.

Owner: Person A (Data & Backend).
Mock data in data/mock_nessie.json uses the same shape Nessie returns,
so flipping USE_MOCK_DATA=false should "just work" once keys are set.
"""

import json
from functools import lru_cache

import httpx

from .. import config
from ..models import Merchant, Purchase


@lru_cache
def _mock() -> dict:
    return json.loads((config.DATA_DIR / "mock_nessie.json").read_text())


def _get(path: str) -> list | dict:
    r = httpx.get(
        f"{config.NESSIE_BASE_URL}{path}",
        params={"key": config.NESSIE_API_KEY},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _to_merchant(m: dict) -> Merchant:
    return Merchant(
        id=m["_id"],
        name=m.get("name", "Unknown"),
        category=m.get("category") or [],
        geocode=m.get("geocode"),
    )


def _to_purchase(p: dict) -> Purchase:
    return Purchase(
        id=p["_id"],
        merchant_id=p["merchant_id"],
        amount=float(p.get("amount", 0)),
        purchase_date=p.get("purchase_date", ""),
        description=p.get("description", "") or "",
    )


def default_customer_id() -> str:
    if config.USE_MOCK_DATA:
        return _mock()["customer"]["_id"]
    return config.NESSIE_CUSTOMER_ID


def list_customers() -> list[dict]:
    if config.USE_MOCK_DATA:
        return [_mock()["customer"]]
    return _get("/customers")  # type: ignore[return-value]


def get_purchases(customer_id: str) -> list[Purchase]:
    if config.USE_MOCK_DATA:
        return [_to_purchase(p) for p in _mock()["purchases"]]
    purchases: list[Purchase] = []
    for acct in _get(f"/customers/{customer_id}/accounts"):  # type: ignore[union-attr]
        purchases += [_to_purchase(p) for p in _get(f"/accounts/{acct['_id']}/purchases")]  # type: ignore[union-attr]
    return purchases


def get_merchant(merchant_id: str) -> Merchant | None:
    if config.USE_MOCK_DATA:
        m = next((m for m in _mock()["merchants"] if m["_id"] == merchant_id), None)
        return _to_merchant(m) if m else None
    try:
        return _to_merchant(_get(f"/merchants/{merchant_id}"))  # type: ignore[arg-type]
    except httpx.HTTPStatusError:
        return None
