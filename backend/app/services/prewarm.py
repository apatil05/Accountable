"""Fill the research cache ahead of time so the demo never waits on (or depends on) Gemini.

Owner: Person A. The CLI wrapper is scripts/prewarm.py; this module holds the logic so it
can be tested without a terminal.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Literal, Sequence

from pymongo.database import Database

from ..db import repos
from .research import ResearchUnavailable

Status = Literal["fresh", "researched", "would research", "unavailable", "failed"]
# A refresher researches one brand and writes its cache document (ethics.refresh, supply_chain.refresh).
Refresher = Callable[[Database, str, str], object]

OK_STATUSES: frozenset[str] = frozenset({"fresh", "researched"})


@dataclass(frozen=True)
class Result:
    brand_id: str
    name: str
    status: Status
    detail: str = ""


def target_brands(db: Database, customer_id: str | None) -> list[tuple[str, str]]:
    """Non-junk brands the customer bought from, or every non-junk brand when customer_id is None."""
    ids = repos.distinct_brand_ids(db, customer_id) if customer_id else None
    return repos.researchable_brands(db, ids)


def _is_fresh(researched_at: datetime | None, now: datetime, max_age: timedelta) -> bool:
    return researched_at is not None and now - researched_at <= max_age


def stale_targets(db: Database, targets: Sequence[tuple[str, str]], collection: str, *,
                  max_age: timedelta, force: bool, now: datetime) -> set[str]:
    """Brand ids that need research: missing, older than max_age, or everything with force."""
    if force:
        return {bid for bid, _ in targets}
    ages = repos.researched_at_by_id(db, collection, [bid for bid, _ in targets])
    return {bid for bid, _ in targets if not _is_fresh(ages.get(bid), now, max_age)}


def prewarm(db: Database, targets: Sequence[tuple[str, str]], collection: str, refresh: Refresher, *,
            max_age: timedelta, force: bool, delay: float, now: datetime,
            sleep: Callable[[float], None] = time.sleep) -> list[Result]:
    """Research every stale target, pausing `delay` seconds between live calls.

    One brand failing does not stop the others. ResearchUnavailable does stop the run:
    it means no brand can be researched here, so the rest are marked unavailable."""
    stale = stale_targets(db, targets, collection, max_age=max_age, force=force, now=now)
    results: list[Result] = []
    unavailable: str | None = None
    called = False
    for bid, name in targets:
        if bid not in stale:
            results.append(Result(bid, name, "fresh"))
            continue
        if unavailable is not None:
            results.append(Result(bid, name, "unavailable", unavailable))
            continue
        if called and delay > 0:
            sleep(delay)
        called = True
        results.append(_research_one(db, bid, name, refresh))
        if results[-1].status == "unavailable":
            unavailable = results[-1].detail
    return results


def _research_one(db: Database, brand_id: str, name: str, refresh: Refresher) -> Result:
    try:
        refresh(db, brand_id, name)
    except ResearchUnavailable as exc:
        return Result(brand_id, name, "unavailable", str(exc))
    except Exception as exc:  # noqa: BLE001 - report and move on to the next brand
        return Result(brand_id, name, "failed", f"{type(exc).__name__}: {exc}")
    return Result(brand_id, name, "researched")


def dry_run(db: Database, targets: Sequence[tuple[str, str]], collection: str, *,
            max_age: timedelta, force: bool, now: datetime) -> list[Result]:
    stale = stale_targets(db, targets, collection, max_age=max_age, force=force, now=now)
    return [Result(bid, name, "would research" if bid in stale else "fresh") for bid, name in targets]
