"""Features 3 + 4: research a merchant's ethics and return a scored, sourced report.

Owner: Person B (Research engine).

Right now this returns mock data from data/mock_ethics.json.
The plan (docs/PLAN.md, "Research engine") is:

  1. Gemini with Google Search grounding researches the merchant per category,
     preferring the user's trusted_sources, and returns JSON matching
     CategoryScore (score, summary, claims[] with source_url).
  2. validation.validate_claims() checks every claim (URL resolves, page
     mentions the merchant, source is not on a blocklist) and sets
     claim.validated. The LLM never sets validated itself.
  3. Cache the result (Postgres/Mongo) so we don't re-research on every page load.
"""

import json
from datetime import datetime, timezone
from functools import lru_cache

from .. import config
from ..models import CategoryScore, EthicsReport, UserProfile
from . import scoring, validation


@lru_cache
def _mock() -> dict:
    return json.loads((config.DATA_DIR / "mock_ethics.json").read_text())


# In-memory cache; swap for a DB table later.
_cache: dict[str, EthicsReport] = {}


def research_with_gemini(merchant_name: str, profile: UserProfile) -> list[CategoryScore]:
    """TODO(Person B): implement with google-genai + Google Search grounding.

    Sketch:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=PROMPT.format(merchant=merchant_name, sources=profile.trusted_sources),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        # parse JSON from resp.text; attach grounding_metadata URLs as claim sources
    """
    raise NotImplementedError


def cached_report(merchant_id: str) -> EthicsReport | None:
    return _cache.get(merchant_id)


def get_report(merchant_id: str, merchant_name: str, profile: UserProfile) -> EthicsReport | None:
    raw = _mock().get(merchant_id)
    data_source = "mock"
    if config.GEMINI_API_KEY and not config.USE_MOCK_DATA:
        try:
            categories = research_with_gemini(merchant_name, profile)
            data_source = "gemini"
        except NotImplementedError:
            categories = [CategoryScore(**c) for c in raw["categories"]] if raw else []
    elif raw:
        categories = [CategoryScore(**c) for c in raw["categories"]]
    else:
        return None

    categories = validation.validate_claims(merchant_name, categories)
    report = EthicsReport(
        merchant_id=merchant_id,
        merchant_name=merchant_name,
        overall_score=scoring.overall(categories),
        personalized_score=scoring.personalized(categories, profile),
        categories=categories,
        data_source=data_source,  # type: ignore[arg-type]
        researched_at=datetime.now(timezone.utc).isoformat(),
    )
    _cache[merchant_id] = report
    return report


def warm_cache(merchants: list[tuple[str, str]], profile: UserProfile) -> None:
    for mid, name in merchants:
        if mid not in _cache:
            get_report(mid, name, profile)
