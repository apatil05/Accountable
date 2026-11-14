"""Shared fixtures. Integration tests run against a throwaway database on the real
MongoDB Atlas cluster (never the shared `accountable` database) and skip cleanly
when MONGODB_URI is not configured."""

import uuid

import pytest

from app import config
from app.db.client import new_client

TEST_DB_PREFIX = "accountable_test_"


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
