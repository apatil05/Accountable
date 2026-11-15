"""Nightly labor databases: download/cache, matching, the two-year window, and the summaries."""

import io
import sqlite3
import zipfile
from datetime import date, datetime, timezone

import httpx
import pytest

from app.services import brand_sources, labor, labordata, nlrb, osha
from scripts import load_data

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
SINCE = date(2024, 11, 14)


# ---------- helpers ----------
def test_window_is_two_years_back():
    assert labordata.window_start(NOW) == SINCE
    assert labordata.window_start(datetime(2028, 2, 29, tzinfo=timezone.utc)) == date(2026, 2, 28)


@pytest.mark.parametrize("text, found", [
    ("SHELL GAS STATION", True), ("GPM SOUTHEAST LLC DBA SHELL", True),
    ("BOMBSHELL SALON", False), ("SHELLFISH SHACK", False), (None, False),
])
def test_whole_word_matching(text, found):
    assert labordata.whole_word_match(text, ["SHELL"]) is found


def test_iso_day_drops_junk_and_typo_years():
    assert labordata.iso_day("2025-03-01 00:00:00+00:00") == "2025-03-01"
    assert labordata.iso_day("NaT") is None
    assert labordata.iso_day("3021-05-01", today=date(2026, 11, 14)) is None


def _zip_with_db() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("whisard.db", b"SQLite placeholder")
    return buf.getvalue()


def test_ensure_downloads_once_then_uses_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(labordata, "CACHE_DIR", tmp_path)
    calls = []
    transport = httpx.MockTransport(lambda r: calls.append(r.url) or httpx.Response(200, content=_zip_with_db()))
    with httpx.Client(transport=transport) as client:
        path = labordata.ensure("whisard", client)
        labordata.ensure("whisard", client)
    assert path.read_bytes() == b"SQLite placeholder"
    assert len(calls) == 1 and "whd-compliance" in str(calls[0])
    assert not (tmp_path / "whisard.db.zip").exists()  # zip removed after extraction


def test_ensure_refuses_failed_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(labordata, "CACHE_DIR", tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as client:
        with pytest.raises(labordata.LabordataError, match="404"):
            labordata.ensure("nlrb", client)


# ---------- tiny SQLite copies of the real schemas ----------
@pytest.fixture
def osha_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE inspection (activity_nr INTEGER, estab_name TEXT, site_city TEXT, site_state TEXT,
            naics_code TEXT, insp_type TEXT, open_date TEXT, close_case_date TEXT);
        CREATE TABLE violation (activity_nr INTEGER, viol_type TEXT, initial_penalty REAL,
            current_penalty REAL, delete_flag TEXT);
        INSERT INTO inspection VALUES
          (1, 'CHIPOTLE MEXICAN GRILL', 'Austin', 'TX', '722513', 'A', '2025-06-01', '2025-09-01'),
          (2, 'CHIPOTLE MEXICAN GRILL', 'Boston', 'MA', '722513', 'B', '2020-01-01', NULL),
          (3, 'CHIPOTLE PEPPER FARM', 'Fresno', 'CA', '111219', 'H', '2025-07-01', NULL);
        INSERT INTO violation VALUES
          (1, 'S', 5000, 3000, NULL), (1, 'S', 1000, 1000, NULL), (1, 'W', 20000, 15000, NULL),
          (1, 'O', 0, 0, 'X');
    """)
    return conn


@pytest.fixture
def nlrb_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE filing (case_number TEXT, name TEXT, case_type TEXT, url TEXT, city TEXT, state TEXT,
            date_filed TEXT, status TEXT, date_closed TEXT, reason_closed TEXT, certified_representative TEXT);
        CREATE TABLE allegation (case_number TEXT, allegation TEXT);
        INSERT INTO filing VALUES
          ('01-CA-1', 'Starbucks Corporation', 'CA', 'https://www.nlrb.gov/case/01-CA-1', 'Boston', 'MA',
           '2025-02-01', 'Open', NULL, NULL, NULL),
          ('01-CA-2', 'Starbucks Corporation', 'CA', NULL, 'Boston', 'MA', '2025-03-01', 'Closed',
           '2025-08-01', 'Informal Settlement', NULL),
          ('01-CA-3', 'Starbucks Corp', 'CA', NULL, NULL, NULL, '2025-04-01', 'Closed', '2025-05-01',
           'Dismissal Non-adjusted', NULL),
          ('01-RC-4', 'Starbucks Corporation', 'RC', NULL, NULL, NULL, '2025-05-01', 'Closed', '2025-07-01',
           'Certific. of Representative', NULL),
          ('01-RC-7', 'Starbucks Corporation', 'RC', NULL, NULL, NULL, '2025-06-01', 'Closed', '2025-08-01',
           'Certification of Results', NULL),
          ('01-CB-5', 'Starbucks Corporation', 'CB', NULL, NULL, NULL, '2025-05-01', 'Open', NULL, NULL, NULL),
          ('01-CA-6', 'Starbucks Corporation', 'CA', NULL, NULL, NULL, '2019-05-01', 'Open', NULL, NULL, NULL);
        INSERT INTO allegation VALUES ('01-CA-1', '8(a)(3) Discharge'), ('01-CA-2', '8(a)(3) Discharge'),
          ('01-CA-2', '8(a)(1) Coercive Statements');
    """)
    return conn


