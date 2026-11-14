"""Thin data-access helpers, one group per collection. Every function takes the
`db` explicitly so tests can point at a throwaway database.

Mongo notes for SQL folks: collections appear on first write, `_id` is the
primary key, and upserts (update_one(..., upsert=True)) replace INSERT ... ON CONFLICT.
"""

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from pymongo import UpdateOne
from pymongo.database import Database


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def bulk_upsert(db: Database, collection: str, docs: Sequence[Mapping[str, Any]]) -> int:
    """Upsert each doc by `_id`, replacing the fields it carries. Idempotent."""
    if not docs:
        return 0
    # `_id` is the filter; it must not also appear in the update body (it is immutable).
    ops = [
        UpdateOne({"_id": d["_id"]}, {"$set": {k: v for k, v in d.items() if k != "_id"}}, upsert=True)
        for d in docs
    ]
    db[collection].bulk_write(ops, ordered=False)
    return len(ops)


# ---------- brands ----------
def seed_brands(db: Database, curated: Sequence[Mapping[str, Any]]) -> int:
    """Curated brands (data/brands.json) are the source of truth for their own fields,
    but `external_ids` and `created_at` are left alone once a brand exists."""
    now = utcnow()
    ops = []
    for b in curated:
        fields = {
            "name": b["name"],
            "categories": list(b.get("categories", [])),
            "aliases": list(b.get("aliases", [])),
            "is_chain": bool(b.get("is_chain", True)),
            "is_junk": False,
            "parent_id": b.get("parent_id"),
            "updated_at": now,
        }
        ops.append(
            UpdateOne(
                {"_id": b["_id"]},
                {"$set": fields, "$setOnInsert": {"created_at": now, "external_ids": {}}},
                upsert=True,
            )
        )
    if ops:
        db.brands.bulk_write(ops, ordered=False)
    return len(ops)


def ensure_brands(db: Database, brands: Sequence[Mapping[str, Any]]) -> int:
    """Insert local/junk brands discovered during sync; never overwrite an existing brand."""
    now = utcnow()
    ops = [
        UpdateOne(
            {"_id": b["_id"]},
            {"$setOnInsert": {
                **{k: v for k, v in b.items() if k != "_id"},
                "created_at": now, "updated_at": now, "external_ids": {},
            }},
            upsert=True,
        )
        for b in brands
    ]
    if ops:
        db.brands.bulk_write(ops, ordered=False)
    return len(ops)


def existing_brand_ids(db: Database, ids: Iterable[str]) -> set[str]:
    return {d["_id"] for d in db.brands.find({"_id": {"$in": list(ids)}}, {"_id": 1})}


def get_brand(db: Database, brand_id: str) -> dict | None:
    return db.brands.find_one({"_id": brand_id})


def get_brands(db: Database, ids: Iterable[str]) -> dict[str, dict]:
    return {d["_id"]: d for d in db.brands.find({"_id": {"$in": list(ids)}})}


# ---------- customers / purchases ----------
def customer_exists(db: Database, customer_id: str) -> bool:
    return db.customers.count_documents({"_id": customer_id}, limit=1) > 0


def list_purchases(db: Database, customer_id: str) -> list[dict]:
    return list(db.purchases.find({"customer_id": customer_id}).sort("purchase_date", -1))


def distinct_brand_ids(db: Database, customer_id: str) -> list[str]:
    return sorted(db.purchases.distinct("brand_id", {"customer_id": customer_id}))


# ---------- profiles ----------
def get_profile(db: Database, customer_id: str) -> dict | None:
    return db.profiles.find_one({"_id": customer_id})


def save_profile(db: Database, customer_id: str, profile: Mapping[str, Any]) -> None:
    doc = {**profile, "updated_at": utcnow()}
    db.profiles.update_one({"_id": customer_id}, {"$set": doc}, upsert=True)


# ---------- sync log ----------
def record_sync_run(db: Database, run: Mapping[str, Any]) -> None:
    db.sync_runs.insert_one(dict(run))


def last_sync_run(db: Database) -> dict | None:
    """Most recent *successful* run, so a failed attempt never looks like fresh data."""
    return db.sync_runs.find_one({"ok": True}, sort=[("started_at", -1)])
