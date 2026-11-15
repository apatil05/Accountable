"""Hand-mapped identifiers for each brand in outside data sources (data/brand_sources.json).

Owner: Person A. Matching "Shell PLC" or "WAL-MART STORES EAST LP" to our brand slugs at
request time would be slow and error-prone, so each brand's IDs are curated once here and
validated when loaded. A brand with no entry simply has no outside-source data.
"""

import json
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .. import config


class DolMatch(BaseModel):
    """How to find a brand in DOL Wage and Hour data: name substrings, narrowed by industry."""

    name_patterns: list[str] = Field(min_length=1, max_length=10)
    naics_prefixes: list[str] = Field(min_length=1, max_length=10)

    @field_validator("name_patterns")
    @classmethod
    def _plain_text(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            if not 2 <= len(p) <= 60 or any(c in p for c in "%_\"\\"):
                raise ValueError("name patterns are plain text of 2-60 characters (wildcards are added for you)")
        return patterns

    @field_validator("naics_prefixes")
    @classmethod
    def _digits(cls, prefixes: list[str]) -> list[str]:
        if any(not p.isdigit() or not 2 <= len(p) <= 6 for p in prefixes):
            raise ValueError("NAICS prefixes are 2-6 digits")
        return prefixes


class BrandSource(BaseModel):
    carbon_factor: Optional[str] = None  # overrides the category-based emission factor
    climate_trace_owner: Optional[str] = Field(None, pattern=r"^E\d{6,15}$")
    dol: Optional[DolMatch] = None


EMPTY = BrandSource()


@lru_cache
def all_sources() -> dict[str, BrandSource]:
    raw = json.loads((config.DATA_DIR / "brand_sources.json").read_text())
    return {brand_id: BrandSource(**entry) for brand_id, entry in raw.items()}


def get(brand_id: str) -> BrandSource:
    return all_sources().get(brand_id, EMPTY)
