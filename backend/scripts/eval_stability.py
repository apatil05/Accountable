"""Measure how much category scores move between pipeline runs on the same merchant.

Runs the full live pipeline (research -> validation -> scoring) several times with the caches
cleared, then prints each category's score per run with mean, range and standard deviation.
Needs GEMINI_API_KEY in backend/.env.
Each run is ~3 minutes and ~25 Gemini calls per merchant.

    python -m scripts.eval_stability                        # from backend/; Starbucks, 3 runs
    python -m scripts.eval_stability --merchant Nike --runs 2
    python -m scripts.eval_stability --reuse-research       # research once, re-run validation + scoring

--reuse-research keeps the first run's research, so the spread is only the judge and scoring.
Without it the spread also includes research variation (different claims and sources each run),
which is what a user sees across server restarts.

Calls ethics.run_pipeline directly, so it needs no database and never writes the research cache.
Run it before and after a scoring change to compare. Uses a default profile (equal weights).
"""

import argparse
import logging
import statistics
import sys
import time

from app import config
from app.models import CATEGORIES
from app.services import ethics, scoring


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

    research = ethics.research_with_gemini(args.merchant) if args.reuse_research else None
    scores: dict[str, list[float | None]] = {c: [] for c in CATEGORIES}
    overall: list[float] = []
    for r in range(args.runs):
        t0 = time.monotonic()
        try:
            categories = ethics.run_pipeline(args.merchant, research)
        except Exception as exc:  # noqa: BLE001
            print(f"run {r + 1}: pipeline failed ({type(exc).__name__}: {exc}); stopping")
            return 1
        by_cat = {c.category: c for c in categories}
        for cat in CATEGORIES:
            c = by_cat.get(cat)
            scores[cat].append(c.score if c else None)
        run_overall = scoring.overall(categories)
        overall.append(run_overall)
        claims = sum(len(c.claims) for c in categories)
        print(f"run {r + 1}/{args.runs}: {time.monotonic() - t0:.0f}s, {claims} validated claims, overall {run_overall}")

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
