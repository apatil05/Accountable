"""Turns raw category scores + the user's weights into one personalized score.
Owner: Person B (Research engine)."""

from ..models import CategoryScore, UserProfile


def overall(categories: list[CategoryScore]) -> float:
    if not categories:
        return 0.0
    return round(sum(c.score for c in categories) / len(categories), 1)


def personalized(categories: list[CategoryScore], profile: UserProfile) -> float:
    """Weighted mean. A weight of 0 means the user doesn't care about that category.

    TODO(Person B): ideas to try
      - treat weight 5 as a deal-breaker: cap the score if that category < 40
      - down-weight categories whose claims aren't validated
    """
    total_w = 0.0
    acc = 0.0
    for c in categories:
        w = profile.weights.get(c.category, 0)
        acc += w * c.score
        total_w += w
    return round(acc / total_w, 1) if total_w else overall(categories)
