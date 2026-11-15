"""Map data: how a merchant's typical product gets made and reaches you.

Owner: Person D (Map & alternatives). Cache plumbing: Person A.

Read path: cached research (`supply_chains` collection, one document per brand slug)
-> mock data. `python -m scripts.prewarm` fills the cache via refresh(); the API never
researches supply chains live, because a map with invented coordinates is worse than none.

TODO(Person D): implement research_supply_chain(): ask Gemini for the stages of the
merchant's top product (returning place names), then geocode them (e.g. Nominatim or a
static lookup table) so the LLM never invents coordinates.
"""

import json
from functools import lru_cache
from typing import Iterable

from pymongo.database import Database

from .. import config
from ..db import repos
from ..models import Alternative, SupplyChain, SupplyChainStage
from .research import ResearchUnavailable, ensure_available


@lru_cache
def _chains() -> dict:
    return json.loads((config.DATA_DIR / "mock_supply_chains.json").read_text())


@lru_cache
def _alts() -> dict:
    return json.loads((config.DATA_DIR / "mock_alternatives.json").read_text())


def research_supply_chain(merchant_name: str) -> tuple[str, list[SupplyChainStage]]:
    """TODO(Person D): return (product, geocoded stages) for the merchant's top product."""
    raise NotImplementedError


def refresh(db: Database, merchant_id: str, merchant_name: str) -> SupplyChain:
    """Research now and overwrite the cache. Raises ResearchUnavailable if it cannot run here."""
    ensure_available(config.GEMINI_API_KEY)
    try:
        product, stages = research_supply_chain(merchant_name)
    except NotImplementedError:
        raise ResearchUnavailable("supply chain research (research_supply_chain) is not implemented yet") from None
    repos.save_supply_chain(db, merchant_id, product, [s.model_dump() for s in stages], "gemini")
    return SupplyChain(merchant_id=merchant_id, product=product, stages=stages, data_source="gemini")


def _from_cache(doc: dict) -> SupplyChain:
    return SupplyChain(
        merchant_id=doc["_id"], product=doc["product"], stages=doc["stages"], data_source=doc["data_source"]
    )


def _from_mock(merchant_id: str) -> SupplyChain | None:
    c = _chains().get(merchant_id)
    return SupplyChain(merchant_id=merchant_id, **c) if c else None


def get_supply_chain(db: Database, merchant_id: str) -> SupplyChain | None:
    cached = repos.get_supply_chain(db, merchant_id)
    return _from_cache(cached) if cached else _from_mock(merchant_id)


def get_supply_chains(db: Database, merchant_ids: Iterable[str]) -> list[SupplyChain]:
    """Cached-or-mock chains for many merchants in one query, in the given order."""
    ids = list(merchant_ids)
    cached = repos.get_supply_chains(db, ids)
    chains = [_from_cache(cached[i]) if i in cached else _from_mock(i) for i in ids]
    return [c for c in chains if c]


def get_alternatives(categories: list[str], prefer_local: bool) -> list[Alternative]:
    """Feature 5. TODO(Person D): Gemini + Places search near the user."""
    seen, out = set(), []
    for cat in categories:
        for a in _alts().get(cat, []):
            if a["name"] not in seen:
                seen.add(a["name"])
                out.append(Alternative(**a))
    if prefer_local:
        out.sort(key=lambda a: not a.is_local)
    return out
