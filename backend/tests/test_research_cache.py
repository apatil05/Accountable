"""Research cache: ethics reports and supply chains persisted per brand slug in Atlas.

Cached research wins over mock data, mock data is never written to the cache, and
personalized scores are computed at read time so a profile change needs no invalidation."""

from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.db import repos
from app.models import CategoryScore, Claim, SupplyChainStage, UserProfile
from app.services import ethics, supply_chain
from app.services.research import ResearchUnavailable

RESEARCHED = [
    CategoryScore(
        category="labor",
        score=20,
        summary="Real research summary.",
        claims=[Claim(text="Union busting fines.", source_url="https://example.org/a", confidence=0.9)],
    ),
    CategoryScore(category="environment", score=80, summary="Good record.", claims=[]),
]
STAGES = [SupplyChainStage(order=1, stage="Farming", place="Huila, Colombia", lat=2.5, lng=-75.5)]


@pytest.fixture
def gemini_on(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(config, "USE_MOCK_DATA", False)


def _save_ethics(db, brand_id="starbucks", name="Starbucks", when=None):
    repos.save_ethics_research(db, brand_id, name, [c.model_dump() for c in RESEARCHED], "gemini", when)


# ---------- repos ----------
def test_ethics_research_round_trip_stamps_researched_at(db):
    _save_ethics(db)
    doc = repos.get_ethics_research(db, "starbucks")
    assert doc["merchant_name"] == "Starbucks"
    assert doc["data_source"] == "gemini"
    assert doc["categories"][0]["claims"][0]["source_url"] == "https://example.org/a"
    assert doc["researched_at"].tzinfo is not None


def test_saving_again_replaces_the_previous_research(db):
    _save_ethics(db)
    repos.save_ethics_research(db, "starbucks", "Starbucks", [], "gemini")
    assert repos.get_ethics_research(db, "starbucks")["categories"] == []
    assert db.ethics_reports.count_documents({}) == 1


def test_researched_at_by_id_reports_only_cached_brands(db):
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _save_ethics(db, when=when)
    assert repos.researched_at_by_id(db, repos.ETHICS_REPORTS, ["starbucks", "amazon"]) == {"starbucks": when}


def test_supply_chain_round_trip_and_batch_read(db):
    repos.save_supply_chain(db, "starbucks", "Latte", [s.model_dump() for s in STAGES], "gemini")
    assert repos.get_supply_chain(db, "starbucks")["product"] == "Latte"
    assert set(repos.get_supply_chains(db, ["starbucks", "amazon"])) == {"starbucks"}


# ---------- ethics read path ----------
def test_cached_research_is_served_instead_of_mock(db):
    _save_ethics(db)
    report = ethics.get_report(db, "starbucks", "Starbucks", UserProfile())
    assert report.data_source == "gemini"
    assert report.categories[0].summary == "Real research summary."
    assert report.categories[0].claims[0].source_url == "https://example.org/a"


def test_personalized_score_follows_the_profile_without_invalidation(db):
    _save_ethics(db)
    labor_only = UserProfile(weights={"labor": 5})
    env_only = UserProfile(weights={"environment": 5})
    assert ethics.get_report(db, "starbucks", "Starbucks", labor_only).personalized_score == 20
    assert ethics.get_report(db, "starbucks", "Starbucks", env_only).personalized_score == 80


def test_mock_fallback_is_served_but_never_cached(db):
    report = ethics.get_report(db, "starbucks", "Starbucks", UserProfile())
    assert report.data_source == "mock"
    assert repos.get_ethics_research(db, "starbucks") is None


def test_unknown_brand_without_cache_or_mock_has_no_report(db):
    assert ethics.get_report(db, "local-cafe", "Local Cafe", UserProfile()) is None


def test_api_reads_never_research_live_even_with_gemini_configured(db, gemini_on, monkeypatch):
    monkeypatch.setattr(ethics, "research_with_gemini", lambda name: pytest.fail("public read called Gemini"))
    assert ethics.get_report(db, "starbucks", "Starbucks", UserProfile()).data_source == "mock"
    assert repos.get_ethics_research(db, "starbucks") is None


# ---------- ethics refresh (used by prewarm) ----------
def test_refresh_requires_a_gemini_key(db, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(ResearchUnavailable):
        ethics.refresh(db, "starbucks", "Starbucks")


def test_refresh_reports_unimplemented_research_as_unavailable(db, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "key")
    with pytest.raises(ResearchUnavailable):
        ethics.refresh(db, "starbucks", "Starbucks")  # research_with_gemini is still a stub


def test_refresh_validates_then_saves_even_in_mock_mode(db, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "key")
    unsourced = [CategoryScore(category="labor", score=50, summary="s", claims=[Claim(text="x", confidence=0.99)])]
    monkeypatch.setattr(ethics, "research_with_gemini", lambda name: unsourced)
    ethics.refresh(db, "starbucks", "Starbucks")
    saved = repos.get_ethics_research(db, "starbucks")
    assert saved["categories"][0]["claims"][0]["validated"] is False  # no source, never validated


# ---------- supply chains ----------
def test_cached_supply_chain_wins_over_mock(db):
    repos.save_supply_chain(db, "starbucks", "Cold brew", [s.model_dump() for s in STAGES], "gemini")
    chain = supply_chain.get_supply_chain(db, "starbucks")
    assert (chain.product, chain.data_source) == ("Cold brew", "gemini")


def test_supply_chain_falls_back_to_mock_without_caching(db):
    assert supply_chain.get_supply_chain(db, "starbucks").data_source == "mock"
    assert repos.get_supply_chain(db, "starbucks") is None


def test_supply_chains_batch_mixes_cache_and_mock_in_order(db):
    repos.save_supply_chain(db, "starbucks", "Cold brew", [s.model_dump() for s in STAGES], "gemini")
    chains = supply_chain.get_supply_chains(db, ["amazon", "local-cafe", "starbucks"])
    assert [(c.merchant_id, c.data_source) for c in chains] == [("amazon", "mock"), ("starbucks", "gemini")]


def test_supply_chain_refresh_is_unavailable_until_implemented(db, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "key")
    with pytest.raises(ResearchUnavailable):
        supply_chain.refresh(db, "starbucks", "Starbucks")


def test_supply_chain_refresh_saves_research(db, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(supply_chain, "research_supply_chain", lambda name: ("Cold brew", STAGES))
    supply_chain.refresh(db, "starbucks", "Starbucks")
    assert repos.get_supply_chain(db, "starbucks")["stages"][0]["place"] == "Huila, Colombia"


def test_old_cache_is_still_served(db):
    _save_ethics(db, when=datetime.now(timezone.utc) - timedelta(days=400))
    assert ethics.get_report(db, "starbucks", "Starbucks", UserProfile()).data_source == "gemini"
