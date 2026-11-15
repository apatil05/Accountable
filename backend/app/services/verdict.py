"""The bottom-line "does this company fit your values?" verdict shown when a
merchant is clicked. Owner: Person B (Research engine).

The label (aligns / mixed / against) is decided in scoring.verdict(), never by
the LLM. Gemini only writes the blurb explaining it, and only sees validated
claims.

Gemini blurbs are written offline by `python -m scripts.prewarm` (refresh_blurb)
and stored per (customer, brand) with a fingerprint of the profile, verdict and
research they were written for. Reads never call Gemini: build_verdict serves the
stored blurb only when its fingerprint still matches, otherwise the template blurb.
The template uses category names and verified claims only, so it can never surface
an unsourced claim.
"""

import hashlib
import json
import logging
import re
from typing import Iterable, Mapping

from pydantic import BaseModel
from pymongo.database import Database

from .. import config
from ..db import repos
from ..models import CategoryScore, UserProfile, ValuesVerdict, VerdictLabel
from . import gemini, scoring
from .research import ensure_available

log = logging.getLogger(__name__)

# Mirrors CATEGORY_LABELS in frontend/src/api/types.ts.
CATEGORY_LABELS = {
    "labor": "Labor & workers",
    "environment": "Environment",
    "animal_welfare": "Animal welfare",
    "community": "Local community",
    "privacy": "Data & privacy",
}


def _label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category.replace("_", " ").capitalize())


def headline(name: str, label: VerdictLabel) -> str:
    return {
        "aligns": f"We think {name} aligns with your values",
        "mixed": f"{name} is a mixed fit for your values",
        "against": f"{name} goes against your values",
    }[label]


def _join(items: list[str]) -> str:
    return " and ".join(items) if len(items) <= 2 else ", ".join(items[:-1]) + f", and {items[-1]}"


def _fact(c: CategoryScore) -> str | None:
    """Best verified claim for a category, as a sentence. Unvalidated claims never surface."""
    good = [cl for cl in c.claims if cl.validated and cl.text.strip()]
    if not good:
        return None
    text = max(good, key=lambda cl: cl.confidence).text.strip()
    return text if text[-1] in ".!?" else text + "."


def _facts(cats: list[CategoryScore], limit: int = 2) -> str:
    return " ".join(f for f in (_fact(c) for c in cats[:limit]) if f)


def template_blurb(
    name: str,
    label: VerdictLabel,
    deal_breakers: list[str],
    categories: list[CategoryScore],
    profile: UserProfile,
    personalized_score: float,
) -> str:
    """Plain-language reasons built only from category names and verified claims.
    Never mentions scores or numbers of its own."""
    w = lambda c: profile.weights.get(c.category, 0)  # noqa: E731
    cared = [c for c in categories if w(c) > 0] or categories
    strengths = sorted((c for c in cared if c.score >= scoring.ALIGNS_AT), key=lambda c: (-w(c), -c.score))[:2]
    weaknesses = sorted((c for c in cared if c.score < scoring.MIXED_AT), key=lambda c: (-w(c), c.score))[:2]

    if label == "against" and deal_breakers:
        hits = [c for c in categories if c.category in deal_breakers]
        noun = "a deal-breaker" if len(hits) == 1 else "deal-breakers"
        lead = f"{name} has a poor record on {_join([_label(c.category) for c in hits])}, which you marked as {noun}."
        return f"{lead} {_facts(hits)}".strip()
    if label == "aligns":
        lead = f"{name} lines up well with what you care about"
        if strengths:
            lead += f", especially {_join([_label(c.category) for c in strengths])}"
        lead += "."
        text = f"{lead} {_facts(strengths, 1)}".strip()
        if weaknesses:
            text += f" Its weakest area is {_label(weaknesses[0].category)}."
        return text
    if label == "mixed":
        if strengths and weaknesses:
            lead = f"{name} does well on {_join([_label(c.category) for c in strengths])} but falls short on {_join([_label(c.category) for c in weaknesses])}."
        elif strengths:
            lead = f"{name} does well on {_join([_label(c.category) for c in strengths])}, but is middling on the rest."
        elif weaknesses:
            lead = f"{name} falls short on {_join([_label(c.category) for c in weaknesses])}."
        else:
            lead = f"{name} is middling across the areas you care about."
        return f"{lead} {_facts(weaknesses or strengths, 1)}".strip()
    # against, no deal-breaker
    lead = f"{name} falls short on what you weight most"
    lead += f": {_join([_label(c.category) for c in weaknesses])}." if weaknesses else "."
    return f"{lead} {_facts(weaknesses)}".strip()


