"""Load outside data sources into Atlas through the shared validate-then-write step.

Usage (from backend/, dev dependencies installed):
    python -m scripts.load_data reports          # company reports in app/data/company_reports.json
    python -m scripts.load_data climate_trace    # Climate TRACE owner totals (no key)
    python -m scripts.load_data dol              # DOL wage and hour cases (nightly file, no key)
    python -m scripts.load_data osha             # OSHA inspections, violations, penalties (6 GB unzipped)
    python -m scripts.load_data nlrb             # NLRB unfair labor practice charges and elections
    python -m scripts.load_data labor            # dol + osha + nlrb
    python -m scripts.load_data all

Labor sources download nightly databases into backend/.cache/labordata/ and keep only the last two
years for brands in brand_sources.json. The cache is deleted afterwards unless --keep-cache.
Safe to re-run: documents are upserted by natural id. Every run is logged in `pipeline_runs`.
Exits 1 if any record was rejected, so a broken source is never silently half-loaded.
"""

import argparse
import shutil
import sys
from datetime import date, datetime, timezone

import httpx
from pymongo.errors import PyMongoError

from app import config
from app.db.client import get_db, ping
from app.db.indexes import ensure_indexes
from app.services import climate_trace, company_reports, labor, labordata, nlrb, osha, pipeline

LABOR = ("dol", "osha", "nlrb")
SOURCES = ("reports", "climate_trace", *LABOR)
MAX_REDIRECTS = 5
DEFAULT_DOL_PAUSE_SECONDS = 3.0  # API mode only: between brands; DOL rate-limits hard (HTTP 429)
LABOR_FILE = {"dol": "whisard", "osha": "osha_enforcement", "nlrb": "nlrb"}


def _last_complete_year() -> int:
    return datetime.now(timezone.utc).year - 1


def _progress(source: str):
    def report(brand_id: str, message: str) -> None:
        print(f"  {source:<5} {brand_id:<16} {message}", flush=True)
    return report


def _log(message: str) -> None:
    print(f"  {message}", flush=True)


def run_labor(db, client: httpx.Client, source: str, since: date, brands: list[str] | None,
              use_api: bool = False, pause: float = DEFAULT_DOL_PAUSE_SECONDS) -> pipeline.LoadReport:
    if source == "dol" and use_api:
        return labor.load(db, client, api_key=config.DOL_API_KEY, only=brands, pause=pause,
                          progress=_progress("dol"))
    conn = labordata.connect(labordata.ensure(LABOR_FILE[source], client, log=_log))
    try:
        if source == "dol":
            return labor.load(db, client, api_key="", only=brands, progress=_progress("dol"),
                              rows_for=lambda match: labor.whisard_rows(conn, match, since))
        loader = osha.load if source == "osha" else nlrb.load
        return loader(db, conn, since=since, only=brands, progress=_progress(source))
    finally:
        conn.close()


def run_source(db, client: httpx.Client, source: str, year: int, brands: list[str] | None = None,
               since: date | None = None, use_api: bool = False,
               pause: float = DEFAULT_DOL_PAUSE_SECONDS) -> pipeline.LoadReport:
    if source == "reports":
        return company_reports.load_reports(
            db, company_reports.curated_entries(), fetch=lambda url: company_reports.download(client, url))
    if source == "climate_trace":
        return climate_trace.load(db, client, year=year)
    return run_labor(db, client, source, since or labordata.window_start(), brands, use_api, pause)


def _print(report: pipeline.LoadReport) -> None:
    status = "ok" if report.ok else "WITH REJECTIONS"
    print(f"{report.source:<15} -> {report.collection:<18} written {report.written:>4}  "
          f"rejected {report.rejected:>3}  {status}")
    for err in report.errors:
        print(f"    - {err}")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", choices=[*SOURCES, "labor", "all"])
    parser.add_argument("--year", type=int, default=_last_complete_year(), help="Climate TRACE year")
    parser.add_argument("--brands", help="labor only: comma-separated brand ids, e.g. torchys,wegmans")
    parser.add_argument("--keep-cache", action="store_true", help="keep downloaded labor databases after the run")
    parser.add_argument("--api", action="store_true", help="dol only: use the rate-limited DOL API instead")
    parser.add_argument("--pause", type=float, default=DEFAULT_DOL_PAUSE_SECONDS, help="--api only: seconds between brands")
    return parser.parse_args(argv)


def _chosen(source: str) -> tuple[str, ...]:
    return SOURCES if source == "all" else LABOR if source == "labor" else (source,)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    brands = [b.strip() for b in args.brands.split(",") if b.strip()] if args.brands else None
    reports, skipped = [], []
    try:
        db = get_db()
        ping(db)
        ensure_indexes(db)
        with httpx.Client(max_redirects=MAX_REDIRECTS) as client:
            for source in _chosen(args.source):
                try:
                    reports.append(run_source(db, client, source, args.year, brands,
                                              use_api=args.api, pause=args.pause))
                except (labor.DolError, labordata.LabordataError) as exc:
                    skipped.append(f"{source}: skipped: {exc}")
    except PyMongoError as exc:
        print(f"Load failed: MongoDB error ({type(exc).__name__}). Check MONGODB_URI and Atlas Network Access.",
              file=sys.stderr)
        return 1
    finally:
        if not args.keep_cache and labordata.CACHE_DIR.exists():
            shutil.rmtree(labordata.CACHE_DIR, ignore_errors=True)  # multi-GB scratch files

    for report in reports:
        _print(report)
    for line in skipped:
        print(line, file=sys.stderr)
    return 0 if all(r.ok for r in reports) and not skipped else 1


if __name__ == "__main__":
    sys.exit(main())
