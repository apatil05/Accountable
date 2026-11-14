"""Sync pipeline against a real (throwaway) Atlas database."""

import pytest

from app.db import repos
from app.models import UserProfile
from app.services import aggregation, ingest, nessie

COLLECTIONS = ("customers", "merchants", "purchases", "brands")


def _counts(db) -> dict[str, int]:
    return {name: db[name].count_documents({}) for name in COLLECTIONS}


def test_mock_sync_loads_everything(db, mock_data):
    result = ingest.sync_customer(db, "cust_demo")
    purchase_count = len(mock_data.purchases)
    assert (result["merchants"], result["purchases"], result["brands_created"]) == (10, purchase_count, 0)
    assert result["errors"] == []
    assert _counts(db) == {
        "customers": 1,
        "merchants": 10,
        "purchases": purchase_count,
        "brands": len(ingest.curated_brands()),
    }


def test_sync_is_idempotent(db):
    ingest.sync_customer(db, "cust_demo")
    before = _counts(db)
    ingest.sync_customer(db, "cust_demo")
    assert _counts(db) == before
    assert db.sync_runs.count_documents({}) == 2  # the log grows, the data does not


def test_merchants_resolve_to_curated_brands(db):
    ingest.sync_customer(db, "cust_demo")
    assert db.merchants.find_one({"_id": "m_amazon"})["brand_id"] == "amazon"
    assert {m["match"]["method"] for m in db.merchants.find()} == {"alias"}


def test_purchase_documents_use_cents_and_dates(db, mock_data):
    ingest.sync_customer(db, "cust_demo")
    expected = mock_data.first("m_starbucks")
    purchase = db.purchases.find_one({"_id": expected["_id"]})
    assert purchase["amount_cents"] == round(expected["amount"] * 100)
    assert purchase["brand_id"] == "starbucks"
    assert purchase["purchase_date"].year == 2026


def test_unknown_customer_raises(db):
    with pytest.raises(nessie.NessieError, match="not found"):
        ingest.sync_customer(db, "nobody")


def test_messy_nessie_style_data_is_normalized(db, monkeypatch):
    merchants = {
        "u1": {"_id": "u1", "name": "amazon.com", "category": "demo"},
        "u2": {"_id": "u2", "name": "Amazon Marketplace", "category": "shopping"},
        "u3": {"_id": "u3", "name": "Old Coffee House", "category": "Coffee"},
        "u4": {"_id": "u4", "name": "Demo Groceries", "category": "grocery"},
    }
    purchases = [
        {"_id": "x1", "merchant_id": "u1", "amount": 10.10, "purchase_date": "2026-10-01", "status": "completed"},
        {"_id": "x2", "merchant_id": "u2", "amount": 20.05, "purchase_date": "2026-10-02", "status": "pending"},
        {"_id": "x3", "merchant_id": "u2", "amount": 99, "purchase_date": "2026-10-03", "status": "cancelled"},
        {"_id": "x4", "merchant_id": "u3", "amount": 4, "purchase_date": "2026-10-04", "status": "completed"},
        {"_id": "x5", "merchant_id": "u4", "amount": 7, "purchase_date": "2026-10-05", "status": "completed"},
        {"_id": "x6", "merchant_id": "ghost", "amount": 1, "purchase_date": "2026-10-06", "description": "Mystery"},
        {"_id": "x7", "merchant_id": "u1", "amount": 1, "purchase_date": "not-a-date"},
    ]
    monkeypatch.setattr(nessie, "fetch_customer", lambda cid: {"_id": cid, "first_name": "A", "last_name": "B"})
    monkeypatch.setattr(nessie, "fetch_accounts", lambda cid: [{"_id": "acct1"}])
    monkeypatch.setattr(nessie, "fetch_purchases", lambda aid: purchases)
    monkeypatch.setattr(nessie, "fetch_merchants", lambda ids: {i: merchants[i] for i in ids if i in merchants})

    result = ingest.sync_customer(db, "cust_x")

    assert result["purchases"] == 6  # the malformed-date purchase is skipped, and reported
    assert any("ghost" in e for e in result["errors"])
    assert any("x7" in e for e in result["errors"])

    # Two Nessie merchants collapse into one brand; string categories were mapped.
    assert db.merchants.find_one({"_id": "u1"})["brand_id"] == "amazon"
    assert db.merchants.find_one({"_id": "u2"})["brand_id"] == "amazon"
    local = db.brands.find_one({"_id": "local-old-coffee-house"})
    assert local["categories"] == ["Coffee"] and not local["is_junk"] and not local["is_chain"]
    junk = db.brands.find_one({"_id": "junk-demo-groceries"})
    assert junk["is_junk"] and junk["categories"] == ["Groceries"]

    summary = aggregation.spending_summary(db, "cust_x", UserProfile())
    amazon = next(m for m in summary.merchants if m.merchant_id == "amazon")
    assert amazon.total_spent == 30.15  # cancelled excluded, pending included
    assert amazon.purchase_count == 2
    assert repos.get_ethics_research(db, "junk-demo-groceries") is None  # junk is never researched
    assert next(m for m in summary.merchants if m.merchant_id == "junk-demo-groceries").personalized_score is None


def test_unusable_amounts_are_skipped_not_fatal(db, monkeypatch):
    purchases = [
        {"_id": "a1", "merchant_id": "m1", "amount": "abc", "purchase_date": "2026-10-01"},
        {"_id": "a2", "merchant_id": "m1", "amount": "NaN", "purchase_date": "2026-10-01"},
        {"_id": "a3", "merchant_id": "m1", "amount": "Infinity", "purchase_date": "2026-10-01"},
        {"_id": "a4", "merchant_id": "m1", "amount": 5, "purchase_date": "2026-10-01"},
    ]
    monkeypatch.setattr(nessie, "fetch_customer", lambda cid: {"_id": cid})
    monkeypatch.setattr(nessie, "fetch_accounts", lambda cid: [{"_id": "acct1"}])
    monkeypatch.setattr(nessie, "fetch_purchases", lambda aid: purchases)
    monkeypatch.setattr(nessie, "fetch_merchants", lambda ids: {"m1": {"_id": "m1", "name": "Starbucks"}})

    result = ingest.sync_customer(db, "cust_x")

    assert result["purchases"] == 1
    assert sum("invalid amount" in e for e in result["errors"]) == 3
    assert db.purchases.find_one({"_id": "a4"})["amount_cents"] == 500


def test_malformed_nessie_rows_raise_a_clean_error_and_log_a_failed_run(db, monkeypatch):
    monkeypatch.setattr(nessie, "fetch_customer", lambda cid: {"_id": cid})
    monkeypatch.setattr(nessie, "fetch_accounts", lambda cid: [{"no_id_here": True}])

    with pytest.raises(nessie.NessieError, match="unexpected data"):
        ingest.sync_customer(db, "cust_x")

    assert db.sync_runs.find_one()["ok"] is False
    assert db.purchases.count_documents({}) == 0


def test_last_sync_ignores_failed_runs(db, monkeypatch):
    ingest.sync_customer(db, "cust_demo")
    good = repos.last_sync_run(db)
    monkeypatch.setattr(nessie, "fetch_accounts", lambda cid: [{"bad": 1}])
    with pytest.raises(nessie.NessieError):
        ingest.sync_customer(db, "cust_demo")
    assert repos.last_sync_run(db)["_id"] == good["_id"]
