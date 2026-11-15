"""Judge retries: one flaky Gemini answer must not throw away a whole brand's research.
No network: the page fetch and the judge are faked."""

import httpx
import pytest

from app.models import CategoryScore, Claim
from app.services import validation

URL = "https://news.example.com/story"


class FakeJudge:
    """Raises the queued errors in order, then answers 'supported'."""

    def __init__(self, *errors: Exception):
        self.errors, self.calls = list(errors), 0

    def __call__(self, merchant, url, tier, claims, page_text):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return {0: validation._Judged(index=1, verdict="supported", quote="", rewritten_text="Acme was fined.",
                                      event_year=2025, category="labor", direction="negative", severity="major")}


@pytest.fixture
def setup(monkeypatch):
    def configure(page_text: str | None, judge: FakeJudge):
        monkeypatch.setattr(validation, "fetch_page", lambda url: validation.Page(url, page_text))
        monkeypatch.setattr(validation, "domain_tier", lambda url, merchant: 3)
        monkeypatch.setattr(validation, "judge", judge)
        monkeypatch.setattr(validation, "_sleep", lambda s: None)
        return [CategoryScore(category="labor", summary="", claims=[Claim(text="Acme was fined.", source_url=URL)])]
    return configure


def test_a_flaky_judge_answer_is_retried_and_the_claim_kept(setup):
    judge = FakeJudge(validation.JudgeNoJSON(f"judge returned no JSON for {URL}"))
    cats, complete = validation.validate_claims("Acme", setup(None, judge))
    assert complete and judge.calls == 2
    assert cats[0].claims[0].validated


def test_a_source_gemini_can_never_read_drops_its_claims_but_keeps_the_brand(setup):
    judge = FakeJudge(*[validation.JudgeNoJSON(f"judge returned no JSON for {URL}")] * validation._JUDGE_ATTEMPTS)
    cats, complete = validation.validate_claims("Acme", setup(None, judge))
    assert complete and judge.calls == validation._JUDGE_ATTEMPTS
    assert not cats[0].claims[0].validated


def test_a_persistent_api_error_still_marks_the_brand_incomplete(setup):
    judge = FakeJudge(*[RuntimeError("503 UNAVAILABLE")] * validation._JUDGE_ATTEMPTS)
    _, complete = validation.validate_claims("Acme", setup("Acme was fined. " * 60, judge))
    assert not complete and judge.calls == validation._JUDGE_ATTEMPTS


def test_a_rate_limit_is_retried(setup):
    judge = FakeJudge(RuntimeError("429 RESOURCE_EXHAUSTED"))
    _, complete = validation.validate_claims("Acme", setup(None, judge))
    assert complete and judge.calls == 2


def test_a_timeout_is_not_retried(setup):
    judge = FakeJudge(httpx.ReadTimeout("slow"))
    cats, complete = validation.validate_claims("Acme", setup(None, judge))
    assert complete and judge.calls == 1  # 30s caps are expensive; timeouts already drop the claims
    assert not cats[0].claims[0].validated
