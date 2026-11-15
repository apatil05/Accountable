"""Company-reported emissions (verified against the report PDF) and Climate TRACE asset emissions."""

import hashlib
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app, get_database
from app.services import climate_trace, company_reports, ingest

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
PAGE = (
    "Greenhouse Gas Emissions 2024 2023 Scope 1 and 2 GHG emissions (MTCO2e) Market-based 203,863 "
    "205,895 Direct (scope 1) GHG emissions (MTCO2e) 158,526 145,478 Scope 2 Total (market-based) "
    "45,337 60,417 Scope 3 Total GHG emissions (MTCO2e) 4,865,709 Total Scope 1,2,3 5,069,572"
)
PDF = b"%PDF-fake"
CHIPOTLE = {
    "brand_id": "chipotle", "status": "reported", "data_year": 2024,
    "source_name": "Chipotle 2024 Sustainability Report",
    "report_url": "https://example.com/chipotle-2024.pdf", "page": 86,
    "figures": {"scope1": 158526, "scope2_market": 45337, "scope12_market": 203863,
                "scope3": 4865709, "total": 5069572},
}
TRADER_JOES = {
    "brand_id": "traderjoes", "status": "not_reported", "data_year": 2024,
    "source_name": "Trader Joe's", "reason": "Privately held; publishes no emissions inventory",
}


def _load(db, entries, page_text=PAGE, fetch=None):
    return company_reports.load_reports(
        db, [company_reports.ReportEntry(**e) for e in entries],
        fetch=fetch or (lambda url: PDF), extract_page=lambda pdf, page: page_text, now=lambda: NOW,
    )


# ---------- number matching ----------
@pytest.mark.parametrize("value, text, found", [
    (158526, "emissions 158,526 tCO2e", True),
    (158526, "emissions 158526 tCO2e", True),
    (158526, "emissions 1,158,526", False),  # must be the whole number, not a substring
    (45.5, "about 45.5 million", True),
    (45337, "45,33 7", False),
])
def test_figures_must_appear_as_whole_numbers(value, text, found):
    assert company_reports.appears(value, text) is found


# ---------- report loader ----------
def test_verified_report_is_written_with_provenance(db):
    report = _load(db, [CHIPOTLE])
    assert (report.written, report.rejected) == (1, 0)
    doc = db.company_emissions.find_one({"_id": "chipotle:company_report:2024"})
    assert doc["figures"]["total"] == 5069572
    prov = doc["provenance"]
    assert (prov["locator"], prov["method"], prov["kind"]) == ("p. 86", "pdf_verified", "reported")
    assert prov["sha256"] == hashlib.sha256(PDF).hexdigest()


def test_a_figure_missing_from_the_page_rejects_the_entry(db):
    typo = {**CHIPOTLE, "figures": {**CHIPOTLE["figures"], "scope1": 158527, "scope12_market": 203864,
                                     "total": 5069573}}
    report = _load(db, [typo])
    assert (report.written, report.rejected) == (0, 1)
    assert "158527" in report.errors[0]
    assert db.company_emissions.count_documents({}) == 0


def test_download_failures_are_rejections_not_crashes(db):
    def broken(url):
        raise httpx.ConnectTimeout("timed out")

    report = _load(db, [CHIPOTLE, TRADER_JOES], fetch=broken)
    assert (report.written, report.rejected) == (1, 1)  # trader joe's needs no download
    assert "ConnectTimeout" in report.errors[0]


def test_figures_in_million_tonnes_are_verified_as_printed_and_stored_in_tonnes(db):
    amazon = {"brand_id": "amazon", "status": "reported", "data_year": 2025, "source_name": "Amazon 2025",
              "report_url": "https://example.com/amazon.pdf", "page": 5, "reported_unit": "MtCO2e",
              "figures": {"scope1": 15.37, "scope2_market": 3.74, "scope3": 61.74, "total": 80.85}}
    page = "Total Emissions (MMT CO2e) 69.55 80.85 Direct Emissions 15.13 15.37 Electricity 2.80 3.74 Other 61.74"
    report = _load(db, [amazon], page_text=page)
    assert (report.written, report.rejected) == (1, 0)
    figures = db.company_emissions.find_one({"_id": "amazon:company_report:2025"})["figures"]
    assert (figures["scope1"], figures["total"]) == (15_370_000, 80_850_000)


def test_not_reported_is_stored_as_an_explicit_fact(db):
    _load(db, [TRADER_JOES])
    doc = db.company_emissions.find_one({"_id": "traderjoes:company_report:2024"})
    assert (doc["status"], doc["figures"]) == ("not_reported", None)
    assert doc["provenance"]["method"] == "manual"


