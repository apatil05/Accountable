"""Features 3 + 4: research a merchant's ethics and return a scored, sourced report.

Owner: Person B (Research engine). Cache plumbing: Person A.

Write path (`python -m scripts.prewarm`, which calls refresh()):
  1. research_with_gemini(): Gemini with Google Search grounding, then a second call that
     structures it as JSON. URLs come only from grounding metadata.
  2. validation.validate_claims() checks each claim against its source page and sets
     claim.validated (docs/VALIDATION_PLAN.md). The LLM never sets validated itself.
  3. Unvalidated claims are dropped, score_categories() computes each category's score in
     code from the validated claims (score=None when none carry weight), and summarize()
     has Gemini rewrite the summaries from those claims only.
  4. The result is saved in the `ethics_reports` collection, one document per brand slug.
     If validation or the summary fails partway, nothing is saved and refresh() raises,
     so prewarm reports the brand as failed and retries it on the next run.

Read path (get_report / reports_for): cached research -> mock data. Never calls Gemini.
Research is profile-independent; the overall and personalized scores and the verdict
are computed from the user's profile at read time. The verdict blurb is the Gemini one
that prewarm stored for this customer when it still matches, else the template.
Mock data is never written to the cache.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from typing import Iterable
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel
from pymongo.database import Database

from .. import config
from ..db import repos
from ..models import CATEGORIES, CategoryScore, Claim, EthicsReport, UserProfile
from . import gemini, scoring, validation
from .research import ensure_available
from .verdict import build_verdict

log = logging.getLogger(__name__)


@lru_cache
def _mock() -> dict:
    return json.loads((config.DATA_DIR / "mock_ethics.json").read_text())


# ---------- Gemini research ----------

_AREAS = {
    "labor": "wages, unionization, working conditions, safety, and labor practices in its supply chain",
    "environment": "emissions, climate commitments and progress, pollution, packaging and waste",
    "animal_welfare": "animal testing, sourcing of animal products, and welfare commitments",
    "community": "effect on local businesses and communities, taxes paid, philanthropy",
    "privacy": "customer data collection and sale, breaches, and privacy fines",
}

_RESEARCH_PROMPT = """Research the ethical track record of the company "{merchant}", a business that consumers shop at.
Use Google Search. Cover each of these areas, citing concrete, verifiable facts (prefer the last 10 years), both positive and negative.
Find at least 5 distinct facts per area, each from a specific source:
{areas}

Source quality: prefer government and court records, regulators, established news outlets,
and recognized ratings organizations, then the company's own reports. Prefer freely readable web
articles over PDFs and paywalled pages. Never use forums or social media (Reddit, X, Facebook,
TikTok, Quora, YouTube), Wikipedia, or review sites (Glassdoor, Indeed).

Report facts only, no recommendations. For each area, finish with a rating from 0 to 100
(100 = exemplary, 50 = typical for its industry, 0 = egregious) and one sentence of reasoning.
"""

_STRUCTURE_PROMPT = """Convert the research below into JSON.

Rules:
- One entry per area, using exactly these keys for "category": {keys}.
- score: the 0-100 rating given in the research for that area (50 if none was given).
- summary: 1 short sentence (25 words max), neutral and factual.
- claims: 4-6 specific factual claims for that area, taken from the CITED STATEMENTS.
  Each claim states one fact. Prefer claims with a source over claims without one.
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


def research_with_gemini(merchant_name: str) -> list[CategoryScore]:
    """Two calls (docs/PLAN.md): grounded research in free text, then structure
    it as JSON. URLs come only from grounding metadata, never from model text."""
    from google.genai import types

    client = gemini.client()
    areas = "\n".join(f"- {k}: {v}" for k, v in _AREAS.items())
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=_RESEARCH_PROMPT.format(merchant=merchant_name, areas=areas),
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


# ---------- scoring and summaries on validated evidence ----------

NO_EVIDENCE_SUMMARY = "Not enough verified evidence was found for this category."

_SUMMARY_PROMPT = """Summarize what the verified facts below say about the company "{merchant}" in each area.
Use ONLY the listed facts. Do not use outside knowledge, and do not rate or score the company.

Each fact is marked (+) if it reflects well on the company and (-) if it reflects badly, with the
year in brackets when known. Give each area's summary as ONE short, neutral, factual sentence
(25 words max) covering the most important fact. When a fact is attributed ("According to X...", "A lawsuit alleges...",
"{merchant} says..."), keep that attribution; never restate an attributed claim as established fact.

{areas}
"""


class _SummarizedCategory(BaseModel):
    category: str
    summary: str


class _SummaryOut(BaseModel):
    categories: list[_SummarizedCategory]


def score_categories(merchant_name: str, categories: list[CategoryScore]) -> None:
    """Set each category's score from its validated claims in code (scoring.category_score),
    in place. Categories with no weighted evidence get score=None and the no-evidence summary."""
    year = datetime.now(timezone.utc).year
    for c in categories:
        c.score = scoring.category_score(c.claims, year)
        if c.score is None:
            c.summary = NO_EVIDENCE_SUMMARY
            continue
        # One line per category so a score can be explained from the logs.
        log.info(
            "score merchant=%r category=%s score=%s claims=[%s]",
            merchant_name, c.category, c.score,
            "; ".join(
                f"{cl._direction}/{cl._severity}/tier{cl._tier}/{cl._event_year or '?'} "
                f"w={scoring.claim_weight(cl, year):.2f}"
                for cl in c.claims
            ),
        )


