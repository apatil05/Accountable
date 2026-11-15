"""Accounts, sessions, and per-user data. Integration tests use the throwaway test database."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app, get_database
from app.services import auth, ingest

EMAIL = "maya@accountable.demo"
PASSWORD = "correct horse battery staple"
NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


# ---------- passwords (no database) ----------
def test_password_round_trip_and_wrong_password():
    stored = auth.hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert auth.verify_password(PASSWORD, stored)
    assert not auth.verify_password(PASSWORD + "x", stored)


def test_each_hash_has_its_own_salt():
    assert auth.hash_password(PASSWORD) != auth.hash_password(PASSWORD)


@pytest.mark.parametrize("stored", ["", "plain", "scrypt$1$2", "bcrypt$a$b$c$d$e", "scrypt$x$8$1$AA==$AA=="])
def test_malformed_stored_hashes_never_verify(stored):
    assert not auth.verify_password(PASSWORD, stored)


def test_short_passwords_are_refused():
    with pytest.raises(ValueError):
        auth.hash_password("short")


def test_throttle_blocks_after_too_many_failures_then_recovers():
    clock = [0.0]
    throttle = auth.LoginThrottle(max_failures=3, window_seconds=60, clock=lambda: clock[0])
    for _ in range(3):
        assert throttle.allowed("a@b.c")
        throttle.failed("a@b.c")
    assert not throttle.allowed("a@b.c")
    assert throttle.allowed("other@b.c")  # per account
    clock[0] = 61
    assert throttle.allowed("a@b.c")


# ---------- accounts and sessions (database) ----------
@pytest.fixture
def user(db):
    return auth.upsert_user(db, email=EMAIL, name="Maya Chen", customer_id="cust_maya", password=PASSWORD)


def test_authenticate_accepts_the_right_password_any_email_case(db, user):
    assert auth.authenticate(db, " MAYA@Accountable.demo ", PASSWORD)["customer_id"] == "cust_maya"
    assert auth.authenticate(db, EMAIL, "wrong password!!") is None
    assert auth.authenticate(db, "nobody@accountable.demo", PASSWORD) is None


def test_upsert_keeps_the_password_unless_a_new_one_is_given(db, user):
    auth.upsert_user(db, email=EMAIL, name="Maya C.", customer_id="cust_maya")
    assert auth.authenticate(db, EMAIL, PASSWORD)["name"] == "Maya C."
    auth.upsert_user(db, email=EMAIL, name="Maya C.", customer_id="cust_maya", password="a brand new password")
    assert auth.authenticate(db, EMAIL, PASSWORD) is None


def test_a_new_user_needs_a_password(db):
    with pytest.raises(ValueError):
        auth.upsert_user(db, email="new@accountable.demo", name="New", customer_id="cust_new")


def test_sessions_resolve_expire_and_end(db, user):
    token = auth.create_session(db, EMAIL, now=NOW)
    assert db.sessions.find_one({"_id": token}) is None  # only a hash of the token is stored
    assert auth.user_for_token(db, token, now=NOW)["email"] == EMAIL
    assert auth.user_for_token(db, token, now=NOW + auth.SESSION_TTL + timedelta(seconds=1)) is None
    assert auth.user_for_token(db, "not-a-token", now=NOW) is None
    auth.end_session(db, token)
    assert auth.user_for_token(db, token, now=NOW) is None


def test_password_hash_never_leaves_the_service(db, user):
    token = auth.create_session(db, EMAIL, now=NOW)
    for found in (auth.authenticate(db, EMAIL, PASSWORD), auth.user_for_token(db, token, now=NOW)):
        assert "password_hash" not in found


# ---------- HTTP ----------
@pytest.fixture
def client(db, user, monkeypatch):
    monkeypatch.setattr(main, "login_throttle", auth.LoginThrottle(max_failures=3, window_seconds=60))
    ingest.sync_customer(db, "cust_demo")
    _give_purchases(db, "cust_maya", "starbucks", 4_20)
    app.dependency_overrides[get_database] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _give_purchases(db, customer_id: str, brand_id: str, cents: int) -> None:
    db.purchases.insert_one({
        "_id": f"{customer_id}_p1", "customer_id": customer_id, "account_id": f"acct_{customer_id}",
        "merchant_id": f"m_{customer_id}_{brand_id}", "brand_id": brand_id, "amount_cents": cents,
        "purchase_date": NOW, "status": "completed", "description": "STARBUCKS", "source": "mock",
    })


def _login(client) -> dict:
    r = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_login_returns_a_token_and_the_user(client):
    body = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()
    assert body["user"] == {"email": EMAIL, "name": "Maya Chen", "customer_id": "cust_maya"}
    assert len(body["token"]) >= 40


def test_wrong_password_and_unknown_user_get_the_same_answer(client):
    wrong = client.post("/api/auth/login", json={"email": EMAIL, "password": "not the password"})
    unknown = client.post("/api/auth/login", json={"email": "who@accountable.demo", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_login_is_throttled_after_repeated_failures(client):
    for _ in range(3):
        client.post("/api/auth/login", json={"email": EMAIL, "password": "not the password"})
    r = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 429


def test_a_logged_in_user_sees_only_their_own_spending(client):
    mine = client.get("/api/spending/summary", headers=_login(client)).json()
    assert mine["customer_id"] == "cust_maya"
    assert [m["merchant_id"] for m in mine["merchants"]] == ["starbucks"]
    assert mine["total_spent"] == 4.20


def test_anonymous_visitors_still_get_the_demo_customer(client):
    assert client.get("/api/spending/summary").json()["customer_id"] == "cust_demo"


def test_me_and_logout(client):
    headers = _login(client)
    assert client.get("/api/auth/me", headers=headers).json()["email"] == EMAIL
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/spending/summary", headers=headers).status_code == 401  # not silently the demo
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("header", ["Bearer nope", "Basic abc", "Bearer ", "token"])
def test_bad_authorization_headers_are_rejected(client, header):
    assert client.get("/api/spending/summary", headers={"Authorization": header}).status_code == 401


def test_a_user_cannot_read_another_customer(client):
    headers = _login(client)
    assert client.get("/api/spending/summary?customer_id=cust_demo", headers=headers).status_code == 403
    assert client.get("/api/spending/transactions?customer_id=cust_demo", headers=headers).status_code == 403


def test_profiles_are_per_user(client):
    headers = _login(client)
    mine = {**client.get("/api/profile", headers=headers).json(), "name": "Maya Chen"}
    assert client.put("/api/profile", json=mine, headers=headers).status_code == 200
    assert client.get("/api/profile", headers=headers).json()["name"] == "Maya Chen"
    assert client.get("/api/profile").json()["name"] != "Maya Chen"


def test_summary_says_where_the_data_came_from(client):
    assert client.get("/api/spending/summary").json()["data_source"] == "mock"


@pytest.mark.parametrize("body", [{"email": EMAIL}, {"password": PASSWORD}, {"email": "x" * 300, "password": PASSWORD},
                                  {"email": EMAIL, "password": "p" * 300}])
def test_malformed_login_bodies_are_rejected(client, body):
    assert client.post("/api/auth/login", json=body).status_code == 422


# ---------- review follow-ups ----------
def test_resetting_a_password_ends_existing_sessions(db, user):
    token = auth.create_session(db, EMAIL, now=NOW)
    auth.upsert_user(db, email=EMAIL, name="Maya Chen", customer_id="cust_maya", password="a whole new password")
    assert auth.user_for_token(db, token, now=NOW) is None


def test_updating_a_name_keeps_sessions(db, user):
    token = auth.create_session(db, EMAIL, now=NOW)
    auth.upsert_user(db, email=EMAIL, name="Maya C.", customer_id="cust_maya")
    assert auth.user_for_token(db, token, now=NOW)["name"] == "Maya C."


@pytest.mark.parametrize("params", [(2**14, 8, 64), (2**14, 64, 1)])
def test_stored_hashes_with_oversized_cost_never_verify(params):
    good = auth.hash_password(PASSWORD).split("$")
    n, r, p = params
    assert not auth.verify_password(PASSWORD, "$".join(["scrypt", str(n), str(r), str(p), good[4], good[5]]))


def test_throttle_memory_is_hard_capped():
    throttle = auth.LoginThrottle(max_failures=3, window_seconds=60, clock=lambda: 0.0)
    throttle.MAX_TRACKED = 4
    for i in range(20):
        throttle.failed(f"user{i}@example.com")
    assert throttle.tracked() <= 4


def test_login_says_busy_instead_of_queueing_unbounded_password_checks(client, monkeypatch):
    slots = auth.threading.BoundedSemaphore(1)
    slots.acquire()  # every slot taken by "other" requests
    monkeypatch.setattr(auth, "_check_slots", slots)
    monkeypatch.setattr(auth, "CHECK_WAIT_SECONDS", 0.05)
    r = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 503


def test_override_never_reaches_a_customer_that_has_an_account(client, monkeypatch):
    monkeypatch.setattr(main.config, "ALLOW_CUSTOMER_OVERRIDE", True)
    assert client.get("/api/spending/summary?customer_id=cust_maya").status_code == 403
    assert client.get("/api/spending/summary?customer_id=cust_demo").status_code == 200  # dev use still works


def test_anonymous_profile_writes_only_ever_touch_the_demo_customer(client, db, monkeypatch):
    monkeypatch.setattr(main.config, "ALLOW_CUSTOMER_OVERRIDE", True)
    body = {**client.get("/api/profile").json(), "name": "Hijacked"}
    assert client.put("/api/profile?customer_id=cust_maya", json=body).status_code == 403
    assert (db.profiles.find_one({"_id": "cust_maya"}) or {}).get("name") != "Hijacked"
