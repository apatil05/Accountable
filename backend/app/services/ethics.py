"""Features 3 + 4: research a merchant's ethics and return a scored, sourced report.

Owner: Person B (Research engine).

Pipeline:
  1. research (profile-independent, cached in _research_cache):
     mock JSON, or Gemini with Google Search grounding when gemini.enabled().
  2. validation.validate_claims() sets claim.validated. The LLM never does.
  3. scoring: overall + personalized score, then the aligns/mixed/against verdict
     and its blurb (services/verdict.py). These depend on the user's profile,
     so the finished report lives in _cache, which PUT /api/profile clears
     without throwing away the (slow, paid) research.

TODO: persist _research_cache to the DB so research survives a restart.
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from .. import config
from ..models import CATEGORIES, CategoryScore, Claim, EthicsReport, UserProfile
from . import gemini, scoring, validation
from .verdict import build_verdict

log = logging.getLogger(__name__)


@lru_cache
def _mock() -> dict:
    return json.loads((config.DATA_DIR / "mock_ethics.json").read_text())


# Profile-dependent finished reports (cleared by PUT /api/profile in main.py).
_cache: dict[str, EthicsReport] = {}
# Profile-independent research: key -> (categories, data_source, researched_at).
_research_cache: dict[str, tuple[list[CategoryScore], str, str]] = {}
_research_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


# ---------- Gemini research ----------

_AREAS = {
    "labor": "wages, unionization, working conditions, safety, and labor practices in its supply chain",
    "environment": "emissions, climate commitments and progress, pollution, packaging and waste",
    "political": "lobbying and political donations (report recipients and amounts factually); "
    "rate transparency and legal/ethical controversy, NOT which side it supports",
    "animal_welfare": "animal testing, sourcing of animal products, and welfare commitments",
    "community": "effect on local businesses and communities, taxes paid, philanthropy",
    "privacy": "customer data collection and sale, breaches, and privacy fines",
}

_RESEARCH_PROMPT = """Research the ethical track record of the company "{merchant}", a business that consumers shop at.
Use Google Search. Cover each of these areas, citing concrete, verifiable facts (prefer the last 10 years), both positive and negative:
{areas}
{sources}
Report facts only, no recommendations. For each area, finish with a rating from 0 to 100
(100 = exemplary, 50 = typical for its industry, 0 = egregious) and one sentence of reasoning.
"""

_STRUCTURE_PROMPT = """Convert the research below into JSON.

Rules:
- One entry per area, using exactly these keys for "category": {keys}.
- score: the 0-100 rating given in the research for that area (50 if none was given).
- summary: 2 sentences, neutral and factual.
- claims: 2-4 specific factual claims for that area, taken from the CITED STATEMENTS.
  "source" is the number of the source that supports the claim, chosen from the numbers listed
  next to that statement. Use 0 if no listed source supports it. Never invent a number.
- confidence: 0-1, how directly the source supports the claim.
- Do not add facts that are not in the research.

RESEARCH:
{text}

CITED STATEMENTS (statement -> source numbers):
{cited}

