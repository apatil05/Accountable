"""The Mongo aggregation pipeline must reproduce the totals the old in-memory code gave."""

from app.models import UserProfile
from app.services import aggregation, ingest


def _summary(db):
    ingest.sync_customer(db, "cust_demo")
    return aggregation.spending_summary(db, "cust_demo", UserProfile())


def test_totals_match_the_mock_purchases_minus_cancelled(db, mock_data):
    summary = _summary(db)
    assert summary.total_spent == mock_data.total()
    assert summary.merchant_count == 10
    assert summary.customer_id == "cust_demo"


def test_merchants_are_sorted_by_spend_and_keyed_by_brand(db, mock_data):
    merchants = _summary(db).merchants
    assert [m.total_spent for m in merchants] == sorted((m.total_spent for m in merchants), reverse=True)
    amazon = next(m for m in merchants if m.merchant_id == "amazon")
    assert (amazon.name, amazon.total_spent, amazon.category) == ("Amazon", mock_data.total("m_amazon"), ["Shopping", "Online"])
    assert amazon.purchase_count == len(mock_data.counted("m_amazon"))


def test_researched_brands_get_a_personalized_score(db):
    amazon = next(m for m in _summary(db).merchants if m.merchant_id == "amazon")
    assert amazon.personalized_score is not None


def test_last_purchase_is_an_iso_date(db, mock_data):
    starbucks = next(m for m in _summary(db).merchants if m.merchant_id == "starbucks")
    assert starbucks.last_purchase == max(p["purchase_date"] for p in mock_data.counted("m_starbucks"))


def test_customer_with_no_purchases_gets_an_empty_summary(db):
    summary = aggregation.spending_summary(db, "cust_empty", UserProfile())
    assert (summary.total_spent, summary.merchant_count, summary.merchants) == (0.0, 0, [])
