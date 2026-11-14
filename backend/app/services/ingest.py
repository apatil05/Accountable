"""Sync Nessie (or mock) data into MongoDB Atlas: fetch -> normalize -> upsert.

Owner: Person A. Idempotent: every document is keyed by its source id, so running
a sync twice (or two teammates syncing at once) leaves identical data.
"""

import json
import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Mapping

from pymongo.database import Database
from pymongo.errors import PyMongoError

from .. import config
from ..db import repos
from . import nessie, normalize

logger = logging.getLogger(__name__)

MAX_LOGGED_ERRORS = 50
COUNT_KEYS = ("merchants", "purchases", "brands_created")


@lru_cache
def curated_brands() -> tuple[dict, ...]:
    return tuple(json.loads((config.DATA_DIR / "brands.json").read_text()))


@lru_cache
def category_map() -> dict[str, str]:
    return json.loads((config.DATA_DIR / "category_map.json").read_text())


@lru_cache
def brand_index() -> normalize.BrandIndex:
    return normalize.build_brand_index(curated_brands())


def seed_brands(db: Database) -> int:
    return repos.seed_brands(db, curated_brands())


def _to_cents(amount: Any) -> int | None:
    """Dollars -> integer cents, or None when the amount is not a usable finite number."""
    try:
        value = Decimal(str(0 if amount is None else amount))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _parse_date(text: Any) -> datetime | None:
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _new_brand_docs(
    matches: Mapping[str, normalize.BrandMatch], raw_categories: Mapping[str, Any]
) -> list[dict]:
    """Local and junk brands (anything not in the curated list), one doc per brand id."""
    curated_ids = {b["_id"] for b in curated_brands()}
    docs: dict[str, dict] = {}
    for merchant_id, match in matches.items():
        if match.brand_id in curated_ids or match.brand_id in docs:
            continue
        docs[match.brand_id] = {
            "_id": match.brand_id,
            "name": match.name,
            "categories": normalize.canonical_categories(raw_categories.get(merchant_id), None, category_map()),
            "aliases": [],
            "is_chain": match.is_chain,
            "is_junk": match.is_junk,
            "parent_id": None,
        }
    return list(docs.values())


def _merchant_docs(raw_merchants: Mapping[str, dict], matches: Mapping[str, normalize.BrandMatch], source: str, now: datetime) -> list[dict]:
    return [
        {
            "_id": merchant_id,
            "source": source,
            "raw_name": raw.get("name") or "",
            "raw_category": raw.get("category"),
            "address": raw.get("address"),
            "geocode": raw.get("geocode"),
            "brand_id": matches[merchant_id].brand_id,
            "match": {"method": matches[merchant_id].method, "score": matches[merchant_id].score},
            "synced_at": now,
        }
        for merchant_id, raw in raw_merchants.items()
    ]


def _purchase_docs(
    raw_purchases: list[tuple[str, dict]],
    customer_id: str,
    matches: Mapping[str, normalize.BrandMatch],
    source: str,
    now: datetime,
    errors: list[str],
) -> list[dict]:
    docs = []
    for account_id, purchase in raw_purchases:
        purchase_id = purchase.get("_id", "?")
        purchase_date = _parse_date(purchase.get("purchase_date"))
        if "_id" not in purchase or purchase_date is None or not purchase.get("merchant_id"):
            errors.append(f"skipped malformed purchase {purchase_id}")
            continue
        amount_cents = _to_cents(purchase.get("amount"))
        if amount_cents is None:
            errors.append(f"skipped purchase {purchase_id}: invalid amount")
            continue
        docs.append(
            {
                "_id": purchase["_id"],
                "source": source,
                "customer_id": customer_id,
                "account_id": account_id,
                "merchant_id": purchase["merchant_id"],
                "brand_id": matches[purchase["merchant_id"]].brand_id,
                "amount_cents": amount_cents,
                "purchase_date": purchase_date,
                "status": purchase.get("status") or "completed",
                "description": purchase.get("description") or "",
                "synced_at": now,
            }
        )
    return docs


