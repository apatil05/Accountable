"""Company-reported emissions, entered by hand from each company's own report and verified by machine.

Owner: Person A. data/company_reports.json lists, per brand, the report URL, the page with the
emissions table, and the figures. load_reports() downloads each PDF, extracts that page's text,
and only accepts the entry if every figure appears on the page as a whole number. Typos and
wrong pages are caught; nothing is published that the source does not literally say.

Entries can later be drafted by an LLM (Person B) in the same format; the verification step
stays the same, which is what makes LLM-drafted numbers safe to show.
"""

import hashlib
import io
import json
import re
from datetime import datetime
from functools import lru_cache
from typing import Callable, Iterable, Literal, Optional

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo.database import Database

from .. import config
from ..db import repos
from ..models import MAX_DATA_YEAR, MIN_DATA_YEAR, CompanyEmissions, EmissionsFigures
from . import pipeline

SOURCE = "company_report"
MAX_PDF_BYTES = 200 * 1024 * 1024  # Chipotle's 2024 report alone is 73 MB
DOWNLOAD_TIMEOUT_SECONDS = 120
USER_AGENT = "Mozilla/5.0 (compatible; AccountableResearch/0.1)"
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
TONNES_PER_UNIT = {"tCO2e": 1, "ktCO2e": 1_000, "MtCO2e": 1_000_000}
_SCOPE_LABEL = re.compile(r"(?i)scope\s*[123]|direct emissions|total emissions")
TONNE_DECIMALS = 3


class ReportTooLarge(RuntimeError):
    pass


class ReportEntry(BaseModel):
    brand_id: str = Field(min_length=1, max_length=100)
    status: Literal["reported", "not_reported"]
    data_year: int = Field(ge=MIN_DATA_YEAR, le=MAX_DATA_YEAR)
    source_name: str = Field(min_length=1, max_length=300)
    report_url: Optional[str] = None
    page: Optional[int] = Field(None, ge=1)  # 1-based, as printed in the PDF viewer
    # Figures are entered exactly as printed (that is what gets verified), then stored in tonnes.
    reported_unit: Literal["tCO2e", "ktCO2e", "MtCO2e"] = "tCO2e"
    figures: Optional[EmissionsFigures] = None
    reason: Optional[str] = None

    @field_validator("report_url")
    @classmethod
    def _https(cls, url: Optional[str]) -> Optional[str]:
        if url is not None and not url.startswith("https://"):
            raise ValueError("report_url must be https://")
        return url

    @model_validator(mode="after")
    def _complete(self) -> "ReportEntry":
        if self.status == "reported" and not (self.report_url and self.page and self.figures):
            raise ValueError("a reported entry needs report_url, page and figures")
        return self


@lru_cache
def curated_entries() -> tuple[ReportEntry, ...]:
    raw = json.loads((config.DATA_DIR / "company_reports.json").read_text())
    return tuple(ReportEntry(**e) for e in raw)


