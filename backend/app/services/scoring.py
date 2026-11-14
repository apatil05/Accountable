"""Turns raw category scores + the user's weights into one personalized score.
Owner: Person B (Research engine)."""

from ..models import CategoryScore, UserProfile, VerdictLabel

# Mirror scoreTone() in frontend/src/components/ScoreBadge.tsx so the badge
# color and the verdict always agree.
ALIGNS_AT = 65
MIXED_AT = 45
# A category the user weighted 5 ("deal-breaker") scoring below this forces "against".
DEAL_BREAKER_WEIGHT = 5
DEAL_BREAKER_BELOW = 40


def overall(categories: list[CategoryScore]) -> float:
    if not categories:
        return 0.0
    return round(sum(c.score for c in categories) / len(categories), 1)


def personalized(categories: list[CategoryScore], profile: UserProfile) -> float:
    """Weighted mean. A weight of 0 means the user doesn't care about that category.

    Weight 5 ("deal-breaker") is handled in verdict(), not here.

    TODO(Person B): down-weight categories whose claims aren't validated
    """
    total_w = 0.0
    acc = 0.0
    for c in categories:
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
        for c in categories
        if profile.weights.get(c.category, 0) >= DEAL_BREAKER_WEIGHT and c.score < DEAL_BREAKER_BELOW
    ]
    if deal_breakers:
        return "against", deal_breakers
    if personalized_score >= ALIGNS_AT:
        return "aligns", []
    if personalized_score >= MIXED_AT:
        return "mixed", []
    return "against", []
