"""Shared fixtures. Integration tests run against a throwaway database on the real
MongoDB Atlas cluster (never the shared `accountable` database) and skip cleanly
when MONGODB_URI is not configured."""

import uuid

import pytest

from app import config
from app.db.client import new_client
from app.services import ethics

TEST_DB_PREFIX = "accountable_test_"


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    """Tests always start in mock-Nessie mode regardless of the developer's .env."""
    monkeypatch.setattr(config, "USE_MOCK_DATA", True)


@pytest.fixture(autouse=True)
def clean_ethics_cache():
    ethics._cache.clear()
    yield
    ethics._cache.clear()


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
