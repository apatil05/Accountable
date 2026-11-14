"""Build app/data/emission_factors.json from the official v1.4.0 supply chain emission factors.

Usage (from backend/, dev dependencies installed):
    python -m scripts.build_emission_factors path/to/SupplyChainGHGEmissionFactorsv1.4.0.xlsx

Download the workbook from https://doi.org/10.5281/zenodo.17202747 (CC BY 4.0). The script
refuses any file whose SHA-256 differs from the published one, fetches the CPI-U index from the
BLS public API (no key) so spending can be converted to the factors' 2024 dollars, and writes a
small JSON file that the app loads at runtime. Re-run only when a new factor version ships.

Methodology choices (also in docs/DATA.md):
- Factors are "with margins" (purchaser price), the right column for consumer spending.
- Each category maps to the PRODUCT bought, not the store (gas -> petroleum refineries).
- Gasoline adds tailpipe CO2 (EPA: 8,887 g/gallon) at the EIA 2024 average price, because
  burning fuel dwarfs producing it and supply-chain factors exclude use.
- Groceries use the median factor of NAICS 311 food manufacturing (product mix unknown).
- No factor for Utilities, Education or Other: v1.4.0 has no electricity rows and the rest
  are too mixed. No estimate beats a misleading one.
"""

import argparse
import hashlib
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import httpx

from app import config

FACTOR_VERSION = "1.4.0"
WORKBOOK_SHA256 = "e47f19ba7330d4adfb77bd4663a2964af1c7b77197a74ea17a76434c9434f6b2"
SHEET = "CO2e"
EMISSIONS_DATA_YEAR = 2023
DOLLAR_YEAR = 2024
SOURCE = {
    "name": "Supply Chain Greenhouse Gas Emission Factors for U.S. Commodities v1.4.0 "
            "(Ingwersen & Young, Cornerstone Sustainability Data Initiative)",
    "url": "https://doi.org/10.5281/zenodo.17202747",
    "license": "CC-BY-4.0",
}

# Tailpipe CO2 per gallon of gasoline burned, and the average US retail price in the factors' dollar year.
GASOLINE_KG_CO2_PER_GALLON = 8.887  # https://www.epa.gov/greenvehicles/greenhouse-gas-emissions-typical-passenger-vehicle
GASOLINE_USD_PER_GALLON_2024 = 3.304  # EIA, US regular all formulations, 2024 annual average

BLS_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
CPI_SERIES = "CUUR0000SA0"  # CPI-U, US city average, all items, not seasonally adjusted

GROCERIES_NAICS_PREFIX = "311"

# factor_id -> (NAICS code or None for a computed factor, label, confidence)
FACTOR_SPECS: dict[str, tuple[str | None, str, str]] = {
    "coffee_shop": ("722515", "Coffee shops and snack bars", "medium"),
    "limited_service_restaurant": ("722513", "Limited-service (fast casual) restaurants", "medium"),
    "full_service_restaurant": ("722511", "Full-service restaurants", "medium"),
    "groceries": (None, "Groceries (median of US food manufacturing)", "low"),
    "gasoline": ("324110", "Gasoline, including burning it", "high"),
    "apparel": ("315240", "Clothing", "medium"),
    "footwear": ("316210", "Footwear", "medium"),
    "electronics": ("334210", "Phones and communications equipment", "medium"),
    "general_merchandise": ("339999", "Mixed manufactured goods", "low"),
    "pharmacy": ("325412", "Medicines and pharmacy goods", "medium"),
    "insurance": ("524126", "Insurance", "medium"),
    "subscriptions": ("515210", "Streaming and subscription media", "medium"),
    "rideshare_taxi": ("485310", "Taxi and rideshare", "medium"),
    "air_travel": ("481111", "Air travel", "low"),
    "entertainment": ("512131", "Movies and entertainment", "low"),
    "housing_rent": ("531110", "Residential rent", "medium"),
}

CATEGORY_FACTORS: dict[str, str] = {
    "Apparel": "apparel",
    "Coffee": "coffee_shop",
    "Electronics": "electronics",
    "Entertainment": "entertainment",
    "Food": "limited_service_restaurant",
    "Gas": "gasoline",
    "Groceries": "groceries",
    "Health": "pharmacy",
    "Housing": "housing_rent",
    "Insurance": "insurance",
    "Online": "general_merchandise",
    "Restaurant": "full_service_restaurant",
    "Shopping": "general_merchandise",
    "Subscriptions": "subscriptions",
    "Transport": "rideshare_taxi",
    "Travel": "air_travel",
}

Row = tuple[str, str, float]  # (NAICS code, title, kg CO2e per 2024 USD with margins)


