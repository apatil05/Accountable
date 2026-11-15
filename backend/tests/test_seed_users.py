"""The three demo accounts: each gets its own customer, purchases, profile, and login."""

import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app, get_database
from app.services import auth, ingest
from scripts import seed_users

# The brands that have cached Gemini research, emissions and labor data; demo users buy only from these.
RESEARCHED = {"amazon", "apple", "chipotle", "hm", "nike", "shell", "starbucks", "target", "traderjoes", "walmart"}
PASSWORDS = {u.email: f"test-password-for-{u.key}" for u in seed_users.DEMO_USERS}


def test_three_users_with_distinct_customers_and_brand_mixes():
    users = seed_users.DEMO_USERS
    assert len(users) == 3
    assert len({u.customer_id for u in users}) == len({u.email for u in users}) == 3
    mixes = [frozenset(u.brands) for u in users]
    assert len(set(mixes)) == 3
    assert all(mix <= RESEARCHED for mix in mixes)


def test_generated_data_is_deterministic_and_never_reuses_demo_ids():
    demo = json.loads((config.DATA_DIR / "mock_nessie.json").read_text())
    demo_ids = {p["_id"] for p in demo["purchases"]} | {m["_id"] for m in demo["merchants"]} | {"acct_demo", "cust_demo"}
    seen: set[str] = set()
    for user in seed_users.DEMO_USERS:
        data = seed_users.build_customer_data(user)
        assert data == seed_users.build_customer_data(user)  # same every run
        ids = {p["_id"] for p in data["purchases"]} | {m["_id"] for m in data["merchants"]} | {data["customer"]["_id"]}
        assert not ids & demo_ids and not ids & seen
        seen |= ids
        assert len(data["purchases"]) >= 60
        assert {p["merchant_id"] for p in data["purchases"]} == {m["_id"] for m in data["merchants"]}
        assert all(p["payer_id"] == data["accounts"][0]["_id"] for p in data["purchases"])
        assert all(p["amount"] > 0 for p in data["purchases"])


@pytest.fixture
def seeded(db):
    ingest.sync_customer(db, "cust_demo")
    before = db.purchases.count_documents({"customer_id": "cust_demo"})
    created = seed_users.seed(db, passwords=PASSWORDS)
    return before, created


def test_seed_loads_each_user_through_the_normal_pipeline(db, seeded):
    before, created = seeded
    assert db.purchases.count_documents({"customer_id": "cust_demo"}) == before  # demo customer untouched
    assert {email for email, _ in created} == set(PASSWORDS)
    for user in seed_users.DEMO_USERS:
        brands = set(db.purchases.distinct("brand_id", {"customer_id": user.customer_id}))
        assert brands == set(user.brands)  # every merchant matched to its brand
        assert db.profiles.find_one({"_id": user.customer_id})["name"] == user.name
        assert auth.authenticate(db, user.email, PASSWORDS[user.email])["customer_id"] == user.customer_id


def test_reseeding_is_idempotent_and_keeps_passwords(db, seeded):
    counts = {u.customer_id: db.purchases.count_documents({"customer_id": u.customer_id}) for u in seed_users.DEMO_USERS}
    created = seed_users.seed(db)  # no passwords given: existing accounts keep theirs
    assert created == []
    assert counts == {u.customer_id: db.purchases.count_documents({"customer_id": u.customer_id})
                      for u in seed_users.DEMO_USERS}
    user = seed_users.DEMO_USERS[0]
    assert auth.authenticate(db, user.email, PASSWORDS[user.email]) is not None


def test_reseeding_keeps_a_profile_the_user_edited(db, seeded):
    user = seed_users.DEMO_USERS[0]
    db.profiles.update_one({"_id": user.customer_id}, {"$set": {"stances": "my own words"}})
    seed_users.seed(db)
    assert db.profiles.find_one({"_id": user.customer_id})["stances"] == "my own words"


def test_each_user_sees_a_different_dashboard(db, seeded):
    app.dependency_overrides[get_database] = lambda: db
    try:
        client = TestClient(app)
        totals = {}
        for user in seed_users.DEMO_USERS:
            token = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORDS[user.email]}).json()["token"]
            body = client.get("/api/spending/summary", headers={"Authorization": f"Bearer {token}"}).json()
            assert body["customer_id"] == user.customer_id
            assert {m["merchant_id"] for m in body["merchants"]} == set(user.brands)
            totals[user.key] = body["total_spent"]
        assert len(set(totals.values())) == 3
    finally:
        app.dependency_overrides.clear()
