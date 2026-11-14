"""Measure how much category scores move between pipeline runs on the same merchant.

Runs the full live pipeline (research -> validation -> scoring) several times with the caches
cleared, then prints each category's score per run with mean, range and standard deviation.
Needs GEMINI_API_KEY in backend/.env; forces live mode even if USE_MOCK_DATA=true.
Each run is ~3 minutes and ~25 Gemini calls per merchant.

    python -m scripts.eval_stability                        # from backend/; Starbucks, 3 runs
    python -m scripts.eval_stability --merchant Nike --runs 2
    python -m scripts.eval_stability --reuse-research       # research once, re-run validation + scoring

--reuse-research keeps the first run's research, so the spread is only the judge and scoring.
Without it the spread also includes research variation (different claims and sources each run),
which is what a user sees across server restarts.

Only uses ethics.get_report and the cache dicts, so it also runs on older code: run it before and
after a scoring change to compare. Uses a default profile (equal weights).
"""

import argparse
import logging
import statistics
import sys
import time

from app import config
from app.models import CATEGORIES, UserProfile
from app.services import ethics


def clear(reuse_research: bool) -> None:
    ethics._cache.clear()
    getattr(ethics, "_validated_cache", {}).clear()
    if not reuse_research:
        ethics._research_cache.clear()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merchant", default="Starbucks")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--reuse-research", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    if not config.GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set (backend/.env); the stability eval needs live research.")
        return 2
    config.USE_MOCK_DATA = False

    profile = UserProfile()
    merchant_id = f"eval-{args.merchant.lower().replace(' ', '-')}"
    scores: dict[str, list[float | None]] = {c: [] for c in CATEGORIES}
    overall: list[float] = []
    for r in range(args.runs):
        clear(args.reuse_research)
        t0 = time.monotonic()
        report = ethics.get_report(merchant_id, args.merchant, profile, ai_blurb=False)
        if report is None or report.data_source != "gemini":
            print(f"run {r + 1}: research failed (got {report.data_source if report else 'nothing'}); stopping")
            return 1
        by_cat = {c.category: c for c in report.categories}
        for cat in CATEGORIES:
            c = by_cat.get(cat)
            scores[cat].append(c.score if c else None)
        overall.append(report.overall_score)
        claims = sum(len(c.claims) for c in report.categories)
        print(f"run {r + 1}/{args.runs}: {time.monotonic() - t0:.0f}s, {claims} validated claims, overall {report.overall_score}")

    def fmt(v: float | None) -> str:
        return "  -  " if v is None else f"{v:5.1f}"

    print(f"\n{args.merchant}, {args.runs} runs{' (research reused)' if args.reuse_research else ''}")
    print(f"{'category':15} {'runs':{6 * args.runs}}  mean  range  stdev")
    spreads = []
    for cat, vals in [*scores.items(), ("overall", overall)]:
        got = [v for v in vals if v is not None]
        runs = " ".join(fmt(v) for v in vals)
        if len(got) >= 2:
            rng = max(got) - min(got)
            sd = statistics.stdev(got)
            if cat != "overall":
                spreads.append(sd)
            print(f"{cat:15} {runs}  {statistics.mean(got):5.1f} {rng:5.1f} {sd:6.1f}")
        else:
            print(f"{cat:15} {runs}")
        if 0 < len(got) < len(vals):
            print(f"{'':15} no score in {len(vals) - len(got)} of {len(vals)} runs")
    if spreads:
        print(f"\nmean per-category stdev: {statistics.mean(spreads):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
