"""NLRB unfair labor practice charges and union election petitions, per brand (last two years).

Owner: Person A. Reads the nightly nlrb.db (see labordata.py), matches the case name against the
brand's name patterns as whole words, and stores matched cases in `nlrb_cases`.

A charge (CA case) is an allegation by workers or a union, not a finding. The UI shows outcomes:
open, remedied (settled or withdrawn after the employer fixed it), or dismissed/withdrawn.
"""

import sqlite3
from collections import Counter
from datetime import date, datetime
from typing import Callable, Iterable, Optional

from pymongo.database import Database

from ..db import repos
from ..models import NlrbCase, OrganizingSummary
from . import brand_sources, labordata, pipeline

SOURCE = "nlrb"
COLLECTION = "nlrb_cases"
MAX_ALLEGATIONS = 10
TOP_ALLEGATIONS = 3
REMEDIED = ("adjusted", "settlement", "compliance")
# certified_representative is never filled in this data; how the case closed says who won.
UNION_WON = "representative"  # "Certific. of Representative": the union was certified
UNION_LOST = "certification of results"  # election held, no union majority
DISMISSED = ("non-adjusted",)

FILINGS_SQL = """
SELECT case_number, name, case_type, url, city, state, date_filed, status, date_closed,
       reason_closed, certified_representative
FROM filing
WHERE ({names}) AND case_type IN ('CA', 'RC', 'RM', 'RD') AND date_filed >= ?
"""


def outcome(status: Optional[str], reason: Optional[str]) -> str:
    text = (reason or "").lower()
    if (status or "").lower().startswith("open") or not text:
        return "open"
    if any(k in text for k in DISMISSED):  # check first: "non-adjusted" contains "adjusted"
        return "dismissed_or_withdrawn"
    if any(k in text for k in REMEDIED):
        return "remedied"
    return "other"


def _allegations(conn: sqlite3.Connection, case_number: str) -> list[str]:
    rows = conn.execute("SELECT allegation FROM allegation WHERE case_number = ? LIMIT ?",
                        (case_number, MAX_ALLEGATIONS))
    return [r["allegation"] for r in rows if r["allegation"]]


def rows_for(conn: sqlite3.Connection, name_patterns: list[str], since: date) -> list[dict]:
    patterns = labordata.like_patterns(name_patterns)
    sql = FILINGS_SQL.format(names=" OR ".join(["UPPER(name) LIKE ?"] * len(patterns)))
    return [dict(r) for r in conn.execute(sql, [*patterns, since.isoformat()])
            if labordata.whole_word_match(r["name"], name_patterns)]


def to_record(row: dict, brand_id: str, allegations: list[str], retrieved_at: datetime) -> dict:
    filed = labordata.iso_day(row["date_filed"])
    return {
        "case_number": row["case_number"],
        "brand_id": brand_id,
        "name": row["name"] or "",
        "case_type": row["case_type"],
        "city": row["city"],
        "state": (row["state"] or None) and row["state"][:2],
        "filed": filed,
        "closed": labordata.iso_day(row["date_closed"]),
        "status": row["status"] or "",
        "outcome": outcome(row["status"], row["reason_closed"]),
        "reason_closed": row["reason_closed"] or None,
        "allegations": allegations,
        "certified_union": row["certified_representative"] or None,
        "url": row["url"],
        "provenance": {
            "source": SOURCE,
            "source_name": "U.S. National Labor Relations Board",
            "source_url": row["url"] or "https://www.nlrb.gov/search/case",
            "locator": f"case {row['case_number']}",
            "data_year": int(filed[:4]) if filed else retrieved_at.year,
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
    records, loaded = [], []
    for brand_id, source in brand_sources.all_sources().items():
        if not source.dol or (wanted is not None and brand_id not in wanted):
            continue
        rows = rows_for(conn, source.dol.name_patterns, since)
        records.extend(to_record(r, brand_id, _allegations(conn, r["case_number"]), retrieved_at) for r in rows)
        loaded.append(brand_id)
        if progress:
            progress(brand_id, f"{len(rows)} NLRB case{'' if len(rows) == 1 else 's'}")
    report = pipeline.load(db, source=SOURCE, collection=COLLECTION, records=records, model=NlrbCase,
                           doc_id=lambda m: f"{m.brand_id}:{m.case_number}", extra={"loaded_brands": loaded}, now=now)
    for brand_id in loaded:
        keep = [f"{brand_id}:{r['case_number']}" for r in records if r["brand_id"] == brand_id]
        db[COLLECTION].delete_many({"brand_id": brand_id, "_id": {"$nin": keep}})
    return report


def summarize(db: Database, brand_id: str, since: date) -> OrganizingSummary:
    loaded = db[pipeline.PIPELINE_RUNS].find_one({"source": SOURCE, "loaded_brands": brand_id}, {"_id": 1}) is not None
    cutoff = datetime.combine(since, datetime.min.time())
    cases = list(db[COLLECTION].find({"brand_id": brand_id, "filed": {"$gte": cutoff}},
                                     {"case_type": 1, "outcome": 1, "allegations": 1, "reason_closed": 1}))
    charges = [c for c in cases if c["case_type"] == "CA"]
    petitions = [c for c in cases if c["case_type"] in ("RC", "RM")]
    top = Counter(a for c in charges for a in c.get("allegations", []))
    return OrganizingSummary(
        loaded=loaded,
        charges=len(charges),
        charges_open=sum(c["outcome"] == "open" for c in charges),
        charges_remedied=sum(c["outcome"] == "remedied" for c in charges),
        charges_dismissed=sum(c["outcome"] == "dismissed_or_withdrawn" for c in charges),
        top_allegations=[a for a, _ in top.most_common(TOP_ALLEGATIONS)],
        petitions=len(petitions),
        unions_certified=sum(UNION_WON in (c.get("reason_closed") or "").lower() for c in petitions),
        elections_union_lost=sum(UNION_LOST in (c.get("reason_closed") or "").lower() for c in petitions),
        decertification_petitions=sum(c["case_type"] == "RD" for c in cases),
    )
