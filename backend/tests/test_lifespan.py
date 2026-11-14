"""Startup behavior: fail fast with clear messages, then index, seed and sync."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from app import config, main
from app.services import ingest


def test_startup_fails_fast_without_a_connection_string(monkeypatch):
    monkeypatch.setattr(config, "MONGODB_URI", "")
    with pytest.raises(config.ConfigError, match="MONGODB_URI"):
        with TestClient(main.app):
            pass


def test_unreachable_atlas_gives_an_actionable_error_without_leaking_the_uri(monkeypatch):
    secret_uri = "mongodb+srv://user:hunter2@cluster0.example.mongodb.net"
    monkeypatch.setattr(config, "MONGODB_URI", secret_uri)
    monkeypatch.setattr(main, "get_db", lambda: SimpleNamespace())

    def unreachable(db):
        raise ServerSelectionTimeoutError(f"timed out connecting to {secret_uri}")

    monkeypatch.setattr(main, "ping", unreachable)
    with pytest.raises(RuntimeError) as exc:
        with TestClient(main.app):
            pass
    message = str(exc.value)
    assert "Network Access" in message
    assert "hunter2" not in message and "mongodb+srv" not in message


def test_startup_creates_indexes_seeds_brands_and_syncs_mock_customer(db, monkeypatch):
    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://configured")
    monkeypatch.setattr(main, "get_db", lambda: db)
    monkeypatch.setattr(main, "ping", lambda database: None)

    with TestClient(main.app):
        assert db.brands.count_documents({}) == len(ingest.curated_brands())
        assert db.customers.count_documents({"_id": "cust_demo"}) == 1
        assert db.purchases.count_documents({}) == 60
        assert "customer_id_1_purchase_date_-1" in db.purchases.index_information()


def test_restart_does_not_resync_an_existing_customer(db, monkeypatch):
    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://configured")
    monkeypatch.setattr(main, "get_db", lambda: db)
    monkeypatch.setattr(main, "ping", lambda database: None)

    with TestClient(main.app):
        pass
    with TestClient(main.app):
        pass
    assert db.sync_runs.count_documents({}) == 1
