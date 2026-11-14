"""Shared fixtures. Integration tests run against a throwaway database on the real
MongoDB Atlas cluster (never the shared `accountable` database) and skip cleanly
when MONGODB_URI is not configured."""

import json
import uuid
from dataclasses import dataclass

import pytest

from app import config
from app.db.client import new_client

TEST_DB_PREFIX = "accountable_test_"
EXCLUDED_STATUSES = ("cancelled",)


@dataclass(frozen=True)
class MockData:
    """Expected values derived from mock_nessie.json, so regenerating it doesn't break tests."""

    purchases: tuple[dict, ...]

    def counted(self, merchant_id: str | None = None) -> list[dict]:
        """Purchases that count toward spend (cancelled ones don't), optionally for one merchant."""
        return [
            p for p in self.purchases
            if p["status"] not in EXCLUDED_STATUSES and merchant_id in (None, p["merchant_id"])
        ]

    def total(self, merchant_id: str | None = None) -> float:
        return round(sum(round(p["amount"] * 100) for p in self.counted(merchant_id)) / 100, 2)

    def first(self, merchant_id: str) -> dict:
        return next(p for p in self.counted(merchant_id))


@pytest.fixture(scope="session")
def mock_data() -> MockData:
    raw = json.loads((config.DATA_DIR / "mock_nessie.json").read_text())
    return MockData(tuple(raw["purchases"]))


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    """Tests always start in mock-Nessie mode regardless of the developer's .env."""
    monkeypatch.setattr(config, "USE_MOCK_DATA", True)


@pytest.fixture(autouse=True)
def isolated_labordata_cache(tmp_path, monkeypatch):
    """Loaders delete the cache after a run; tests must never touch the real multi-GB downloads."""
    from app.services import labordata

    monkeypatch.setattr(labordata, "CACHE_DIR", tmp_path / "labordata-cache")


@pytest.fixture
def db():
    if not config.MONGODB_URI:
        pytest.skip("MONGODB_URI is not set; Atlas integration tests skipped")
    client = new_client()
    name = f"{TEST_DB_PREFIX}{uuid.uuid4().hex[:10]}"
    assert name.startswith(TEST_DB_PREFIX)  # never drop anything else
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()
