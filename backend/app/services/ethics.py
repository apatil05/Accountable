"""Features 3 + 4: research a merchant's ethics and return a scored, sourced report.

Owner: Person B (Research engine). Cache plumbing: Person A.

The plan (docs/PLAN.md, "Research engine") is:

  1. Gemini with Google Search grounding researches the merchant per category and
     returns JSON matching CategoryScore (score, summary, claims[] with source_url).
  2. validation.validate_claims() checks every claim (URL resolves, page mentions the
     merchant, source is not on a blocklist) and sets claim.validated. The LLM never
     sets validated itself.
  3. The validated categories are cached in the `ethics_reports` collection, one document
     per brand slug. `python -m scripts.prewarm` fills it before the demo.

Read path: cached research -> mock data (the API never researches live). Research is
profile-independent; personalized scores are computed from the profile at read time.
Mock data is never written to the cache.
"""

import json
from functools import lru_cache
from typing import Iterable

from pymongo.database import Database

from .. import config
from ..db import repos
from ..models import CategoryScore, EthicsReport, UserProfile
from . import scoring, validation
from .research import ResearchUnavailable, ensure_available

@lru_cache
def _mock() -> dict:
    return json.loads((config.DATA_DIR / "mock_ethics.json").read_text())


def research_with_gemini(merchant_name: str) -> list[CategoryScore]:
    """TODO(Person B): implement with google-genai + Google Search grounding.

    Profile-independent on purpose: the result is cached once per brand and shared by
    every user. Apply the user's trusted_sources at validation or display time instead.

    Sketch:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=PROMPT.format(merchant=merchant_name),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        # parse JSON from resp.text; attach grounding_metadata URLs as claim sources
    """
    raise NotImplementedError


def refresh(db: Database, merchant_id: str, merchant_name: str) -> list[CategoryScore]:
    """Research now, validate, and overwrite the cache. Used by prewarm and on cache misses.

    Raises ResearchUnavailable when research cannot run here (no key, not implemented)."""
    ensure_available(config.GEMINI_API_KEY)
    try:
        categories = research_with_gemini(merchant_name)
    except NotImplementedError:
        raise ResearchUnavailable("ethics research (research_with_gemini) is not implemented yet") from None
    validated = validation.validate_claims(merchant_name, categories)
    repos.save_ethics_research(db, merchant_id, merchant_name, [c.model_dump() for c in validated], "gemini")
    return validated


def _build(merchant_id: str, merchant_name: str, categories: list[CategoryScore], data_source: str,
           researched_at: str | None, profile: UserProfile) -> EthicsReport:
    return EthicsReport(
        merchant_id=merchant_id,
        merchant_name=merchant_name,
        overall_score=scoring.overall(categories),
        personalized_score=scoring.personalized(categories, profile),
        categories=categories,
        data_source=data_source,  # type: ignore[arg-type]
        researched_at=researched_at,
    )


def _from_cache(doc: dict, merchant_name: str, profile: UserProfile) -> EthicsReport:
    categories = [CategoryScore(**c) for c in doc["categories"]]
    return _build(doc["_id"], merchant_name, categories, doc["data_source"],
                  doc["researched_at"].isoformat(), profile)


def _from_mock(merchant_id: str, merchant_name: str, profile: UserProfile) -> EthicsReport | None:
    raw = _mock().get(merchant_id)
    if not raw:
        return None
    categories = validation.validate_claims(merchant_name, [CategoryScore(**c) for c in raw["categories"]])
    return _build(merchant_id, merchant_name, categories, "mock", None, profile)


def get_report(db: Database, merchant_id: str, merchant_name: str, profile: UserProfile) -> EthicsReport | None:
    """Cached research, else mock data. Never calls Gemini: this runs on public, unauthenticated
    requests, and a paid call per cache miss (retried on every failure) is a cost-abuse vector.
    Research happens only in `python -m scripts.prewarm`."""
    cached = repos.get_ethics_research(db, merchant_id)
    if cached:
        return _from_cache(cached, merchant_name, profile)
    return _from_mock(merchant_id, merchant_name, profile)


def reports_for(db: Database, merchants: Iterable[tuple[str, str]], profile: UserProfile) -> dict[str, EthicsReport]:
    """Cached-or-mock reports for many merchants in one query. Never researches live, so
    the spending summary stays fast on a cold cache."""
    pairs = list(merchants)
    cached = {d["_id"]: d for d in repos.get_ethics_researches(db, [mid for mid, _ in pairs])}
    reports = {
        mid: _from_cache(cached[mid], name, profile) if mid in cached else _from_mock(mid, name, profile)
        for mid, name in pairs
    }
    return {mid: r for mid, r in reports.items() if r}
