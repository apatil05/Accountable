"""Gemini verdict blurbs: written by prewarm, stored per (customer, brand), served on read only
while they still match the profile, verdict and research they were written for."""

from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.db import repos
from app.models import CategoryScore, Claim, UserProfile
from app.services import ethics, gemini, ingest, prewarm, verdict
from app.services.research import ResearchUnavailable
from scripts import prewarm as prewarm_script

CUSTOMER = "cust_demo"
WHEN = datetime(2026, 11, 1, tzinfo=timezone.utc)
RESEARCH = [
    CategoryScore(category="labor", score=20, summary="Poor labor record.",
                  claims=[Claim(text="Fined for union busting.", source_url="https://example.org/a",
                                confidence=0.9, validated=True)]),
    CategoryScore(category="environment", score=80, summary="Good record.", claims=[]),
]
PROFILE = UserProfile(weights={"labor": 4, "environment": 2})
PROSE = "Starbucks falls short on how it treats workers, which you care about. It was fined for union busting."


@pytest.fixture
def gemini_on(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(config, "USE_MOCK_DATA", False)


@pytest.fixture
def fake_gemini(monkeypatch):
    """Returns queued blurbs in order (the last one repeats) and records each call."""
    calls: list[str] = []
    queue = [PROSE]

    def fake(name, *args):
        calls.append(name)
        return queue[min(len(calls), len(queue)) - 1]

    monkeypatch.setattr(verdict, "_gemini_blurb", fake)
    return queue, calls


def _research(db, when=WHEN):
    repos.save_ethics_research(db, "starbucks", "Starbucks", [c.model_dump() for c in RESEARCH], "gemini", when)


def _verdict(db, profile=PROFILE, customer_id=CUSTOMER):
    return ethics.get_report(db, "starbucks", "Starbucks", profile, customer_id).verdict


# ---------- read path ----------
def test_stored_blurb_is_served_while_it_matches(db, gemini_on, fake_gemini):
    _research(db)
    verdict.refresh_blurb(db, CUSTOMER, "starbucks", "Starbucks", PROFILE)
    v = _verdict(db)
    assert (v.source, v.blurb) == ("gemini", PROSE)
    # Another customer, or no customer, never sees it.
    assert _verdict(db, customer_id="someone_else").source == "template"
    assert _verdict(db, customer_id=None).source == "template"


@pytest.mark.parametrize("change", ["weights", "stances", "research", "prompt"])
def test_stale_blurb_falls_back_to_template(db, gemini_on, fake_gemini, monkeypatch, change):
    _research(db)
    verdict.refresh_blurb(db, CUSTOMER, "starbucks", "Starbucks", PROFILE)
    profile = PROFILE
    if change == "weights":
        profile = PROFILE.model_copy(update={"weights": {"labor": 1, "environment": 5}})
    elif change == "stances":
        profile = PROFILE.model_copy(update={"stances": "I care about unions."})
    elif change == "research":
        _research(db, WHEN + timedelta(days=1))
    else:
        monkeypatch.setattr(verdict, "PROMPT_VERSION", verdict.PROMPT_VERSION + 1)
    v = _verdict(db, profile)
    assert v.source == "template" and v.blurb != PROSE
    assert verdict.stale_blurb_ids(db, CUSTOMER, [("starbucks", "Starbucks")], profile) == {"starbucks"}


def test_reads_never_call_gemini_even_with_a_stored_blurb(db, gemini_on, fake_gemini, monkeypatch):
    _research(db)
    verdict.refresh_blurb(db, CUSTOMER, "starbucks", "Starbucks", PROFILE)
    monkeypatch.setattr(verdict, "_gemini_blurb", lambda *a: pytest.fail("read path called Gemini"))
    monkeypatch.setattr(gemini, "client", lambda: pytest.fail("read path called Gemini"))
    assert _verdict(db).source == "gemini"
    assert _verdict(db, PROFILE.model_copy(update={"stances": "x"})).source == "template"


def test_mock_mode_without_keys_uses_the_template(db, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "USE_MOCK_DATA", True)
    assert _verdict(db).source == "template"


# ---------- write path ----------
def test_refresh_requires_a_gemini_key(db, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    _research(db)
    with pytest.raises(ResearchUnavailable):
        verdict.refresh_blurb(db, CUSTOMER, "starbucks", "Starbucks", PROFILE)


def test_refresh_without_cached_research_fails(db, gemini_on, fake_gemini):
    with pytest.raises(ValueError, match="no cached research"):
        verdict.refresh_blurb(db, CUSTOMER, "starbucks", "Starbucks", PROFILE)


def test_a_blurb_that_mentions_a_score_is_retried_then_rejected(db, gemini_on, fake_gemini):
    queue, calls = fake_gemini
    queue[:] = ["Starbucks scores 31 out of 100 on your priorities."]
    _research(db)
    with pytest.raises(ValueError, match="mentions a score"):
        verdict.refresh_blurb(db, CUSTOMER, "starbucks", "Starbucks", PROFILE)
    assert len(calls) == verdict.MAX_ATTEMPTS
    assert repos.get_verdict_blurb(db, CUSTOMER, "starbucks") is None


def test_a_bad_first_attempt_is_replaced_by_a_good_retry(db, gemini_on, fake_gemini):
    queue, calls = fake_gemini
    queue[:] = ["Its fit score is 31/100.", PROSE]
    _research(db)
    assert verdict.refresh_blurb(db, CUSTOMER, "starbucks", "Starbucks", PROFILE) == PROSE
    assert len(calls) == 2


@pytest.mark.parametrize("text, problem", [
    (PROSE, None),
    ("Walmart's U.S. stores cut emissions 30%. It pays below a living wage. Inc. filings show fines.", None),
    ("One. Two. Three. Four.", "more than 3 sentences"),
    ("It scored poorly.", "mentions a score"),
    ("   ", "empty"),
])
def test_blurb_problem(text, problem):
    assert verdict.blurb_problem(text) == problem


# ---------- prewarm ----------
def test_prewarm_skips_fresh_blurbs_and_redoes_stale_ones(db, gemini_on, fake_gemini):
    _, calls = fake_gemini
    _research(db)
    targets = [("starbucks", "Starbucks")]

    def run(profile):
        stale = verdict.stale_blurb_ids(db, CUSTOMER, targets, profile)
        refresh = lambda db_, bid, name: verdict.refresh_blurb(db_, CUSTOMER, bid, name, profile)  # noqa: E731
        results = prewarm.prewarm(db, targets, repos.VERDICT_BLURBS, refresh, max_age=timedelta(days=30),
                                  force=False, delay=0, now=WHEN, stale=stale)
        return [r.status for r in results]

    assert run(PROFILE) == ["researched"]
    assert run(PROFILE) == ["fresh"]
    assert run(PROFILE.model_copy(update={"stances": "new"})) == ["researched"]
    assert len(calls) == 2


@pytest.fixture
def cli_db(db, monkeypatch):
    monkeypatch.setattr(prewarm_script, "get_db", lambda: db)
    ingest.sync_customer(db, CUSTOMER)
    return db


def test_cli_blurbs_dry_run_writes_nothing(cli_db, fake_gemini, capsys):
    _, calls = fake_gemini
    assert prewarm_script.main(["--only", "blurbs", "--dry-run"]) == 0
    assert "would research" in capsys.readouterr().out
    assert calls == []


def test_cli_rejects_blurbs_for_all_brands():
    with pytest.raises(SystemExit):
        prewarm_script.main(["--only", "blurbs", "--all-brands"])
