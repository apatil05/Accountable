"""Merchant-name normalization. Cases come from real names seen in the Nessie
enterprise dataset (see docs/PLAN.md), so these double as regression tests."""

import json

import pytest

from app import config
from app.services import normalize


@pytest.fixture(scope="module")
def brands() -> list[dict]:
    return json.loads((config.DATA_DIR / "brands.json").read_text())


@pytest.fixture(scope="module")
def index(brands):
    return normalize.build_brand_index(brands)


# ---------- clean_name ----------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Amazon", "amazon"),
        ("amazon.com", "amazon"),
        ("Amazon.com", "amazon"),
        ("Netflix.Com", "netflix"),
        ("AMZN Mktp US*2K", "amzn mktp us"),
        ("Amazon (live mode, sandbox payment)", "amazon"),
        ("Walmart (demo)", "walmart"),
        ("Costco Durham (fictional demo warehouse)", "costco durham"),
        ("COSTCO WHSE #0472", "costco whse"),
        ("Starbucks · Reitz Union", "starbucks"),
        ("Trader Joe’s", "trader joes"),
        ("McDonald's", "mcdonalds"),
        ("  Starbucks  ", "starbucks"),
        ("Chipotle Mexican Grill, Inc.", "chipotle mexican grill"),
        ("Harbor Grocery [UNDERTOW-demo-d34910948bf331cc]", "harbor grocery"),
        ("H&M", "h&m"),
        ("", ""),
    ],
)
def test_clean_name(raw, expected):
    assert normalize.clean_name(raw) == expected


# ---------- resolve_brand ----------
@pytest.mark.parametrize(
    "raw, brand_id, method",
    [
        ("Amazon", "amazon", "alias"),
        ("amazon.com", "amazon", "alias"),
        ("Amazon Marketplace", "amazon", "alias"),
        ("AMZN Mktp US*2K", "amazon", "alias"),
        ("Amazon (live mode, sandbox payment)", "amazon", "alias"),
        ("Walmart (demo)", "walmart", "alias"),
        ("Walmart Chapel Hill (fictional demo store)", "walmart", "prefix"),
        ("COSTCO WHSE #0472", "costco", "alias"),
        ("Costco Wholesale Austin", "costco", "prefix"),
        ("Starbucks · Reitz Union", "starbucks", "alias"),
        ("Starbuks", "starbucks", "fuzzy"),
        ("Trader Joe’s", "traderjoes", "alias"),
        ("H&M", "hm", "alias"),
        ("Apple Store", "apple", "alias"),
        ("Uber Eats", "ubereats", "alias"),
        ("Whole Foods Market", "wholefoods", "alias"),
    ],
)
def test_resolve_known_brands(index, raw, brand_id, method):
    match = normalize.resolve_brand(raw, index)
    assert match.brand_id == brand_id
    assert match.method == method
    assert not match.is_junk


# Brands found missing when the normalizer was run over 6,724 real Nessie enterprise merchants.
@pytest.mark.parametrize(
    "raw, brand_id",
    [
        ("Delta Air Lines", "delta"),
        ("Publix Super Market", "publix"),
        ("H-E-B", "heb"),
        ("Domino's", "dominos"),
        ("Torchy's Tacos", "torchys"),
        ("Sam's Club (demo)", "samsclub"),  # a real brand with a "(demo)" suffix must not become junk
        ("Sam's Club Durham (fictional demo club)", "samsclub"),
        ("iCloud", "apple"),
        ("Mint Mobile", "mintmobile"),
        ("Planet Fitness", "planetfitness"),
        ("Taco Bell", "tacobell"),
        ("Home Lepot", "homedepot"),
        ("Pret A Manger", "pret"),
        ("Steam", "steam"),
    ],
)
def test_brands_found_in_the_real_dataset(index, raw, brand_id):
    match = normalize.resolve_brand(raw, index)
    assert (match.brand_id, match.is_junk) == (brand_id, False)


