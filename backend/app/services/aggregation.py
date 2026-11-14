"""Feature 1: summarize merchants + purchase frequency + money total.
Owner: Person A (Data & Backend)."""

from collections import defaultdict

from ..models import MerchantSummary, SpendingSummary, UserProfile
from . import ethics, nessie, scoring


def spending_summary(customer_id: str, profile: UserProfile) -> SpendingSummary:
    purchases = nessie.get_purchases(customer_id)
    by_merchant: dict[str, list] = defaultdict(list)
    for p in purchases:
        by_merchant[p.merchant_id].append(p)

    rows: list[MerchantSummary] = []
    for mid, ps in by_merchant.items():
        m = nessie.get_merchant(mid)
        report = ethics.cached_report(mid)
        rows.append(
            MerchantSummary(
                merchant_id=mid,
                name=m.name if m else mid,
                category=m.category if m else [],
                total_spent=round(sum(p.amount for p in ps), 2),
                purchase_count=len(ps),
                last_purchase=max(p.purchase_date for p in ps),
                personalized_score=scoring.personalized(report.categories, profile) if report else None,
            )
        )
    rows.sort(key=lambda r: r.total_spent, reverse=True)
    return SpendingSummary(
        customer_id=customer_id,
        total_spent=round(sum(r.total_spent for r in rows), 2),
        merchant_count=len(rows),
        merchants=rows,
    )
