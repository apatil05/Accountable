"""The one way data from an outside source gets into Atlas: validate, then write, then log.

Owner: Person A. Every loader (company reports, Climate TRACE, DOL, ...) builds plain dicts
and hands them to load(). A record that fails its Pydantic model is rejected and counted,
never written, so the app only ever reads documents that passed the contract in models.py.
Each call leaves one row in `pipeline_runs` (written / rejected / first errors).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Iterable, Mapping, TypeVar

from pydantic import BaseModel, ValidationError
from pymongo.database import Database

from ..db import repos

logger = logging.getLogger(__name__)

MAX_LOGGED_ERRORS = 50
PIPELINE_RUNS = "pipeline_runs"

M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class LoadReport:
    source: str
    collection: str
    written: int
    rejected: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.rejected == 0


def _bson_safe(value: Any) -> Any:
    """MongoDB stores datetimes but not bare dates; store a date as UTC midnight."""
    if isinstance(value, dict):
        return {k: _bson_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_bson_safe(v) for v in value]
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    return value


def _describe(record: Mapping[str, Any], exc: ValidationError) -> str:
    label = record.get("brand_id") or record.get("case_id") or "record"
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first.get("loc", ())) or "record"
    return f"{label}: {where}: {first.get('msg', 'invalid')}"


def validate(records: Iterable[Mapping[str, Any]], model: type[M],
             doc_id: Callable[[M], str]) -> tuple[list[tuple[str, M]], list[str]]:
    """Split records into (id, model) pairs that passed and error messages for those that did not."""
    valid: list[tuple[str, M]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for record in records:
        try:
            parsed = model(**record)
        except ValidationError as exc:
            errors.append(_describe(record, exc))
            continue
        key = doc_id(parsed)
        if key in seen:
            errors.append(f"{key}: duplicate id in this batch")
            continue
        seen.add(key)
        valid.append((key, parsed))
    return valid, errors


def load(db: Database, *, source: str, collection: str, records: Iterable[Mapping[str, Any]],
         model: type[M], doc_id: Callable[[M], str], prior_errors: Iterable[str] = (),
         extra: Mapping[str, Any] | None = None,
         now: Callable[[], datetime] = repos.utcnow) -> LoadReport:
    """`prior_errors` are records the caller already rejected (download failed, figure not found);
    they are counted and logged with this run so every loss shows up in pipeline_runs."""
    started = now()
    valid, invalid = validate(records, model, doc_id)
    errors = [*prior_errors, *invalid]
    docs = [{"_id": key, **_bson_safe(m.model_dump())} for key, m in valid]
    repos.bulk_upsert(db, collection, docs)
    report = LoadReport(source, collection, len(docs), len(errors), tuple(errors[:MAX_LOGGED_ERRORS]))
    db[PIPELINE_RUNS].insert_one({
        "source": source, "collection": collection, "started_at": started, "finished_at": now(),
        "written": report.written, "rejected": report.rejected, "errors": list(report.errors), "ok": report.ok,
        **(extra or {}),
    })
    if errors:
        logger.warning("%s: %d record(s) rejected; first: %s", source, len(errors), errors[0])
    return report
