"""The data CLIs: coverage report, report-page finder, and the loader entry point."""

from datetime import datetime, timezone

from app.services import ingest
from scripts import data_report, find_report_page, load_data

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


# ---------- coverage report ----------
def test_coverage_shows_what_each_brand_has(db):
    ingest.sync_customer(db, "cust_demo")
    prov = {"source": "company_report", "source_name": "r", "data_year": 2024, "retrieved_at": NOW,
            "method": "pdf_verified", "kind": "reported", "locator": "p. 86"}
    db.company_emissions.insert_one({"_id": "chipotle:company_report:2024", "brand_id": "chipotle",
                                     "data_year": 2024, "status": "reported", "provenance": prov})
    rows = {r["brand"]: r for r in data_report.coverage(db, "cust_demo")}
    assert len(rows) == 10
    assert rows["chipotle"]["reported_emissions"] == "2024 p. 86"
    assert rows["starbucks"]["carbon_factor"] == "coffee_shop (medium)"
    assert rows["shell"]["labor_cases"] == "not loaded"
    assert rows["amazon"]["ethics"] == "mock"
    assert "brand" in data_report.render(list(rows.values())).splitlines()[0]


def test_coverage_marks_not_reported_brands(db):
    ingest.sync_customer(db, "cust_demo")
    prov = {"source": "manual", "source_name": "TJ", "data_year": 2024, "retrieved_at": NOW,
            "method": "manual", "kind": "reported"}
    db.company_emissions.insert_one({"_id": "traderjoes:company_report:2024", "brand_id": "traderjoes",
                                     "data_year": 2024, "status": "not_reported", "provenance": prov})
    row = next(r for r in data_report.coverage(db, "cust_demo") if r["brand"] == "traderjoes")
    assert row["reported_emissions"] == "not reported (2024)"


# ---------- report page finder ----------
def test_candidate_pages_rank_emissions_tables_first():
    pages = [
        "Our mission is to serve great food.",
        "Scope 1 emissions 158,526 Scope 2 45,337 Scope 3 4,865,709",
        "Scope 1 is mentioned once with 1,000 tons",  # only one scope mention: not a table
    ]
    found = find_report_page.candidate_pages(pages)
    assert [page for page, _, _ in found] == [2]
    assert "158,526" in found[0][2]


# ---------- loader entry point ----------
def test_load_data_routes_each_source(monkeypatch):
    calls = []
    monkeypatch.setattr(load_data.company_reports, "load_reports", lambda db, entries, fetch: calls.append("reports"))
    monkeypatch.setattr(load_data.climate_trace, "load", lambda db, client, year: calls.append(("trace", year)))
    monkeypatch.setattr(load_data.labor, "load", lambda db, client, api_key: calls.append("dol"))
    for source in load_data.SOURCES:
        load_data.run_source(None, None, source, 2025)
    assert calls == ["reports", ("trace", 2025), "dol"]


def test_load_data_exits_nonzero_without_a_dol_key(db, monkeypatch, capsys):
    monkeypatch.setattr(load_data, "get_db", lambda: db)
    monkeypatch.setattr(load_data.config, "DOL_API_KEY", "")
    assert load_data.main(["dol"]) == 1
    assert "DOL_API_KEY" in capsys.readouterr().err
