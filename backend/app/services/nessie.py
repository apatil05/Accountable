"""Capital One Nessie client: fetch-only, returns raw dicts.

Owner: Person A (Data & Backend). Normalization and storage live in
services/normalize.py and services/ingest.py; the API reads from MongoDB, not from here.

Mock data in data/mock_nessie.json uses the same shape Nessie returns, so
flipping USE_MOCK_DATA=false should "just work" once keys are set.

Security: Nessie takes the API key as a query parameter, so error messages here
are built by hand and never include the request URL.
"""

import json
import logging
import re
from functools import lru_cache
from typing import Any, Iterable

import httpx

from .. import config

REQUEST_TIMEOUT_SECONDS = 15
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")  # real ids are 24-hex or uuid; mock ids are slugs

_http = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

# httpx logs the full request URL at INFO, and ours carries the API key as a query parameter.
for _noisy_logger in ("httpx", "httpcore"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)


class NessieError(RuntimeError):
    """Any Nessie failure. The message is safe to show to API clients."""


class InvalidIdError(ValueError):
    """An id that could not be a Nessie id (guards against path injection)."""


def validate_id(value: str, kind: str = "id") -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise InvalidIdError(f"Invalid Nessie {kind}")
    return value


@lru_cache
def _mock() -> dict:
    return json.loads((config.DATA_DIR / "mock_nessie.json").read_text())


def _get(path: str) -> Any | None:
    """GET a Nessie path. Returns None on 404; raises NessieError on anything else."""
    try:
        response = _http.get(f"{config.NESSIE_BASE_URL}{path}", params={"key": config.NESSIE_API_KEY})
    except httpx.TimeoutException:
        raise NessieError("Nessie request timed out") from None
    except httpx.HTTPError as exc:
        raise NessieError(f"Nessie request failed ({type(exc).__name__})") from None
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise NessieError(f"Nessie returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise NessieError("Nessie returned invalid JSON") from None


def _as_list(data: Any) -> list[dict]:
    return data if isinstance(data, list) else []


def default_customer_id() -> str:
    if config.USE_MOCK_DATA:
        return _mock()["customer"]["_id"]
    return config.NESSIE_CUSTOMER_ID


def list_customers() -> list[dict]:
    if config.USE_MOCK_DATA:
        return [_mock()["customer"]]
    return _as_list(_get("/customers"))


def fetch_customer(customer_id: str) -> dict | None:
    validate_id(customer_id, "customer id")
    if config.USE_MOCK_DATA:
        customer = _mock()["customer"]
        return customer if customer["_id"] == customer_id else None
    return _get(f"/customers/{customer_id}")


def fetch_accounts(customer_id: str) -> list[dict]:
    validate_id(customer_id, "customer id")
    if config.USE_MOCK_DATA:
        return [a for a in _mock()["accounts"] if a["customer_id"] == customer_id]
    return _as_list(_get(f"/customers/{customer_id}/accounts"))


def fetch_purchases(account_id: str) -> list[dict]:
    validate_id(account_id, "account id")
    if config.USE_MOCK_DATA:
        return [p for p in _mock()["purchases"] if p.get("payer_id") == account_id]
    return _as_list(_get(f"/accounts/{account_id}/purchases"))


def fetch_merchants(merchant_ids: Iterable[str]) -> dict[str, dict]:
    """Each distinct id is fetched once. Ids Nessie doesn't know are omitted."""
    unique = sorted({validate_id(i, "merchant id") for i in merchant_ids})
    if config.USE_MOCK_DATA:
        by_id = {m["_id"]: m for m in _mock()["merchants"]}
        return {i: by_id[i] for i in unique if i in by_id}
    found: dict[str, dict] = {}
    for merchant_id in unique:
        merchant = _get(f"/merchants/{merchant_id}")
        if isinstance(merchant, dict):
            found[merchant_id] = merchant
    return found
