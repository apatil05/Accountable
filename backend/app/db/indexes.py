"""Indexes for the queries the app actually runs. Safe to call on every startup:
create_index is a no-op when the index already exists."""

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database


def ensure_indexes(db: Database) -> None:
    db.purchases.create_index([("customer_id", ASCENDING), ("purchase_date", DESCENDING)])
    db.purchases.create_index([("customer_id", ASCENDING), ("brand_id", ASCENDING)])
    db.merchants.create_index([("brand_id", ASCENDING)])
    db.sync_runs.create_index([("customer_id", ASCENDING), ("started_at", DESCENDING)])
    db.sync_runs.create_index([("ok", ASCENDING), ("started_at", DESCENDING)])
