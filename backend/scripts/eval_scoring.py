"""Offline checks for scoring.category_score (no API key, no network).

    python -m scripts.eval_scoring      # from backend/

Checks properties of the formula rather than exact numbers, so the weights in scoring.py can be
tuned without editing this file. Prints a few example scores. Exits 1 if any check fails.
"""

import sys

from app.models import Claim
from app.services import scoring

YEAR = 2026


def claim(direction: str, severity: str = "major", tier: int = 1, year: int = YEAR) -> Claim:
    c = Claim(text=f"{direction} {severity} tier{tier} {year}", validated=True)
    c._direction, c._severity, c._tier, c._event_year = direction, severity, tier, year
    return c


def score(*claims: Claim) -> float | None:
    return scoring.category_score(list(claims), YEAR)


CHECKS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def main() -> int:
    print("examples:")
    examples = {
        "1 major tier-1 recent negative": [claim("negative")],
        "1 minor company pledge (tier 4 positive)": [claim("positive", "minor", 4)],
        "company admits a major problem (tier 4 negative)": [claim("negative", "major", 4)],
        "3 major negatives + 3 minor positives": [claim("negative")] * 3 + [claim("positive", "minor", 2)] * 3,
        "6 notable tier-2 negatives": [claim("negative", "notable", 2)] * 6,
        "major tier-1 negative from 2010": [claim("negative", year=2010)],
    }
    for name, cl in examples.items():
        print(f"  {score(*cl)!s:>5}  {name}")

    print("\nchecks:")
    check("no claims -> None", score() is None)
    check("only unlabeled (neutral) claims -> None", score(claim("neutral")) is None)

    # Every weight = 1 (major, tier 1, recent) must equal the old shrink with s in {0, 100}.
    same = True
    for n_pos in range(5):
        for n_neg in range(5):
            if n_pos + n_neg == 0:
                continue
            n = n_pos + n_neg
            s = 100 * n_pos / n
            old = round(50 + (s - 50) * n / (n + 2), 1)
            new = score(*([claim("positive")] * n_pos + [claim("negative")] * n_neg))
            same &= abs(old - new) < 0.11
    check("all weights 1 -> matches old 50 + (s-50)*n/(n+2)", same)

    check("one major tier-1 recent negative can trigger a deal-breaker (< 40)",
          score(claim("negative")) < scoring.DEAL_BREAKER_BELOW)
    check("company self-praise moves the score less than self-admitted harm",
          score(claim("positive", tier=4)) - 50 < 50 - score(claim("negative", tier=4)))
    check("tier 1 moves the score more than tier 3 (other sites)",
          score(claim("negative", tier=1)) < score(claim("negative", tier=3)))
    check("major moves the score more than notable, notable more than minor",
          score(claim("negative", "major")) < score(claim("negative", "notable")) < score(claim("negative", "minor")))
    check("recent events move the score more than old ones",
          score(claim("negative", year=YEAR - 1)) < score(claim("negative", year=YEAR - 10)))
    check("events within the recent window count fully",
          score(claim("negative", year=YEAR - scoring.RECENT_YEARS)) == score(claim("negative")))
    check("unknown year counts less than a recent one",
          score(claim("negative", year=0)) > score(claim("negative")))
    check("future year (e.g. a 2030 target) counts as recent",
          score(claim("positive", year=YEAR + 4)) == score(claim("positive")))
    check("symmetric: equal positive and negative evidence -> 50",
          score(claim("positive"), claim("negative")) == 50.0)
    many_neg = score(*[claim("negative")] * 200)
    many_pos = score(*[claim("positive")] * 200)
    check("always within 0-100 and never fully extreme", 0 < many_neg < 1 and 99 < many_pos < 100)
    check("more agreeing evidence moves further from 50",
          score(*[claim("negative")] * 4) < score(*[claim("negative")] * 2) < score(claim("negative")))

    failed = [n for n, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
