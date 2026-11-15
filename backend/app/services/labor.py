"""US Department of Labor, Wage and Hour Division (WHD) enforcement cases, per brand.

Owner: Person A. Free API with a key from https://dataportal.dol.gov (login.gov account).
Each concluded case lists back wages owed, penalties, workers affected and violation counts.
Brands are found by name substrings plus an industry (NAICS) filter from brand_sources.json,
so "CHIPOTLE PEPPER FARM" is not counted against the restaurant chain.

Caveats shown in the UI: cases are against individual locations and franchisees trading under
the brand's name; a large chain naturally has more cases than a small one.

Request details (from a maintained client, verified against the live API): the key is the
X-API-KEY query parameter, rows come wrapped in {"data": [...]}, an empty result is HTTP 204, and
the API rate-limits with HTTP 429. The key travels in the URL, so httpx request logging is
silenced here and errors are reported by type and status only, never by URL.
"""

import json
import logging
import sqlite3
import time
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional

import httpx
from pymongo.database import Database

from ..db import repos
from ..models import LaborCase, LaborSummary
from . import brand_sources, labordata, nlrb, osha, pipeline
from .brand_sources import DolMatch

# httpx logs full request URLs at INFO, and the DOL key is in the URL.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

SOURCE = "dol_whd"
API_URL = "https://apiprod.dol.gov/v4/get/WHD/enforcement/json"
PAGE_SIZE = 200
MAX_PAGES = 25  # 5,000 rows per brand; hitting it means the name patterns are too broad
TIMEOUT_SECONDS = 60
MAX_RETRIES = 4  # on HTTP 429 only
RETRY_BASE_SECONDS = 5.0
MAX_RETRY_DELAY_SECONDS = 60.0
NAME_FIELDS = ("trade_nm", "legal_name")
PENALTY_SUFFIX = "_cmp_assd_amt"  # civil money penalties, one field per law (flsa_, fmla_, ...)
CHILD_LABOR_FIELD = "flsa_cl_violtn_cnt"
CASE_URL = "https://enforcedata.dol.gov/"


class DolError(RuntimeError):
    """Raised with a message that is safe to log (never contains the API key)."""


def name_filter(match: DolMatch) -> str:
    """DOL's `like` is case-sensitive with no implicit wildcards, so OR the variants that occur."""
    values = {f"%{v}%" for p in match.name_patterns for v in (p, p.upper(), p.title())}
    conditions = [{"field": f, "operator": "like", "value": v} for f in NAME_FIELDS for v in sorted(values)]
    return json.dumps({"or": conditions})


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    try:
        delay = float(resp.headers.get("Retry-After", ""))
    except ValueError:
        delay = RETRY_BASE_SECONDS * 2 ** attempt
    return min(max(delay, 0.0), MAX_RETRY_DELAY_SECONDS)


def _get(client: httpx.Client, params: dict, sleep: Callable[[float], None]) -> httpx.Response:
    """GET with backoff on HTTP 429 (DOL rate limit); other statuses are returned as-is."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.get(API_URL, params=params, timeout=TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise DolError(type(exc).__name__) from None
        if resp.status_code != 429 or attempt == MAX_RETRIES:
            return resp
        sleep(_retry_delay(resp, attempt))
    return resp


def _page_rows(resp: httpx.Response) -> list[dict]:
    try:
        body = resp.json()
    except ValueError:
        raise DolError("response was not JSON") from None
    rows = body.get("data") if isinstance(body, dict) else None
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise DolError("unexpected response shape")
    return rows


def fetch_rows(client: httpx.Client, api_key: str, match: DolMatch,
               sleep: Callable[[float], None] = time.sleep) -> list[dict]:
    """All rows matching the brand's name patterns. Raises DolError rather than returning a
    silently truncated list, because a truncated list would make load() delete real cases."""
    rows: list[dict] = []
    params = {"X-API-KEY": api_key, "filter_object": name_filter(match), "limit": PAGE_SIZE,
              "sort_by": "case_id", "sort": "asc"}
    for page in range(MAX_PAGES):
        resp = _get(client, {**params, "offset": page * PAGE_SIZE}, sleep)
        if resp.status_code == 204:
            return rows
        if resp.status_code != 200:
            raise DolError(f"HTTP {resp.status_code}")
        batch = _page_rows(resp)
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
    raise DolError(f"more than {MAX_PAGES * PAGE_SIZE} matching rows; narrow the name patterns")


WHISARD_CASES_SQL = """
SELECT c.case_id, c.trade_nm, c.legal_name, c.st_cd, c.naic_cd, c.findings_start_date,
       c.findings_end_date, c.case_violtn_cnt, c.ee_violtd_cnt, c.bw_atp_amt, c.cmp_assd,
       (SELECT COALESCE(SUM(v.violations), 0) FROM violations v
         WHERE v.case_id = c.case_id AND v.type = 'flsa_cl') AS child_labor
