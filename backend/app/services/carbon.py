"""Spend-based carbon estimates: dollars spent x a US industry-average emission factor.

Owner: Person A. Factors come from data/emission_factors.json, generated once from the
official v1.4.0 workbook by scripts/build_emission_factors.py (see that file for methodology).
Spending is converted to the factors' dollar year with CPI-U before multiplying, so a 2026
dollar is not counted as a 2024 dollar.

These are estimates of the emissions behind what the user bought, not company-reported
figures; the UI must label them "estimated".
"""

import bisect
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from .. import config
from ..models import CarbonEstimate
from . import brand_sources

logger = logging.getLogger(__name__)

KG_DECIMALS = 2


@dataclass(frozen=True)
class Factor:
    id: str
    label: str
    naics: str
    kg_per_usd: float  # producing it (supply chain, with margins)
    use_phase_kg_per_usd: float  # using it (burning fuel); 0 for everything else
    confidence: str

    @property
    def total_kg_per_usd(self) -> float:
        return self.kg_per_usd + self.use_phase_kg_per_usd


@lru_cache
def table() -> dict:
    return json.loads((config.DATA_DIR / "emission_factors.json").read_text())


@lru_cache
def _sorted_months() -> tuple[str, ...]:
    return tuple(sorted(table()["cpi"]["monthly"]))


def factor(factor_id: str) -> Factor:
    f = table()["factors"][factor_id]
    return Factor(factor_id, f["label"], f["naics"], f["kg_per_usd"], f["use_phase_kg_per_usd"], f["confidence"])


def factor_for(brand_id: str, categories: Iterable[str]) -> Factor | None:
    """The brand's curated override, else the first of its categories that has a factor."""
    override = brand_sources.get(brand_id).carbon_factor
    if override in table()["factors"]:
        return factor(override)
    if override:
        logger.error("brand %s names unknown carbon factor %r; using its categories", brand_id, override)
    mapping = table()["categories"]
    for category in categories:
        if mapping.get(category) in table()["factors"]:
            return factor(mapping[category])
    return None


def cpi_for(year_month: str) -> float:
    """CPI for "YYYY-MM": the nearest earlier published month, clamped to the table's range."""
    months = _sorted_months()
    i = bisect.bisect_right(months, year_month) - 1
    return table()["cpi"]["monthly"][months[max(i, 0)]]


def to_base_dollars(amount_cents: int, year_month: str) -> float:
    return amount_cents / 100 * table()["cpi"]["base_value"] / cpi_for(year_month)


def estimate(monthly_cents: Iterable[tuple[str, int]], f: Factor) -> CarbonEstimate:
    dollars = sum(to_base_dollars(cents, ym) for ym, cents in monthly_cents)
    return CarbonEstimate(
        kg_co2e=round(dollars * f.total_kg_per_usd, KG_DECIMALS),
        factor_id=f.id,
        factor_label=f"{f.label} (NAICS {f.naics})",
        confidence=f.confidence,  # type: ignore[arg-type]
        data_year=table()["source"]["emissions_data_year"],
        includes_use_phase=f.use_phase_kg_per_usd > 0,
    )