@pytest.fixture
def whisard_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE cases (case_id INTEGER, trade_nm TEXT, legal_name TEXT, st_cd TEXT, naic_cd TEXT,
            findings_start_date TEXT, findings_end_date TEXT, case_violtn_cnt INTEGER, ee_violtd_cnt INTEGER,
            bw_atp_amt REAL, cmp_assd REAL);
        CREATE TABLE violations (case_id INTEGER, type TEXT, violations INTEGER);
        INSERT INTO cases VALUES
          (10, 'Chipotle Mexican Grill', 'CMG LLC', 'TX', '722513', '2025-01-01', '2025-06-30 00:00:00+00:00', 4, 9, 1500.5, 750),
          (11, 'Chipotle', 'CMG LLC', 'TX', '722513', '2018-01-01', '2018-06-30', 1, 1, 10, 0),
          (12, 'Chipotle', 'CMG LLC', 'TX', '722513', '2025-01-01', '3021-05-01', 1, 1, 10, 0);
        INSERT INTO violations VALUES (10, 'flsa_cl', 2), (10, 'flsa', 2);
    """)
    return conn


# ---------- wages from the nightly file ----------
def test_whisard_rows_apply_window_names_and_child_labor(whisard_conn):
    rows = labor.whisard_rows(whisard_conn, brand_sources.get("chipotle").dol, SINCE)
    assert [r["case_id"] for r in rows] == [10]  # 2018 is outside the window, 3021 is a typo
    assert (rows[0]["flsa_cmp_assd_amt"], rows[0][labor.CHILD_LABOR_FIELD]) == (750, 2)


def test_wages_load_from_the_file_without_a_key(db, whisard_conn):
    report = labor.load(db, None, api_key="", only=["chipotle"], now=lambda: NOW,
                        rows_for=lambda m: labor.whisard_rows(whisard_conn, m, SINCE))
    assert report.written == 1
    doc = db.labor_cases.find_one({"_id": "chipotle:10"})
    assert (doc["penalties_cents"], doc["child_labor_violations"]) == (75000, 2)
    assert doc["provenance"]["method"] == "download"


# ---------- OSHA ----------
def test_osha_matches_by_name_industry_and_window(osha_conn):
    rows = osha.inspections_for(osha_conn, brand_sources.get("chipotle").dol, SINCE)
    assert [r["activity_nr"] for r in rows] == [1]  # 2 is too old, 3 is a farm


def test_osha_totals_skip_deleted_violations(osha_conn):
    t = osha.violation_totals(osha_conn, ["1"])["1"]
    assert (t["serious"], t["willful"], t["other"], t["current_cents"]) == (2, 1, 0, 1_900_000)


def test_osha_load_and_summary(db, osha_conn):
    report = osha.load(db, osha_conn, since=SINCE, only=["chipotle"], now=lambda: NOW)
    assert (report.written, report.rejected) == (1, 0)
    s = osha.summarize(db, "chipotle", SINCE)
    assert (s.loaded, s.inspections, s.after_accident, s.serious, s.willful, s.penalties) == (True, 1, 1, 2, 1, 19000.0)
    assert osha.summarize(db, "nike", SINCE).loaded is False


# ---------- NLRB ----------
@pytest.mark.parametrize("status, reason, expected", [
    ("Open", None, "open"), ("Closed", "Informal Settlement", "remedied"),
    ("Closed", "Withdrawal Adjusted", "remedied"), ("Closed", "Dismissal Non-adjusted", "dismissed_or_withdrawn"),
    ("Closed", "Withdrawal Non-adjusted", "dismissed_or_withdrawn"), ("Closed", "Unit Clarification", "other"),
])
def test_nlrb_outcomes(status, reason, expected):
    assert nlrb.outcome(status, reason) == expected


def test_nlrb_load_and_summary(db, nlrb_conn):
    report = nlrb.load(db, nlrb_conn, since=SINCE, only=["starbucks"], now=lambda: NOW)
    assert (report.written, report.rejected) == (5, 0)  # CB (against a union) and the 2019 case excluded
    s = nlrb.summarize(db, "starbucks", SINCE)
    assert (s.charges, s.charges_open, s.charges_remedied, s.charges_dismissed) == (3, 1, 1, 1)
    assert (s.petitions, s.unions_certified, s.elections_union_lost) == (2, 1, 1)
    assert s.top_allegations[0] == "8(a)(3) Discharge"


def test_labor_summary_bundles_all_three_for_the_window(db, osha_conn, whisard_conn):
    labor.load(db, None, api_key="", only=["chipotle"], now=lambda: NOW,
               rows_for=lambda m: labor.whisard_rows(whisard_conn, m, SINCE))
    osha.load(db, osha_conn, since=SINCE, only=["chipotle"], now=lambda: NOW)
    s = labor.summarize(db, "chipotle", since=SINCE)
    assert s.window_start == "2024-11-14"
    assert (s.case_count, s.back_wages, s.safety.inspections, s.organizing.loaded) == (1, 1500.5, 1, False)


# ---------- loader script ----------
def test_load_data_deletes_the_cache_unless_asked_to_keep_it(db, tmp_path, monkeypatch):
    monkeypatch.setattr(labordata, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(load_data, "get_db", lambda: db)

    def fake_run(*args, **kwargs):
        labordata.CACHE_DIR.mkdir(exist_ok=True)
        return load_data.pipeline.LoadReport("nlrb", "nlrb_cases", 0, 0, ())

    monkeypatch.setattr(load_data, "run_labor", fake_run)
    assert load_data.main(["nlrb", "--keep-cache"]) == 0
    assert labordata.CACHE_DIR.exists()
    assert load_data.main(["nlrb"]) == 0
    assert not labordata.CACHE_DIR.exists()
