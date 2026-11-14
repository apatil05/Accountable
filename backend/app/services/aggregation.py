"""Feature 1: summarize merchants + purchase frequency + money total.
Owner: Person A (Data & Backend).

Runs as a MongoDB aggregation pipeline over `purchases`, grouped by brand. Think of
it as: SELECT brand_id, SUM(amount), COUNT(*), MAX(date) ... GROUP BY brand_id ORDER BY 2 DESC.
"""

from pymongo.database import Database

from ..db import repos
from ..models import MerchantSummary, SpendingSummary, UserProfile
from . import ethics, scoring

# Statuses seen so far: completed/pending (Nessie), executed (our mock data), cancelled.
# Only cancelled purchases are excluded, so new purchases that start out "pending" still count.
CANCELLED_STATUSES = ("cancelled", "canceled")


def _spend_by_brand(db: Database, customer_id: str) -> list[dict]:
    pipeline = [
        {"$match": {"customer_id": customer_id, "status": {"$nin": list(CANCELLED_STATUSES)}}},
        {
            "$group": {
                "_id": "$brand_id",
                "total_cents": {"$sum": "$amount_cents"},
                "count": {"$sum": 1},
                "last_purchase": {"$max": "$purchase_date"},
            }
        },
        {"$sort": {"total_cents": -1, "_id": 1}},
    ]
    return list(db.purchases.aggregate(pipeline))


def spending_summary(db: Database, customer_id: str, profile: UserProfile) -> SpendingSummary:
    rows = _spend_by_brand(db, customer_id)
    brands = repos.get_brands(db, [r["_id"] for r in rows])

    # Test/demo records are never researched, so they never get a score.
    researchable = [
        (r["_id"], brands[r["_id"]]["name"])
        for r in rows
        if r["_id"] in brands and not brands[r["_id"]].get("is_junk")
    ]
    ethics.warm_cache(researchable, profile)

    merchants = []
    for row in rows:
        brand = brands.get(row["_id"], {})
        report = ethics.cached_report(row["_id"])
        merchants.append(
            MerchantSummary(
                merchant_id=row["_id"],
                name=brand.get("name", row["_id"]),
                category=brand.get("categories", []),
                total_spent=round(row["total_cents"] / 100, 2),
                purchase_count=row["count"],
                last_purchase=row["last_purchase"].date().isoformat(),
                personalized_score=scoring.personalized(report.categories, profile) if report else None,
            )
        )
    return SpendingSummary(
        customer_id=customer_id,
        total_spent=round(sum(r["total_cents"] for r in rows) / 100, 2),
        merchant_count=len(merchants),
        merchants=merchants,
    )
