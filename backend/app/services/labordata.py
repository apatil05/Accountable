"""Nightly US labor-enforcement databases from the labordata project (github.com/labordata).

Owner: Person A. Each file is a SQLite database rebuilt every night from an official source:
  whisard           DOL Wage and Hour Division concluded cases (DOL publishes quarterly)
  nlrb              NLRB unfair labor practice charges and union elections (nlrb.gov)
  osha_enforcement  OSHA inspections, violations and penalties
They replace the rate-limited DOL API: one download, then local queries with no limits.

Storage: files are scratch space in backend/.cache/labordata/ (gitignored; OSHA alone is over 1 GB)
on whatever machine runs the loader (a laptop, or the nightly GitHub Action). Only matched,
validated facts go to Atlas; the API never reads these files.
"""

import re
import sqlite3
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "labordata"
RELEASE = "https://github.com/labordata/{repo}/releases/download/nightly/{name}.db.zip"
FILES = {
    "whisard": "whd-compliance",
    "nlrb": "nlrb-data",
    "osha_enforcement": "osha-enforcement",
}
FRESH_FOR = timedelta(hours=20)  # files are rebuilt nightly
MAX_ZIP_BYTES = 3 * 1024 ** 3
DOWNLOAD_TIMEOUT_SECONDS = 900
LOOKBACK_YEARS = 2  # the app shows the last two years only; older cases are history, not current practice


class LabordataError(RuntimeError):
    pass


def window_start(now: datetime | None = None, years: int = LOOKBACK_YEARS) -> date:
    today = (now or datetime.now(timezone.utc)).date()
    day = 28 if (today.month == 2 and today.day == 29) else today.day
    return today.replace(year=today.year - years, day=day)


def db_path(name: str) -> Path:
    if name not in FILES:
        raise LabordataError(f"unknown labordata file {name!r}")
    return CACHE_DIR / f"{name}.db"


def _is_fresh(path: Path, now: datetime) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return now - modified < FRESH_FOR


def _download(client: httpx.Client, url: str, dest: Path, name: str) -> None:
    size = 0
    with client.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
        if resp.status_code != 200:
            raise LabordataError(f"{name}: download failed with HTTP {resp.status_code}")
        with dest.open("wb") as out:
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_ZIP_BYTES:
                    raise LabordataError(f"{name}: download larger than {MAX_ZIP_BYTES // 1024 ** 3} GB")
                out.write(chunk)


def ensure(name: str, client: httpx.Client, *, now: datetime | None = None, force: bool = False,
           log: Callable[[str], None] = lambda msg: None) -> Path:
    """Download and unzip `name` unless a copy from the last 20 hours is cached. Returns the .db path."""
    path = db_path(name)
    now = now or datetime.now(timezone.utc)
    if not force and _is_fresh(path, now):
        return path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = CACHE_DIR / f"{name}.db.zip"
    log(f"downloading {name}")
    _download(client, RELEASE.format(repo=FILES[name], name=name), zip_path, name)
    with zipfile.ZipFile(zip_path) as zf:
        member = next((m for m in zf.namelist() if m.endswith(".db")), None)
        if member is None:
            raise LabordataError(f"{name}: archive has no .db file")
        extracted = Path(zf.extract(member, CACHE_DIR))
    if extracted != path:
        extracted.replace(path)
    path.touch()
    zip_path.unlink(missing_ok=True)
    return path


def connect(path: Path) -> sqlite3.Connection:
    """Read-only connection; the loaders never modify the downloaded data."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def like_patterns(name_patterns: list[str]) -> list[str]:
    return [f"%{p.upper()}%" for p in name_patterns]


def whole_word_match(text: str | None, name_patterns: list[str]) -> bool:
    """SQL LIKE '%SHELL%' also hits 'BOMBSHELL SALON'; require a pattern as whole words."""
    upper = (text or "").upper()
    return any(re.search(rf"(?<![A-Z0-9]){re.escape(p.upper())}(?![A-Z0-9])", upper) for p in name_patterns)


def iso_day(value: object, today: date | None = None) -> str | None:
    """'2025-03-01 00:00:00+00:00' -> '2025-03-01'. Junk, 'NaT' and far-future typos (3021) -> None."""
    text = str(value or "")[:10]
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    return text if day.year <= (today or date.today()).year + 1 else None
