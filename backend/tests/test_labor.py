"""DOL Wage and Hour enforcement cases: fetch, match to brands, store, and summarize."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app, get_database
from app.services import brand_sources, ingest, labor

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
KEY = "secret-dol-key"


def _row(case_id, trade="CHIPOTLE MEXICAN GRILL", naics="722513", bw=1234.5, cmp=500.0, cl=None, **extra):
    row = {"case_id": case_id, "trade_nm": trade, "legal_name": "CHIPOTLE SERVICES LLC", "st_cd": "MA",
           "naic_cd": naics, "findings_start_date": "2021-01-01", "findings_end_date": "2022-12-31 00:00:00",
           "case_violtn_cnt": 3, "ee_violtd_cnt": 12, "bw_atp_amt": bw, "flsa_cmp_assd_amt": cmp, "cmp_assd_cnt": 7, **extra}
    if cl is not None:
        row["flsa_cl_violtn_cnt"] = cl
    return row


class FakeDol:
    """Serves rows for one brand pattern; everything else gets 204 (DOL's 'no matches')."""

    def __init__(self, rows_by_term: dict[str, list[dict]], status: int = 200, page_size: int = 2):
        self.rows_by_term, self.status, self.page_size, self.requests = rows_by_term, status, page_size, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        flt = json.dumps(json.loads(request.url.params["filter_object"]))
        rows = next((r for term, r in self.rows_by_term.items() if term in flt), [])
        offset = int(request.url.params.get("offset", 0))
        page = rows[offset: offset + int(request.url.params["limit"])]
        return httpx.Response(200, json={"data": page}) if page else httpx.Response(204)


def _load(db, fake, monkeypatch, page_size=2):
    monkeypatch.setattr(labor, "PAGE_SIZE", page_size)
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        return labor.load(db, client, api_key=KEY, now=lambda: NOW)


def test_requests_carry_the_key_and_a_name_filter(db, monkeypatch):
    fake = FakeDol({})
    _load(db, fake, monkeypatch)
    first = fake.requests[0]
    assert first.url.params["X-API-KEY"] == KEY
    flt = json.loads(first.url.params["filter_object"])
    assert {c["operator"] for c in flt["or"]} == {"like"}
    assert all(c["value"].startswith("%") and c["value"].endswith("%") for c in flt["or"])


def test_cases_are_matched_paginated_and_filtered_by_industry(db, monkeypatch):
    rows = [_row(1), _row(2), _row(3, cl=2), _row(4, trade="CHIPOTLE PEPPER FARM", naics="111219")]
    _load(db, FakeDol({"CHIPOTLE": rows}), monkeypatch)
    ids = {d["_id"] for d in db.labor_cases.find({"brand_id": "chipotle"})}
    assert ids == {"chipotle:1", "chipotle:2", "chipotle:3"}  # case 4 is a farm, not the restaurant chain
    doc = db.labor_cases.find_one({"_id": "chipotle:1"})
    assert (doc["back_wages_cents"], doc["penalties_cents"], doc["state"]) == (123450, 50000, "MA")
    assert doc["findings_end"].year == 2022
    assert doc["provenance"]["kind"] == "enforcement_record"


def test_summary_totals_cases(db, monkeypatch):
    ingest.seed_brands(db)
    _load(db, FakeDol({"CHIPOTLE": [_row(1), _row(2, bw=100.0, cl=1)]}), monkeypatch)
    s = labor.summarize(db, "chipotle")
    assert (s.loaded, s.case_count, s.violations, s.employees_affected) == (True, 2, 6, 24)
    assert (s.back_wages, s.penalties, s.child_labor_cases) == (1334.5, 1000.0, 1)
    assert (s.earliest_finding, s.latest_finding) == ("2021-01-01", "2022-12-31")


def test_a_loaded_brand_with_no_cases_differs_from_an_unloaded_one(db, monkeypatch):
    assert labor.summarize(db, "starbucks").loaded is False
    _load(db, FakeDol({}), monkeypatch)
    s = labor.summarize(db, "starbucks")
    assert (s.loaded, s.case_count) == (True, 0)


def test_fetch_errors_never_leak_the_key(db, monkeypatch):
    report = _load(db, FakeDol({}, status=500), monkeypatch)
    assert report.rejected == len([b for b in brand_sources.all_sources().values() if b.dol])
    assert all(KEY not in e for e in report.errors)
    run = db.pipeline_runs.find_one({"source": labor.SOURCE})
    assert KEY not in json.dumps(run, default=str)
    assert labor.summarize(db, "chipotle").loaded is False


def test_rate_limits_are_retried_with_backoff():
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(429),
                      httpx.Response(200, json={"data": [_row(1)]})])
    sleeps = []
    match = brand_sources.get("chipotle").dol
    with httpx.Client(transport=httpx.MockTransport(lambda r: next(responses))) as client:
        rows = labor.fetch_rows(client, KEY, match, sleep=sleeps.append)
    assert len(rows) == 1
    assert sleeps == [7.0, labor.RETRY_BASE_SECONDS * 2]


