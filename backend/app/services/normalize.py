"""Merchant-name normalization: messy Nessie/bank names -> canonical brands.

Owner: Person A. Pure functions with no I/O so they are fast to test; callers
pass in the brand list (data/brands.json) and category map (data/category_map.json).

Resolution order for a raw merchant name:
  1. alias   exact match on the cleaned name ("amazon.com" -> amazon)
  2. prefix  cleaned name starts with an alias ("walmart chapel hill" -> walmart);
             skipped for brands flagged `strict` (ambiguous words like Apple/Target)
  3. fuzzy   typo tolerance via rapidfuzz ("starbuks" -> starbucks)
  4. junk    test/demo/placeholder records, flagged so research skips them
  5. local   anything else becomes its own local-<slug> brand
"""

import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from rapidfuzz import fuzz, process

CANONICAL_CATEGORIES: tuple[str, ...] = (
    "Food", "Coffee", "Groceries", "Shopping", "Online", "Apparel", "Electronics",
    "Restaurant", "Gas", "Transport", "Utilities", "Subscriptions", "Health",
    "Entertainment", "Travel", "Education", "Insurance", "Housing", "Other",
)

FUZZY_THRESHOLD = 90
MIN_PREFIX_ALIAS_LEN = 4
MIN_FUZZY_LEN = 5

JUNK_TOKENS = frozenset({"demo", "test", "sandbox", "fictional", "placeholder"})
GENERIC_NAMES = frozenset({"bar", "online store", "boba shop", "shop", "store", "demo"})

_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_STAR_SUFFIX = re.compile(r"\*.*$")
_STORE_NUMBER = re.compile(r"#\s*\d+")
_TLD = re.compile(r"\.(com|net|org|io)\b")
_LEGAL_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|sa de cv)\b\.?")
_NOT_NAME_CHARS = re.compile(r"[^a-z0-9&\- ]")
_APOSTROPHES = re.compile(r"['’‘`]")
_WORD = re.compile(r"[a-z0-9]+")

MatchMethod = Literal["alias", "prefix", "fuzzy", "local", "junk"]


@dataclass(frozen=True)
class BrandMatch:
    brand_id: str
    name: str
    method: MatchMethod
    score: float
    is_junk: bool
    is_chain: bool
    categories: tuple[str, ...] = ()
    parent_id: str | None = None


@dataclass(frozen=True)
class BrandIndex:
    alias_to_id: Mapping[str, str]
    brands: Mapping[str, Mapping[str, Any]]
    prefix_aliases: tuple[tuple[str, str], ...]  # longest alias first
    fuzzy_aliases: tuple[str, ...]


def clean_name(raw: str | None) -> str:
    """Lowercase and strip store numbers, brackets, domains, legal suffixes, punctuation."""
    text = unicodedata.normalize("NFKC", raw or "").casefold()
    text = _APOSTROPHES.sub("", text)
    text = _BRACKETS.sub(" ", text)
    text = text.split("·")[0].split(" | ")[0]  # "Starbucks · Reitz Union"
    text = _STAR_SUFFIX.sub("", text)  # "amzn mktp us*2k"
    text = _STORE_NUMBER.sub(" ", text)
    text = _TLD.sub("", text)
    text = _LEGAL_SUFFIX.sub(" ", text)
    text = _NOT_NAME_CHARS.sub(" ", text)
    return " ".join(text.split())


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "unknown"


def build_brand_index(brands: Sequence[Mapping[str, Any]]) -> BrandIndex:
    alias_to_id: dict[str, str] = {}
    for brand in brands:
        for alias in {clean_name(brand["name"]), *(clean_name(a) for a in brand.get("aliases", []))}:
            if not alias:
                continue
            owner = alias_to_id.setdefault(alias, brand["_id"])
            if owner != brand["_id"]:
                raise ValueError(f"alias {alias!r} claimed by both {owner!r} and {brand['_id']!r}")

    strict_ids = {b["_id"] for b in brands if b.get("strict")}
    prefix = sorted(
        ((a, i) for a, i in alias_to_id.items() if i not in strict_ids and len(a) >= MIN_PREFIX_ALIAS_LEN),
        key=lambda pair: (-len(pair[0]), pair[0]),
    )
    fuzzy = tuple(sorted(a for a in alias_to_id if len(a) >= MIN_FUZZY_LEN))
    return BrandIndex(
        alias_to_id=MappingProxyType(alias_to_id),
        brands=MappingProxyType({b["_id"]: MappingProxyType(dict(b)) for b in brands}),
        prefix_aliases=tuple(prefix),
        fuzzy_aliases=fuzzy,
    )


def _known(index: BrandIndex, brand_id: str, method: MatchMethod, score: float) -> BrandMatch:
    brand = index.brands[brand_id]
    return BrandMatch(
        brand_id=brand_id,
        name=brand["name"],
        method=method,
        score=score,
        is_junk=False,
        is_chain=bool(brand.get("is_chain", True)),
        categories=tuple(brand.get("categories", ())),
        parent_id=brand.get("parent_id"),
    )


def _is_junk(raw: str, cleaned: str) -> bool:
    lowered = raw.casefold()
    return "@" in lowered or cleaned in GENERIC_NAMES or bool(JUNK_TOKENS & set(_WORD.findall(lowered)))


def resolve_brand(raw_name: str | None, index: BrandIndex) -> BrandMatch:
    raw = (raw_name or "").strip()
    cleaned = clean_name(raw)

    if cleaned and (brand_id := index.alias_to_id.get(cleaned)):
        return _known(index, brand_id, "alias", 100.0)

    for alias, brand_id in index.prefix_aliases:
        if cleaned.startswith(alias + " "):
            return _known(index, brand_id, "prefix", 100.0)

    if len(cleaned) >= MIN_FUZZY_LEN:
        best = process.extractOne(cleaned, index.fuzzy_aliases, scorer=fuzz.ratio, score_cutoff=FUZZY_THRESHOLD)
        if best:
            alias, score, _ = best
            return _known(index, index.alias_to_id[alias], "fuzzy", round(score, 1))

    if not cleaned or _is_junk(raw, cleaned):
        return BrandMatch(
            brand_id=f"junk-{slugify(cleaned)}", name=raw or "Unknown", method="junk",
            score=0.0, is_junk=True, is_chain=False,
        )
    return BrandMatch(
        brand_id=f"local-{slugify(cleaned)}", name=cleaned.title(), method="local",
        score=0.0, is_junk=False, is_chain=False,
    )


def canonical_categories(
    raw_category: str | Sequence[str] | None,
    brand_categories: Sequence[str] | None,
    category_map: Mapping[str, str],
) -> list[str]:
    """Curated brand categories win; otherwise map raw labels to the canonical list."""
    if brand_categories:
        return list(dict.fromkeys(brand_categories))

    labels = [raw_category] if isinstance(raw_category, str) else list(raw_category or [])
    canonical_by_key = {c.casefold(): c for c in CANONICAL_CATEGORIES}
    mapped: list[str] = []
    for label in labels:
        key = label.strip().casefold() if isinstance(label, str) else ""
        mapped.append(category_map.get(key) or canonical_by_key.get(key) or "Other")

    unique = list(dict.fromkeys(mapped))
    if len(unique) > 1:
        unique = [c for c in unique if c != "Other"]
    return unique or ["Other"]
