"""Category scores from validated claims, and category scores + the user's weights into one
personalized score. Owner: Person B (Research engine). No AI in this module."""

from collections.abc import Sequence
from datetime import datetime, timezone

from ..models import CategoryScore, Claim, UserProfile, VerdictLabel

# Mirror scoreTone() in frontend/src/components/ScoreBadge.tsx so the badge
# color and the verdict always agree.
ALIGNS_AT = 65
MIXED_AT = 45
# A category the user weighted 5 ("deal-breaker") scoring below this forces "against".
DEAL_BREAKER_WEIGHT = 5
DEAL_BREAKER_BELOW = 40


# ---------- category score from claims ----------
#
# Each validated claim has a weight w = severity * tier * recency and a direction.
#   score = (50 * PRIOR + sum(w * target)) / (PRIOR + sum(w)),  target 100 positive / 0 negative
# PRIOR acts like PRIOR claims sitting at 50, so a little evidence can't push a score to an
# extreme. With every w = 1 this equals the old shrink 50 + (s - 50) * n / (n + 2).

PRIOR = 2.0
SEVERITY_W = {"major": 1.0, "notable": 0.6, "minor": 0.3}
DEFAULT_SEVERITY = "notable"
# Tier 4 is the company's own site: self-praise (pledges, sustainability reports) counts little,
# a company admitting something bad about itself counts fully.
TIER_W = {1: 1.0, 2: 0.9, 3: 0.6}
COMPANY_POSITIVE_W = 0.4
COMPANY_NEGATIVE_W = 1.0
UNKNOWN_TIER_W = 0.6
RECENT_YEARS = 3  # events this recent count fully
HALF_LIFE_YEARS = 5  # older events halve in weight every 5 years after that
UNKNOWN_YEAR_W = 0.7


def _this_year() -> int:
    return datetime.now(timezone.utc).year


def tier_weight(tier: int, direction: str) -> float:
    if tier == 4:
        return COMPANY_POSITIVE_W if direction == "positive" else COMPANY_NEGATIVE_W
    return TIER_W.get(tier, UNKNOWN_TIER_W)


def recency_weight(event_year: int, this_year: int) -> float:
    if not event_year:
        return UNKNOWN_YEAR_W
    age = max(0, this_year - event_year)
    if age <= RECENT_YEARS:
        return 1.0
    return 0.5 ** ((age - RECENT_YEARS) / HALF_LIFE_YEARS)


def claim_weight(claim: Claim, this_year: int | None = None) -> float:
    """0 for claims without a direction (they should not reach scoring)."""
    if claim._direction not in ("positive", "negative"):
        return 0.0
    year = this_year or _this_year()
    return (
        SEVERITY_W.get(claim._severity, SEVERITY_W[DEFAULT_SEVERITY])
        * tier_weight(claim._tier, claim._direction)
        * recency_weight(claim._event_year, year)
    )


def category_score(claims: Sequence[Claim], this_year: int | None = None) -> float | None:
    """None when no claim carries weight (not enough verified evidence)."""
    year = this_year or _this_year()
    total_w, acc = 0.0, 0.0
    for c in claims:
        w = claim_weight(c, year)
        total_w += w
        acc += w * (100.0 if c._direction == "positive" else 0.0)
    if total_w == 0:
        return None
    return round((50.0 * PRIOR + acc) / (PRIOR + total_w), 1)


# ---------- personalized score and verdict ----------


def _scored(categories: list[CategoryScore]) -> list[CategoryScore]:
    """Categories with a score. score=None means not enough verified evidence; it is skipped."""
    return [c for c in categories if c.score is not None]


def overall(categories: list[CategoryScore]) -> float:
    scored = _scored(categories)
    if not scored:
        return 0.0
    return round(sum(c.score for c in scored) / len(scored), 1)


def personalized(categories: list[CategoryScore], profile: UserProfile) -> float:
    """Weighted mean. A weight of 0 means the user doesn't care about that category.

    Weight 5 ("deal-breaker") is handled in verdict(), not here.

    Categories without a score (no verified evidence) are skipped.
    """
    total_w = 0.0
    acc = 0.0
    for c in _scored(categories):
        w = profile.weights.get(c.category, 0)
        acc += w * c.score
        total_w += w
    return round(acc / total_w, 1) if total_w else overall(categories)


def verdict(
    categories: list[CategoryScore], profile: UserProfile, personalized_score: float
) -> tuple[VerdictLabel, list[str]]:
    """Decide aligns / mixed / against. Code decides this, never the LLM."""
    deal_breakers = [
        c.category
        for c in _scored(categories)
        if profile.weights.get(c.category, 0) >= DEAL_BREAKER_WEIGHT and c.score < DEAL_BREAKER_BELOW
    ]
    if deal_breakers:
        return "against", deal_breakers
    if personalized_score >= ALIGNS_AT:
        return "aligns", []
    if personalized_score >= MIXED_AT:
        return "mixed", []
    return "against", []
