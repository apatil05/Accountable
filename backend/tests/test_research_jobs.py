"""On-demand research for companies without saved research. Gemini is always faked."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import repos
from app.main import app, get_database
from app.services import ethics, ingest, research_jobs
from app.services.research import ResearchUnavailable

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


@pytest.fixture
def jobs(db, monkeypatch):
    """Runs jobs inline (no thread) with a fake researcher that records what it was asked to do."""
    ingest.seed_brands(db)
    calls = []

    def fake_refresh(_db, brand_id, name):
        calls.append(brand_id)
        repos.save_ethics_research(_db, brand_id, name, [], "gemini")

    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ethics, "refresh", fake_refresh)
    monkeypatch.setattr(research_jobs, "_spawn", lambda fn: fn())
    return calls


def _ask(db, brand="torchys", now=NOW):
    return research_jobs.request(db, brand, now=now)


def test_an_unresearched_brand_is_researched_once_and_then_ready(db, jobs):
    assert _ask(db) == "started"
    assert jobs == ["torchys"]
    assert _ask(db) == "ready"
    assert jobs == ["torchys"]  # saved, so never paid for twice


def test_junk_and_unknown_brands_are_never_researched(db, jobs):
    db.brands.update_one({"_id": "torchys"}, {"$set": {"is_junk": True}})
    assert _ask(db) == "unknown"
    assert _ask(db, "not-a-brand") == "unknown"
    assert jobs == []


def test_no_gemini_key_means_unavailable(db, jobs, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert _ask(db) == "unavailable"
    assert jobs == []


def test_only_one_research_runs_at_a_time(db, jobs, monkeypatch):
    monkeypatch.setattr(research_jobs, "_spawn", lambda fn: None)  # never finishes
    assert _ask(db) == "started"
    assert _ask(db) == "running"  # same brand
    assert _ask(db, "wegmans") == "busy"  # another brand waits
    assert jobs == []


def test_a_stuck_job_stops_blocking_after_a_while(db, jobs, monkeypatch):
    monkeypatch.setattr(research_jobs, "_spawn", lambda fn: None)
    _ask(db)
    monkeypatch.setattr(research_jobs, "_spawn", lambda fn: fn())
    assert _ask(db, "wegmans", now=NOW + research_jobs.STALE_AFTER + timedelta(seconds=1)) == "started"


def test_daily_cap_stops_further_research(db, jobs, monkeypatch):
    monkeypatch.setattr(config, "RESEARCH_DAILY_CAP", 2)
    assert [_ask(db, b) for b in ("torchys", "wegmans", "whataburger")] == ["started", "started", "capped"]
    assert _ask(db, "whataburger", now=NOW + timedelta(days=1)) == "started"


def test_a_failed_brand_is_not_retried_straight_away(db, jobs, monkeypatch):
    def boom(_db, brand_id, name):
        raise RuntimeError("judge failed")

    monkeypatch.setattr(ethics, "refresh", boom)
    assert _ask(db) == "started"
    assert _ask(db, now=NOW + timedelta(minutes=5)) == "failed"
    assert _ask(db, now=NOW + research_jobs.RETRY_AFTER + timedelta(minutes=1)) == "started"


def test_researcher_that_cannot_run_is_reported_failed(db, jobs, monkeypatch):
    def nope(_db, brand_id, name):
        raise ResearchUnavailable("no key")

    monkeypatch.setattr(ethics, "refresh", nope)
    _ask(db)
    assert _ask(db, now=NOW + timedelta(minutes=5)) == "failed"


def test_endpoint_returns_the_status(db, jobs):
    app.dependency_overrides[get_database] = lambda: db
    try:
        client = TestClient(app)
        assert client.post("/api/merchants/torchys/research").json() == {"status": "started"}
        assert client.post("/api/merchants/torchys/research").json() == {"status": "ready"}
        assert client.post("/api/merchants/not-a-brand/research").status_code == 404
    finally:
        app.dependency_overrides.clear()