def _fetch(customer_id: str) -> tuple[dict, list[dict], list[tuple[str, dict]], dict[str, dict]]:
    customer = nessie.fetch_customer(customer_id)
    if customer is None:
        raise nessie.NessieError(f"Customer {customer_id} was not found")
    accounts = nessie.fetch_accounts(customer_id)
    purchases = [(a["_id"], p) for a in accounts for p in nessie.fetch_purchases(a["_id"])]
    merchants = nessie.fetch_merchants({p["merchant_id"] for _, p in purchases if p.get("merchant_id")})
    return customer, accounts, purchases, merchants


def _resolve(
    raw_purchases: list[tuple[str, dict]], raw_merchants: Mapping[str, dict], errors: list[str]
) -> dict[str, normalize.BrandMatch]:
    index = brand_index()
    matches = {mid: normalize.resolve_brand(m.get("name"), index) for mid, m in raw_merchants.items()}
    for _, purchase in raw_purchases:
        merchant_id = purchase.get("merchant_id")
        if merchant_id and merchant_id not in matches:
            errors.append(f"merchant {merchant_id} not found in Nessie")
            matches[merchant_id] = normalize.resolve_brand(purchase.get("description"), index)
    return matches


def _run_sync(db: Database, customer_id: str, source: str) -> tuple[dict[str, int], list[str]]:
    customer, accounts, raw_purchases, raw_merchants = _fetch(customer_id)
    seed_brands(db)
    errors: list[str] = []
    matches = _resolve(raw_purchases, raw_merchants, errors)

    new_brands = _new_brand_docs(matches, {mid: m.get("category") for mid, m in raw_merchants.items()})
    already_there = repos.existing_brand_ids(db, [b["_id"] for b in new_brands])
    repos.ensure_brands(db, new_brands)

    now = repos.utcnow()
    customer_doc = {
        "_id": customer_id,
        "source": source,
        "first_name": customer.get("first_name", ""),
        "last_name": customer.get("last_name", ""),
        "address": customer.get("address"),
        "account_ids": [a["_id"] for a in accounts],
        "synced_at": now,
    }
    merchant_docs = _merchant_docs(raw_merchants, matches, source, now)
    purchase_docs = _purchase_docs(raw_purchases, customer_id, matches, source, now, errors)
    repos.bulk_upsert(db, "customers", [customer_doc])
    repos.bulk_upsert(db, "merchants", merchant_docs)
    repos.bulk_upsert(db, "purchases", purchase_docs)

    counts = {
        "merchants": len(merchant_docs),
        "purchases": len(purchase_docs),
        "brands_created": len(new_brands) - len(already_there),
    }
    return counts, errors[:MAX_LOGGED_ERRORS]


def _log_run(db: Database, customer_id: str, source: str, started_at: datetime, ok: bool,
             counts: Mapping[str, int] | None = None, errors: list[str] | None = None) -> None:
    """Best effort: a failure to write the log must not mask the sync's own outcome."""
    try:
        repos.record_sync_run(db, {
            "customer_id": customer_id, "source": source, "ok": ok,
            "started_at": started_at, "finished_at": repos.utcnow(),
            "counts": dict(counts or {}), "errors": errors or [],
        })
    except PyMongoError as exc:
        logger.warning("could not record sync run: %s", type(exc).__name__)


def sync_customer(db: Database, customer_id: str) -> dict:
    """Pull one customer's accounts, purchases and merchants, normalize, and upsert."""
    started_at = repos.utcnow()
    source = "mock" if config.USE_MOCK_DATA else "nessie"
    try:
        counts, errors = _run_sync(db, customer_id, source)
    except (KeyError, TypeError, AttributeError):
        _log_run(db, customer_id, source, started_at, ok=False, errors=["unexpected data shape"])
        raise nessie.NessieError("Nessie returned unexpected data") from None
    except Exception as exc:
        _log_run(db, customer_id, source, started_at, ok=False, errors=[type(exc).__name__])
        raise
    _log_run(db, customer_id, source, started_at, ok=True, counts=counts, errors=errors)
    return {"customer_id": customer_id, "source": source, **counts, "errors": errors}
