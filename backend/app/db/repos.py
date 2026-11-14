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


# ---------- research cache ----------
# One document per brand slug, independent of any user's profile. Only real research is
# stored here; mock data is served from data/*.json and never written to these collections.
ETHICS_REPORTS = "ethics_reports"
SUPPLY_CHAINS = "supply_chains"


def _save_research(db: Database, collection: str, brand_id: str, fields: Mapping[str, Any],
                   researched_at: datetime | None) -> None:
    doc = {**fields, "researched_at": researched_at or utcnow()}
    db[collection].replace_one({"_id": brand_id}, doc, upsert=True)


def save_ethics_research(db: Database, brand_id: str, merchant_name: str, categories: Sequence[Mapping[str, Any]],
                         data_source: str, researched_at: datetime | None = None) -> None:
    fields = {"merchant_name": merchant_name, "categories": list(categories), "data_source": data_source}
    _save_research(db, ETHICS_REPORTS, brand_id, fields, researched_at)


def get_ethics_research(db: Database, brand_id: str) -> dict | None:
    return db[ETHICS_REPORTS].find_one({"_id": brand_id})


def get_ethics_researches(db: Database, ids: Iterable[str]) -> list[dict]:
    return list(db[ETHICS_REPORTS].find({"_id": {"$in": list(ids)}}))


def save_supply_chain(db: Database, brand_id: str, product: str, stages: Sequence[Mapping[str, Any]],
                      data_source: str, researched_at: datetime | None = None) -> None:
    fields = {"product": product, "stages": list(stages), "data_source": data_source}
    _save_research(db, SUPPLY_CHAINS, brand_id, fields, researched_at)


def get_supply_chain(db: Database, brand_id: str) -> dict | None:
    return db[SUPPLY_CHAINS].find_one({"_id": brand_id})


def get_supply_chains(db: Database, ids: Iterable[str]) -> dict[str, dict]:
    return {d["_id"]: d for d in db[SUPPLY_CHAINS].find({"_id": {"$in": list(ids)}})}


def researched_at_by_id(db: Database, collection: str, ids: Iterable[str]) -> dict[str, datetime]:
    """When each cached brand was last researched; brands with no cache entry are absent."""
    cursor = db[collection].find({"_id": {"$in": list(ids)}}, {"researched_at": 1})
    return {d["_id"]: d["researched_at"] for d in cursor}


def researchable_brands(db: Database, ids: Iterable[str] | None = None) -> list[tuple[str, str]]:
    """(slug, name) of non-junk brands, optionally limited to `ids`, sorted by slug."""
    query: dict[str, Any] = {"is_junk": {"$ne": True}}
    if ids is not None:
        query["_id"] = {"$in": list(ids)}
    return [(d["_id"], d["name"]) for d in db.brands.find(query, {"name": 1}).sort("_id", 1)]


# ---------- outside-source facts ----------
COMPANY_EMISSIONS = "company_emissions"
LABOR_CASES = "labor_cases"


def get_company_emissions(db: Database, brand_id: str) -> list[dict]:
    """Every emissions document for a brand (all sources and years), newest year first."""
    return list(db[COMPANY_EMISSIONS].find({"brand_id": brand_id}).sort([("data_year", -1), ("_id", 1)]))


EMPTY_LABOR_TOTALS = {"cases": 0, "violations": 0, "employees": 0, "back_wages_cents": 0,
                      "penalties_cents": 0, "child_labor_cases": 0, "earliest": None, "latest": None}


def labor_totals(db: Database, brand_id: str, since: datetime | None = None) -> dict:
    """Sums over a brand's cases (concluded on or after `since`), computed by MongoDB."""
    match: dict[str, Any] = {"brand_id": brand_id}
    if since is not None:
        match["findings_end"] = {"$gte": since}
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": None,
            "cases": {"$sum": 1},
            "violations": {"$sum": "$violations"},
            "employees": {"$sum": "$employees_affected"},
            "back_wages_cents": {"$sum": "$back_wages_cents"},
            "penalties_cents": {"$sum": "$penalties_cents"},
            "child_labor_cases": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$child_labor_violations", 0]}, 0]}, 1, 0]}},
            "earliest": {"$min": "$findings_start"},
            "latest": {"$max": "$findings_end"},
        }},
    ]
    rows = list(db[LABOR_CASES].aggregate(pipeline))
    return {**EMPTY_LABOR_TOTALS, **{k: v for k, v in rows[0].items() if k != "_id"}} if rows else dict(EMPTY_LABOR_TOTALS)


def delete_company_reports_except(db: Database, keep_ids: Iterable[str]) -> int:
    """Remove report entries that were deleted or re-dated in company_reports.json."""
    stale = {"provenance.source": {"$in": ["company_report", "manual"]}, "_id": {"$nin": list(keep_ids)}}
    return db[COMPANY_EMISSIONS].delete_many(stale).deleted_count


def delete_labor_cases_except(db: Database, brand_id: str, keep_ids: Iterable[str]) -> int:
    """Drop a brand's cases that a fresh load no longer matched (e.g. after narrowing its patterns)."""
    return db[LABOR_CASES].delete_many({"brand_id": brand_id, "_id": {"$nin": list(keep_ids)}}).deleted_count


# ---------- sync log ----------
def record_sync_run(db: Database, run: Mapping[str, Any]) -> None:
    db.sync_runs.insert_one(dict(run))


def last_sync_run(db: Database) -> dict | None:
    """Most recent *successful* run, so a failed attempt never looks like fresh data."""
    return db.sync_runs.find_one({"ok": True}, sort=[("started_at", -1)])
