"""The mock-purchase generator: deterministic, realistic in shape, and in sync with the committed file."""

import json
from collections import defaultdict
from datetime import date

from app import config
from scripts import generate_mock_purchases as gen

FULL_MONTHS = ("2026-06", "2026-07", "2026-08", "2026-09", "2026-10")
MONTHLY_SPEND_RANGE = (800, 1400)
PURCHASE_FIELDS = {"_id", "merchant_id", "payer_id", "purchase_date", "amount", "status", "medium", "description"}


def _spend_by_month(purchases: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for p in purchases:
        if p["status"] != "cancelled":
            totals[p["purchase_date"][:7]] += p["amount"]
    return totals


def test_same_seed_gives_the_same_purchases():
    assert gen.generate() == gen.generate()
    assert gen.generate(seed=1) != gen.generate(seed=2)


def test_committed_mock_file_matches_the_generator():
    committed = json.loads((config.DATA_DIR / "mock_nessie.json").read_text())["purchases"]
    assert committed == gen.generate(), "run: python -m scripts.generate_mock_purchases"


def test_purchases_are_nessie_shaped_ordered_and_uniquely_numbered():
    purchases = gen.generate()
    assert all(set(p) == PURCHASE_FIELDS for p in purchases)
    assert [p["_id"] for p in purchases] == [f"p_{i:04d}" for i in range(1, len(purchases) + 1)]
    dates = [p["purchase_date"] for p in purchases]
    assert dates == sorted(dates)
    assert dates[0] >= gen.START.isoformat() and dates[-1] <= gen.END.isoformat()


def test_amounts_are_positive_dollars_and_cents():
    for p in gen.generate():
        assert p["amount"] > 0
        assert round(p["amount"], 2) == p["amount"]


def test_every_demo_merchant_appears():
    mock = json.loads((config.DATA_DIR / "mock_nessie.json").read_text())
    assert {p["merchant_id"] for p in gen.generate()} == {m["_id"] for m in mock["merchants"]}


def test_full_months_look_like_one_persons_card_spend():
    by_month = _spend_by_month(gen.generate())
    low, high = MONTHLY_SPEND_RANGE
    assert all(low <= by_month[m] <= high for m in FULL_MONTHS), by_month


def test_frequent_merchants_are_frequent_and_cheap():
    purchases = gen.generate()
    coffee = [p["amount"] for p in purchases if p["merchant_id"] == "m_starbucks"]
    assert len(coffee) > 60
    assert max(coffee) < 15


def test_only_the_last_days_are_pending_and_one_charge_is_voided():
    purchases = gen.generate()
    pending_dates = {p["purchase_date"] for p in purchases if p["status"] == "pending"}
    assert pending_dates and min(pending_dates) >= "2026-11-12"
    cancelled = [p for p in purchases if p["status"] == "cancelled"]
    assert len(cancelled) == 1 and cancelled[0]["merchant_id"] == "m_amazon"


def test_subscriptions_bill_monthly_on_a_fixed_day():
    icloud = [p["purchase_date"] for p in gen.generate() if p["merchant_id"] == "m_apple" and p["amount"] == 2.99]
    assert icloud and all(d.endswith("-03") for d in icloud)
    assert len(icloud) == 6  # June..November


def test_one_off_outside_the_range_is_skipped():
    purchases = gen.generate(start=date(2026, 10, 1), end=date(2026, 10, 31))
    assert not any(p["description"].startswith("APPLE STORE") for p in purchases)
