"""Research a company on demand when it has no saved research. Owner: Person A.

A company page for a brand nobody has researched asks `POST /api/merchants/{id}/research`; the server
runs the same pipeline as `scripts.prewarm` (ethics.refresh) in a background thread and saves the result
to Atlas, so the page fills in when it finishes (a few minutes) and every later visit is free.

This is a paid, slow call reachable from a public URL, so it is fenced in:
  - only real catalog brands (never junk records or made-up ids), and each is researched once: a saved
    result is "ready" forever, so total spend is bounded by the size of the catalog
  - one research at a time (single worker), a daily cap (config.RESEARCH_DAILY_CAP), and a cooldown
    before a failed brand is tried again
  - no Gemini key means "unavailable"; nothing is attempted
Jobs are recorded in `research_jobs` (one document per attempt). This assumes ONE API process, which is
how the app is hosted; a stuck job stops blocking after STALE_AFTER (e.g. the server restarted mid-run).
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from pymongo.database import Database

from .. import config
from ..db import repos
from . import ethics

log = logging.getLogger(__name__)

JOBS = "research_jobs"
STALE_AFTER = timedelta(minutes=20)  # a research normally takes 3-5 minutes
RETRY_AFTER = timedelta(hours=1)  # cooldown before a failed brand is tried again

_lock = threading.Lock()


def _spawn(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="research-job", daemon=True).start()


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _finish(db: Database, job_id: str, state: str, error: str | None = None) -> None:
    db[JOBS].update_one({"_id": job_id}, {"$set": {"state": state, "error": error, "finished_at": repos.utcnow()}})


def _work(db: Database, job_id: str, brand_id: str, name: str) -> Callable[[], None]:
    def run() -> None:
        try:
            ethics.refresh(db, brand_id, name)  # saves the research itself, only when every step succeeded
            _finish(db, job_id, "done")
        except Exception as exc:  # noqa: BLE001 - recorded, and the page is told "failed"
            log.warning("on-demand research for %s failed: %s", brand_id, type(exc).__name__)
            _finish(db, job_id, "failed", type(exc).__name__)  # type only: messages can echo request details
    return run


def request(db: Database, brand_id: str, now: datetime | None = None) -> str:
    """Make sure research for `brand_id` exists or is on its way. Returns one of:
    ready, started, running, busy, capped, failed, unavailable, unknown."""
    now = now or datetime.now(timezone.utc)
    brand = repos.get_brand(db, brand_id)
    if not brand or brand.get("is_junk"):
        return "unknown"
    cached = repos.get_ethics_research(db, brand_id)
    if cached and cached.get("data_source") == "gemini":
        return "ready"
    if not config.GEMINI_API_KEY:
        return "unavailable"

    with _lock:  # check-then-start must be one step, or two visitors could start two researches
        running = db[JOBS].find_one({"state": "running", "started_at": {"$gt": now - STALE_AFTER}})
        if running:
            return "running" if running["brand_id"] == brand_id else "busy"
        if db[JOBS].find_one({"brand_id": brand_id, "state": "failed", "started_at": {"$gt": now - RETRY_AFTER}}):
            return "failed"
        if db[JOBS].count_documents({"started_at": {"$gte": _day_start(now)}}) >= config.RESEARCH_DAILY_CAP:
            return "capped"
        job_id = uuid.uuid4().hex
        db[JOBS].insert_one({"_id": job_id, "brand_id": brand_id, "state": "running", "started_at": now})
    _spawn(_work(db, job_id, brand_id, brand["name"]))
    return "started"
