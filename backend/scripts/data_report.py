"""Which facts does each brand have? One row per brand, before the demo.

Usage (from backend/):
    python -m scripts.data_report                 # the default customer's brands
    python -m scripts.data_report --all-brands    # every non-junk brand in the catalog

Columns: carbon factor (and confidence), company-reported emissions, Climate TRACE estimate,
DOL labor cases, and whether ethics research / supply chains are cached or still mock.
Read-only: changes nothing.
"""

import argparse
import sys

from pymongo.database import Database
from pymongo.errors import PyMongoError

from app import config
from app.db import repos
from app.db.client import get_db, ping
from app.services import carbon, labor, nessie, prewarm

DASH = "-"
COLUMNS = ("brand", "carbon_factor", "reported_emissions", "climate_trace", "labor_cases", "ethics", "supply")


def _emissions_cells(docs: list[dict]) -> tuple[str, str]:
    reported = next((d for d in docs if d["provenance"]["source"] in ("company_report", "manual")), None)
    trace = next((d for d in docs if d["provenance"]["source"] == "climate_trace"), None)
    if reported is None:
        rep = DASH
    elif reported["status"] == "not_reported":
        rep = f"not reported ({reported['data_year']})"
    else:
        rep = f"{reported['data_year']} {reported['provenance'].get('locator') or ''}".strip()
    return rep, (str(trace["data_year"]) if trace else DASH)


def coverage_row(db: Database, brand_id: str, categories: list[str]) -> dict[str, str]:
    factor = carbon.factor_for(brand_id, categories)
    reported, trace = _emissions_cells(repos.get_company_emissions(db, brand_id))
    work = labor.summarize(db, brand_id)
    return {
        "brand": brand_id,
        "carbon_factor": f"{factor.id} ({factor.confidence})" if factor else DASH,
        "reported_emissions": reported,
        "climate_trace": trace,
        "labor_cases": str(work.case_count) if work.loaded else "not loaded",
        "ethics": "cached" if repos.get_ethics_research(db, brand_id) else "mock",
        "supply": "cached" if repos.get_supply_chain(db, brand_id) else "mock",
    }


def coverage(db: Database, customer_id: str | None) -> list[dict[str, str]]:
    brands = repos.get_brands(db, [bid for bid, _ in prewarm.target_brands(db, customer_id)])
    return [coverage_row(db, bid, b.get("categories", [])) for bid, b in sorted(brands.items())]


def render(rows: list[dict[str, str]]) -> str:
    widths = {c: max(len(c), *(len(r[c]) for r in rows)) for c in COLUMNS}
    lines = ["  ".join(c.ljust(widths[c]) for c in COLUMNS)]
    lines += ["  ".join(r[c].ljust(widths[c]) for c in COLUMNS) for r in rows]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all-brands", action="store_true")
    args = parser.parse_args(argv)
    try:
        config.validate()
        db = get_db()
        ping(db)
        customer_id = None if args.all_brands else nessie.default_customer_id()
        rows = coverage(db, customer_id)
    except (config.ConfigError, PyMongoError) as exc:
        print(f"Report failed: {type(exc).__name__}: {exc if isinstance(exc, config.ConfigError) else ''}",
              file=sys.stderr)
        return 1
    print(render(rows) if rows else "No brands yet. Run `python -m scripts.sync` first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