def _factor(factor_id: str, rows_by_code: dict[str, Row], food: list[float]) -> dict:
    code, label, confidence = FACTOR_SPECS[factor_id]
    if code is None:
        return {"naics": f"{GROCERIES_NAICS_PREFIX}xxx", "label": label, "confidence": confidence,
                "kg_per_usd": round(statistics.median(food), 4), "use_phase_kg_per_usd": 0.0,
                "basis": f"median of {len(food)} NAICS {GROCERIES_NAICS_PREFIX} factors"}
    if code not in rows_by_code:
        raise ValueError(f"NAICS {code} ({factor_id}) is not in the workbook")
    _, title, value = rows_by_code[code]
    use_phase = GASOLINE_KG_CO2_PER_GALLON / GASOLINE_USD_PER_GALLON_2024 if factor_id == "gasoline" else 0.0
    return {"naics": code, "label": label, "confidence": confidence, "kg_per_usd": value,
            "use_phase_kg_per_usd": round(use_phase, 4), "basis": f"NAICS {code} {title}"}


def build(rows: Iterable[Row], cpi_monthly: dict[str, float], cpi_base: float,
          retrieved_at: datetime, sha256: str) -> dict:
    """Pure transform from workbook rows + CPI to the JSON document the app loads."""
    rows_by_code = {r[0]: r for r in rows}
    food = [r[2] for code, r in rows_by_code.items() if code.startswith(GROCERIES_NAICS_PREFIX)]
    if not food:
        raise ValueError(f"no NAICS {GROCERIES_NAICS_PREFIX} rows for the groceries factor")
    unknown = sorted(set(CATEGORY_FACTORS.values()) - set(FACTOR_SPECS))
    if unknown:
        raise ValueError(f"categories map to unknown factors: {unknown}")
    return {
        "version": FACTOR_VERSION,
        "source": {**SOURCE, "sha256": sha256, "emissions_data_year": EMISSIONS_DATA_YEAR,
                   "dollar_year": DOLLAR_YEAR, "retrieved_at": retrieved_at.isoformat()},
        "unit": f"kg CO2e per {DOLLAR_YEAR} USD, purchaser price (with margins)",
        "cpi": {"series": CPI_SERIES, "base_year": DOLLAR_YEAR, "base_value": cpi_base,
                "monthly": dict(sorted(cpi_monthly.items()))},
        "factors": {fid: _factor(fid, rows_by_code, food) for fid in FACTOR_SPECS},
        "categories": CATEGORY_FACTORS,
    }


def read_workbook(path: Path) -> list[Row]:
    from openpyxl import load_workbook  # dev-only dependency

    sheet = load_workbook(path, read_only=True)[SHEET]
    return [(str(code), str(title), float(with_margins))
            for code, title, _gas, _unit, _no_margin, _margin, with_margins, _ref
            in sheet.iter_rows(min_row=2, values_only=True) if code is not None]


def fetch_cpi(client: httpx.Client, first_year: int, last_year: int) -> tuple[dict[str, float], float]:
    """Monthly CPI-U ("YYYY-MM" -> index) and the annual average of DOLLAR_YEAR."""
    body = {"seriesid": [CPI_SERIES], "startyear": str(first_year), "endyear": str(last_year),
            "annualaverage": True}
    resp = client.post(BLS_URL, json=body, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS request failed: {payload.get('message')}")
    monthly, base = {}, None
    for item in payload["Results"]["series"][0]["data"]:
        try:
            value = float(item["value"])
        except ValueError:
            continue  # BLS marks missing months with "-" (e.g. the Oct 2025 shutdown)
        if item["period"] == "M13" and int(item["year"]) == DOLLAR_YEAR:
            base = value
        elif item["period"].startswith("M") and item["period"] != "M13":
            monthly[f"{item['year']}-{item['period'][1:]}"] = value
    if base is None:
        raise RuntimeError(f"BLS returned no {DOLLAR_YEAR} annual average")
    return monthly, base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workbook", type=Path)
    args = parser.parse_args(argv)

    digest = hashlib.sha256(args.workbook.read_bytes()).hexdigest()
    if digest != WORKBOOK_SHA256:
        print(f"Refusing {args.workbook}: SHA-256 {digest} is not the published v{FACTOR_VERSION} file.",
              file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    try:
        with httpx.Client() as client:
            monthly, base = fetch_cpi(client, DOLLAR_YEAR, now.year)
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"Could not fetch CPI from BLS: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    doc = build(read_workbook(args.workbook), monthly, base, now, digest)
    out = config.DATA_DIR / "emission_factors.json"
    out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"Wrote {out} ({len(doc['factors'])} factors, CPI through {max(monthly)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