def summarize(merchant_name: str, categories: list[CategoryScore]) -> None:
    """Replace each scored category's research summary with one written from its validated
    claims only (in place). Raises if Gemini fails or skips a category."""
    from google.genai import types

    scored = [c for c in categories if c.score is not None and c.claims]
    if not scored:
        return
    sign = {"positive": "(+) ", "negative": "(-) "}
    areas = "\n\n".join(
        f"{c.category}:\n"
        + "\n".join(
            f"- {sign.get(cl._direction, '')}{f'[{cl._event_year}] ' if cl._event_year else ''}{cl.text}"
            for cl in c.claims
        )
        for c in scored
    )
    resp = gemini.client().models.generate_content(
        model=config.GEMINI_MODEL,
        contents=_SUMMARY_PROMPT.format(merchant=merchant_name, areas=areas),
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=_SummaryOut, temperature=0.1
        ),
    )
    out = resp.parsed if isinstance(resp.parsed, _SummaryOut) else _SummaryOut.model_validate_json(resp.text)
    got = {r.category: r for r in out.categories}
    missing = [c.category for c in scored if c.category not in got]
    if missing:
        raise ValueError(f"summary skipped categories {missing}")
    for c in scored:
        c.summary = got[c.category].summary.strip() or c.summary


# ---------- write path (prewarm) ----------


def run_pipeline(merchant_name: str, research: list[CategoryScore] | None = None) -> list[CategoryScore]:
    """Research (unless `research` is given), validate, drop unvalidated claims, score, and
    summarize. Touches no database. Raises when any step fails partway."""
    if research is None:
        research = research_with_gemini(merchant_name)
    # Copy so a caller's research (e.g. reused across eval runs) is never mutated.
    categories = [c.model_copy(deep=True) for c in research]
    categories, complete = validation.validate_claims(merchant_name, categories)
    if not complete:
        raise RuntimeError(f"validation incomplete for {merchant_name} (a judge call failed); not saved")
    # Unvalidated claims are never shown (CLAUDE.md), so they are never saved either.
    for c in categories:
        c.claims = [cl for cl in c.claims if cl.validated]
    # Score before saving: the claim labels scoring needs are private fields that are not stored.
    score_categories(merchant_name, categories)
    summarize(merchant_name, categories)
    return categories


def refresh(db: Database, merchant_id: str, merchant_name: str) -> list[CategoryScore]:
    """Run the pipeline and overwrite the cache. Used by prewarm.

    Raises ResearchUnavailable when there is no Gemini key, and any other exception when a
    step fails partway (nothing is saved, so the next prewarm run retries this brand)."""
    ensure_available(config.GEMINI_API_KEY)
    categories = run_pipeline(merchant_name)
    repos.save_ethics_research(db, merchant_id, merchant_name, [c.model_dump() for c in categories], "gemini")
    return categories


# ---------- read path (API) ----------


def _build(merchant_id: str, merchant_name: str, categories: list[CategoryScore], data_source: str,
           researched_at: str | None, profile: UserProfile, stored_blurb: dict | None = None) -> EthicsReport:
    for c in categories:
        for cl in c.claims:
            cl.source_tier = validation.domain_tier(cl.source_url, merchant_name) if cl.source_url else None
    personalized = scoring.personalized(categories, profile)
    return EthicsReport(
        merchant_id=merchant_id,
        merchant_name=merchant_name,
        overall_score=scoring.overall(categories),
        personalized_score=personalized,
        categories=categories,
        # Never generates: a Gemini call here would be a paid call per page load. Prewarm writes
        # stored blurbs; build_verdict serves one only if it still matches this profile.
        verdict=build_verdict(merchant_name, categories, profile, personalized, stored_blurb, researched_at),
        data_source=data_source,  # type: ignore[arg-type]
        researched_at=researched_at,
    )


def _from_cache(doc: dict, merchant_name: str, profile: UserProfile, stored_blurb: dict | None = None) -> EthicsReport:
    categories = [CategoryScore(**c) for c in doc["categories"]]
    return _build(doc["_id"], merchant_name, categories, doc["data_source"],
                  doc["researched_at"].isoformat(), profile, stored_blurb)


def _from_mock(merchant_id: str, merchant_name: str, profile: UserProfile) -> EthicsReport | None:
    raw = _mock().get(merchant_id)
    if not raw:
        return None
    categories = [CategoryScore(**c) for c in raw["categories"]]
    # Mock claims are unsourced placeholders, which are never validated or shown.
    # The categories keep their mock scores for the offline demo.
    for c in categories:
        c.claims = []
    return _build(merchant_id, merchant_name, categories, "mock", None, profile)


def get_report(db: Database, merchant_id: str, merchant_name: str, profile: UserProfile,
               customer_id: str | None = None) -> EthicsReport | None:
    """Cached research, else mock data. Never calls Gemini: this runs on public, unauthenticated
    requests, and a paid call per cache miss (retried on every failure) is a cost-abuse vector.
    Research and Gemini blurbs happen only in `python -m scripts.prewarm`. `customer_id` is the
    owner of `profile`; with it, a stored blurb for that customer can be served."""
    cached = repos.get_ethics_research(db, merchant_id)
    if cached:
        stored = repos.get_verdict_blurb(db, customer_id, merchant_id) if customer_id else None
        return _from_cache(cached, merchant_name, profile, stored)
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
