"""Load outside data sources into Atlas through the shared validate-then-write step.

Usage (from backend/, dev dependencies installed):
    python -m scripts.load_data reports          # company reports in app/data/company_reports.json
    python -m scripts.load_data climate_trace    # Climate TRACE owner totals (no key)
    python -m scripts.load_data dol              # DOL wage and hour cases (needs DOL_API_KEY)
    python -m scripts.load_data all

Safe to re-run: documents are upserted by natural id. Every run is logged in `pipeline_runs`.
Exits 1 if any record was rejected, so a broken source is never silently half-loaded.
"""

import argparse
import sys
from datetime import datetime, timezone

import httpx
from pymongo.errors import PyMongoError

from app import config
from app.db.client import get_db, ping
from app.db.indexes import ensure_indexes
from app.services import climate_trace, company_reports, labor, pipeline

SOURCES = ("reports", "climate_trace", "dol")
MAX_REDIRECTS = 5


def _last_complete_year() -> int:
    return datetime.now(timezone.utc).year - 1


def run_source(db, client: httpx.Client, source: str, year: int) -> pipeline.LoadReport:
    if source == "reports":
        return company_reports.load_reports(
            db, company_reports.curated_entries(), fetch=lambda url: company_reports.download(client, url))
    if source == "climate_trace":
        return climate_trace.load(db, client, year=year)
    return labor.load(db, client, api_key=config.DOL_API_KEY)


def _print(report: pipeline.LoadReport) -> None:
    status = "ok" if report.ok else "WITH REJECTIONS"
    print(f"{report.source:<15} -> {report.collection:<18} written {report.written:>4}  "
          f"rejected {report.rejected:>3}  {status}")
    for err in report.errors:
        print(f"    - {err}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", choices=[*SOURCES, "all"])
    parser.add_argument("--year", type=int, default=_last_complete_year(), help="Climate TRACE year")
    args = parser.parse_args(argv)
    chosen = SOURCES if args.source == "all" else (args.source,)

    reports, skipped = [], []
    try:
        db = get_db()
        ping(db)
        ensure_indexes(db)
        with httpx.Client(max_redirects=MAX_REDIRECTS) as client:
            for source in chosen:
                try:
                    reports.append(run_source(db, client, source, args.year))
                except labor.DolError as exc:  # e.g. no key: skip this source, keep the others
                    skipped.append(f"{source}: skipped: {exc}")
    except PyMongoError as exc:
        print(f"Load failed: MongoDB error ({type(exc).__name__}). Check MONGODB_URI and Atlas Network Access.",
              file=sys.stderr)
        return 1

    for report in reports:
        _print(report)
    for line in skipped:
        print(line, file=sys.stderr)
    return 0 if all(r.ok for r in reports) and not skipped else 1


if __name__ == "__main__":
    sys.exit(main())
