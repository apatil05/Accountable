"""Spend-based carbon estimates: factor choice, inflation adjustment, and the summary fields."""

import pytest
from pydantic import ValidationError

from app.models import UserProfile
from app.services import aggregation, brand_sources, carbon, ingest
from scripts import build_emission_factors as build_script

TABLE = carbon.table()


def _cpi(ym: str) -> float:
    return TABLE["cpi"]["monthly"][ym]


# ---------- factor choice ----------
def test_brand_override_wins_over_categories():
    assert carbon.factor_for("starbucks", ["Food", "Coffee"]).id == "coffee_shop"


def test_first_mappable_category_is_used():
    assert carbon.factor_for("walmart", ["Shopping", "Groceries"]).id == "general_merchandise"
    assert carbon.factor_for("local-x", ["Utilities", "Groceries"]).id == "groceries"


def test_no_factor_for_unmapped_categories():
    assert carbon.factor_for("local-power", ["Utilities"]) is None
    assert carbon.factor_for("local-x", []) is None


def test_every_curated_override_names_a_real_factor():
    for brand_id, source in brand_sources.all_sources().items():
        if source.carbon_factor:
            assert source.carbon_factor in TABLE["factors"], brand_id


def test_brand_sources_reject_wildcards_in_dol_patterns():
    with pytest.raises(ValidationError):
        brand_sources.BrandSource(dol={"name_patterns": ["%AMAZON%"], "naics_prefixes": ["454"]})


# ---------- inflation adjustment ----------
def test_dollars_are_converted_to_the_factor_dollar_year():
    base = TABLE["cpi"]["base_value"]
    assert carbon.to_base_dollars(10_000, "2026-03") == pytest.approx(100 * base / _cpi("2026-03"))


def test_missing_months_use_the_nearest_earlier_month():
    assert "2025-10" not in TABLE["cpi"]["monthly"]  # BLS skipped it (2025 shutdown)
    assert carbon.cpi_for("2025-10") == _cpi("2025-09")


def test_months_outside_the_table_clamp_to_its_ends():
    months = sorted(TABLE["cpi"]["monthly"])
    assert carbon.cpi_for("1999-01") == _cpi(months[0])
    assert carbon.cpi_for("2099-12") == _cpi(months[-1])


# ---------- estimate ----------
def test_estimate_multiplies_real_dollars_by_the_factor():
    coffee = carbon.factor("coffee_shop")
    est = carbon.estimate([("2026-03", 10_000)], coffee)
    expected = carbon.to_base_dollars(10_000, "2026-03") * coffee.kg_per_usd
    assert est.kg_co2e == pytest.approx(expected, abs=0.01)
    assert (est.factor_id, est.includes_use_phase) == ("coffee_shop", False)
    assert est.data_year == TABLE["source"]["emissions_data_year"]


def test_gasoline_includes_burning_the_fuel():
    gas = carbon.factor("gasoline")
    est = carbon.estimate([("2024-06", 10_000)], gas)
    assert est.includes_use_phase is True
    assert est.kg_co2e > 100 * gas.kg_per_usd * 5  # tailpipe dominates production


# ---------- build script (pure transform) ----------
def test_build_rejects_a_workbook_missing_a_needed_code():
    rows = [("311111", "Dog food", 0.5)]
    with pytest.raises(ValueError, match="not in the workbook"):
        build_script.build(rows, {"2024-01": 300.0}, 300.0, build_script.datetime.now(), "0" * 64)


def test_fetch_cpi_parses_months_skips_gaps_and_finds_the_base_year():
    import httpx

    payload = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"data": [
        {"year": "2025", "period": "M10", "value": "-"},
        {"year": "2025", "period": "M09", "value": "324.800"},
        {"year": "2024", "period": "M13", "value": "313.689"},
        {"year": "2024", "period": "M01", "value": "308.417"},
    ]}]}}
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    monthly, base = build_script.fetch_cpi(client, 2024, 2025)
    assert monthly == {"2025-09": 324.8, "2024-01": 308.417}
    assert base == 313.689


def test_fetch_cpi_fails_loudly_without_a_base_year():
    import httpx

    payload = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"data": []}]}}
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    with pytest.raises(RuntimeError, match="annual average"):
        build_script.fetch_cpi(client, 2024, 2025)


# ---------- spending summary ----------
def test_summary_carries_per_merchant_and_total_carbon(db):
    ingest.sync_customer(db, "cust_demo")
    summary = aggregation.spending_summary(db, "cust_demo", UserProfile())
    with_carbon = [m for m in summary.merchants if m.carbon]
    assert with_carbon, "demo brands all have factors"
    starbucks = next(m for m in summary.merchants if m.merchant_id == "starbucks")
    assert starbucks.carbon.factor_id == "coffee_shop"
    assert summary.estimated_kg_co2e == pytest.approx(sum(m.carbon.kg_co2e for m in with_carbon), abs=0.1)


def test_unknown_override_falls_back_to_categories(monkeypatch):
    monkeypatch.setattr(brand_sources, "get", lambda bid: brand_sources.BrandSource(carbon_factor="typo"))
    assert carbon.factor_for("x", ["Coffee"]).id == "coffee_shop"