FROM cases c
WHERE ({names}) AND c.findings_end_date >= ? AND c.findings_end_date < ?
"""


def whisard_rows(conn: sqlite3.Connection, match: DolMatch, since: date) -> list[dict]:
    """Cases from the nightly whisard.db, shaped like DOL API rows so to_case() handles both."""
    patterns = labordata.like_patterns(match.name_patterns)
    names = " OR ".join(["UPPER(c.trade_nm) LIKE ? OR UPPER(c.legal_name) LIKE ?"] * len(patterns))
    params = [p for p in patterns for _ in range(2)] + [since.isoformat(), f"{date.today().year + 2}"]
    rows = []
    for r in conn.execute(WHISARD_CASES_SQL.format(names=names), params):
        if not (labordata.whole_word_match(r["trade_nm"], match.name_patterns)
                or labordata.whole_word_match(r["legal_name"], match.name_patterns)):
            continue
        rows.append({**{k: r[k] for k in r.keys() if k not in ("cmp_assd", "child_labor")},
                     "flsa_cmp_assd_amt": r["cmp_assd"], CHILD_LABOR_FIELD: r["child_labor"]})
    return rows


def _in_industry(row: dict, match: DolMatch) -> bool:
    code = str(row.get("naic_cd") or "")
    return any(code.startswith(p) for p in match.naics_prefixes)


def _date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _cents(value: Any) -> int:
    try:
        return max(round(float(value or 0) * 100), 0)
    except (TypeError, ValueError):
        return 0


def _count(value: Any) -> int:
    try:
        return max(int(float(value or 0)), 0)
    except (TypeError, ValueError):
        return 0


def _penalty_cents(row: dict) -> int:
    return sum(_cents(v) for k, v in row.items() if k.endswith(PENALTY_SUFFIX))


def to_case(row: dict, brand_id: str, retrieved_at: datetime, method: str = "api") -> dict:
    end = _date(row.get("findings_end_date"))
    child = row.get(CHILD_LABOR_FIELD)
    return {
        "case_id": str(row.get("case_id", "")),
        "brand_id": brand_id,
        "trade_name": str(row.get("trade_nm") or ""),
        "legal_name": str(row.get("legal_name") or ""),
        "state": (row.get("st_cd") or None),
        "naics": str(row.get("naic_cd") or "") or None,
        "findings_start": _date(row.get("findings_start_date")),
        "findings_end": end,
        "violations": _count(row.get("case_violtn_cnt")),
        "employees_affected": _count(row.get("ee_violtd_cnt")),
        "back_wages_cents": _cents(row.get("bw_atp_amt")),
        "penalties_cents": _penalty_cents(row),
        "child_labor_violations": None if child is None else _count(child),
        "provenance": {
            "source": SOURCE,
            "source_name": "U.S. Department of Labor, Wage and Hour Division",
            "source_url": CASE_URL,
            "locator": f"case {row.get('case_id')}",
            "data_year": (end or retrieved_at.date()).year,
            "retrieved_at": retrieved_at,
            "method": method,
            "kind": "enforcement_record",
        },
    }


def doc_id(case: LaborCase) -> str:
    """Per brand, so a case matched by two brands is kept for both instead of colliding."""
    return f"{case.brand_id}:{case.case_id}"


def _prune(db: Database, loaded: list[str], records: list[dict]) -> None:
    """Drop cases a fresh load no longer matched. A brand whose fetch returned nothing keeps its
    cases: an empty answer is more likely an outage than every case vanishing."""
    for brand_id in loaded:
        keep = [f"{brand_id}:{r['case_id']}" for r in records if r["brand_id"] == brand_id]
        if keep:
            repos.delete_labor_cases_except(db, brand_id, keep)


def _targets(only: Optional[Iterable[str]]) -> list[tuple[str, DolMatch]]:
    mapped = {b: s.dol for b, s in brand_sources.all_sources().items() if s.dol}
    if only is None:
        return list(mapped.items())
    wanted = list(only)
    unknown = sorted(set(wanted) - set(mapped))
    if unknown:
        raise DolError(f"no DOL mapping in brand_sources.json for: {', '.join(unknown)}")
    return [(b, mapped[b]) for b in wanted]


def load(db: Database, client: httpx.Client, *, api_key: str,
         now: Callable[[], datetime] = repos.utcnow,
         sleep: Callable[[float], None] = time.sleep,
         only: Optional[Iterable[str]] = None,
         pause: float = 0.0,
         progress: Optional[Callable[[str, str], None]] = None,
         rows_for: Optional[Callable[[DolMatch], list[dict]]] = None) -> pipeline.LoadReport:
    """Fetch every mapped brand, or just `only`. `rows_for(match)` reads rows from a downloaded
    database instead of the API (no key, no rate limit). `pause` seconds between API brands eases
    DOL's rate limit; `progress(brand, message)` is called as each brand finishes."""
    if rows_for is None and not api_key:
        raise DolError("DOL_API_KEY is not set (free key: https://dataportal.dol.gov/api-keys)")
    targets = _targets(only)
    retrieved_at = now()
    records, errors, loaded = [], [], []
    for i, (brand_id, match) in enumerate(targets):
        if i and pause > 0 and rows_for is None:
            sleep(pause)
        try:
            rows = rows_for(match) if rows_for else fetch_rows(client, api_key, match, sleep=sleep)
        except DolError as exc:
            errors.append(f"{brand_id}: {exc}")
            if progress:
                progress(brand_id, f"failed ({exc})")
            continue
        method = "download" if rows_for else "api"
        cases = [to_case(r, brand_id, retrieved_at, method) for r in rows if _in_industry(r, match)]
        records.extend(cases)
        loaded.append(brand_id)
        if progress:
            progress(brand_id, f"{len(cases)} case{'' if len(cases) == 1 else 's'}")
    report = pipeline.load(
        db, source=SOURCE, collection=repos.LABOR_CASES, records=records, model=LaborCase,
        doc_id=doc_id, prior_errors=errors, extra={"loaded_brands": loaded}, now=now,
    )
    _prune(db, loaded, records)
    return report


def summarize(db: Database, brand_id: str, since: Optional[date] = None) -> LaborSummary:
    """Wages, safety and organizing for the last two years (or since `since`), computed in MongoDB."""
    since = since or labordata.window_start()
    cutoff = datetime.combine(since, datetime.min.time())
    loaded = db[pipeline.PIPELINE_RUNS].find_one({"source": SOURCE, "loaded_brands": brand_id},
                                                 {"_id": 1}) is not None
    totals = repos.labor_totals(db, brand_id, since=cutoff)
    return LaborSummary(
        merchant_id=brand_id,
        window_start=since.isoformat(),
        safety=osha.summarize(db, brand_id, since),
        organizing=nlrb.summarize(db, brand_id, since),
        loaded=loaded,
        case_count=totals["cases"],
        violations=totals["violations"],
        employees_affected=totals["employees"],
        back_wages=totals["back_wages_cents"] / 100,
        penalties=totals["penalties_cents"] / 100,
        child_labor_cases=totals["child_labor_cases"],
        earliest_finding=totals["earliest"].date().isoformat() if totals["earliest"] else None,
        latest_finding=totals["latest"].date().isoformat() if totals["latest"] else None,
    )
