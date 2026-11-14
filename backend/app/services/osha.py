"""OSHA workplace-safety inspections per brand (last two years): violations and penalties.

Owner: Person A. Reads the nightly osha_enforcement.db (see labordata.py). OSHA records the
establishment (one store or warehouse), so brands match on establishment name as whole words plus
the brand's NAICS prefixes, the same rule as wage cases. Matched inspections go to `osha_inspections`.

Violation types: S serious, W willful, R repeat, O other-than-serious. Penalties are the current
amount (after settlement or contest), which is what the employer actually owes.
"""

import sqlite3
from datetime import date, datetime
from typing import Callable, Iterable, Optional

from pymongo.database import Database

from ..db import repos
from ..models import OshaInspection, SafetySummary
from . import brand_sources, labordata, pipeline
from .brand_sources import DolMatch

SOURCE = "osha"
COLLECTION = "osha_inspections"
ACCIDENT_TYPES = {"A", "M"}  # A accident, M fatality/catastrophe
VIOLATION_FIELDS = {"S": "serious", "W": "willful", "R": "repeat", "O": "other"}

INSPECTIONS_SQL = """
SELECT activity_nr, estab_name, site_city, site_state, naics_code, insp_type, open_date, close_case_date
FROM inspection
WHERE ({names}) AND open_date >= ?
"""
# The violation table has no index on activity_nr, so join it once against a temp table of the
# inspections we care about (one full scan) instead of one scan per brand.
VIOLATIONS_SQL = """
SELECT v.activity_nr, v.viol_type, COUNT(*) AS n,
       SUM(COALESCE(v.initial_penalty, 0)) AS initial, SUM(COALESCE(v.current_penalty, 0)) AS current
FROM violation v JOIN temp.wanted w ON CAST(v.activity_nr AS TEXT) = w.nr
WHERE v.delete_flag IS NULL OR v.delete_flag = ''
GROUP BY v.activity_nr, v.viol_type
"""


def _cents(value: object) -> int:
    try:
        return max(round(float(value or 0) * 100), 0)
    except (TypeError, ValueError):
        return 0


def inspections_for(conn: sqlite3.Connection, match: DolMatch, since: date) -> list[dict]:
    patterns = labordata.like_patterns(match.name_patterns)
    sql = INSPECTIONS_SQL.format(names=" OR ".join(["UPPER(estab_name) LIKE ?"] * len(patterns)))
    out = []
    for r in conn.execute(sql, [*patterns, since.isoformat()]):
        naics = str(r["naics_code"] or "")
        if labordata.whole_word_match(r["estab_name"], match.name_patterns) and \
                any(naics.startswith(p) for p in match.naics_prefixes):
            out.append(dict(r))
    return out


def violation_totals(conn: sqlite3.Connection, activity_nrs: list[str]) -> dict[str, dict]:
    """activity_nr -> {serious, willful, repeat, other, initial_cents, current_cents}."""
    totals: dict[str, dict] = {}
    if not activity_nrs:
        return totals
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS wanted (nr TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM temp.wanted")
    conn.executemany("INSERT OR IGNORE INTO temp.wanted VALUES (?)", [(n,) for n in activity_nrs])
    for r in conn.execute(VIOLATIONS_SQL):
        t = totals.setdefault(str(r["activity_nr"]), {**{f: 0 for f in VIOLATION_FIELDS.values()},
                                                      "initial_cents": 0, "current_cents": 0})
        field = VIOLATION_FIELDS.get((r["viol_type"] or "").upper())
        if field:
            t[field] += r["n"]
        t["initial_cents"] += _cents(r["initial"])
        t["current_cents"] += _cents(r["current"])
    return totals


def to_record(row: dict, brand_id: str, totals: dict, retrieved_at: datetime) -> dict:
    opened = labordata.iso_day(row["open_date"])
    nr = str(row["activity_nr"])
    t = totals.get(nr, {})
    return {
        "activity_nr": nr,
        "brand_id": brand_id,
        "establishment": row["estab_name"] or "",
        "city": row["site_city"],
        "state": (row["site_state"] or None) and row["site_state"][:2],
        "naics": str(row["naics_code"] or "") or None,
        "opened": opened,
        "closed": labordata.iso_day(row["close_case_date"]),
        "inspection_type": row["insp_type"] or "",
        "after_accident": (row["insp_type"] or "") in ACCIDENT_TYPES,
        **{f: t.get(f, 0) for f in VIOLATION_FIELDS.values()},
        "initial_penalty_cents": t.get("initial_cents", 0),
        "current_penalty_cents": t.get("current_cents", 0),
        "provenance": {
            "source": SOURCE,
            "source_name": "U.S. Occupational Safety and Health Administration",
            "source_url": f"https://www.osha.gov/ords/imis/establishment.inspection_detail?id={nr}",
            "locator": f"inspection {nr}",
            "data_year": int(opened[:4]) if opened else retrieved_at.year,
            "retrieved_at": retrieved_at,
            "method": "download",
            "kind": "enforcement_record",
        },
    }


def load(db: Database, conn: sqlite3.Connection, *, since: date, only: Optional[Iterable[str]] = None,
         now: Callable[[], datetime] = repos.utcnow,
         progress: Optional[Callable[[str, str], None]] = None) -> pipeline.LoadReport:
    retrieved_at = now()
    wanted = set(only) if only is not None else None
    matched: list[tuple[str, dict]] = []
    loaded = []
    for brand_id, source in brand_sources.all_sources().items():
        if not source.dol or (wanted is not None and brand_id not in wanted):
            continue
        rows = inspections_for(conn, source.dol, since)
        matched.extend((brand_id, r) for r in rows)
        loaded.append(brand_id)
        if progress:
            progress(brand_id, f"{len(rows)} OSHA inspection{'' if len(rows) == 1 else 's'}")
    totals = violation_totals(conn, sorted({str(r["activity_nr"]) for _, r in matched}))
    records = [to_record(r, brand_id, totals, retrieved_at) for brand_id, r in matched]
    report = pipeline.load(db, source=SOURCE, collection=COLLECTION, records=records, model=OshaInspection,
                           doc_id=lambda m: f"{m.brand_id}:{m.activity_nr}", extra={"loaded_brands": loaded}, now=now)
    for brand_id in loaded:
        keep = [f"{brand_id}:{r['activity_nr']}" for r in records if r["brand_id"] == brand_id]
        db[COLLECTION].delete_many({"brand_id": brand_id, "_id": {"$nin": keep}})
    return report


def summarize(db: Database, brand_id: str, since: date) -> SafetySummary:
    loaded = db[pipeline.PIPELINE_RUNS].find_one({"source": SOURCE, "loaded_brands": brand_id}, {"_id": 1}) is not None
    cutoff = datetime.combine(since, datetime.min.time())
    docs = list(db[COLLECTION].find({"brand_id": brand_id, "opened": {"$gte": cutoff}}))
    return SafetySummary(
        loaded=loaded,
        inspections=len(docs),
        after_accident=sum(bool(d.get("after_accident")) for d in docs),
        serious=sum(d.get("serious", 0) for d in docs),
        willful=sum(d.get("willful", 0) for d in docs),
        repeat=sum(d.get("repeat", 0) for d in docs),
        penalties=sum(d.get("current_penalty_cents", 0) for d in docs) / 100,
    )
