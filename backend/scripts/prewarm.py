"""Research brands ahead of time so the demo serves cached data instead of calling Gemini live.

Usage (from backend/):
    python -m scripts.prewarm                    # the default customer's brands, ethics + supply chains
    python -m scripts.prewarm --all-brands       # every non-junk brand in the catalog
    python -m scripts.prewarm --only ethics      # just one kind of research
    python -m scripts.prewarm --force            # re-research even fresh entries
    python -m scripts.prewarm --dry-run          # show what would be researched

Needs GEMINI_API_KEY. Safe to re-run: fresh entries (younger than --max-age-days) are skipped.
Exits 1 if any target brand is still uncached afterwards (its page would fall back to mock data).
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from pymongo.errors import PyMongoError

from app import config
from app.db import repos
from app.db.client import get_db, ping
from app.services import ethics, nessie, prewarm, supply_chain

DEFAULT_MAX_AGE_DAYS = 30
# Gemini's free tier allows a handful of requests per minute; each research call may use several.
DEFAULT_DELAY_SECONDS = 5.0

KINDS = {
    "ethics": (repos.ETHICS_REPORTS, lambda: ethics.refresh),
    "supply": (repos.SUPPLY_CHAINS, lambda: supply_chain.refresh),
}


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--customer", help="Nessie customer id (default: the configured default customer)")
    scope.add_argument("--all-brands", action="store_true", help="every non-junk brand, not just one customer's")
    parser.add_argument("--only", choices=sorted(KINDS), help="research only this kind")
    parser.add_argument("--force", action="store_true", help="re-research entries that are still fresh")
    parser.add_argument("--max-age-days", type=float, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="seconds between live calls")
    parser.add_argument("--dry-run", action="store_true", help="list what would be researched, change nothing")
    args = parser.parse_args(argv)
    if args.max_age_days < 0 or args.delay < 0:
        parser.error("--max-age-days and --delay must not be negative")
    return args


def _print(kind: str, results: list[prewarm.Result]) -> None:
    print(f"\n{kind}:")
    for r in results:
        detail = f"  ({r.detail})" if r.detail else ""
        print(f"  {r.status:<15} {r.brand_id:<28} {r.name}{detail}")


def _run_kind(db, kind: str, targets, args, now) -> list[prewarm.Result]:
    collection, refresher = KINDS[kind]
    common = {"max_age": timedelta(days=args.max_age_days), "force": args.force, "now": now}
    if args.dry_run:
        return prewarm.dry_run(db, targets, collection, **common)
    return prewarm.prewarm(db, targets, collection, refresher(), delay=args.delay, **common)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    try:
        config.validate()
        customer_id = None if args.all_brands else nessie.validate_id(
            args.customer or nessie.default_customer_id(), "customer id")
        db = get_db()
        ping(db)
        targets = prewarm.target_brands(db, customer_id)
        now = datetime.now(timezone.utc)
        outcomes = {kind: _run_kind(db, kind, targets, args, now) for kind in ([args.only] if args.only else KINDS)}
    except (config.ConfigError, nessie.InvalidIdError) as exc:
        print(f"Prewarm failed: {exc}", file=sys.stderr)
        return 1
    except PyMongoError as exc:
        print(f"Prewarm failed: MongoDB error ({type(exc).__name__}). Check MONGODB_URI and Atlas Network Access.",
              file=sys.stderr)
        return 1

    if not targets:
        print("No brands to research. Run `python -m scripts.sync` first.")
        return 1
    for kind, results in outcomes.items():
        _print(kind, results)
    if args.dry_run:
        return 0
    missing = sum(r.status not in prewarm.OK_STATUSES for results in outcomes.values() for r in results)
    if missing:
        print(f"\n{missing} entr{'y' if missing == 1 else 'ies'} not cached; those pages will show mock data.")
        return 1
    print("\nAll target brands are cached.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