SOURCES:
{sources}
"""


class _ClaimOut(BaseModel):
    text: str
    source: int  # 1-based index into the grounding sources, 0 = none
    confidence: float


class _CategoryOut(BaseModel):
    category: str
    score: float
    summary: str
    claims: list[_ClaimOut]


class _ResearchOut(BaseModel):
    categories: list[_CategoryOut]


def _resolve(uri: str) -> str:
    """Grounding URIs are Google redirect links. Read the Location header
    (one request to Google, no visit to the target site). Keep the original on failure."""
    try:
        with httpx.Client(timeout=5.0, follow_redirects=False) as c:
            r = c.get(uri)
            loc = r.headers.get("location")
            return loc if r.is_redirect and loc else uri
    except httpx.HTTPError:
        return uri


def research_with_gemini(merchant_name: str, profile: UserProfile) -> list[CategoryScore]:
    """Two calls (docs/PLAN.md): grounded research in free text, then structure
    it as JSON. URLs come only from grounding metadata, never from model text."""
    from google.genai import types

    client = gemini.client()
    areas = "\n".join(f"- {k}: {v}" for k, v in _AREAS.items())
    sources_hint = (
        f"Prefer these sources when they cover the topic: {', '.join(profile.trusted_sources)}.\n"
        if profile.trusted_sources
        else ""
    )
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=_RESEARCH_PROMPT.format(merchant=merchant_name, areas=areas, sources=sources_hint),
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    text = resp.text or ""
    if not text.strip():
        raise ValueError("Gemini returned no research text")

    meta = resp.candidates[0].grounding_metadata if resp.candidates else None
    chunks = list((meta.grounding_chunks if meta else None) or [])
    supports = list((meta.grounding_supports if meta else None) or [])
    # 1-based source number -> (uri, title)
    sources: dict[int, tuple[str, str]] = {
        i + 1: (ch.web.uri, ch.web.title or "")
        for i, ch in enumerate(chunks)
        if ch.web and ch.web.uri
    }
    cited = "\n".join(
        f'- "{s.segment.text}" -> {[i + 1 for i in s.grounding_chunk_indices if i + 1 in sources]}'
        for s in supports
        if s.segment and s.segment.text and s.grounding_chunk_indices
    )

    structured = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=_STRUCTURE_PROMPT.format(
            keys=", ".join(CATEGORIES),
            text=text,
            cited=cited or "(none)",
            sources="\n".join(f"[{n}] {title}" for n, (_, title) in sources.items()) or "(none)",
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_ResearchOut,
            temperature=0.1,
        ),
    )
    out = (
        structured.parsed
        if isinstance(structured.parsed, _ResearchOut)
        else _ResearchOut.model_validate_json(structured.text)
    )

    used = {cl.source for c in out.categories for cl in c.claims if cl.source in sources}
    with ThreadPoolExecutor(max_workers=8) as pool:
        resolved = dict(zip(used, pool.map(lambda n: _resolve(sources[n][0]), used)))

    categories: dict[str, CategoryScore] = {}
    for c in out.categories:
        if c.category not in CATEGORIES or c.category in categories:
            continue
        claims = []
        for cl in c.claims:
            url = resolved.get(cl.source)
            name = (sources[cl.source][1] or urlparse(url).netloc) if url else None
            claims.append(
                Claim(
                    text=cl.text,
                    source_url=url,
                    source_name=name,
                    confidence=max(0.0, min(1.0, cl.confidence)),
                )
            )
        categories[c.category] = CategoryScore(
            category=c.category,
            score=max(0.0, min(100.0, c.score)),
            summary=c.summary,
            claims=claims,
        )
    if not categories:
        raise ValueError("Gemini returned no usable categories")
    return [categories[k] for k in CATEGORIES if k in categories]


# ---------- research cache ----------


def _research_key(merchant_id: str, profile: UserProfile) -> str:
    return merchant_id + "|" + ",".join(sorted(s.strip().lower() for s in profile.trusted_sources))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _research(merchant_id: str, merchant_name: str, profile: UserProfile) -> tuple[list[CategoryScore], str, str] | None:
    key = _research_key(merchant_id, profile)
    with _locks_guard:
        lock = _research_locks.setdefault(key, threading.Lock())
    with lock:  # one research call per merchant even with concurrent requests
        if key in _research_cache:
            return _research_cache[key]

        raw = _mock().get(merchant_id)
        if gemini.enabled():
            try:
                log.info("Researching %s with Gemini", merchant_name)
                result = (research_with_gemini(merchant_name, profile), "gemini", _now())
                _research_cache[key] = result
                return result
            except Exception:
                log.exception("Gemini research failed for %s; falling back to mock", merchant_name)
                # Not cached, so it is retried after the next profile change / restart.
                if raw:
                    return [CategoryScore(**c) for c in raw["categories"]], "mock", _now()
                return None
        if not raw:
            return None
        result = ([CategoryScore(**c) for c in raw["categories"]], "mock", _now())
        _research_cache[key] = result
        return result


# ---------- public API ----------


def cached_report(merchant_id: str) -> EthicsReport | None:
    return _cache.get(merchant_id)


def get_report(
    merchant_id: str, merchant_name: str, profile: UserProfile, ai_blurb: bool = True
) -> EthicsReport | None:
    """ai_blurb=False skips the Gemini blurb call (used when pre-warming the
    dashboard); the report is upgraded to a Gemini blurb when it's next opened."""
    cached = _cache.get(merchant_id)
    if cached:
        if ai_blurb and gemini.enabled() and cached.verdict.source == "template":
            cached.verdict = build_verdict(
                merchant_name, cached.categories, profile, cached.personalized_score, use_ai=True
            )
        return cached

    research = _research(merchant_id, merchant_name, profile)
    if research is None:
        return None
    raw_categories, data_source, researched_at = research

    # Copy so validation never mutates the shared research cache.
    categories = validation.validate_claims(merchant_name, [c.model_copy(deep=True) for c in raw_categories])
    personalized = scoring.personalized(categories, profile)
    report = EthicsReport(
        merchant_id=merchant_id,
        merchant_name=merchant_name,
        overall_score=scoring.overall(categories),
        personalized_score=personalized,
        categories=categories,
        verdict=build_verdict(merchant_name, categories, profile, personalized, use_ai=ai_blurb),
        data_source=data_source,  # type: ignore[arg-type]
        researched_at=researched_at,
    )
    _cache[merchant_id] = report
    return report


def warm_cache(merchants: list[tuple[str, str]], profile: UserProfile) -> None:
    todo = [(mid, name) for mid, name in merchants if mid not in _cache]
    if not todo:
        return
    # Concurrent so a cold dashboard in Gemini mode isn't N serial grounded calls.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda m: get_report(m[0], m[1], profile, ai_blurb=False), todo))