def download(client: httpx.Client, url: str, max_bytes: int = MAX_PDF_BYTES) -> bytes:
    """Stream the file, refusing anything larger than max_bytes or redirected off https."""
    chunks, size = [], 0
    with client.stream("GET", url, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                       timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
        resp.raise_for_status()
        if resp.url.scheme != "https":
            raise ValueError("report redirected to a non-https URL")
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise ReportTooLarge(f"larger than {max_bytes // (1024 * 1024)} MB")
            chunks.append(chunk)
    return b"".join(chunks)


def extract_page_text(pdf: bytes, page: int) -> str:
    from pypdf import PdfReader  # dev-only dependency; loaders run from a laptop, not the API

    reader = PdfReader(io.BytesIO(pdf))
    if page > len(reader.pages):
        raise ValueError(f"the PDF has only {len(reader.pages)} pages")
    return reader.pages[page - 1].extract_text() or ""


def _canonical(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def appears(value: float, text: str) -> bool:
    """True if `value` occurs in `text` as a whole number (commas allowed, no partial matches)."""
    wanted = _canonical(value)
    return any(_canonical(float(tok.replace(",", ""))) == wanted for tok in _NUMBER.findall(text))


def _looks_like_a_year(value: float) -> bool:
    return float(value).is_integer() and MIN_DATA_YEAR <= value <= MAX_DATA_YEAR


def missing_figures(figures: EmissionsFigures, text: str) -> list[str]:
    """Figures that cannot be confirmed on the page. A page with no "Scope" label is not an emissions
    table, and a figure that looks like a year (2024) would match the column headers, so both fail."""
    if not _SCOPE_LABEL.search(text):
        return ["page has no 'Scope' label"]
    return [f"{name}={_canonical(v)}" for name, v in figures.model_dump().items()
            if v is not None and (_looks_like_a_year(v) or not appears(v, text))]


def _provenance(entry: ReportEntry, retrieved_at: datetime, sha256: Optional[str]) -> dict:
    verified = entry.status == "reported"
    return {
        "source": SOURCE if verified else "manual",
        "source_name": entry.source_name,
        "source_url": entry.report_url,
        "locator": f"p. {entry.page}" if entry.page else None,
        "data_year": entry.data_year,
        "retrieved_at": retrieved_at,
        "method": "pdf_verified" if verified else "manual",
        "kind": "reported",
        "sha256": sha256,
    }


def _in_tonnes(figures: EmissionsFigures, unit: str) -> dict:
    """Exact tonnes: integral values stay ints; fractional tonnes are kept, not rounded away."""
    scale = TONNES_PER_UNIT[unit]

    def convert(v: float | None) -> float | int | None:
        if v is None:
            return None
        tonnes = round(v * scale, TONNE_DECIMALS)
        return int(tonnes) if float(tonnes).is_integer() else tonnes

    return {k: convert(v) for k, v in figures.model_dump().items()}


def _record(entry: ReportEntry, retrieved_at: datetime, sha256: Optional[str]) -> dict:
    return {
        "brand_id": entry.brand_id,
        "data_year": entry.data_year,
        "status": entry.status,
        "reason": entry.reason,
        "figures": _in_tonnes(entry.figures, entry.reported_unit) if entry.figures else None,
        "provenance": _provenance(entry, retrieved_at, sha256),
    }


def _verified_record(entry: ReportEntry, fetch: Callable[[str], bytes],
                     extract_page: Callable[[bytes, int], str], retrieved_at: datetime) -> dict:
    """The entry as a record, or raises with a message naming what failed."""
    if entry.status == "not_reported":
        return _record(entry, retrieved_at, None)
    pdf = fetch(entry.report_url)  # type: ignore[arg-type]
    missing = missing_figures(entry.figures, extract_page(pdf, entry.page))  # type: ignore[arg-type]
    if missing:
        raise ValueError(f"not found on p. {entry.page}: {', '.join(missing)}")
    return _record(entry, retrieved_at, hashlib.sha256(pdf).hexdigest())


def load_reports(db: Database, entries: Iterable[ReportEntry], *, fetch: Callable[[str], bytes],
                 extract_page: Callable[[bytes, int], str] = extract_page_text,
                 now: Callable[[], datetime] = repos.utcnow) -> pipeline.LoadReport:
    entries = list(entries)
    records, errors = [], []
    retrieved_at = now()
    fetch_once = lru_cache(maxsize=None)(fetch)  # several entries can share one large PDF
    for entry in entries:
        try:
            records.append(_verified_record(entry, fetch_once, extract_page, retrieved_at))
        except Exception as exc:  # noqa: BLE001 - one bad report must not stop the others
            errors.append(f"{entry.brand_id} {entry.data_year}: {type(exc).__name__}: {exc}")
    report = pipeline.load(
        db, source=SOURCE, collection=repos.COMPANY_EMISSIONS, records=records, model=CompanyEmissions,
        doc_id=lambda m: _doc_id(m.brand_id, m.data_year), prior_errors=errors, now=now,
    )
    # Entries removed from (or re-dated in) the JSON file disappear; failed ones keep their old data.
    repos.delete_company_reports_except(db, [_doc_id(e.brand_id, e.data_year) for e in entries])
    return report


def _doc_id(brand_id: str, data_year: int) -> str:
    return f"{brand_id}:{SOURCE}:{data_year}"
