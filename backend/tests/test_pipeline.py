"""Shared loader step: validate every record, write only the valid ones, log the run."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import CompanyEmissions, EmissionsFigures, Provenance
from app.services import pipeline

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def _prov(**overrides) -> dict:
    base = {
        "source": "company_report",
        "source_name": "Chipotle 2024 Sustainability Report",
        "source_url": "https://example.com/report.pdf",
        "locator": "p. 86",
        "data_year": 2024,
        "retrieved_at": NOW,
        "method": "pdf_verified",
        "kind": "reported",
    }
    return {**base, **overrides}


def _emissions(**overrides) -> dict:
    base = {
        "brand_id": "chipotle",
        "data_year": 2024,
        "status": "reported",
        "figures": {"scope1": 158526, "scope2_market": 45337, "scope12_market": 203863},
        "provenance": _prov(),
    }
    return {**base, **overrides}


# ---------- models ----------
def test_provenance_rejects_non_https_urls():
    with pytest.raises(ValidationError):
        Provenance(**_prov(source_url="http://example.com/x.pdf"))


def test_provenance_rejects_implausible_years():
    with pytest.raises(ValidationError):
        Provenance(**_prov(data_year=1890))


def test_figures_reject_negative_values():
    with pytest.raises(ValidationError):
        EmissionsFigures(scope1=-1)


def test_scope12_must_match_its_parts():
    with pytest.raises(ValidationError, match="scope 1 \\+ scope 2"):
        CompanyEmissions(**_emissions(figures={"scope1": 100, "scope2_market": 50, "scope12_market": 300}))


def test_total_must_match_scope12_plus_scope3():
    with pytest.raises(ValidationError, match="total"):
        CompanyEmissions(**_emissions(figures={"scope12_market": 200, "scope3": 800, "total": 5000}))


def test_reported_needs_figures_and_not_reported_needs_a_reason():
    with pytest.raises(ValidationError):
        CompanyEmissions(**_emissions(figures=None))
    with pytest.raises(ValidationError):
        CompanyEmissions(**_emissions(status="not_reported", figures=None, reason=None))
    ok = CompanyEmissions(**_emissions(status="not_reported", figures=None, reason="private company"))
    assert ok.figures is None


# ---------- load ----------
def _load(db, records):
    return pipeline.load(
        db, source="test", collection="company_emissions", records=records,
        model=CompanyEmissions, doc_id=lambda m: f"{m.brand_id}:{m.data_year}", now=lambda: NOW,
    )


def test_valid_records_are_written_and_invalid_ones_rejected(db):
    bad = _emissions(brand_id="nike", figures={"scope1": -5})
    report = _load(db, [_emissions(), bad])
    assert (report.written, report.rejected) == (1, 1)
    assert "nike" in report.errors[0]
    doc = db.company_emissions.find_one({"_id": "chipotle:2024"})
    assert doc["figures"]["scope1"] == 158526
    assert doc["provenance"]["retrieved_at"].year == 2026  # stored as a real date, not a string


def test_loading_twice_is_idempotent(db):
    _load(db, [_emissions()])
    _load(db, [_emissions()])
    assert db.company_emissions.count_documents({}) == 1


def test_duplicate_ids_in_one_batch_are_rejected(db):
    report = _load(db, [_emissions(), _emissions()])
    assert (report.written, report.rejected) == (1, 1)
    assert "duplicate" in report.errors[0]


def test_every_run_is_logged(db):
    _load(db, [_emissions(), _emissions(brand_id="x", figures={"scope1": -1})])
    run = db.pipeline_runs.find_one({"source": "test"})
    assert (run["written"], run["rejected"], run["ok"]) == (1, 1, False)
    assert run["collection"] == "company_emissions"


def test_a_clean_run_is_ok(db):
    _load(db, [_emissions()])
    assert db.pipeline_runs.find_one({"source": "test"})["ok"] is True