def test_persistent_rate_limit_becomes_a_safe_error():
    match = brand_sources.get("chipotle").dol
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as client:
        with pytest.raises(labor.DolError, match="HTTP 429"):
            labor.fetch_rows(client, KEY, match, sleep=lambda s: None)


def test_reload_drops_cases_that_no_longer_match(db, monkeypatch):
    _load(db, FakeDol({"CHIPOTLE": [_row(1), _row(2)]}), monkeypatch)
    _load(db, FakeDol({"CHIPOTLE": [_row(2)]}), monkeypatch)
    assert {d["_id"] for d in db.labor_cases.find()} == {"chipotle:2"}


def test_an_empty_reload_keeps_existing_cases(db, monkeypatch):
    _load(db, FakeDol({"CHIPOTLE": [_row(1)]}), monkeypatch)
    _load(db, FakeDol({}), monkeypatch)  # e.g. an outage that answers 204
    assert db.labor_cases.count_documents({"brand_id": "chipotle"}) == 1


def test_a_case_matching_two_brands_is_kept_for_both(db, monkeypatch):
    shared = _row(9, trade="WALMART TARGET OUTLET", naics="452112")
    _load(db, FakeDol({"WAL-MART": [shared], "TARGET STORE": [shared]}), monkeypatch)
    assert {d["_id"] for d in db.labor_cases.find()} == {"walmart:9", "target:9"}


def test_hitting_the_page_cap_is_an_error_not_a_silent_cutoff(monkeypatch):
    monkeypatch.setattr(labor, "MAX_PAGES", 2)
    monkeypatch.setattr(labor, "PAGE_SIZE", 1)
    fake = FakeDol({"CHIPOTLE": [_row(1), _row(2), _row(3)]})
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        with pytest.raises(labor.DolError, match="narrow"):
            labor.fetch_rows(client, KEY, brand_sources.get("chipotle").dol)


def test_non_json_responses_are_safe_errors():
    html = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>gateway</html>"))
    with httpx.Client(transport=html) as client:
        with pytest.raises(labor.DolError, match="not JSON"):
            labor.fetch_rows(client, KEY, brand_sources.get("chipotle").dol)


def test_missing_key_is_refused():
    with pytest.raises(labor.DolError):
        labor.load(None, None, api_key="")  # type: ignore[arg-type]


def test_labor_endpoint(db, monkeypatch):
    ingest.seed_brands(db)
    _load(db, FakeDol({"CHIPOTLE": [_row(1)]}), monkeypatch)
    app.dependency_overrides[get_database] = lambda: db
    try:
        body = TestClient(app).get("/api/merchants/chipotle/labor").json()
        assert (body["case_count"], body["back_wages"]) == (1, 1234.5)
        assert TestClient(app).get("/api/merchants/nope/labor").status_code == 404
    finally:
        app.dependency_overrides.clear()
