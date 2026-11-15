"""Accounts and sessions. Owner: Person A.

Each user is linked to one customer (their Nessie-shaped purchase data in `purchases`); logging in
only decides WHICH customer the API shows. Anonymous visitors keep seeing the demo customer.

Passwords: scrypt from the standard library with a random 16-byte salt per user, stored as
"scrypt$n$r$p$salt$hash" (base64). Plain passwords are never stored or logged.
Sessions: a random 32-byte bearer token goes to the browser; only its SHA-256 is stored in
`sessions`, so a copy of the database holds no usable session. Sessions expire after SESSION_TTL
(a TTL index removes them; lookups also check the time).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable

from pymongo.database import Database

USERS = "users"
SESSIONS = "sessions"
SESSION_TTL = timedelta(days=7)
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 200
MAX_EMAIL_LENGTH = 254

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
# Limits on parameters read back from a stored hash, so a tampered row can't make one check cost minutes.
_MAX_SCRYPT_N, _MAX_SCRYPT_R, _MAX_SCRYPT_P = 2**15, 16, 4
# Each check takes ~16 MB and real CPU; at most this many run at once, and a login that can't get a slot
# within CHECK_WAIT_SECONDS is told to retry instead of queueing (a flood of made-up emails can't pile up).
_check_slots = threading.BoundedSemaphore(4)
CHECK_WAIT_SECONDS = 10.0


class Busy(RuntimeError):
    """Too many password checks are running; the caller should answer 503 and let the user retry."""
_SALT_BYTES = 16
_KEY_BYTES = 32


# ---------- passwords ----------
def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=_KEY_BYTES, maxmem=64 * 1024 * 1024)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError(f"password must be {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters")
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _scrypt(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    """False for a wrong password AND for anything malformed; never raises."""
    parts = (stored or "").split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = (int(x) for x in parts[1:4])
        salt, expected = base64.b64decode(parts[4], validate=True), base64.b64decode(parts[5], validate=True)
        if not (salt and expected) or n > _MAX_SCRYPT_N or r > _MAX_SCRYPT_R or p > _MAX_SCRYPT_P:
            return False
        return hmac.compare_digest(_scrypt(password, salt, n, r, p), expected)
    except (ValueError, binascii.Error, OverflowError, MemoryError):
        return False


# A real hash to check against when the email is unknown, so "no such user" takes as long as
# "wrong password" and response times don't reveal which emails have accounts.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


# ---------- users ----------
def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _public(doc: dict) -> dict:
    return {"email": doc["_id"], "name": doc.get("name", ""), "customer_id": doc["customer_id"]}


def upsert_user(db: Database, *, email: str, name: str, customer_id: str, password: str | None = None,
                now: datetime | None = None) -> dict:
    """Create or update an account. An existing password is kept unless a new one is given."""
    key, now = normalize_email(email), now or datetime.now(timezone.utc)
    fields: dict = {"name": name, "customer_id": customer_id, "updated_at": now}
    if password is not None:
        fields["password_hash"] = hash_password(password)
    elif db[USERS].find_one({"_id": key}, {"_id": 1}) is None:
        raise ValueError(f"new account {key} needs a password")
    db[USERS].update_one({"_id": key}, {"$set": fields, "$setOnInsert": {"created_at": now}}, upsert=True)
    if password is not None:
        db[SESSIONS].delete_many({"email": key})  # a new password logs out every old session
    return _public({"_id": key, **fields})


def customer_has_account(db: Database, customer_id: str) -> bool:
    return db[USERS].count_documents({"customer_id": customer_id}, limit=1) > 0


def authenticate(db: Database, email: str, password: str) -> dict | None:
    """The account for these credentials, or None. Raises Busy when too many checks are running."""
    doc = db[USERS].find_one({"_id": normalize_email(email)})
    if not _check_slots.acquire(timeout=CHECK_WAIT_SECONDS):
        raise Busy("too many logins at once")
    try:
        if doc is None:
            verify_password(password, _DUMMY_HASH)  # same work as a real check
            return None
        return _public(doc) if verify_password(password, doc.get("password_hash", "")) else None
    finally:
        _check_slots.release()


# ---------- sessions ----------
def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Database, email: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    db[SESSIONS].insert_one({"_id": _token_id(token), "email": normalize_email(email),
                             "created_at": now, "expires_at": now + SESSION_TTL})
    return token


def user_for_token(db: Database, token: str, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    session = db[SESSIONS].find_one({"_id": _token_id(token), "expires_at": {"$gt": now}})
    if session is None:
        return None
    doc = db[USERS].find_one({"_id": session["email"]})
    return _public(doc) if doc else None


def end_session(db: Database, token: str) -> None:
    db[SESSIONS].delete_one({"_id": _token_id(token)})


# ---------- brute-force throttle ----------
class LoginThrottle:
    """At most `max_failures` failed logins per account per `window_seconds` (in this process).
    Guards the scrypt CPU cost as much as the passwords, which are long and random."""

    MAX_TRACKED = 10_000  # sweep expired entries past this many accounts, so made-up emails can't pile up

    def __init__(self, max_failures: int = 10, window_seconds: float = 300,
                 clock: Callable[[], float] = time.monotonic):
        self._max, self._window, self._clock = max_failures, window_seconds, clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, q: deque[float]) -> deque[float]:
        cutoff = self._clock() - self._window
        while q and q[0] <= cutoff:
            q.popleft()
        return q

    def allowed(self, email: str) -> bool:
        key = normalize_email(email)
        with self._lock:
            q = self._failures.get(key)
            if q is None:
                return True
            if not self._prune(q):
                del self._failures[key]
                return True
            return len(q) < self._max

    def failed(self, email: str) -> None:
        key = normalize_email(email)
        with self._lock:
            if key not in self._failures and len(self._failures) >= self.MAX_TRACKED:
                self._failures = {k: q for k, q in self._failures.items() if self._prune(q)}
                # Still full of live entries (a flood of made-up emails): forget the oldest half.
                if len(self._failures) >= self.MAX_TRACKED:
                    keep = list(self._failures)[len(self._failures) // 2:]
                    self._failures = {k: self._failures[k] for k in keep}
            self._failures.setdefault(key, deque()).append(self._clock())

    def tracked(self) -> int:
        with self._lock:
            return len(self._failures)