@pytest.mark.parametrize("raw", ["Steam Cleaning Co", "Zoom Photo Studio", "Notion Cafe", "Boots And Saddles", "Staples Of Italy"])
def test_ambiguous_new_brands_do_not_swallow_local_businesses(index, raw):
    assert normalize.resolve_brand(raw, index).method == "local"


@pytest.mark.parametrize("raw", ["Apple Orchard Cafe", "Target Practice Supply", "Shell Beach Bakery", "Subway Tile Outlet"])
def test_strict_brands_do_not_prefix_match(index, raw):
    # Ambiguous common words must not swallow unrelated local businesses.
    match = normalize.resolve_brand(raw, index)
    assert match.method == "local"
    assert match.brand_id.startswith("local-")


@pytest.mark.parametrize(
    "raw",
    [
        "Cursor test grocery",
        "Demo Groceries",
        "Fictional Diner (demo)",
        "Harbor Grocery [UNDERTOW-demo-d34910948bf331cc]",
        "someone@example.com",
        "Online store",
        "Bar",
        "Runway sandbox: Alibaba.com",
        # machine-generated test merchants seen in the real dataset
        "atl-ad1d246c3cc32858e79acd5c",
        "Scurry Sample Campus Market s-07d865efbaf86ffdb825",
        "Simulated restaurant",
        "ZZ Flow Probe Merchant",
        "Pharmacy (demo history)",
    ],
)
def test_junk_is_flagged(index, raw):
    match = normalize.resolve_brand(raw, index)
    assert match.is_junk
    assert match.method == "junk"
    assert match.brand_id.startswith("junk-")


def test_unknown_business_becomes_local_brand(index):
    match = normalize.resolve_brand("Old Coffee House", index)
    assert match.brand_id == "local-old-coffee-house"
    assert match.method == "local"
    assert not match.is_chain


def test_blank_name_is_junk(index):
    assert normalize.resolve_brand("   ", index).is_junk


def test_resolution_is_deterministic(index):
    first = normalize.resolve_brand("Walmart Chapel Hill (fictional demo store)", index)
    assert normalize.resolve_brand("Walmart Chapel Hill (fictional demo store)", index) == first


def test_build_index_rejects_duplicate_alias():
    brands = [
        {"_id": "a", "name": "A", "aliases": ["same"]},
        {"_id": "b", "name": "B", "aliases": ["same"]},
    ]
    with pytest.raises(ValueError, match="same"):
        normalize.build_brand_index(brands)


# ---------- canonical_categories ----------
@pytest.fixture(scope="module")
def category_map() -> dict:
    return json.loads((config.DATA_DIR / "category_map.json").read_text())


def test_brand_categories_win_over_raw_label(category_map):
    # Target is labeled "Grocery" 121 times in the sandbox but is general retail.
    assert normalize.canonical_categories("Grocery", ["Shopping"], category_map) == ["Shopping"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("food", ["Food"]),
        ("Groceries", ["Groceries"]),
        ("GROCERY", ["Groceries"]),
        ("gas_station", ["Gas"]),
        ("Eating out", ["Restaurant"]),
        ("OTHER", ["Other"]),
        ("demo", ["Other"]),
        (None, ["Other"]),
        ("", ["Other"]),
        ("totally-unknown-label", ["Other"]),
        ("auto", ["Transport"]),
        ("hotel", ["Travel"]),
        ("flight", ["Travel"]),
        ("pharmacy", ["Health"]),
        ("books", ["Shopping"]),
        ("fitness", ["Health"]),
        ("university", ["Education"]),
        ("home_improvement", ["Shopping"]),
        (["Food", "Coffee"], ["Food", "Coffee"]),
        (["shopping", "Shopping"], ["Shopping"]),
    ],
)
def test_canonical_categories_without_brand(category_map, raw, expected):
    assert normalize.canonical_categories(raw, None, category_map) == expected


def test_every_mapped_category_is_canonical(category_map):
    assert set(category_map.values()) <= set(normalize.CANONICAL_CATEGORIES)


def test_brand_categories_are_canonical(brands):
    for b in brands:
        assert set(b["categories"]) <= set(normalize.CANONICAL_CATEGORIES), b["_id"]