def test_curated_entries_file_is_valid():
    entries = company_reports.curated_entries()
    assert entries and all(e.report_url or e.status == "not_reported" for e in entries)


def test_entries_need_a_page_and_url_when_reported():
    with pytest.raises(ValueError):
        company_reports.ReportEntry(**{**CHIPOTLE, "page": None})


def test_downloads_over_the_size_cap_are_refused():
    big = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 50))
    with httpx.Client(transport=big) as client:
        with pytest.raises(company_reports.ReportTooLarge):
            company_reports.download(client, "https://example.com/r.pdf", max_bytes=10)


# ---------- Climate TRACE ----------
def _trace_client(status=200, quantity=76919761.8):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["ownerIds"] == "E100000000746"
        if status != 200:
            return httpx.Response(status, json={"detail": "nope"})
        return httpx.Response(200, json={"totals": {"summaries": [{"gas": "co2e_100yr", "emissionsQuantity": quantity}]}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_climate_trace_owner_totals_are_loaded_as_estimates(db):
    with _trace_client() as client:
        report = climate_trace.load(db, client, year=2025, now=lambda: NOW)
    assert (report.written, report.rejected) == (1, 0)
    doc = db.company_emissions.find_one({"_id": "shell:climate_trace:2025"})
    assert doc["figures"]["owned_assets"] == pytest.approx(76919761.8)
    assert (doc["provenance"]["kind"], doc["provenance"]["source"]) == ("estimated", "climate_trace")


def test_climate_trace_errors_are_logged_rejections(db):
    with _trace_client(status=500) as client:
        report = climate_trace.load(db, client, year=2025, now=lambda: NOW)
    assert (report.written, report.rejected) == (0, 1)
    assert "shell" in report.errors[0]


# ---------- API ----------
def test_emissions_endpoint_lists_newest_first(db):
    ingest.seed_brands(db)
    _load(db, [CHIPOTLE, {**CHIPOTLE, "data_year": 2023, "source_name": "Chipotle 2023 report"}])
    app.dependency_overrides[get_database] = lambda: db
    try:
        rows = TestClient(app).get("/api/merchants/chipotle/emissions").json()
        assert [r["data_year"] for r in rows] == [2024, 2023]
        assert TestClient(app).get("/api/merchants/starbucks/emissions").json() == []
    finally:
        app.dependency_overrides.clear()


# ---------- review fixes ----------
def test_year_like_figures_never_verify(db):
    sneaky = {**CHIPOTLE, "figures": {"scope1": 2024}}
    report = _load(db, [sneaky])
    assert report.rejected == 1 and "scope1=2024" in report.errors[0]


def test_pages_without_a_scope_label_are_rejected(db):
    report = _load(db, [CHIPOTLE], page_text="Revenue 158,526 45,337 203,863 4,865,709 5,069,572")
    assert report.rejected == 1 and "Scope" in report.errors[0]


def test_entries_removed_from_the_file_are_removed_from_the_database(db):
    _load(db, [CHIPOTLE, TRADER_JOES])
    _load(db, [CHIPOTLE])
    assert db.company_emissions.find_one({"_id": "traderjoes:company_report:2024"}) is None


def test_failed_downloads_keep_previous_data(db):
    _load(db, [CHIPOTLE])

    def broken(url):
        raise httpx.ConnectTimeout("down")

    _load(db, [CHIPOTLE], fetch=broken)
    assert db.company_emissions.find_one({"_id": "chipotle:company_report:2024"}) is not None


def test_shared_pdfs_are_downloaded_once(db):
    calls = []
    other_year = {**CHIPOTLE, "data_year": 2023}
    _load(db, [CHIPOTLE, other_year], fetch=lambda url: calls.append(url) or PDF)
    assert len(calls) == 1


def test_climate_trace_zero_is_not_published(db):
    with _trace_client(quantity=0) as client:
        report = climate_trace.load(db, client, year=2025, now=lambda: NOW)
    assert (report.written, report.rejected) == (0, 1)


def test_emissions_endpoint_skips_hand_broken_documents(db):
    ingest.seed_brands(db)
    _load(db, [CHIPOTLE])
    db.company_emissions.insert_one({"_id": "chipotle:manual:1999", "brand_id": "chipotle", "data_year": 1999})
    app.dependency_overrides[get_database] = lambda: db
    try:
        rows = TestClient(app).get("/api/merchants/chipotle/emissions").json()
        assert [r["data_year"] for r in rows] == [2024]
    finally:
        app.dependency_overrides.clear()