class _BlurbOut(BaseModel):
    blurb: str


_BLURB_PROMPT = """You write a short explanation for a shopper about whether a company fits their personal values.

The verdict has ALREADY been decided by our scoring system: "{label}". Explain it; do not change, soften, or contradict it.
Headline the shopper sees above your text: "{headline}"

Rules:
- 2-3 sentences, plain text, second person ("you"), no markdown, no headline repeat.
- Never mention our scores or ratings (no "score", no "out of 100"). Describe the reasons in words.
- Lead with the single biggest reason, then support it with the specific verified facts behind it.
- Use ONLY the facts in the data below. Do not add any fact, number, or claim that is not in it.
- Focus on the areas the shopper weights most (weight 0 = doesn't care, 5 = deal-breaker).
- If deal-breakers are listed, mention them first.
- Relate it to the shopper's own stances when they are relevant. Do not take a political side yourself.

Data:
{data}
"""


def _gemini_blurb(
    name: str,
    label: VerdictLabel,
    deal_breakers: list[str],
    categories: list[CategoryScore],
    profile: UserProfile,
    personalized_score: float,
) -> str:
    from google.genai import types

    data = {
        "company": name,
        "deal_breakers": [_label(c) for c in deal_breakers],
        "shopper_stances": profile.stances or "(none given)",
        "areas": [
            {
                "area": _label(c.category),
                "shopper_weight": profile.weights.get(c.category, 0),
                "standing": "strong" if c.score >= scoring.ALIGNS_AT else "weak" if c.score < scoring.MIXED_AT else "middling",
                "summary": c.summary,
                # Only validated claims may reach the user.
                "verified_facts": [cl.text for cl in c.claims if cl.validated],
            }
            for c in categories
        ],
    }
    resp = gemini.client().models.generate_content(
        model=config.GEMINI_MODEL,
        contents=_BLURB_PROMPT.format(
            label=label, headline=headline(name, label), data=json.dumps(data, indent=1)
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_BlurbOut,
            temperature=0.3,
        ),
    )
    parsed = resp.parsed if isinstance(resp.parsed, _BlurbOut) else _BlurbOut.model_validate_json(resp.text)
    blurb = parsed.blurb.strip()
    if not blurb:
        raise ValueError("empty blurb")
    return blurb


# Bump when the prompt or blurb rules change: every stored blurb then reads as stale.
PROMPT_VERSION = 1
MAX_SENTENCES = 3
MAX_ATTEMPTS = 2

_SCORE_TALK = re.compile(r"\b\d{1,3}\s*(?:out of|/)\s*100\b|\bscor(?:e|es|ed|ing)\b", re.I)
_ABBREV = re.compile(r"\b(?:Inc|Co|Corp|Ltd|St|Mr|Ms|Dr|vs|U\.S|U\.K|[A-Z])\.")
_SENTENCE_END = re.compile(r"[.!?](?=\s+[\"A-Z]|\s*$)")


def _scored(categories: list[CategoryScore]) -> list[CategoryScore]:
    # Unscored categories (not enough verified evidence) can't be strengths or weaknesses.
    return [c for c in categories if c.score is not None]


