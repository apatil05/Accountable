"""Feature 1: summarize merchants + purchase frequency + money total.
Owner: Person A (Data & Backend).

Runs as MongoDB aggregation pipelines over `purchases`, grouped by brand. Think of
it as: SELECT brand_id, SUM(amount), COUNT(*), MAX(date) ... GROUP BY brand_id ORDER BY 2 DESC.
"""

from collections import defaultdict

from pymongo.database import Database

from ..db import repos
from ..models import CarbonEstimate, MerchantSummary, SpendingSummary, UserProfile
from . import carbon, ethics, scoring

# Statuses seen so far: completed/pending (Nessie), executed (our mock data), cancelled.
# Only cancelled purchases are excluded, so new purchases that start out "pending" still count.
CANCELLED_STATUSES = ("cancelled", "canceled")


def _counted(customer_id: str) -> dict:
    return {"$match": {"customer_id": customer_id, "status": {"$nin": list(CANCELLED_STATUSES)}}}


def _spend_by_brand(db: Database, customer_id: str) -> list[dict]:
    pipeline = [
        _counted(customer_id),
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


def _monthly_spend_by_brand(db: Database, customer_id: str) -> dict[str, list[tuple[str, int]]]:
    """brand_id -> [("YYYY-MM", cents), ...], so each month is inflation-adjusted on its own."""
    pipeline = [
        _counted(customer_id),
        {"$group": {
            "_id": {"brand": "$brand_id", "month": {"$dateToString": {"format": "%Y-%m", "date": "$purchase_date"}}},
            "cents": {"$sum": "$amount_cents"},
        }},
    ]
    out: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for row in db.purchases.aggregate(pipeline):
        out[row["_id"]["brand"]].append((row["_id"]["month"], row["cents"]))
    return out


def _carbon_by_brand(db: Database, customer_id: str, brands: dict[str, dict]) -> dict[str, CarbonEstimate]:
    estimates = {}
    for brand_id, months in _monthly_spend_by_brand(db, customer_id).items():
        brand = brands.get(brand_id)
        if not brand or brand.get("is_junk"):
            continue
        factor = carbon.factor_for(brand_id, brand.get("categories", []))
        if factor:
            estimates[brand_id] = carbon.estimate(months, factor)
    return estimates


def spending_summary(db: Database, customer_id: str, profile: UserProfile) -> SpendingSummary:
    rows = _spend_by_brand(db, customer_id)
    brands = repos.get_brands(db, [r["_id"] for r in rows])

    # Test/demo records are never researched, so they never get a score or a carbon estimate.
    researchable = [
        (r["_id"], brands[r["_id"]]["name"])
        for r in rows
        if r["_id"] in brands and not brands[r["_id"]].get("is_junk")
    ]
    reports = ethics.reports_for(db, researchable, profile)
    footprints = _carbon_by_brand(db, customer_id, brands)

    merchants = []
    for row in rows:
        brand = brands.get(row["_id"], {})
        report = reports.get(row["_id"])
        merchants.append(
            MerchantSummary(
                merchant_id=row["_id"],
                name=brand.get("name", row["_id"]),
                category=brand.get("categories", []),
                total_spent=round(row["total_cents"] / 100, 2),
                purchase_count=row["count"],
                last_purchase=row["last_purchase"].date().isoformat(),
                personalized_score=scoring.personalized(report.categories, profile) if report else None,
                carbon=footprints.get(row["_id"]),
            )
        )
    return SpendingSummary(
        customer_id=customer_id,
        total_spent=round(sum(r["total_cents"] for r in rows) / 100, 2),
        merchant_count=len(merchants),
        merchants=merchants,
        estimated_kg_co2e=round(sum(f.kg_co2e for f in footprints.values()), carbon.KG_DECIMALS) if footprints else None,
    )
