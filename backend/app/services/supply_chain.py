"""Map data: how a merchant's typical product gets made and reaches you.

Owner: Person D (Map & alternatives).
Mock for now. Later: ask Gemini for the stages of the merchant's top product
(returning place names), then geocode them (e.g. Nominatim or a static
lookup table) so the LLM never invents coordinates.
"""

import json
from functools import lru_cache

from .. import config
from ..models import Alternative, SupplyChain


@lru_cache
def _chains() -> dict:
    return json.loads((config.DATA_DIR / "mock_supply_chains.json").read_text())


@lru_cache
def _alts() -> dict:
    return json.loads((config.DATA_DIR / "mock_alternatives.json").read_text())


def get_supply_chain(merchant_id: str) -> SupplyChain | None:
    c = _chains().get(merchant_id)
    if not c:
        return None
    return SupplyChain(merchant_id=merchant_id, **c)


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