def fingerprint(profile: UserProfile, label: VerdictLabel, deal_breakers: list[str], researched_at: str) -> str:
    """What a stored blurb was written for. Any change here makes it stale."""
    key = {
        "v": PROMPT_VERSION,
        "weights": dict(sorted(profile.weights.items())),
        "stances": profile.stances,
        "label": label,
        "deal_breakers": sorted(deal_breakers),
        "researched_at": researched_at,
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


def blurb_problem(text: str) -> str | None:
    """Why a generated blurb can't be shown, or None if it's fine. Enforced in code, not just the prompt."""
    if not text.strip():
        return "empty"
    if _SCORE_TALK.search(text):
        return "mentions a score"
    if len(_SENTENCE_END.findall(_ABBREV.sub("", text.strip()))) > MAX_SENTENCES:
        return f"more than {MAX_SENTENCES} sentences"
    return None


def build_verdict(
    name: str,
    categories: list[CategoryScore],
    profile: UserProfile,
    personalized_score: float,
    stored: Mapping | None = None,
    researched_at: str | None = None,
) -> ValuesVerdict:
    """Never calls Gemini. Serves `stored` (a verdict_blurbs doc) only if it was written for
    this exact profile, verdict and research; otherwise the template blurb."""
    categories = _scored(categories)
    label, deal_breakers = scoring.verdict(categories, profile, personalized_score)
    blurb, source = template_blurb(name, label, deal_breakers, categories, profile, personalized_score), "template"
    if stored and researched_at and stored.get("fingerprint") == fingerprint(profile, label, deal_breakers, researched_at):
        blurb, source = stored["blurb"], "gemini"
    return ValuesVerdict(
        label=label,
        headline=headline(name, label),
        blurb=blurb,
        deal_breakers=deal_breakers,
        source=source,  # type: ignore[arg-type]
    )


# ---------- write path (prewarm only) ----------


def _expected(doc: Mapping, profile: UserProfile) -> tuple[list[CategoryScore], VerdictLabel, list[str], float, str]:
    """Verdict inputs for a cached ethics_reports doc, computed exactly as the read path does."""
    categories = [CategoryScore(**c) for c in doc["categories"]]
    personalized = scoring.personalized(categories, profile)
    scored = _scored(categories)
    label, deal_breakers = scoring.verdict(scored, profile, personalized)
    return scored, label, deal_breakers, personalized, doc["researched_at"].isoformat()


def stale_blurb_ids(db: Database, customer_id: str, targets: Iterable[tuple[str, str]],
                    profile: UserProfile) -> set[str]:
    """Brands whose stored blurb is missing or no longer matches. Brands without cached
    research count as stale too, so prewarm reports them as failed instead of fresh."""
    ids = [bid for bid, _ in targets]
    research = {d["_id"]: d for d in repos.get_ethics_researches(db, ids)}
    stored = repos.get_verdict_blurbs(db, customer_id, ids)
    stale = set()
    for bid in ids:
        if bid not in research or bid not in stored:
            stale.add(bid)
            continue
        _, label, deal_breakers, _, researched_at = _expected(research[bid], profile)
        if stored[bid].get("fingerprint") != fingerprint(profile, label, deal_breakers, researched_at):
            stale.add(bid)
    return stale


def refresh_blurb(db: Database, customer_id: str, brand_id: str, name: str, profile: UserProfile) -> str:
    """Write and store a Gemini blurb for one brand. Raises ResearchUnavailable without a key,
    ValueError when there is no cached research or every attempt breaks the blurb rules."""
    ensure_available(config.GEMINI_API_KEY)
    doc = repos.get_ethics_research(db, brand_id)
    if not doc:
        raise ValueError("no cached research; run the ethics prewarm first")
    categories, label, deal_breakers, personalized, researched_at = _expected(doc, profile)
    problem = "no attempt"
    for _ in range(MAX_ATTEMPTS):
        blurb = _gemini_blurb(name, label, deal_breakers, categories, profile, personalized)
        problem = blurb_problem(blurb)
        if problem is None:
            repos.save_verdict_blurb(db, customer_id, brand_id, blurb,
                                     fingerprint(profile, label, deal_breakers, researched_at))
            return blurb
        log.warning("Rejected Gemini blurb for %s (%s): %r", name, problem, blurb)
    raise ValueError(f"blurb rejected: {problem}")
