"""Prewarm: research every target brand ahead of the demo so pages load from the cache."""

from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.db import repos
from app.services import ingest, prewarm
from app.services.research import ResearchUnavailable
from scripts import prewarm as prewarm_script

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
MAX_AGE = timedelta(days=30)
TARGETS = [("amazon", "Amazon"), ("starbucks", "Starbucks"), ("target", "Target")]


class FakeRefresher:
    """Writes a cache row like the real refresh does, and can fail on chosen brands."""

    def __init__(self, fail: dict[str, Exception] | None = None):
        self.fail, self.calls = fail or {}, []

    def __call__(self, db, brand_id: str, name: str) -> None:
        self.calls.append(brand_id)
        if brand_id in self.fail:
            raise self.fail[brand_id]
        repos.save_ethics_research(db, brand_id, name, [], "gemini", NOW)


def _run(db, refresher, **overrides):
    sleeps = []
    kwargs = {"max_age": MAX_AGE, "force": False, "delay": 2.0, "now": NOW, "sleep": sleeps.append}
    results = prewarm.prewarm(db, TARGETS, repos.ETHICS_REPORTS, refresher, **{**kwargs, **overrides})
    return {r.brand_id: r.status for r in results}, sleeps


def test_researches_missing_and_stale_brands_but_skips_fresh_ones(db):
    repos.save_ethics_research(db, "amazon", "Amazon", [], "gemini", NOW - timedelta(days=1))
    repos.save_ethics_research(db, "target", "Target", [], "gemini", NOW - timedelta(days=31))
    refresher = FakeRefresher()
    statuses, _ = _run(db, refresher)
    assert statuses == {"amazon": "fresh", "starbucks": "researched", "target": "researched"}
    assert refresher.calls == ["starbucks", "target"]


def test_force_researches_fresh_brands_too(db):
    repos.save_ethics_research(db, "amazon", "Amazon", [], "gemini", NOW)
    refresher = FakeRefresher()
    _run(db, refresher, force=True)
    assert refresher.calls == ["amazon", "starbucks", "target"]


def test_sleeps_only_between_live_calls(db):
    repos.save_ethics_research(db, "amazon", "Amazon", [], "gemini", NOW)
    _, sleeps = _run(db, FakeRefresher())
    assert sleeps == [2.0]  # starbucks, pause, target


def test_unavailable_research_stops_the_run_and_marks_the_rest(db):
    refresher = FakeRefresher(fail={"amazon": ResearchUnavailable("no key")})
    statuses, _ = _run(db, refresher)
    assert statuses == {"amazon": "unavailable", "starbucks": "unavailable", "target": "unavailable"}
    assert refresher.calls == ["amazon"]


def test_one_failure_does_not_stop_the_others(db):
    refresher = FakeRefresher(fail={"starbucks": RuntimeError("timeout")})
    results = prewarm.prewarm(
        db, TARGETS, repos.ETHICS_REPORTS, refresher,
        max_age=MAX_AGE, force=False, delay=0, now=NOW, sleep=lambda s: None,
    )
    failed = next(r for r in results if r.brand_id == "starbucks")
    assert (failed.status, failed.detail) == ("failed", "RuntimeError: timeout")
    assert [r.status for r in results if r.brand_id != "starbucks"] == ["researched", "researched"]


def test_target_brands_are_the_customers_non_junk_brands(db):
    ingest.sync_customer(db, "cust_demo")
    db.brands.insert_one({"_id": "junk-x", "name": "Junk", "categories": [], "is_junk": True})
    db.purchases.insert_one({"_id": "pj", "customer_id": "cust_demo", "brand_id": "junk-x"})
    targets = prewarm.target_brands(db, "cust_demo")
    assert len(targets) == 10
    assert ("starbucks", "Starbucks") in targets
    assert all(bid != "junk-x" for bid, _ in targets)


def test_all_brands_covers_the_catalog_without_junk(db):
    ingest.seed_brands(db)
    db.brands.insert_one({"_id": "junk-x", "name": "Junk", "categories": [], "is_junk": True})
    targets = prewarm.target_brands(db, None)
    assert len(targets) == db.brands.count_documents({"is_junk": False})
    assert all(bid != "junk-x" for bid, _ in targets)


# ---------- CLI ----------
@pytest.fixture
def cli_db(db, monkeypatch):
    monkeypatch.setattr(prewarm_script, "get_db", lambda: db)
    ingest.sync_customer(db, "cust_demo")
    return db


def test_cli_exits_nonzero_when_research_is_unavailable(cli_db, monkeypatch, capsys):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert prewarm_script.main(["--only", "ethics", "--delay", "0"]) == 1
    out = capsys.readouterr().out
    assert "unavailable" in out and "starbucks" in out


def test_cli_exits_zero_when_every_brand_is_cached(cli_db, monkeypatch):
    for bid, name in prewarm.target_brands(cli_db, "cust_demo"):
        repos.save_ethics_research(cli_db, bid, name, [], "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert prewarm_script.main(["--only", "ethics"]) == 0


def test_cli_dry_run_researches_nothing(cli_db, monkeypatch, capsys):
    monkeypatch.setattr(prewarm_script.ethics, "refresh", lambda *a: pytest.fail("dry run researched"))
    assert prewarm_script.main(["--only", "ethics", "--dry-run"]) == 0
    assert "would research" in capsys.readouterr().out
