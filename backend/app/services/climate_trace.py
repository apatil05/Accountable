"""Climate TRACE: independent, asset-level emissions for brands that own heavy-emitting facilities.

Owner: Person A. Free API (no key, CC BY 4.0, beta: keep volume low). Climate TRACE models
emissions of physical assets (refineries, power plants, ...) and links them to owners, so it
covers Shell but not retailers. Brands opt in with `climate_trace_owner` in brand_sources.json.

Caveat shown with the number: owner totals count 100% of each asset's emissions toward an owner
with a majority stake, so they are an estimate of owned-asset emissions, not a reported Scope 1.
"""

from datetime import datetime
from typing import Callable

import httpx
from pymongo.database import Database

from ..db import repos
from ..models import CompanyEmissions
from . import brand_sources, pipeline

SOURCE = "climate_trace"
API_URL = "https://api.climatetrace.org/v7/sources/emissions"
GAS = "co2e_100yr"
TIMEOUT_SECONDS = 60
EXPLORE_URL = "https://climatetrace.org/explore"


def owner_total(client: httpx.Client, owner_id: str, year: int) -> float:
    """Tonnes CO2e (100-year GWP) from all assets of `owner_id` in `year`."""
    resp = client.get(API_URL, params={"year": year, "gas": GAS, "ownerIds": owner_id}, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    summaries = resp.json()["totals"]["summaries"]
    tonnes = float(next(s["emissionsQuantity"] for s in summaries if s["gas"] == GAS))
    if tonnes <= 0:
        # Zero means "no assets found", not "emits nothing"; never publish it as a reported figure.
        raise ValueError("no emissions data for this owner and year")
    return tonnes


def _record(brand_id: str, owner_id: str, year: int, tonnes: float, retrieved_at: datetime) -> dict:
    return {
        "brand_id": brand_id,
        "data_year": year,
        "status": "reported",
        "figures": {"owned_assets": tonnes},
        "provenance": {
            "source": SOURCE,
            "source_name": "Climate TRACE (owned emitting assets)",
            "source_url": EXPLORE_URL,
            "locator": f"owner {owner_id}",
            "data_year": year,
            "retrieved_at": retrieved_at,
            "method": "api",
            "kind": "estimated",
            "license": "CC-BY-4.0",
        },
    }


def load(db: Database, client: httpx.Client, *, year: int,
         now: Callable[[], datetime] = repos.utcnow) -> pipeline.LoadReport:
    records, errors = [], []
    retrieved_at = now()
    for brand_id, source in brand_sources.all_sources().items():
        if not source.climate_trace_owner:
            continue
        try:
            tonnes = owner_total(client, source.climate_trace_owner, year)
            records.append(_record(brand_id, source.climate_trace_owner, year, tonnes, retrieved_at))
        except (httpx.HTTPError, KeyError, StopIteration, ValueError) as exc:
            errors.append(f"{brand_id}: {type(exc).__name__}")
    return pipeline.load(
        db, source=SOURCE, collection=repos.COMPANY_EMISSIONS, records=records, model=CompanyEmissions,
        doc_id=lambda m: f"{m.brand_id}:{SOURCE}:{m.data_year}", prior_errors=errors, now=now,
    )
